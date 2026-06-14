"""
Provider abstraction + adapters + circuit breaker.

A `Provider` turns (model, prompt) into a response with token accounting and
latency. Two worlds, one interface:

  - RealProvider     : live calls to Anthropic / OpenAI / Google / Ollama over
                       httpx, used automatically for any model whose provider has
                       an API key in the environment (ANTHROPIC_API_KEY, etc.).
  - MockProvider     : a faithful *simulator* used when no key is present, so the
                       entire gateway — routing, caching, fallback, dashboards —
                       runs end-to-end offline with realistic latency, token
                       counts, content, and occasional failures. This is what
                       makes the demo work on any laptop with zero credentials.

The `CircuitBreaker` tracks per-provider health: after consecutive failures the
circuit opens and the fallback manager skips that provider until a cooldown
elapses. You can also force an "outage" from the API to demo failover live.
"""

from __future__ import annotations

import asyncio
import os
import random
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

from .config import MODELS, ModelSpec
from .cost import expected_output_tokens, estimate_tokens

# Keep the demo snappy: simulate a fraction of real latency, capped.
SIM_LATENCY_SCALE = float(os.getenv("GATEWAY_SIM_LATENCY_SCALE", "0.18"))
SIM_LATENCY_CAP_MS = 900.0


@dataclass
class ProviderResponse:
    ok: bool
    text: str
    model: str
    provider: str
    tokens_in: int
    tokens_out: int
    latency_ms: float
    error: Optional[str] = None
    simulated: bool = True


# --------------------------------------------------------------------------- #
#  Mock content generation — realistic, intent-aware, deterministic-ish
# --------------------------------------------------------------------------- #

_MOCK_TEMPLATES: Dict[str, str] = {
    "code": (
        "Here's a clean implementation:\n\n```python\n"
        "def solution(data):\n    # {hint}\n    result = sorted(data)\n    return result\n```\n\n"
        "It runs in O(n log n) time. Edge cases (empty input, duplicates) are handled."
    ),
    "math": (
        "Let's work through it step by step.\n\n1. Restate the problem and identify the unknown.\n"
        "2. Apply the relevant identity.\n3. Simplify.\n\n**Answer:** the result is 42 "
        "(rounded), derived from the closed-form expression above."
    ),
    "reasoning": (
        "There are three considerations worth weighing.\n\nFirst, the upside is meaningful but "
        "front-loaded. Second, the risk concentrates in the tail. Third, reversibility is high, "
        "which lowers the cost of being wrong.\n\nOn balance, I'd proceed incrementally — capture "
        "most of the value while keeping the downside bounded."
    ),
    "translation": "Voici la traduction : « {hint} » — rendue de manière naturelle et idiomatique.",
    "summarization": (
        "**TL;DR**\n- The core claim holds under the stated assumptions.\n"
        "- Two caveats limit generality.\n- Net: actionable, with one open question."
    ),
    "rag": (
        "Based on the provided context, the policy states that {hint}. The relevant passage "
        "specifies a 30-day window and two documented exceptions. No other section contradicts this."
    ),
    "qa": "The answer is Paris — it has been the capital since 987 AD.",
    "chat": "Happy to help! {hint} Want me to go a little deeper on any part of that?",
    "classification": "positive",
}


def _mock_text(intent: str, prompt: str) -> str:
    hint = prompt.strip().split("\n")[0][:80]
    tmpl = _MOCK_TEMPLATES.get(intent, "Here is a helpful, on-topic response to your request.")
    return tmpl.format(hint=hint)


class MockProvider:
    """A simulator faithful enough to drive the whole system end-to-end."""

    def __init__(self, seed: int = 7):
        self.rng = random.Random(seed)

    async def generate(self, spec: ModelSpec, prompt: str, intent: str,
                       force_fail: bool = False) -> ProviderResponse:
        tin = estimate_tokens(prompt)
        tout = expected_output_tokens(intent)
        # jitter output length a bit so cost/latency vary run-to-run
        tout = max(4, int(tout * self.rng.uniform(0.8, 1.25)))

        mean = spec.latency_base_ms + spec.latency_per_tok_ms * tout
        latency = mean * self.rng.lognormvariate(0.0, 0.30)  # realistic skew

        # simulate the wall-clock (scaled so demos stay fast)
        await asyncio.sleep(min(SIM_LATENCY_CAP_MS, latency * SIM_LATENCY_SCALE) / 1000.0)

        # simulate failure: forced outage, or random per model reliability
        failed = force_fail or (self.rng.random() > spec.reliability)
        if failed:
            return ProviderResponse(
                ok=False, text="", model=spec.id, provider=spec.provider,
                tokens_in=tin, tokens_out=0, latency_ms=round(latency, 1),
                error="upstream_timeout" if not force_fail else "forced_outage",
            )

        text = _mock_text(intent, prompt)
        return ProviderResponse(
            ok=True, text=text, model=spec.id, provider=spec.provider,
            tokens_in=tin, tokens_out=len(text.split()) + tout // 3,
            latency_ms=round(latency, 1), simulated=True,
        )


# --------------------------------------------------------------------------- #
#  Real provider adapters (used automatically when API keys are present)
# --------------------------------------------------------------------------- #

class RealProvider:
    """Live calls. Only constructed for providers that have a key configured."""

    def __init__(self):
        import httpx
        self._client = httpx.AsyncClient(timeout=60.0)

    async def generate(self, spec: ModelSpec, prompt: str, intent: str,
                       force_fail: bool = False) -> ProviderResponse:
        if force_fail:
            return ProviderResponse(False, "", spec.id, spec.provider,
                                    estimate_tokens(prompt), 0, 0.0,
                                    error="forced_outage", simulated=False)
        t0 = time.perf_counter()
        try:
            if spec.provider == "anthropic":
                text, tin, tout = await self._anthropic(spec, prompt)
            elif spec.provider == "openai":
                text, tin, tout = await self._openai(spec, prompt)
            elif spec.provider == "xai":
                text, tin, tout = await self._xai(spec, prompt)
            elif spec.provider == "groq":
                text, tin, tout = await self._groq(spec, prompt)
            elif spec.provider == "google":
                text, tin, tout = await self._google(spec, prompt)
            elif spec.provider == "ollama":
                text, tin, tout = await self._ollama(spec, prompt)
            else:
                raise RuntimeError(f"no adapter for provider {spec.provider}")
            latency = (time.perf_counter() - t0) * 1000.0
            return ProviderResponse(True, text, spec.id, spec.provider, tin, tout,
                                    round(latency, 1), simulated=False)
        except Exception as e:  # noqa: BLE001
            latency = (time.perf_counter() - t0) * 1000.0
            return ProviderResponse(False, "", spec.id, spec.provider,
                                    estimate_tokens(prompt), 0, round(latency, 1),
                                    error=str(e)[:140], simulated=False)

    async def _anthropic(self, spec, prompt):
        key = os.environ["ANTHROPIC_API_KEY"]
        r = await self._client.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": spec.id, "max_tokens": 1024,
                  "messages": [{"role": "user", "content": prompt}]},
        )
        r.raise_for_status()
        d = r.json()
        text = "".join(b.get("text", "") for b in d.get("content", []))
        u = d.get("usage", {})
        return text, u.get("input_tokens", estimate_tokens(prompt)), u.get("output_tokens", len(text.split()))

    async def _openai(self, spec, prompt):
        key = os.environ["OPENAI_API_KEY"]
        r = await self._client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"authorization": f"Bearer {key}", "content-type": "application/json"},
            json={"model": spec.id, "messages": [{"role": "user", "content": prompt}]},
        )
        r.raise_for_status()
        d = r.json()
        text = d["choices"][0]["message"]["content"]
        u = d.get("usage", {})
        return text, u.get("prompt_tokens", estimate_tokens(prompt)), u.get("completion_tokens", len(text.split()))

    async def _xai(self, spec, prompt):
        # xAI's API is OpenAI-compatible; same request/response shape, different host.
        key = os.environ.get("XAI_API_KEY") or os.environ["GROK_API_KEY"]
        r = await self._client.post(
            "https://api.x.ai/v1/chat/completions",
            headers={"authorization": f"Bearer {key}", "content-type": "application/json"},
            json={"model": spec.id, "max_tokens": 1024,
                  "messages": [{"role": "user", "content": prompt}]},
        )
        r.raise_for_status()
        d = r.json()
        text = d["choices"][0]["message"]["content"]
        u = d.get("usage", {})
        return text, u.get("prompt_tokens", estimate_tokens(prompt)), u.get("completion_tokens", len(text.split()))

    async def _groq(self, spec, prompt):
        # GroqCloud is OpenAI-compatible; spec.id is the exact Groq model id.
        key = os.environ["GROQ_API_KEY"]
        r = await self._client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"authorization": f"Bearer {key}", "content-type": "application/json"},
            json={"model": spec.id, "max_tokens": 1024,
                  "messages": [{"role": "user", "content": prompt}]},
        )
        r.raise_for_status()
        d = r.json()
        text = d["choices"][0]["message"]["content"]
        u = d.get("usage", {})
        return text, u.get("prompt_tokens", estimate_tokens(prompt)), u.get("completion_tokens", len(text.split()))

    async def _google(self, spec, prompt):
        key = os.environ["GEMINI_API_KEY"]
        r = await self._client.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{spec.id}:generateContent?key={key}",
            json={"contents": [{"parts": [{"text": prompt}]}]},
        )
        r.raise_for_status()
        d = r.json()
        text = d["candidates"][0]["content"]["parts"][0]["text"]
        return text, estimate_tokens(prompt), len(text.split())

    async def _ollama(self, spec, prompt):
        base = os.getenv("OLLAMA_HOST", "http://localhost:11434")
        model = spec.id.replace("-local", "").replace("llama-3.1-8b", "llama3.1:8b")
        r = await self._client.post(
            f"{base}/api/generate", json={"model": model, "prompt": prompt, "stream": False},
        )
        r.raise_for_status()
        d = r.json()
        text = d.get("response", "")
        return text, estimate_tokens(prompt), len(text.split())


# --------------------------------------------------------------------------- #
#  Circuit breaker + registry
# --------------------------------------------------------------------------- #

@dataclass
class BreakerState:
    failures: int = 0
    opened_at: float = 0.0
    forced_outage: bool = False


class CircuitBreaker:
    def __init__(self, threshold: int = 3, cooldown_s: float = 15.0):
        self.threshold = threshold
        self.cooldown = cooldown_s
        self._state: Dict[str, BreakerState] = {}

    def _s(self, provider: str) -> BreakerState:
        return self._state.setdefault(provider, BreakerState())

    def available(self, provider: str) -> bool:
        s = self._s(provider)
        if s.forced_outage:
            return False
        if s.failures >= self.threshold:
            if (time.time() - s.opened_at) >= self.cooldown:
                s.failures = self.threshold - 1   # half-open: allow a probe
                return True
            return False
        return True

    def record(self, provider: str, ok: bool) -> None:
        s = self._s(provider)
        if ok:
            s.failures = 0
        else:
            s.failures += 1
            if s.failures >= self.threshold:
                s.opened_at = time.time()

    def set_outage(self, provider: str, on: bool) -> None:
        self._s(provider).forced_outage = on
        if on:
            self._s(provider).opened_at = time.time()

    def health(self) -> Dict[str, dict]:
        out = {}
        for prov in {m.provider for m in MODELS.values()}:
            s = self._s(prov)
            out[prov] = {
                "available": self.available(prov),
                "failures": s.failures,
                "forced_outage": s.forced_outage,
                "status": "outage" if s.forced_outage else
                          ("degraded" if s.failures >= self.threshold else
                           ("watch" if s.failures > 0 else "healthy")),
            }
        return out


class ProviderRegistry:
    """Routes a model call to a real adapter (if keyed) or the mock simulator."""

    def __init__(self):
        self.mock = MockProvider()
        self.breaker = CircuitBreaker()
        self._real: Optional[RealProvider] = None
        self._keyed = {
            "anthropic": bool(os.getenv("ANTHROPIC_API_KEY")),
            "openai": bool(os.getenv("OPENAI_API_KEY")),
            "xai": bool(os.getenv("XAI_API_KEY") or os.getenv("GROK_API_KEY")),
            "groq": bool(os.getenv("GROQ_API_KEY")),
            "google": bool(os.getenv("GEMINI_API_KEY")),
            "ollama": bool(os.getenv("OLLAMA_HOST")),
        }
        if any(self._keyed.values()):
            try:
                self._real = RealProvider()
            except Exception:
                self._real = None

    def is_live(self, provider: str) -> bool:
        return self._real is not None and self._keyed.get(provider, False)

    async def call(self, model_id: str, prompt: str, intent: str,
                   force_fail: bool = False) -> ProviderResponse:
        spec = MODELS[model_id]
        if self.is_live(spec.provider):
            resp = await self._real.generate(spec, prompt, intent, force_fail)
        else:
            resp = await self.mock.generate(spec, prompt, intent, force_fail)
        self.breaker.record(spec.provider, resp.ok)
        return resp

    def mode(self) -> Dict[str, str]:
        return {
            prov: ("live" if self.is_live(prov) else "simulated")
            for prov in {m.provider for m in MODELS.values()}
        }
