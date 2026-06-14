"""
Routing engine + reinforcement-learning router (PRD Features 1 & 15).

The router decides which model serves a request. It is a **contextual bandit**:

  context (arm-set) = the request's intent  -> config.ROUTING_CANDIDATES[intent]
  arms              = the candidate models for that intent
  reward            = w_q*quality - w_c*norm_cost - w_l*norm_latency   (computed
                      after the call, in the gateway, and fed back via `update`)

Action selection uses **Thompson sampling** over a Gaussian belief about each
arm's mean reward (Welford online mean/variance). Each arm is seeded with an
informed prior from `quality_prior` minus expected cost/latency penalties and a
small pseudo-count, so day-one routing is already sensible — then the posterior
sharpens toward whatever actually performs best for *this* traffic. Unavailable
providers (open circuit / forced outage) are filtered out before sampling, so the
same machinery delivers health-aware **smart fallback** (PRD Feature 6).

This is genuinely "the router learns the optimal provider over time" — and the
learned policy (best arm per intent + confidence) is exposed for the dashboard.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .config import (
    COST_ANCHOR_USD,
    EPSILON,
    LATENCY_ANCHOR_MS,
    MODELS,
    ROUTER_WEIGHTS,
    ROUTING_CANDIDATES,
)
from .cost import CostEstimate


def reward_from(quality: float, cost_usd: float, latency_ms: float) -> float:
    """Scalarized multi-objective reward in roughly [0, 1]."""
    norm_cost = min(1.0, cost_usd / COST_ANCHOR_USD)
    norm_lat = min(1.0, latency_ms / LATENCY_ANCHOR_MS)
    w = ROUTER_WEIGHTS
    r = w["quality"] * quality - w["cost"] * norm_cost - w["latency"] * norm_lat
    # squash into [0,1] for stable bandit stats
    return max(0.0, min(1.0, (r + w["cost"] + w["latency"]) / (w["quality"] + w["cost"] + w["latency"])))


@dataclass
class ArmStat:
    n: float = 0.0
    mean: float = 0.0
    m2: float = 0.0          # for online variance (Welford)

    def update(self, reward: float) -> None:
        self.n += 1
        delta = reward - self.mean
        self.mean += delta / self.n
        self.m2 += delta * (reward - self.mean)

    @property
    def std(self) -> float:
        if self.n < 2:
            return 0.25            # wide prior uncertainty
        return math.sqrt(max(1e-6, self.m2 / (self.n - 1)))


@dataclass
class RouteDecision:
    intent: str
    chosen: str
    candidates: List[str]
    sampled_scores: Dict[str, float]
    expected_reward: Dict[str, float]
    explored: bool
    excluded: List[str] = field(default_factory=list)
    rationale: str = ""


class BanditRouter:
    def __init__(self, seed: int = 13):
        self.rng = random.Random(seed)
        self.arms: Dict[str, Dict[str, ArmStat]] = {}
        self.decisions = 0
        self.explorations = 0
        self._seed_priors()

    def _seed_priors(self) -> None:
        """Informed priors so routing is sensible before any learning."""
        for intent, cands in ROUTING_CANDIDATES.items():
            self.arms[intent] = {}
            for m in cands:
                spec = MODELS[m]
                # rough prior reward: quality minus a typical cost/latency penalty
                approx_cost = spec.price_for(400, 200)
                approx_lat = spec.latency_base_ms + spec.latency_per_tok_ms * 200
                prior = reward_from(spec.quality_prior, approx_cost, approx_lat)
                st = ArmStat()
                st.n = 2.0          # weak pseudo-count
                st.mean = prior
                self.arms[intent][m] = st

    def route(self, intent: str, available_providers: Dict[str, bool],
              force_model: Optional[str] = None) -> RouteDecision:
        cands = list(ROUTING_CANDIDATES.get(intent, [])) or [next(iter(MODELS))]
        excluded = [m for m in cands if not available_providers.get(MODELS[m].provider, True)]
        usable = [m for m in cands if m not in excluded]
        if not usable:
            usable = cands  # everything's down; pick the least-bad anyway

        self.decisions += 1

        # explicit override (used by the playground "force model" control)
        if force_model and force_model in MODELS:
            return RouteDecision(intent, force_model, cands, {}, {}, False,
                                 excluded, rationale="forced by caller")

        stats = self.arms.setdefault(intent, {})
        expected = {m: stats.get(m, ArmStat()).mean for m in usable}

        # Thompson sampling: draw a plausible reward for each arm, take the best
        sampled: Dict[str, float] = {}
        for m in usable:
            st = stats.setdefault(m, ArmStat())
            sigma = st.std / math.sqrt(max(1.0, st.n))
            sampled[m] = self.rng.gauss(st.mean, max(0.02, sigma))

        explored = self.rng.random() < EPSILON
        if explored:
            chosen = self.rng.choice(usable)         # forced exploration
            self.explorations += 1
        else:
            chosen = max(sampled, key=sampled.get)   # exploit the sampled belief

        greedy = max(expected, key=expected.get) if expected else chosen
        rationale = (
            f"exploration draw" if explored else
            f"highest sampled reward; posterior-best is {greedy} "
            f"(E[r]={expected.get(greedy, 0):.3f})"
        )
        if excluded:
            rationale += f"; skipped {', '.join(excluded)} (provider down)"

        return RouteDecision(
            intent=intent, chosen=chosen, candidates=cands,
            sampled_scores={k: round(v, 4) for k, v in sampled.items()},
            expected_reward={k: round(v, 4) for k, v in expected.items()},
            explored=explored, excluded=excluded, rationale=rationale,
        )

    def update(self, intent: str, model: str, reward: float) -> None:
        self.arms.setdefault(intent, {}).setdefault(model, ArmStat()).update(reward)

    # ---- introspection for the dashboard ---------------------------------- #
    def policy(self) -> Dict[str, dict]:
        out = {}
        for intent, arms in self.arms.items():
            if not arms:
                continue
            best = max(arms, key=lambda m: arms[m].mean)
            out[intent] = {
                "best_model": best,
                "expected_reward": round(arms[best].mean, 4),
                "arms": {
                    m: {"E_reward": round(st.mean, 4), "pulls": int(st.n), "std": round(st.std, 3)}
                    for m, st in sorted(arms.items(), key=lambda kv: -kv[1].mean)
                },
            }
        return out

    def stats(self) -> dict:
        return {
            "decisions": self.decisions,
            "explorations": self.explorations,
            "exploration_rate": round(self.explorations / self.decisions, 3) if self.decisions else 0.0,
        }
