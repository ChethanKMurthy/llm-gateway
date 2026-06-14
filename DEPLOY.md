# Deploying a live demo link

The gateway is **one always-on container** (FastAPI + an SSE stream + in-memory
metrics). It is *not* serverless — it needs one persistent process — so deploy it
to a container host. With `GATEWAY_AUTODEMO=1` (the Dockerfile default) the
dashboard **self-drives**: a visitor lands on a live, moving demo with no input.

> Run the public demo **fully simulated — no API keys**. It's free, abuse-proof,
> and the routing/caching/security/cost logic is all still real. Never put a Groq/
> Anthropic key on a public URL; anyone could drain it.

`.env` is gitignored, so your key never leaves your laptop.

---

## The MLOps story this deployment tells

The point isn't just "it's online" — it's that the repo is shaped like something a
platform team would actually run. Point a reviewer at:

| Signal | Where |
|---|---|
| **Containerized**, non-root, layer-cached | `Dockerfile` (+ `HEALTHCHECK`) |
| **CI/CD gate** — test → build → smoke-test the image | `.github/workflows/ci.yml` |
| **Test suite** — caching, routing, guardrails, failover | `tests/` (`pytest -q`) |
| **Liveness / readiness probes** | `GET /health`, `GET /ready` |
| **Prometheus metrics** for scrape → Grafana/alerting | `GET /metrics` |
| **Infrastructure as code** | `render.yaml`, `Makefile` |
| **12-factor config** (env-driven, `$PORT`, no baked secrets) | `Dockerfile`, `.env.example` |
| **Reliability**: circuit breaker, health-aware failover | `providers.py` |
| **Observability of the model layer**: cost/latency/quality per request, learned routing policy | the console |

That last row is the ML-specific part: it's not just app metrics, it's **LLM cost,
latency percentiles, cache hit-rate, response-quality scoring, and a routing policy
that learns** — the things an *LLMOps* team has to watch but usually can't see.

---

## Best free options (ranked for this project)

### 1. Hugging Face Spaces (Docker) — best for an AI/ML audience ⭐
Free, **persistent** (no idle spin-down), and ML-native — deploying here is itself
an MLOps signal that AI companies recognize. Steps:

1. Push this repo to GitHub (below), or upload files directly to the Space.
2. Create a **Space** → SDK **Docker** → name it.
3. Make the Space's `README.md` the contents of **`hf-space-README.md`** (its
   front-matter sets `sdk: docker`, `app_port: 8000`). Add the rest of the repo.
4. HF builds the `Dockerfile` and serves at `https://<you>-llm-gateway.hf.space`.

Optional: add a provider key as a **Space secret** only if you accept the rate-limit/cost risk.

### 2. Render — simplest, free, IaC blueprint
Free web service, **reads `render.yaml`** (infra as code). One caveat: free
instances spin down after ~15 min idle and cold-start in ~50s.

1. Push to GitHub.
2. Render → **New + → Blueprint** → pick the repo → it provisions from `render.yaml`.
3. You get `https://llm-gateway-xxxx.onrender.com`.

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy)

### 3. Fly.io — most "infra cred", global, real health checks
Generous free allowance, Docker-native, regions, and platform health checks that
read the container `HEALTHCHECK`. `fly launch` (detects the `Dockerfile`) →
`fly deploy`. Set `fly secrets set GATEWAY_AUTODEMO=1` if needed.

### 4. Google Cloud Run — serverless containers, GCP signal
Generous always-free tier. `gcloud run deploy --source .`. Caveat: scale-to-zero
means in-memory metrics reset on cold start (the autodemo repopulates them); fine
for a demo, and it shows you understand the statefulness tradeoff.

**Why not Vercel/Netlify?** They're serverless/edge — no persistent process, so the
SSE stream and in-memory metrics don't fit. Knowing *why* is the MLOps point.

---

## First push (GitHub)

```bash
git add -A && git commit -m "deploy artifacts"
gh repo create llm-gateway --public --source=. --remote=origin --push   # gh CLI
# or: git remote add origin https://github.com/<you>/llm-gateway.git && git push -u origin main
```

Confirm `.env` is **not** staged (`git status`) before pushing.

---

## Local Docker (test the exact image first)

```bash
make docker-build && make docker-run     # http://localhost:8000  (simulated + autodemo)
curl localhost:8000/health && curl localhost:8000/metrics | head
```
