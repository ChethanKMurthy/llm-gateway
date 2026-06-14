"""
The gateway orchestrator — the pipeline that ties every component together.

For each request it runs, in order, emitting a step into a human-readable `trace`
at every stage (this trace is what the dashboard animates):

  1. Security scan        — block secrets / injection, redact PII
  2. Classify intent      — picks the candidate model set & cache threshold
  3. Token optimization   — strip dead tokens, bank the savings
  4. Cache lookup         — L1 exact, then L2 semantic (adaptive threshold)
  5. Cost/latency predict — per-candidate, for the router & the dashboard
  6. Route                — Thompson-sampling bandit over healthy candidates
  7. Call + smart fallback— on failure, walk the candidate chain
  8. Quality score        — score the answer
  9. Learn                — feed reward to the router, adapt cache threshold,
                            store in cache, fingerprint, emit metrics

Everything is measured against a baseline ("send everything to the frontier
model") so savings are always quantified.
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple

from . import cost as costmod
from . import optimizer as opt
from . import quality as qual
from . import security as sec
from .cache import MultiLevelCache
from .classifier import PromptClassifier
from .config import BASELINE_MODEL, MODELS, ROUTING_CANDIDATES
from .embeddings import build_default_embedder
from .metrics import Metrics, RequestEvent
from .providers import ProviderRegistry
from .router import BanditRouter, reward_from


class Gateway:
    def __init__(self):
        self.embedder = build_default_embedder()
        self.classifier = PromptClassifier(self.embedder)
        self.cache = MultiLevelCache(self.embedder)
        self.router = BanditRouter()
        self.providers = ProviderRegistry()
        self.metrics = Metrics()
        self._id = 0

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    async def complete(self, prompt: str, *, team: str = "default", user: str = "anonymous",
                       force_model: Optional[str] = None, optimize: bool = True,
                       use_cache: bool = True) -> dict:
        rid = self._next_id()
        t_start = time.perf_counter()
        trace: List[dict] = []

        def step(stage, status, detail, data=None, t0=None):
            trace.append({
                "stage": stage, "status": status, "detail": detail,
                "data": data or {},
                "ms": round((time.perf_counter() - t0) * 1000, 2) if t0 else 0.0,
            })

        # ---- 1. Security ---------------------------------------------------
        ts = time.perf_counter()
        verdict = sec.scan(prompt)
        if verdict.action == "block":
            step("security", "blocked", verdict.reason, {"findings": verdict.findings,
                 "injection_score": verdict.injection_score}, ts)
            ev = RequestEvent(
                id=rid, ts=time.time(), intent="blocked", prompt_preview=prompt[:80],
                served_from="blocked", model=None, provider=None, cost_usd=0.0,
                baseline_usd=0.0, saved_usd=0.0, latency_ms=0.0, quality=0.0,
                cache_level=None, fallbacks=0, team=team, user=user, blocked=True,
                security_action="block",
            )
            self.metrics.record(ev)
            return self._result(rid, "[request blocked by security layer]", "blocked",
                                "blocked", None, None, 0, 0, 0, 0, 0, None, True, trace)
        working_prompt = verdict.sanitized_prompt or prompt
        step("security", "ok", verdict.reason,
             {"action": verdict.action, "redactions": verdict.redactions,
              "injection_score": verdict.injection_score, "findings": verdict.findings}, ts)

        # ---- 2. Classify ---------------------------------------------------
        ts = time.perf_counter()
        cls = self.classifier.classify(working_prompt)
        intent = cls.intent
        step("classify", "ok", f"intent = {intent} ({cls.confidence:.0%} conf)",
             {"intent": intent, "confidence": round(cls.confidence, 3),
              "distribution": {k: round(v, 3) for k, v in sorted(
                  cls.distribution.items(), key=lambda x: -x[1])[:4]}}, ts)

        # ---- 3. Optimize ---------------------------------------------------
        ts = time.perf_counter()
        if optimize:
            o = opt.optimize(working_prompt)
            working_prompt = o.optimized
            step("optimize", "ok", f"-{o.tokens_saved} tokens ({o.pct_saved}%)",
                 {"before": o.tokens_before, "after": o.tokens_after,
                  "pct": o.pct_saved}, ts)
        else:
            step("optimize", "skipped", "optimization disabled", {}, ts)

        # baseline cost: the naive "frontier for everything" approach
        baseline_est = costmod.predict(BASELINE_MODEL, working_prompt, intent)
        baseline_cost = baseline_est.cost_usd

        # ---- 4. Cache ------------------------------------------------------
        ts = time.perf_counter()
        emb = self.embedder.embed(working_prompt)
        if use_cache:
            cr = self.cache.lookup(working_prompt, intent, emb)
            if cr.hit:
                latency = (time.perf_counter() - t_start) * 1000
                step("cache", "hit", f"{cr.level} hit (sim={cr.similarity:.3f} ≥ {cr.threshold:.3f})",
                     {"level": cr.level, "similarity": round(cr.similarity, 4),
                      "threshold": round(cr.threshold, 4)}, ts)
                entry = cr.entry
                saved = entry.cost_saved
                ev = RequestEvent(
                    id=rid, ts=time.time(), intent=intent, prompt_preview=working_prompt[:80],
                    served_from=cr.level, model=entry.model, provider=MODELS[entry.model].provider,
                    cost_usd=0.0, baseline_usd=baseline_cost, saved_usd=saved,
                    latency_ms=round(latency, 1), quality=entry.quality, cache_level=cr.level,
                    fallbacks=0, team=team, user=user, security_action=verdict.action,
                )
                self.metrics.record(ev)
                return self._result(rid, entry.response, intent, cr.level, entry.model,
                                    MODELS[entry.model].provider, 0.0, baseline_cost, saved,
                                    round(latency, 1), entry.quality, cr.level, False, trace)
            step("cache", "miss", f"no hit (best sim={cr.best_similarity:.3f} < {cr.threshold:.3f})",
                 {"best_similarity": round(cr.best_similarity, 4),
                  "threshold": round(cr.threshold, 4), "entries": self.cache.size}, ts)
        else:
            step("cache", "skipped", "cache disabled", {}, ts)

        # ---- 5. Predict ----------------------------------------------------
        ts = time.perf_counter()
        candidates = ROUTING_CANDIDATES.get(intent, [BASELINE_MODEL])
        estimates = costmod.predict_all(working_prompt, intent, candidates)
        step("predict", "ok", f"costed {len(candidates)} candidates",
             {"candidates": {m: {"cost": round(e.cost_usd, 6), "p95_ms": e.p95_ms}
                             for m, e in estimates.items()}}, ts)

        # ---- 6. Route ------------------------------------------------------
        ts = time.perf_counter()
        health = {p: ok["available"] for p, ok in self.providers.breaker.health().items()}
        decision = self.router.route(intent, health, force_model=force_model)
        step("route", "explore" if decision.explored else "exploit",
             f"→ {decision.chosen}  ({decision.rationale})",
             {"chosen": decision.chosen, "candidates": decision.candidates,
              "expected_reward": decision.expected_reward,
              "sampled": decision.sampled_scores, "excluded": decision.excluded}, ts)

        # ---- 7. Call + smart fallback -------------------------------------
        ts = time.perf_counter()
        chain = self._fallback_chain(decision.chosen, intent)
        resp, fallbacks, attempts = await self._call_with_fallback(chain, working_prompt, intent)
        if not resp.ok:
            latency = (time.perf_counter() - t_start) * 1000
            step("call", "error", f"all {len(attempts)} providers failed", {"attempts": attempts}, ts)
            ev = RequestEvent(
                id=rid, ts=time.time(), intent=intent, prompt_preview=working_prompt[:80],
                served_from="error", model=resp.model, provider=resp.provider,
                cost_usd=0.0, baseline_usd=baseline_cost, saved_usd=0.0,
                latency_ms=round(latency, 1), quality=0.0, cache_level=None,
                fallbacks=fallbacks, team=team, user=user, security_action=verdict.action,
            )
            self.metrics.record(ev)
            return self._result(rid, "[all providers unavailable]", intent, "error",
                                resp.model, resp.provider, 0, baseline_cost, 0,
                                round(latency, 1), 0, None, False, trace)
        step("call", "ok",
             f"{resp.model} responded in {resp.latency_ms:.0f}ms"
             + (f" after {fallbacks} fallback(s)" if fallbacks else ""),
             {"model": resp.model, "provider": resp.provider, "latency_ms": resp.latency_ms,
              "tokens_in": resp.tokens_in, "tokens_out": resp.tokens_out,
              "live": not resp.simulated, "attempts": attempts}, ts)

        actual_cost = costmod.actual_cost(resp.model, resp.tokens_in, resp.tokens_out)

        # ---- 8. Quality ----------------------------------------------------
        ts = time.perf_counter()
        q = qual.score(working_prompt, resp.text, intent, self.embedder)
        step("quality", "ok", f"score = {q.score:.2f}",
             {"score": q.score, "relevance": q.relevance, "completeness": q.completeness,
              "hallucination_risk": q.hallucination_risk}, ts)

        # ---- 9. Learn ------------------------------------------------------
        ts = time.perf_counter()
        r = reward_from(q.score, actual_cost, resp.latency_ms)
        self.router.update(intent, resp.model, r)
        if use_cache:
            self.cache.store(working_prompt, resp.text, intent, resp.model, emb, actual_cost, q.score)
            self.cache.adapt_threshold(intent, q.score)
        step("learn", "ok",
             f"reward={r:.3f} → router updated; cached; threshold[{intent}]={self.cache.threshold_for(intent):.3f}",
             {"reward": round(r, 4), "new_threshold": round(self.cache.threshold_for(intent), 4)}, ts)

        latency = (time.perf_counter() - t_start) * 1000
        saved = max(0.0, baseline_cost - actual_cost)
        ev = RequestEvent(
            id=rid, ts=time.time(), intent=intent, prompt_preview=working_prompt[:80],
            served_from="model", model=resp.model, provider=resp.provider,
            cost_usd=actual_cost, baseline_usd=baseline_cost, saved_usd=saved,
            latency_ms=round(latency, 1), quality=q.score, cache_level=None,
            fallbacks=fallbacks, team=team, user=user, security_action=verdict.action,
        )
        self.metrics.record(ev, tokens_in=resp.tokens_in, tokens_out=resp.tokens_out)

        return self._result(rid, resp.text, intent, "model", resp.model, resp.provider,
                            actual_cost, baseline_cost, saved, round(latency, 1),
                            q.score, None, False, trace)

    # ---- fallback --------------------------------------------------------- #
    def _fallback_chain(self, primary: str, intent: str) -> List[str]:
        chain = [primary]
        for m in ROUTING_CANDIDATES.get(intent, []):
            if m not in chain:
                chain.append(m)
        # last resort: the free local model never costs anything to try
        if "llama-3.1-8b-local" not in chain:
            chain.append("llama-3.1-8b-local")
        return chain

    async def _call_with_fallback(self, chain: List[str], prompt: str, intent: str):
        """Walk the candidate chain until one succeeds. `fallbacks` = the number
        of failed/skipped attempts that preceded the eventual winner."""
        attempts: List[dict] = []
        failed_before = 0
        last = None
        for model in chain:
            prov = MODELS[model].provider
            if not self.providers.breaker.available(prov):
                attempts.append({"model": model, "provider": prov, "result": "skipped (circuit open)"})
                failed_before += 1
                continue
            resp = await self.providers.call(model, prompt, intent)
            last = resp
            attempts.append({"model": model, "provider": prov,
                             "result": "ok" if resp.ok else f"fail: {resp.error}",
                             "latency_ms": resp.latency_ms})
            if resp.ok:
                return resp, failed_before, attempts
            failed_before += 1
        return (last or _dead(chain[-1])), failed_before, attempts

    # ---- helpers ---------------------------------------------------------- #
    def _result(self, rid, response, intent, served_from, model, provider, cost,
                baseline, saved, latency, quality, cache_level, blocked, trace) -> dict:
        return {
            "id": rid, "response": response, "intent": intent, "served_from": served_from,
            "model": model, "provider": provider, "cost_usd": round(cost, 6),
            "baseline_usd": round(baseline, 6), "saved_usd": round(saved, 6),
            "latency_ms": latency, "quality": quality, "cache_level": cache_level,
            "blocked": blocked, "trace": trace,
        }


def _dead(model: str):
    from .providers import ProviderResponse
    return ProviderResponse(False, "", model, MODELS[model].provider, 0, 0, 0.0, error="unavailable")
