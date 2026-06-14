"""
Central configuration for the Intelligent LLM Gateway.

This is the single source of truth for:
  - the model catalog (pricing, latency profiles, capability priors)
  - intent taxonomy and per-intent routing policy
  - cache / security / routing tunables

Pricing is in USD per 1,000,000 tokens and reflects publicly listed rates for the
major providers in 2026. Numbers are easy to update in one place and everything
downstream (cost engine, router, dashboard) reads from here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


# --------------------------------------------------------------------------- #
#  Model catalog
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ModelSpec:
    id: str
    provider: str                 # anthropic | openai | google | ollama
    tier: str                     # frontier | balanced | fast | local
    # USD per 1M tokens
    price_in: float
    price_out: float
    # quality prior in [0, 1] — a *starting belief*; the RL router refines this
    quality_prior: float
    # latency model: base overhead (ms) + per-output-token cost (ms/token)
    latency_base_ms: float
    latency_per_tok_ms: float
    # reliability prior: probability a call succeeds (used by the mock provider
    # to simulate the real world, and by the circuit breaker as a prior)
    reliability: float
    context_window: int
    # intents this model is a strong default for (used to seed routing)
    good_at: List[str] = field(default_factory=list)

    def price_for(self, tokens_in: int, tokens_out: int) -> float:
        return (tokens_in / 1e6) * self.price_in + (tokens_out / 1e6) * self.price_out


# The catalog. A deliberately diverse fleet so routing has interesting choices:
# a frontier reasoner, a balanced workhorse, a cheap-fast model, and a free local model.
#
# Pricing reflects publicly listed rates as of June 2026 (USD / 1M tokens).
# Anthropic & Google figures are from official pricing pages; OpenAI 5.x from
# official, verified during research. Model IDs track the current flagships —
# update them in this one place as new versions ship.
MODELS: Dict[str, ModelSpec] = {
    # ---- Anthropic ----
    "claude-opus-4-8": ModelSpec(
        id="claude-opus-4-8", provider="anthropic", tier="frontier",
        price_in=5.00, price_out=25.00, quality_prior=0.97,
        latency_base_ms=650, latency_per_tok_ms=6.5, reliability=0.992,
        context_window=200_000, good_at=["reasoning", "code", "math"],
    ),
    "claude-sonnet-4-6": ModelSpec(
        id="claude-sonnet-4-6", provider="anthropic", tier="balanced",
        price_in=3.00, price_out=15.00, quality_prior=0.93,
        latency_base_ms=420, latency_per_tok_ms=4.2, reliability=0.994,
        context_window=200_000, good_at=["code", "reasoning", "rag", "summarization"],
    ),
    "claude-haiku-4-5": ModelSpec(
        id="claude-haiku-4-5", provider="anthropic", tier="fast",
        price_in=1.00, price_out=5.00, quality_prior=0.86,
        latency_base_ms=240, latency_per_tok_ms=2.1, reliability=0.995,
        context_window=200_000, good_at=["chat", "summarization", "qa", "classification"],
    ),
    # ---- OpenAI ----
    "gpt-5.5": ModelSpec(
        id="gpt-5.5", provider="openai", tier="frontier",
        price_in=5.00, price_out=30.00, quality_prior=0.96,
        latency_base_ms=720, latency_per_tok_ms=7.2, reliability=0.989,
        context_window=400_000, good_at=["reasoning", "math", "code"],
    ),
    "gpt-5.4-mini": ModelSpec(
        id="gpt-5.4-mini", provider="openai", tier="balanced",
        price_in=0.75, price_out=4.50, quality_prior=0.88,
        latency_base_ms=330, latency_per_tok_ms=3.0, reliability=0.992,
        context_window=400_000, good_at=["qa", "chat", "summarization", "rag"],
    ),
    "gpt-5.4-nano": ModelSpec(
        id="gpt-5.4-nano", provider="openai", tier="fast",
        price_in=0.20, price_out=1.25, quality_prior=0.79,
        latency_base_ms=180, latency_per_tok_ms=1.4, reliability=0.993,
        context_window=128_000, good_at=["classification", "qa", "chat"],
    ),
    # ---- Google ----
    "gemini-3.1-pro": ModelSpec(
        id="gemini-3.1-pro", provider="google", tier="frontier",
        price_in=2.00, price_out=12.00, quality_prior=0.94,
        latency_base_ms=600, latency_per_tok_ms=5.5, reliability=0.988,
        context_window=1_000_000, good_at=["reasoning", "rag", "translation"],
    ),
    "gemini-2.5-flash": ModelSpec(
        id="gemini-2.5-flash", provider="google", tier="fast",
        price_in=0.30, price_out=2.50, quality_prior=0.83,
        latency_base_ms=210, latency_per_tok_ms=1.8, reliability=0.991,
        context_window=1_000_000, good_at=["translation", "summarization", "chat", "qa"],
    ),
    # ---- xAI / Grok ----
    "grok-4.3": ModelSpec(
        id="grok-4.3", provider="xai", tier="frontier",
        price_in=1.25, price_out=2.50, quality_prior=0.94,
        latency_base_ms=580, latency_per_tok_ms=5.8, reliability=0.989,
        context_window=256_000, good_at=["reasoning", "code", "math", "chat"],
    ),
    "grok-4-fast": ModelSpec(
        id="grok-4-fast", provider="xai", tier="fast",
        price_in=0.20, price_out=0.50, quality_prior=0.83,
        latency_base_ms=190, latency_per_tok_ms=1.6, reliability=0.991,
        context_window=2_000_000, good_at=["qa", "chat", "summarization", "classification"],
    ),
    # ---- Groq (GroqCloud — open models on Groq's LPU; the speed play) ----
    # Latency profiles are deliberately low: Groq's whole value prop is tokens/sec,
    # so the router naturally favors these for latency-sensitive intents.
    "openai/gpt-oss-120b": ModelSpec(
        id="openai/gpt-oss-120b", provider="groq", tier="frontier",
        price_in=0.15, price_out=0.75, quality_prior=0.90,
        latency_base_ms=160, latency_per_tok_ms=1.2, reliability=0.988,
        context_window=131_000, good_at=["reasoning", "code", "math"],
    ),
    "llama-3.3-70b-versatile": ModelSpec(
        id="llama-3.3-70b-versatile", provider="groq", tier="balanced",
        price_in=0.59, price_out=0.79, quality_prior=0.85,
        latency_base_ms=120, latency_per_tok_ms=0.9, reliability=0.991,
        context_window=128_000, good_at=["rag", "summarization", "chat", "translation"],
    ),
    "qwen/qwen3-32b": ModelSpec(
        id="qwen/qwen3-32b", provider="groq", tier="balanced",
        price_in=0.29, price_out=0.59, quality_prior=0.84,
        latency_base_ms=130, latency_per_tok_ms=1.0, reliability=0.989,
        context_window=131_000, good_at=["code", "math", "reasoning"],
    ),
    "llama-3.1-8b-instant": ModelSpec(
        id="llama-3.1-8b-instant", provider="groq", tier="fast",
        price_in=0.05, price_out=0.08, quality_prior=0.75,
        latency_base_ms=90, latency_per_tok_ms=0.5, reliability=0.992,
        context_window=128_000, good_at=["qa", "chat", "classification", "summarization"],
    ),
    # ---- Local / private (zero marginal cost; only compute) ----
    "llama-3.1-8b-local": ModelSpec(
        id="llama-3.1-8b-local", provider="ollama", tier="local",
        price_in=0.0, price_out=0.0, quality_prior=0.70,
        latency_base_ms=120, latency_per_tok_ms=3.5, reliability=0.985,
        context_window=128_000, good_at=["chat", "qa", "classification", "private"],
    ),
}

DEFAULT_MODEL = "claude-sonnet-4-6"


# --------------------------------------------------------------------------- #
#  Intent taxonomy
# --------------------------------------------------------------------------- #

# The prompt classifier maps every request to one of these intents. The router
# uses the intent to pick a candidate set of models; the cache uses it to pick a
# similarity threshold (translation needs to be near-exact, FAQ can be loose).
INTENTS: List[str] = [
    "code", "math", "reasoning", "translation",
    "summarization", "rag", "qa", "chat", "classification",
]

# Candidate models per intent, in rough preference order. The router treats these
# as the "arms" of a contextual bandit and learns which one is actually best.
ROUTING_CANDIDATES: Dict[str, List[str]] = {
    "code":           ["claude-sonnet-4-6", "openai/gpt-oss-120b", "claude-opus-4-8", "qwen/qwen3-32b"],
    "math":           ["gpt-5.5", "openai/gpt-oss-120b", "claude-opus-4-8", "qwen/qwen3-32b"],
    "reasoning":      ["claude-opus-4-8", "gpt-5.5", "openai/gpt-oss-120b", "grok-4.3"],
    "translation":    ["gemini-2.5-flash", "llama-3.3-70b-versatile", "claude-haiku-4-5", "grok-4-fast"],
    "summarization":  ["llama-3.1-8b-instant", "claude-haiku-4-5", "llama-3.3-70b-versatile", "gpt-5.4-mini"],
    "rag":            ["claude-sonnet-4-6", "llama-3.3-70b-versatile", "gemini-3.1-pro", "grok-4.3"],
    "qa":             ["llama-3.1-8b-instant", "gpt-5.4-nano", "grok-4-fast", "claude-haiku-4-5"],
    "chat":           ["llama-3.1-8b-instant", "claude-haiku-4-5", "grok-4-fast", "llama-3.1-8b-local"],
    "classification": ["llama-3.1-8b-instant", "gpt-5.4-nano", "grok-4-fast", "llama-3.1-8b-local"],
}

# A naive baseline every demo compares against: "just send everything to the
# frontier model." This is what most teams do before they have a gateway.
BASELINE_MODEL = "gpt-5.5"


# --------------------------------------------------------------------------- #
#  Cache configuration
# --------------------------------------------------------------------------- #

# Per-intent *starting* semantic-similarity thresholds. A higher threshold means
# we only treat prompts as "the same" when they are very close. Translation and
# code must be near-exact (a one-word change matters); chat/FAQ can be loose.
# These are only seeds — the adaptive controller nudges them based on observed
# quality of cache hits.
#
# NOTE on calibration: these values are tuned to the DEFAULT HashingEmbedder,
# whose cosine scale is naturally compressed (strong paraphrases land ~0.60–0.85,
# unrelated prompts ~0.0). They are deliberately set just below the paraphrase
# band per intent and just above the "one-word-flip" danger zone (e.g. open vs
# close a file ≈ 0.77). If you swap in a sentence-transformer (MiniLM/BGE), retune
# upward toward the ~0.90–0.98 range that those denser embeddings produce.
SEMANTIC_THRESHOLDS: Dict[str, float] = {
    "code":           0.80,
    "math":           0.82,
    "translation":    0.84,
    "reasoning":      0.72,
    "rag":            0.70,
    "summarization":  0.66,
    "qa":             0.64,
    "chat":           0.68,
    "classification": 0.72,
}
DEFAULT_THRESHOLD = 0.72

CACHE_MAX_ENTRIES = 5_000          # L2 semantic store capacity (LRU eviction)
CACHE_TTL_SECONDS = 60 * 60 * 24   # 24h freshness window
EMBED_DIM = 384                    # embedding dimensionality


# --------------------------------------------------------------------------- #
#  Router reward weights
# --------------------------------------------------------------------------- #

# The bandit maximizes:   reward = w_q*quality - w_c*norm_cost - w_l*norm_latency
# Tune these to express the org's appetite. Defaults favor quality but punish
# runaway cost — the posture most engineering managers actually want.
ROUTER_WEIGHTS = {"quality": 1.0, "cost": 0.45, "latency": 0.25}

# Normalization anchors so cost/latency land in roughly [0, 1].
COST_ANCHOR_USD = 0.02     # a "typical expensive" request
LATENCY_ANCHOR_MS = 4000.0

EPSILON = 0.08             # exploration rate for the epsilon-greedy fallback path
PROMETHEUS_PORT = None     # reserved for OpenTelemetry/Prometheus export


# --------------------------------------------------------------------------- #
#  Security configuration
# --------------------------------------------------------------------------- #

SECURITY_BLOCK_ON = {"secret", "api_key", "credential"}   # hard-block categories
SECURITY_REDACT_ON = {"pii_email", "pii_phone", "pii_ssn", "pii_card"}  # redact, allow
INJECTION_BLOCK_THRESHOLD = 0.75   # block when injection score exceeds this
