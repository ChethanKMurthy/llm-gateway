<div align="center">

# Intelligent Multi-Provider LLM Gateway

**Semantic caching · dynamic routing · cost optimization · enterprise observability**

A production-shaped control plane that sits between your application and every
LLM provider, and makes each request *cheaper, faster, safer, and observable* —
with a live dashboard that shows it happening in real time.

`FastAPI + numpy` backend · zero-dependency dashboard · runs offline on any laptop

</div>

---

## Why this exists

Most "LLM caching" projects stop at *"embed the prompt, check cosine similarity,
return on a hit."* That solves duplicate requests. It does not solve what real AI
platform teams actually fight: **cost, latency, reliability, model selection,
governance, and observability** — all at once.

This project is the next thing up: an **intelligent gateway**. Every request flows
through a nine-stage pipeline before (and instead of) hitting a model:

```
client → [ security → classify → optimize → cache → predict → route → call → quality → learn ] → provider
```

Each stage is a real, inspectable component — and the dashboard animates the whole
pipeline for any prompt you type.

This implements the full PRD/TRD vision (all 15 features below), grounded in a
research pass on the 2026 provider landscape (Portkey, LiteLLM, GPTCache, RouteLLM,
vCache) and current pricing.

---

## Quickstart

```bash
./run.sh demo
```

That sets up a virtualenv, starts the gateway on **http://127.0.0.1:8000**, and
streams realistic traffic so the dashboard is alive immediately. Open the URL.

Other entry points:

```bash
./run.sh                 # just serve (empty dashboard you can drive by hand)
./run.sh traffic         # send traffic to an already-running server
python -m scripts.traffic --inproc --n 300 --outage   # no server, drive the engine directly
```

**No API keys required.** Every provider falls back to a faithful simulator, so
the entire system — routing, caching, failover, dashboards — runs end-to-end
offline. Drop in a real key (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, …) and *that*
provider switches to live calls automatically; everything else stays simulated.

---

## The dashboard

A single, dependency-free page (no CDN, no build step) that talks to the gateway:

- **Headline KPIs** — real spend vs frontier-only baseline, $ saved, cache
  hit-rate, p95 latency, requests, projected monthly bill.
- **Interactive playground** — type a prompt and watch it flow through all nine
  stages, each lighting up with its real trace (intent, cache decision, routing
  rationale, cost, quality). Toggle caching/optimization, or force a model.
- **Live request feed** — every request streamed over Server-Sent Events.
- **Real-time charts** — spend-vs-baseline area, cache-composition donut, latency
  p50/p95/p99 (all hand-rolled on `<canvas>`).
- **RL router policy** — the learned best model per intent, with each arm's
  expected reward and pull count, updating as traffic flows.
- **Provider health** — circuit-breaker status + per-provider percentiles. Click
  **fail** on any provider to trigger live failover and watch the router reroute.
- **Cost attribution** — spend by model, requests by intent, savings by team.

---

## Architecture

```
                         ┌──────────────────────────────────────────────┐
   client  ──POST──▶     │                  Gateway                      │
                         │                                              │
                         │  1 Security    secrets/injection/PII guard   │
                         │  2 Classify    intent → candidates + thresh  │
                         │  3 Optimize    strip dead tokens             │
                         │  4 Cache       L1 exact → L2 semantic        │──hit──▶ return
                         │  5 Predict     per-model cost + p50/95/99    │
                         │  6 Route       Thompson-sampling bandit      │
                         │  7 Call        + health-aware smart fallback │──▶ Anthropic / OpenAI / Groq
                         │  8 Quality     relevance/halluc/format score │     xAI / Google / Ollama
                         │  9 Learn       reward → router; adapt cache  │     (live or simulated)
                         │                                              │
                         └───────────────┬──────────────────────────────┘
                                         │ events
                                         ▼
                         Metrics ──SSE──▶ Dashboard (spend, savings, latency, policy…)
```

Every module is independently swappable behind a clean interface:

| File | Responsibility |
|------|----------------|
| `gateway/gateway.py` | orchestrator — runs the pipeline, emits the trace |
| `gateway/embeddings.py` | feature-hashing embedder (+ auto MiniLM upgrade) |
| `gateway/classifier.py` | nearest-centroid intent classifier + lexical boosts |
| `gateway/cache.py` | L1/L2/L3 cache + adaptive per-intent thresholds |
| `gateway/router.py` | contextual bandit (Thompson sampling) + learned policy |
| `gateway/cost.py` | token estimation, cost + latency (log-normal) prediction |
| `gateway/security.py` | injection/jailbreak scoring, PII redaction, secret blocking |
| `gateway/optimizer.py` | conservative token/prompt compression |
| `gateway/quality.py` | model-free response quality scoring |
| `gateway/providers.py` | real adapters + simulator + circuit breaker |
| `gateway/metrics.py` | percentiles, time-series, breakdowns, SSE pub/sub |
| `gateway/app.py` | FastAPI surface + dashboard host |

---

## How the PRD/TRD maps to code

| # | PRD Feature | Where | Status |
|---|-------------|-------|--------|
| 1 | Dynamic model routing | `router.py` | ✅ contextual bandit over per-intent candidates |
| 2 | Multi-level cache (L1/L2/L3) | `cache.py` | ✅ exact hash → semantic cosine → answer cache |
| 3 | Prompt classification engine | `classifier.py` | ✅ nearest-centroid + lexical, full distribution |
| 4 | Cost prediction engine | `cost.py` | ✅ per-model $ before execution |
| 5 | Latency prediction (P50/95/99) | `cost.py`, `metrics.py` | ✅ log-normal model + measured percentiles |
| 6 | Smart fallback | `gateway.py`, `providers.py` | ✅ health-aware candidate chain |
| 7 | Prompt fingerprinting | `cache.py`, `metrics.py` | ✅ hash + embedding + intent + cost stored |
| 8 | Cache learning system | `cache.py` | ✅ adaptive per-intent thresholds |
| 9 | Response quality scoring | `quality.py` | ✅ relevance/completeness/halluc/format |
| 10 | Real-time cost dashboard | `frontend/` | ✅ spend/savings/team/model/projection |
| 11 | Prompt security layer | `security.py` | ✅ injection, PII redaction, secret block |
| 12 | Token optimization | `optimizer.py` | ✅ lossless-ish compression with realized savings |
| 13 | Streaming/replay cache | `cache.py` | ◑ responses cached & replayed (not chunk-level streaming) |
| 14 | OpenTelemetry-style observability | `metrics.py` | ✅ span-shaped events, SSE pipeline (in-memory) |
| 15 | Reinforcement-learning router | `router.py` | ✅ Thompson-sampling bandit, learns optimal arm per intent |

---

## What's real vs simulated (read this)

Senior reviewers care about this distinction, so it's stated plainly:

**Real algorithms, running for every request:**
- feature-hashing embeddings + cosine semantic search (the same technique as
  scikit-learn's `HashingVectorizer`)
- nearest-centroid intent classification
- multi-level cache with LRU eviction and adaptive thresholds
- a genuine Thompson-sampling contextual bandit that learns from reward feedback
- circuit breaker, health-aware fallback, percentile latency tracking
- token-count and cost math against the **real 2026 price table** (`config.py`)
- the security regex/heuristic guardrails

**Simulated by default (swappable for real with one env var):**
- the **LLM responses themselves**. With no API key, `providers.py` returns a
  faithful simulator — realistic latency (log-normal), token counts, intent-aware
  content, and occasional failures — so the gateway runs offline. Set
  `ANTHROPIC_API_KEY` etc. and that provider makes live calls instead.
- the default **embedder** is lexical feature-hashing (zero dependencies). Its
  cosine scale is compressed vs a neural embedder, and it can't resolve negation
  (`open` vs `close a file` ≈ 0.77) — the known failure mode of lexical
  embeddings. `pip install sentence-transformers` and the gateway auto-upgrades
  to MiniLM; retune thresholds upward toward 0.9–0.98.

**Demo numbers honesty:** the bundled traffic is intentionally cache-friendly
(Zipfian popularity + paraphrases), so it shows ~75–86% hit-rate. Real mixed
production traffic is more like **20–45%** (classification/FAQ higher, RAG/chat
lower). Routing savings of **50–70%** are well-supported by RouteLLM-class results;
the headline "~95% cheaper than frontier-only" combines caching *and* routing on
favorable traffic — directional, not a guarantee.

---

## API

```bash
# route a request through the gateway (returns the response + full 9-step trace)
curl -X POST localhost:8000/v1/complete \
  -H 'content-type: application/json' \
  -d '{"prompt":"Write a python function to reverse a linked list","team":"search"}'
```

| Endpoint | Purpose |
|----------|---------|
| `POST /v1/complete` | main gateway call (response + trace) |
| `GET /api/summary` | headline metrics |
| `GET /api/breakdowns` | by provider / model / intent / team / user |
| `GET /api/providers` | health, mode, per-provider percentiles |
| `GET /api/policy` | the RL router's learned policy |
| `GET /api/cache` | cache stats + adaptive thresholds |
| `GET /api/models` | model catalog + pricing |
| `GET /api/recent` · `/api/timeseries` | feed + charts data |
| `GET /api/stream` | SSE: one event per request |
| `POST /api/outage?provider=openai&on=true` | force an outage to demo failover |
| `GET /docs` | interactive OpenAPI docs |

---

## Design choices a reviewer might ask about

- **Why a bandit, not a trained router?** A contextual bandit needs no offline
  training set, learns online from real reward, and is honest about exploration.
  Arms are seeded with informed priors (quality − expected cost/latency) so day-one
  routing is already sensible, then the posterior sharpens to *your* traffic.
- **Why global semantic search, not intent-scoped?** Similarity is itself the
  safety gate (cross-topic prompts have ~0 cosine), so searching the whole store
  makes the cache robust to classifier drift while per-content thresholds keep
  strict types (code, translation) strict.
- **Why no vector DB?** The L2 store is a single numpy matrix — one vectorized dot
  product per lookup, fast to tens of thousands of entries, and trivially swapped
  for Qdrant/Redis-Vector. Right altitude for the problem.
- **Why hand-rolled charts?** Zero CDN means the dashboard renders with no network
  — important for a live demo on conference wifi.

---

## Project layout

```
gateway/          backend package (one file per concern)
frontend/         index.html · styles.css · app.js  (no build step)
scripts/traffic.py  realistic demo traffic generator
run.sh            one-command setup + serve + demo
requirements.txt  fastapi · uvicorn · numpy · httpx
```

Built from the PRD/TRD as a complete, runnable product.
