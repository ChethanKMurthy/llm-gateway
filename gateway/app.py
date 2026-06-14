"""
FastAPI application — the gateway's HTTP surface + the dashboard host.

Endpoints
  POST /v1/complete        main gateway call (returns response + full trace)
  GET  /api/summary        headline metrics (spend, savings, hit-rate, latency)
  GET  /api/breakdowns     by provider / model / intent / team / user
  GET  /api/providers      live health, mode (live vs simulated), per-provider P50/95/99
  GET  /api/policy         the RL router's learned policy per intent
  GET  /api/cache          cache stats + adaptive thresholds
  GET  /api/models         the model catalog (pricing, tiers)
  GET  /api/recent         recent request events (live feed)
  GET  /api/timeseries     rolling 1s-bucket series for charts
  GET  /api/stream         Server-Sent Events: one message per request, live
  POST /api/outage         toggle a forced provider outage (demo failover)
  GET  /                    the dashboard SPA
"""

from __future__ import annotations

import asyncio
import json
import os
import random
from contextlib import asynccontextmanager

try:                                  # auto-load a .env if present (best-effort)
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .config import MODELS
from .gateway import Gateway
from .models import CompletionRequest


async def _autodemo():
    """Self-driving traffic so a hosted demo lands alive. Enabled with
    GATEWAY_AUTODEMO=1 — an initial burst to populate the dashboard, then a
    gentle trickle (with the occasional blocked attack) to keep the feed moving.
    Safe to run in simulated mode (no API spend); avoid with live keys."""
    from scripts.traffic import build_stream, TEAMS, USERS
    rng = random.Random(7)
    for _, prompt, _ in build_stream(140, rng):       # warm-up burst
        try:
            await gw.complete(prompt, team=rng.choice(TEAMS), user=rng.choice(USERS))
        except Exception:
            pass
    while True:                                        # live trickle
        try:
            _, prompt, _ = build_stream(1, rng)[0]
            await gw.complete(prompt, team=rng.choice(TEAMS), user=rng.choice(USERS))
        except Exception:
            pass
        await asyncio.sleep(rng.uniform(0.7, 2.2))


@asynccontextmanager
async def lifespan(app: "FastAPI"):
    task = asyncio.create_task(_autodemo()) if os.getenv("GATEWAY_AUTODEMO") else None
    try:
        yield
    finally:
        if task:
            task.cancel()


app = FastAPI(title="Intelligent LLM Gateway", version="1.0.0", lifespan=lifespan)
gw = Gateway()

_FRONTEND = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")


@app.post("/v1/complete")
async def complete(req: CompletionRequest):
    return await gw.complete(
        req.prompt, team=req.team, user=req.user, force_model=req.force_model,
        optimize=req.optimize, use_cache=req.use_cache,
    )


@app.get("/api/summary")
async def summary():
    s = gw.metrics.summary()
    s["mode"] = gw.providers.mode()
    s["embedder"] = type(gw.embedder).__name__
    s["router"] = gw.router.stats()
    return s


@app.get("/api/breakdowns")
async def breakdowns():
    return gw.metrics.breakdowns()


@app.get("/api/providers")
async def providers():
    return {
        "health": gw.providers.breaker.health(),
        "mode": gw.providers.mode(),
        "latency": gw.metrics.provider_latencies(),
    }


@app.get("/api/policy")
async def policy():
    return {"policy": gw.router.policy(), "stats": gw.router.stats()}


@app.get("/api/cache")
async def cache():
    return gw.cache.stats()


@app.get("/api/models")
async def models():
    return {
        mid: {
            "provider": m.provider, "tier": m.tier,
            "price_in": m.price_in, "price_out": m.price_out,
            "quality_prior": m.quality_prior, "context_window": m.context_window,
            "good_at": m.good_at,
        }
        for mid, m in MODELS.items()
    }


@app.get("/api/recent")
async def recent(n: int = 40):
    return gw.metrics.recent(n)


@app.get("/api/timeseries")
async def timeseries():
    return gw.metrics.time_series()


@app.post("/api/outage")
async def outage(provider: str, on: bool = True):
    gw.providers.breaker.set_outage(provider, on)
    return {"provider": provider, "outage": on, "health": gw.providers.breaker.health()}


@app.get("/api/stream")
async def stream():
    async def event_gen():
        q = gw.metrics.subscribe()
        try:
            # greet immediately so the client knows it's connected
            yield f"data: {json.dumps({'type': 'hello'})}\n\n"
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"data: {json.dumps(ev)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            gw.metrics.unsubscribe(q)

    return StreamingResponse(event_gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/health")
async def health():
    """Liveness probe (k8s/Render/Fly style)."""
    return {"status": "ok", "models": len(MODELS), "cache_entries": gw.cache.size}


@app.get("/ready")
async def ready():
    """Readiness probe — the gateway is ready once the embedder/classifier are built."""
    ok = gw.embedder is not None and bool(gw.classifier.centroids)
    return {"ready": ok, "embedder": type(gw.embedder).__name__}


@app.get("/metrics")
async def metrics_prometheus():
    """Prometheus exposition. Point a Prometheus/Grafana/OTel-collector scrape here.
    This is the observability contract an SRE/MLOps team would wire into alerting."""
    from fastapi.responses import PlainTextResponse
    s = gw.metrics
    lat = s.latency_percentiles()
    lines: list[str] = []

    def metric(name, value, help_, typ="gauge", labels=""):
        if help_:
            lines.append(f"# HELP {name} {help_}")
            lines.append(f"# TYPE {name} {typ}")
        lines.append(f"{name}{('{' + labels + '}') if labels else ''} {value}")

    metric("gateway_requests_total", s.total, "Total requests processed", "counter")
    metric("gateway_cache_hits_total", s.l1, "Cache hits by level", "counter", 'level="l1"')
    lines.append(f'gateway_cache_hits_total{{level="l2"}} {s.l2}')
    metric("gateway_model_calls_total", s.model_calls, "Requests served by a live/sim model", "counter")
    metric("gateway_blocked_total", s.blocked, "Requests blocked by the security layer", "counter")
    metric("gateway_errors_total", s.errors, "Requests that errored after fallback", "counter")
    metric("gateway_fallbacks_total", s.fallbacks, "Provider fallbacks taken", "counter")
    metric("gateway_cost_usd_total", round(s.cost_real, 6), "Actual spend (USD)", "counter")
    metric("gateway_baseline_cost_usd_total", round(s.cost_baseline, 6), "Frontier-only baseline spend (USD)", "counter")
    metric("gateway_saved_usd_total", round(s.saved, 6), "Cost saved vs baseline (USD)", "counter")
    metric("gateway_tokens_total", s.tokens_in, "Tokens processed", "counter", 'direction="in"')
    lines.append(f'gateway_tokens_total{{direction="out"}} {s.tokens_out}')
    metric("gateway_cache_hit_ratio", round(s.hit_rate, 4), "Cache hit ratio [0,1]")
    metric("gateway_savings_ratio", round(s.savings_pct / 100, 4), "Cost savings ratio [0,1]")
    metric("gateway_latency_milliseconds", lat["p50"], "Request latency by quantile (ms)", "summary", 'quantile="0.5"')
    lines.append(f'gateway_latency_milliseconds{{quantile="0.95"}} {lat["p95"]}')
    lines.append(f'gateway_latency_milliseconds{{quantile="0.99"}} {lat["p99"]}')

    health_ = gw.providers.breaker.health()
    lines.append("# HELP gateway_provider_up Provider availability (1=up, 0=down)")
    lines.append("# TYPE gateway_provider_up gauge")
    for prov, h in health_.items():
        lines.append(f'gateway_provider_up{{provider="{prov}"}} {1 if h["available"] else 0}')

    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")


# ---- static / SPA --------------------------------------------------------- #
if os.path.isdir(_FRONTEND):
    app.mount("/static", StaticFiles(directory=_FRONTEND), name="static")


def _page(name: str):
    path = os.path.join(_FRONTEND, name)
    if os.path.exists(path):
        return FileResponse(path)
    return {"message": "Intelligent LLM Gateway API.", "docs": "/docs", "missing": name}


@app.get("/")
async def index():
    return _page("index.html")


@app.get("/about")
async def about():
    return _page("about.html")


@app.get("/console")
async def console():
    return _page("console.html")
