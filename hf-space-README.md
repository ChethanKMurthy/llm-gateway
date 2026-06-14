---
title: Intelligent LLM Gateway
emoji: 🚦
colorFrom: red
colorTo: gray
sdk: docker
app_port: 8000
pinned: true
short_description: Multi-provider LLM gateway — semantic cache, routing, cost, observability
---

# Intelligent Multi-Provider LLM Gateway

Live demo. Semantic caching, dynamic model routing, cost/latency prediction,
security guardrails, and a reinforcement-learning router — with a live console.

The dashboard self-drives (simulated providers, no keys). Open it and watch a
request flow through all nine stages. Health: `/health` · `/ready` · Prometheus
metrics: `/metrics`.

> When deploying to Hugging Face Spaces, this file becomes the Space's `README.md`
> (the front-matter above configures the Docker Space). Everything else builds
> from the repo's `Dockerfile`.
