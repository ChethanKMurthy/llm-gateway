"""
Cost prediction (PRD Feature 4) and latency prediction (PRD Feature 5).

Before a single token is generated we estimate, for any candidate model:
  - expected input tokens  (from the prompt)
  - expected output tokens (from intent — code answers are long, classification short)
  - expected dollar cost
  - expected latency, as a P50/P95/P99 distribution

Token counting: a true BPE tokenizer (tiktoken) would be ideal, but to stay
dependency-free we use a calibrated estimator. Empirically, English text is
~4 chars/token and ~0.75 tokens/word; we blend both and correct for code/markup
density. This lands within a few percent of real tokenizers for typical prompts —
good enough to drive routing and budgets, and trivially swappable for tiktoken.

Latency: each model has a base overhead + per-output-token cost (see config). We
model run-to-run variance as log-normal (the shape real inference latency
actually takes) to produce honest P50/P95/P99 numbers.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Dict

from .config import MODELS, ModelSpec

_WORD_RE = re.compile(r"\S+")

# Typical output length (in tokens) by intent — used to predict output cost.
_EXPECTED_OUTPUT_TOKENS: Dict[str, int] = {
    "code": 420,
    "math": 260,
    "reasoning": 520,
    "translation": 180,
    "summarization": 140,
    "rag": 300,
    "qa": 80,
    "chat": 120,
    "classification": 12,
}
_DEFAULT_OUTPUT_TOKENS = 200


def estimate_tokens(text: str) -> int:
    """Calibrated token estimate without a tokenizer dependency."""
    if not text:
        return 0
    chars = len(text)
    words = len(_WORD_RE.findall(text))
    by_chars = chars / 4.0
    by_words = words / 0.75
    est = 0.5 * by_chars + 0.5 * by_words
    # code / markup is denser in tokens — bump if it looks like code
    if re.search(r"[{};()\[\]<>]|```|def |function|=>", text):
        est *= 1.15
    return max(1, int(round(est)))


def expected_output_tokens(intent: str) -> int:
    return _EXPECTED_OUTPUT_TOKENS.get(intent, _DEFAULT_OUTPUT_TOKENS)


@dataclass
class CostEstimate:
    model: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    p50_ms: float
    p95_ms: float
    p99_ms: float


def _latency_percentiles(spec: ModelSpec, tokens_out: int):
    mean = spec.latency_base_ms + spec.latency_per_tok_ms * tokens_out
    # log-normal with sigma ~0.35 -> realistic right-skewed tail
    sigma = 0.35
    mu = math.log(max(1.0, mean)) - 0.5 * sigma * sigma
    p50 = math.exp(mu)                       # median
    p95 = math.exp(mu + 1.645 * sigma)
    p99 = math.exp(mu + 2.326 * sigma)
    return p50, p95, p99


def predict(model_id: str, prompt: str, intent: str) -> CostEstimate:
    spec = MODELS[model_id]
    tin = estimate_tokens(prompt)
    tout = expected_output_tokens(intent)
    cost = spec.price_for(tin, tout)
    p50, p95, p99 = _latency_percentiles(spec, tout)
    return CostEstimate(
        model=model_id, tokens_in=tin, tokens_out=tout, cost_usd=cost,
        p50_ms=round(p50, 1), p95_ms=round(p95, 1), p99_ms=round(p99, 1),
    )


def predict_all(prompt: str, intent: str, candidates) -> Dict[str, CostEstimate]:
    return {m: predict(m, prompt, intent) for m in candidates}


def actual_cost(model_id: str, tokens_in: int, tokens_out: int) -> float:
    return MODELS[model_id].price_for(tokens_in, tokens_out)
