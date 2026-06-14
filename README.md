# Gateway

**An intelligent control plane for LLM calls — semantic caching, model routing, cost control, and observability in a single hop.**

![Gateway hero](docs/hero.gif)

### ▶ Live demo — **[llm-gateway-w26g.onrender.com](https://llm-gateway-w26g.onrender.com)**  ·  [open the console →](https://llm-gateway-w26g.onrender.com/console)

> It's on Render's free tier, so if it's been idle give the first request ~40 seconds to
> wake up. The demo runs fully simulated (no API keys) and self-drives, so you land on a
> live, moving dashboard.

[![Live demo](https://img.shields.io/badge/live%20demo-online-2ea043)](https://llm-gateway-w26g.onrender.com)
[![CI](https://github.com/ChethanKMurthy/llm-gateway/actions/workflows/ci.yml/badge.svg)](https://github.com/ChethanKMurthy/llm-gateway/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.12%2B-blue)
![Tests](https://img.shields.io/badge/tests-17%20passing-brightgreen)
![Dependencies](https://img.shields.io/badge/frontend%20deps-0-orange)

Most teams send every prompt to one big model and pay for it twice — once because the
model is overkill for the task, and again because they re-answer questions they already
answered an hour ago. Gateway is the layer that fixes both, plus the unglamorous stuff
that actually breaks in production: guardrails, failover, and metrics you can act on.

It's a running system, not a slide deck. Type a prompt into the console and watch it
move through nine stages before a single token gets billed.

---

## TL;DR

- **What it is.** A multi-provider LLM gateway — one endpoint in front of Anthropic,
  OpenAI, Groq, Google, xAI, and local Ollama.
- **What it does, per request.** Classify the intent → check two layers of cache →
  predict the cost → route to the model that's genuinely best for *that* task → call it
  with automatic failover → score the answer → learn from it. Every step is logged into
  a trace you can read.
- **Why it's more than a cache.** The router is a Thompson-sampling bandit that learns
  the best model per task type from live reward. The cache does real embedding-based
  semantic matching with thresholds that tune themselves. Everything is exposed as
  Prometheus metrics and a live console.
- **Numbers (demo traffic).** ~75–90% of requests served from cache or a cheaper model;
  p50 for a cache hit is ~1 ms versus seconds for a model call. Real mixed traffic lands
  closer to 20–45% — I'm explicit about that below.
- **Stack.** Python · FastAPI · numpy. The dashboard is hand-written vanilla JS with
  **zero dependencies** — no CDN, no build step, charts drawn on `<canvas>`. Runs fully
  offline with simulated providers; drop in an API key and that provider goes live.
- **Try it.** Open the **[live demo](https://llm-gateway-w26g.onrender.com/console)**, or run
  locally with `make dev` → http://localhost:8000.

---

## See it work

![Playground running a request](docs/playground.gif)

That's the console playground: one prompt, the whole pipeline, the real trace. A cache
hit short-circuits and returns in about a millisecond; a miss gets classified, priced,
routed, called, scored, and fed back into the router. **[Try it live →](https://llm-gateway-w26g.onrender.com/console)**

---

## Why I built it

I kept seeing the same shape of waste. A translation, an FAQ lookup, and a genuinely hard
reasoning task would all get fired at the most expensive frontier model, billed at the top
rate, and — for anything repetitive — re-answered from scratch every time. Meanwhile the
operational basics were missing: when a provider had a bad five minutes, the whole feature
went down with it, and nobody could tell you what any of it cost until the invoice landed.

A "semantic cache" solves part of that. But the interesting version is the whole control
plane around the cache: routing, cost prediction, guardrails, failover, and observability,
all in one place. So that's what this is.

---

## How it works

Every request runs the same nine-stage pipeline. The console animates it live; here's the
same thing on the landing page:

![The nine-stage pipeline](docs/pipeline.gif)

```
client ──▶ │ security │ classify │ optimize │ cache │ predict │ route │ call │ quality │ learn │ ──▶ provider
                                                 │
                                            (hit ⇒ return ~1 ms)
```

### 1 · Security (`gateway/security.py`)
Runs before anything leaves the building. It scores prompt-injection / jailbreak attempts
and hard-blocks anything above a threshold, redacts PII (emails, phones, SSNs, cards) in
place, and refuses to forward leaked secrets — API keys, AWS keys, private keys, including
the modern formats (`sk-proj-…`, `gsk_…`, `xai-…`, Google `AIza…`). The point is that a
careless prompt can't exfiltrate a credential to a third-party provider.

### 2 · Classification (`gateway/classifier.py`)
Maps the prompt to one of nine intents (code, math, reasoning, translation, summarization,
RAG, QA, chat, classification). It's nearest-centroid classification — I embed a set of
prototype phrases per intent once, average them into a centroid, and score each prompt by
cosine similarity — plus a handful of high-precision lexical signals (a code fence, a
"translate to French", a math operator). The intent decides two things downstream: which
models are even candidates, and how strict the cache should be.

### 3 · Token optimization (`gateway/optimizer.py`)
A conservative, lossless-ish rewrite: collapse whitespace, strip filler ("please could you
kindly just…"), de-duplicate repeated instructions — but never touch anything inside a code
fence. It reports the realized token savings, which the cost engine credits.

### 4 · The multi-level cache (`gateway/cache.py`) — the part I'm most happy with
Two layers, checked cheapest first:

- **L1 — exact.** A hash of the normalized prompt + intent. O(1), ~0.1 ms. Catches the
  byte-identical repeats.
- **L2 — semantic.** The prompt is embedded and compared by cosine similarity against every
  stored entry. A hit means *"someone asked an equivalent question before."* The whole store
  is a single numpy matrix, so a lookup is one vectorized dot product — fast to tens of
  thousands of entries with no vector database, and trivially swappable for Qdrant or
  Redis-Vector later.

Two design choices worth calling out:

- **Thresholds are per-intent and adaptive.** Translation and code have to be near-exact
  (a one-word change matters); FAQ-style chat can be loose. Each intent starts at a sensible
  threshold and then *self-tunes*: if a semantic hit later scores as low-quality, that
  intent gets stricter. This is the "cache learning" idea — a simple, stable controller
  instead of a single magic number.
- **Search is global, gated by the matched entry's threshold.** Similarity is itself the
  safety rail (two unrelated prompts have ~0 cosine), so searching the whole store makes the
  cache robust to the classifier occasionally disagreeing with itself between paraphrases.

> The default embedder is a dependency-free feature-hashing vectorizer (the same trick as
> scikit-learn's `HashingVectorizer`). It captures lexical and near-paraphrase similarity
> well and is deterministic across processes. Its honest limitation is negation — *"open a
> file"* vs *"close a file"* land close — which is the known failure mode of lexical
> embeddings. Install `sentence-transformers` and it auto-upgrades to MiniLM; retune the
> thresholds upward.

### 5 · Cost & latency prediction (`gateway/cost.py`)
Before any call, for every candidate model: estimated input/output tokens, dollar cost
against a real 2026 price table, and a latency distribution (p50/p95/p99) modelled as
log-normal — the right-skewed shape real inference latency actually takes. This is what the
router optimizes against, and what the dashboard projects into a monthly bill.

### 6 · Routing — a bandit that learns (`gateway/router.py`)
This is the bit that makes it a *platform* and not a lookup table. Routing is a **contextual
bandit**:

- **context** = the request's intent → its candidate model set
- **arms** = those candidate models
- **reward** = `w_q·quality − w_c·cost − w_l·latency`, computed *after* the call

Action selection is **Thompson sampling** over a running estimate of each arm's mean reward.
Each arm is seeded with an informed prior (its quality prior minus an expected cost/latency
penalty) so day-one routing is already sensible — then the posterior sharpens toward
whatever actually performs on the traffic it sees. It needs no offline training set, it's
honest about exploration, and the learned policy (best model per intent, with confidence) is
exposed in the console. Knock a provider offline and unavailable arms are filtered out before
sampling — so the same machinery gives you health-aware failover for free.

### 7 · Call + failover (`gateway/providers.py`)
A `Provider` turns (model, prompt) into a response with token accounting and latency. Two
worlds behind one interface: real adapters (Anthropic / OpenAI / Groq / Google / Ollama over
`httpx`) used automatically for any provider that has a key in the environment, and a
faithful simulator used otherwise. A **circuit breaker** tracks per-provider health; after
consecutive failures it opens and the request walks a fallback chain until something answers.

### 8 · Quality scoring (`gateway/quality.py`)
Every response gets a cheap, model-free score in [0, 1]: relevance (prompt↔response cosine),
completeness (length appropriate for the intent), a hallucination-risk heuristic (hedging,
fabricated-citation patterns, degenerate repetition), and format fit. It feeds the router's
reward and the cache's threshold controller.

### 9 · Learn
The reward updates the bandit, the response is written to both cache layers, the intent's
threshold is nudged, and a span-shaped event is emitted to the metrics layer. The system is
a little smarter for the next request.

---

## What's real vs simulated

I'd want to know this if I were reviewing it, so here it is plainly.

**Real, and running on every request:** the embeddings and semantic search, the classifier,
the multi-level cache and its adaptive thresholds, the Thompson-sampling bandit, the circuit
breaker and failover, the cost/latency math against a real price table, and the security
guardrails.

**Simulated by default:** the **LLM responses themselves**. With no API key the simulator
returns realistic latency, token counts, intent-aware content, and the occasional failure —
so the whole system runs offline with zero credentials. Set `ANTHROPIC_API_KEY`,
`GROQ_API_KEY`, etc. and that provider makes live calls instead; the rest stay simulated.

**On the numbers:** the bundled demo traffic is intentionally cache-friendly (a Zipfian
popularity curve plus paraphrases), which is why it shows a 75–90% hit-rate. Real mixed
production traffic is more like **20–45%** — higher for classification and FAQ, lower for
RAG and open chat. Routing savings of 50–70% are well supported by RouteLLM-class results.
I'd rather show calibrated ranges than a single hero number.

---

## Observability

![The live console, streaming](docs/dashboard.gif)

The console is the real control plane, streamed over server-sent events: spend vs a
frontier-only baseline, cache hit-rate by level, latency percentiles, the learned routing
policy, provider health, and cost attribution by model / intent / team. For machines there's
a Prometheus endpoint:

```bash
curl localhost:8000/metrics
# gateway_requests_total 1240
# gateway_cache_hits_total{level="l1"} 612
# gateway_saved_usd_total 1.83
# gateway_latency_milliseconds{quantile="0.95"} 412.7
# gateway_provider_up{provider="groq"} 1
```

Plus `/health` (liveness) and `/ready` (readiness) for container orchestrators.

---

## Running it

```bash
make install      # venv + dependencies (FastAPI, uvicorn, numpy, httpx)
make dev          # serve on :8000 with self-driving demo traffic
# open http://localhost:8000   → landing
#      http://localhost:8000/console → the live dashboard
```

No keys needed. To make a provider live, add its key to a `.env` (gitignored) and restart:

```bash
echo 'GROQ_API_KEY=gsk_...' >> .env
make serve
```

Drive traffic at a running server, with a simulated provider outage halfway through to show
failover:

```bash
make traffic
```

---

## Tests & CI

```bash
make test    # 17 tests: caching, routing, guardrails, cost, classification, failover
```

CI runs on every push: the suite on Python 3.12 and 3.13, then a Docker build that boots the
container and smoke-tests `/health`, `/ready`, and `/metrics`. (Writing these caught a real
bug — the secret detector was missing modern `sk-proj-`/`gsk_` key formats.)

---

## Project layout

```
gateway/            the backend — one file per concern
  gateway.py        orchestrator: runs the pipeline, builds the trace
  classifier.py     intent classification
  cache.py          L1 exact + L2 semantic cache, adaptive thresholds
  router.py         Thompson-sampling contextual bandit
  cost.py           token / cost / latency prediction
  security.py       injection scoring, PII redaction, secret blocking
  optimizer.py      token compression
  quality.py        response quality scoring
  providers.py      real adapters + simulator + circuit breaker
  metrics.py        percentiles, time-series, SSE, Prometheus
  config.py         model catalog, pricing, routing policy, thresholds
frontend/           landing + about + console (vanilla JS, no build)
tests/              pytest suite
scripts/traffic.py  realistic traffic generator
```

---

## A few decisions, if you're curious

**Why a bandit instead of a trained router?** No offline dataset required, it adapts to your
traffic online, and it's honest about exploration. Informed priors mean it's useful on day
one rather than after a training run.

**Why no vector DB?** At this scale a numpy matrix and one dot product per lookup is faster
than the network hop to a vector service, and the interface is a clean seam to swap in Qdrant
or Redis-Vector when the working set outgrows memory. Right tool for the size.

**Why hand-rolled charts and zero frontend dependencies?** A page about fast, efficient
infrastructure should itself be fast — and it means the console renders with no network at
all, which matters when you're demoing on conference wifi.

**Why ship a simulator at all?** So the entire system — routing, caching, failover, the whole
dashboard — runs end-to-end on any laptop with no credentials, while the exact same code path
goes live the moment a real key is present.

---

## Deploying a live demo

The instance above runs on **[Render](https://llm-gateway-w26g.onrender.com)** from the
`render.yaml` Blueprint in this repo. It's one always-on container — it holds the SSE stream,
in-memory metrics, and the bandit's learned state, so it's deliberately *not* serverless.

```bash
make docker-build && make docker-run    # run the exact image locally → http://localhost:8000
```

The same `Dockerfile` deploys to Hugging Face Spaces or Fly.io. Run any public demo
**simulated, with no keys** — it's free, abuse-proof, and the routing/caching/security/cost
logic is all still real. Full instructions in [DEPLOY.md](DEPLOY.md).
