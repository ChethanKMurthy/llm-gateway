"""
Observability / analytics engine (PRD Features 5, 7, 10, 14).

Aggregates everything the gateway does into the numbers an engineering manager
actually wants on a screen:

  - total requests, cache hit-rate (by level), error/fallback rate
  - real spend vs the naive "frontier-for-everything" baseline  -> $ saved
  - latency P50/P95/P99 (overall and per provider)
  - breakdowns by provider / model / intent / team / user
  - a rolling time-series for live charts
  - a bounded event log for the live request feed

It also implements a lightweight pub/sub so the API can stream events to the
dashboard over Server-Sent Events. This is the in-memory analog of an
OpenTelemetry pipeline; the event schema is OTel-span-shaped on purpose.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional

import numpy as np


@dataclass
class RequestEvent:
    id: int
    ts: float
    intent: str
    prompt_preview: str
    served_from: str            # "L1" | "L2" | "model" | "blocked" | "error"
    model: Optional[str]
    provider: Optional[str]
    cost_usd: float
    baseline_usd: float
    saved_usd: float
    latency_ms: float
    quality: float
    cache_level: Optional[str]
    fallbacks: int
    team: str
    user: str
    blocked: bool = False
    security_action: str = "allow"


def _percentile(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    return float(np.percentile(np.asarray(values), p))


class Metrics:
    def __init__(self, window: int = 4000):
        self._events: Deque[RequestEvent] = deque(maxlen=window)
        self._latencies: Deque[float] = deque(maxlen=window)
        self._lat_by_provider: Dict[str, Deque[float]] = defaultdict(lambda: deque(maxlen=1500))

        # running totals
        self.total = 0
        self.l1 = 0
        self.l2 = 0
        self.model_calls = 0
        self.blocked = 0
        self.errors = 0
        self.fallbacks = 0
        self.cost_real = 0.0
        self.cost_baseline = 0.0
        self.tokens_in = 0
        self.tokens_out = 0

        self.by_provider: Dict[str, dict] = defaultdict(lambda: {"requests": 0, "cost": 0.0, "errors": 0})
        self.by_model: Dict[str, dict] = defaultdict(lambda: {"requests": 0, "cost": 0.0})
        self.by_intent: Dict[str, int] = defaultdict(int)
        self.by_team: Dict[str, dict] = defaultdict(lambda: {"requests": 0, "cost": 0.0, "saved": 0.0})
        self.by_user: Dict[str, dict] = defaultdict(lambda: {"requests": 0, "cost": 0.0})

        # rolling time-series (1s buckets)
        self._series: Deque[dict] = deque(maxlen=180)
        self._bucket_start = time.time()
        self._bucket = self._fresh_bucket()

        self._subscribers: List[asyncio.Queue] = []
        self._t0 = time.time()

    def _fresh_bucket(self) -> dict:
        return {"t": int(time.time()), "requests": 0, "cost": 0.0, "saved": 0.0,
                "hits": 0, "latency_sum": 0.0, "latency_n": 0}

    # ---- ingestion -------------------------------------------------------- #
    def record(self, ev: RequestEvent, tokens_in: int = 0, tokens_out: int = 0) -> None:
        self.total += 1
        self._events.appendleft(ev)
        self.by_intent[ev.intent] += 1
        self.by_team[ev.team]["requests"] += 1
        self.by_team[ev.team]["cost"] += ev.cost_usd
        self.by_team[ev.team]["saved"] += ev.saved_usd
        self.by_user[ev.user]["requests"] += 1
        self.by_user[ev.user]["cost"] += ev.cost_usd

        self.cost_real += ev.cost_usd
        self.cost_baseline += ev.baseline_usd
        self.tokens_in += tokens_in
        self.tokens_out += tokens_out
        self.fallbacks += ev.fallbacks

        if ev.cache_level == "L1":
            self.l1 += 1
        elif ev.cache_level == "L2":
            self.l2 += 1
        elif ev.blocked:
            self.blocked += 1
        elif ev.served_from == "error":
            self.errors += 1
        else:
            self.model_calls += 1

        if ev.latency_ms > 0:
            self._latencies.append(ev.latency_ms)
        if ev.provider:
            self.by_provider[ev.provider]["requests"] += 1
            self.by_provider[ev.provider]["cost"] += ev.cost_usd
            if ev.served_from == "error":
                self.by_provider[ev.provider]["errors"] += 1
            self._lat_by_provider[ev.provider].append(ev.latency_ms)
        if ev.model:
            self.by_model[ev.model]["requests"] += 1
            self.by_model[ev.model]["cost"] += ev.cost_usd

        self._roll_series(ev)
        self._publish(ev)

    def _roll_series(self, ev: RequestEvent) -> None:
        now = time.time()
        if now - self._bucket_start >= 1.0:
            self._series.append(self._bucket)
            self._bucket = self._fresh_bucket()
            self._bucket_start = now
        b = self._bucket
        b["requests"] += 1
        b["cost"] += ev.cost_usd
        b["saved"] += ev.saved_usd
        if ev.cache_level in ("L1", "L2"):
            b["hits"] += 1
        if ev.latency_ms > 0:
            b["latency_sum"] += ev.latency_ms
            b["latency_n"] += 1

    # ---- pub/sub for SSE -------------------------------------------------- #
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=64)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self._subscribers:
            self._subscribers.remove(q)

    def _publish(self, ev: RequestEvent) -> None:
        payload = ev.__dict__
        for q in list(self._subscribers):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass

    # ---- reads ------------------------------------------------------------ #
    @property
    def cache_hits(self) -> int:
        return self.l1 + self.l2

    @property
    def hit_rate(self) -> float:
        cacheable = self.total - self.blocked
        return self.cache_hits / cacheable if cacheable else 0.0

    @property
    def saved(self) -> float:
        return max(0.0, self.cost_baseline - self.cost_real)

    @property
    def savings_pct(self) -> float:
        return (self.saved / self.cost_baseline * 100.0) if self.cost_baseline else 0.0

    def latency_percentiles(self) -> dict:
        lat = list(self._latencies)
        return {
            "p50": round(_percentile(lat, 50), 1),
            "p95": round(_percentile(lat, 95), 1),
            "p99": round(_percentile(lat, 99), 1),
        }

    def recent(self, n: int = 40) -> List[dict]:
        return [e.__dict__ for e in list(self._events)[:n]]

    def time_series(self) -> List[dict]:
        out = []
        for b in list(self._series) + [self._bucket]:
            n = b["latency_n"] or 1
            out.append({
                "t": b["t"], "requests": b["requests"],
                "cost": round(b["cost"], 6), "saved": round(b["saved"], 6),
                "hits": b["hits"],
                "avg_latency": round(b["latency_sum"] / n, 1),
            })
        return out

    def summary(self) -> dict:
        uptime = max(1.0, time.time() - self._t0)
        return {
            "total_requests": self.total,
            "cache_hits": self.cache_hits,
            "l1_hits": self.l1,
            "l2_hits": self.l2,
            "model_calls": self.model_calls,
            "blocked": self.blocked,
            "errors": self.errors,
            "fallbacks": self.fallbacks,
            "hit_rate": round(self.hit_rate, 4),
            "cost_real": round(self.cost_real, 6),
            "cost_baseline": round(self.cost_baseline, 6),
            "saved": round(self.saved, 6),
            "savings_pct": round(self.savings_pct, 2),
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "rps": round(self.total / uptime, 2),
            "latency": self.latency_percentiles(),
            "projected_monthly_real": round(self.cost_real / uptime * 86400 * 30, 2),
            "projected_monthly_baseline": round(self.cost_baseline / uptime * 86400 * 30, 2),
        }

    def provider_latencies(self) -> Dict[str, dict]:
        out = {}
        for prov, lat in self._lat_by_provider.items():
            vals = list(lat)
            out[prov] = {
                "p50": round(_percentile(vals, 50), 1),
                "p95": round(_percentile(vals, 95), 1),
                "p99": round(_percentile(vals, 99), 1),
                "n": len(vals),
            }
        return out

    def breakdowns(self) -> dict:
        return {
            "by_provider": {k: {"requests": v["requests"], "cost": round(v["cost"], 6),
                                "errors": v["errors"]} for k, v in self.by_provider.items()},
            "by_model": {k: {"requests": v["requests"], "cost": round(v["cost"], 6)}
                         for k, v in self.by_model.items()},
            "by_intent": dict(self.by_intent),
            "by_team": {k: {"requests": v["requests"], "cost": round(v["cost"], 6),
                            "saved": round(v["saved"], 6)} for k, v in self.by_team.items()},
            "by_user": {k: {"requests": v["requests"], "cost": round(v["cost"], 6)}
                        for k, v in sorted(self.by_user.items(), key=lambda kv: -kv[1]["cost"])[:10]},
        }
