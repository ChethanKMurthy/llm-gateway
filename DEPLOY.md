# Deploying the gateway (recruiter-facing demo)

The app is a single always-on container (FastAPI + a server-sent-events stream +
in-memory metrics). It needs **one persistent process** — not serverless — so use a
container host. With `GATEWAY_AUTODEMO=1` the dashboard **self-drives**: visitors
land on a live, moving demo with zero interaction.

> **Run the public demo fully simulated (no API keys).** It's free, abuse-proof,
> and always works. Never put your Groq/Anthropic key on a public URL — anyone
> could drain it. The Dockerfile defaults to simulated + autodemo already.

The repo ships ready to deploy: `Dockerfile`, `.dockerignore` (keeps `.env` out of
the image), and `render.yaml`. `.env` is gitignored, so your key never leaves your
laptop.

---

## Option A — Render (simplest, free) ⭐ recommended

1. Push this repo to GitHub (see "First push" below).
2. Go to **dashboard.render.com → New + → Blueprint**, pick the repo.
   Render reads `render.yaml` and provisions a free Docker web service.
3. Wait ~3 min for the build. You get a URL like
   `https://llm-gateway-xxxx.onrender.com`. Share it.

Free instances sleep after ~15 min idle and cold-start in ~50s — fine for a demo.
For an always-warm instance, bump the plan to Starter ($7/mo).

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy)

## Option B — Hugging Face Spaces (best for AI/ML recruiters, free)

A Docker Space is a great look for an AI infra demo. Create a **Docker** Space, then
push these files to it. The Space's `README.md` must start with this front matter
(prepend it, or keep a separate copy for the Space):

```yaml
---
title: Intelligent LLM Gateway
emoji: 🚦
colorFrom: red
colorTo: indigo
sdk: docker
app_port: 8000
pinned: true
---
```

HF builds the `Dockerfile` automatically and serves on the Space URL. Autodemo is
already on, so it's alive immediately. (Add keys as **Space secrets** only if you
truly want live calls — and rate-limit first.)

## Option C — Railway / Fly.io

- **Railway:** New Project → Deploy from GitHub repo. It detects the `Dockerfile`.
  Set `GATEWAY_AUTODEMO=1` (already in the Dockerfile). Generate a domain.
- **Fly.io:** `fly launch` (it reads the `Dockerfile`) → `fly deploy`. Set
  `fly secrets set GATEWAY_AUTODEMO=1` if needed.

## Option D — Local Docker (to test the exact image first)

```bash
docker build -t llm-gateway .
docker run -p 8000:8000 llm-gateway          # simulated + autodemo
# open http://localhost:8000
```

To run the image with live Groq locally (NOT for public hosting):

```bash
docker run -p 8000:8000 -e GROQ_API_KEY=gsk_xxx -e GATEWAY_AUTODEMO=0 llm-gateway
```

---

## First push (GitHub)

```bash
git init && git add -A && git commit -m "Intelligent LLM Gateway"
git branch -M main
git remote add origin https://github.com/<you>/llm-gateway.git
git push -u origin main
```

`.env` is gitignored — confirm with `git status` that it is **not** staged before
pushing.

---

## Notes for the demo URL

- The headline numbers (savings %, hit-rate) come from the autodemo's
  cache-friendly traffic. The README is explicit that real mixed traffic is more
  like 20–45% hit-rate — leave that note in; reviewers trust calibrated claims.
- The playground works on the hosted URL too: visitors can type prompts and watch
  the pipeline. In simulated mode responses are templated but the *routing, caching,
  security, cost, and latency logic is all real*.
