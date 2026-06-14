"""
Response quality scoring (PRD Feature 9).

Every response is scored in [0, 1] on a few cheap, model-free signals. The score
feeds three things:
  - the RL router's reward (good answers reinforce the chosen model)
  - the adaptive cache controller (a low-quality semantic hit tightens the threshold)
  - the dashboard's quality column

Signals:
  - relevance        : embedding cosine between prompt and response (on-topic?)
  - completeness     : is the answer a reasonable length for the intent?
  - hallucination_risk: heuristic flags (hedging, contradiction, fabricated-citation
                        patterns, empty/degenerate output) -> lower is better
  - format_fit       : does the output shape match the intent (code -> has code, etc.)

These are deliberately transparent heuristics. In production you'd add an
LLM-as-judge or a learned reward model; the interface here (`score(...) -> Quality`)
is the same.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict

import numpy as np

from .embeddings import Embedder, cosine

_HEDGES = re.compile(
    r"\b(i'?m not sure|i cannot|i can'?t help|as an ai|i don'?t have access|"
    r"it depends|might be|possibly|unclear|i apologize)\b", re.I,
)
_FABRICATION = re.compile(r"\[\d+\]|\bet al\.|\baccording to (a )?(study|research)\b", re.I)

_TARGET_LEN = {  # target output token-ish length per intent
    "code": 400, "math": 250, "reasoning": 500, "translation": 180,
    "summarization": 130, "rag": 280, "qa": 70, "chat": 110, "classification": 10,
}


@dataclass
class Quality:
    score: float
    relevance: float
    completeness: float
    hallucination_risk: float
    format_fit: float


def _completeness(text: str, intent: str) -> float:
    n = len(text.split())
    target = _TARGET_LEN.get(intent, 200) * 0.75   # words ~ 0.75 * tokens
    if n == 0:
        return 0.0
    ratio = n / target
    # full credit in a band around target, penalize far too short / too long
    if 0.4 <= ratio <= 2.0:
        return 1.0
    if ratio < 0.4:
        return max(0.2, ratio / 0.4)
    return max(0.4, 2.0 / ratio)


def _format_fit(text: str, intent: str) -> float:
    if intent == "code":
        return 1.0 if re.search(r"```|def |function|class |const |=>|;", text) else 0.55
    if intent == "classification":
        return 1.0 if len(text.split()) <= 8 else 0.7
    if intent == "translation":
        return 1.0
    return 0.9


def _hallucination_risk(text: str) -> float:
    risk = 0.05
    if not text.strip():
        return 1.0
    if _HEDGES.search(text):
        risk += 0.25
    if _FABRICATION.search(text):
        risk += 0.20
    # degenerate repetition
    words = text.lower().split()
    if words:
        uniq = len(set(words)) / len(words)
        if uniq < 0.4:
            risk += 0.3
    return min(1.0, risk)


def score(prompt: str, response: str, intent: str, embedder: Embedder) -> Quality:
    pv = embedder.embed(prompt)
    rv = embedder.embed(response) if response.strip() else np.zeros_like(pv)
    relevance = max(0.0, cosine(pv, rv))
    # relevance from a lexical embedder is naturally modest; rescale to a fair band
    relevance = min(1.0, 0.45 + relevance * 0.9)

    completeness = _completeness(response, intent)
    hrisk = _hallucination_risk(response)
    fit = _format_fit(response, intent)

    composite = (
        0.40 * relevance + 0.25 * completeness + 0.20 * fit + 0.15 * (1.0 - hrisk)
    )
    return Quality(
        score=round(float(np.clip(composite, 0.0, 1.0)), 3),
        relevance=round(relevance, 3),
        completeness=round(completeness, 3),
        hallucination_risk=round(hrisk, 3),
        format_fit=round(fit, 3),
    )
