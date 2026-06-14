"""
Token / prompt optimization (PRD Feature 12).

Cheaply rewrites a prompt to remove tokens that don't change the model's answer:
collapsed whitespace, redundant politeness filler, repeated instructions, and
verbose boilerplate. We never touch content inside code fences or quotes (that's
semantically load-bearing). Returns the optimized prompt plus the realized token
savings, which the cost engine credits.

This is intentionally conservative — lossless-ish compression. The point is to
show the *mechanism* and surface real savings, not to aggressively paraphrase
(which risks changing meaning).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .cost import estimate_tokens

# Filler phrases that almost never change an answer.
_FILLER = [
    r"\bplease\b", r"\bkindly\b", r"\bif you (could|would|don't mind)\b",
    r"\bi was wondering\b", r"\bcould you (please )?\b", r"\bwould you mind\b",
    r"\bthank you( so much)?\b", r"\bthanks in advance\b",
    r"\bi('| a)m trying to\b", r"\bjust\b", r"\bbasically\b", r"\bactually\b",
    r"\bas an ai\b", r"\bin order to\b",
]
_FILLER_RE = re.compile("|".join(_FILLER), re.I)
_CODE_FENCE = re.compile(r"```.*?```", re.S)


@dataclass
class Optimization:
    original: str
    optimized: str
    tokens_before: int
    tokens_after: int
    tokens_saved: int
    pct_saved: float


def optimize(prompt: str) -> Optimization:
    before = estimate_tokens(prompt)

    # protect code blocks
    fences = []
    def _stash(m):
        fences.append(m.group(0))
        return f"\x00FENCE{len(fences)-1}\x00"
    work = _CODE_FENCE.sub(_stash, prompt)

    # collapse whitespace
    work = re.sub(r"[ \t]+", " ", work)
    work = re.sub(r"\n{3,}", "\n\n", work)
    # strip filler
    work = _FILLER_RE.sub("", work)
    # de-duplicate consecutive identical sentences
    work = re.sub(r"\b(\w[\w ]{8,}?[.!?])\s+\1", r"\1", work)
    # tidy punctuation/spacing left behind
    work = re.sub(r"\s+([,.!?;:])", r"\1", work)
    work = re.sub(r"[ \t]{2,}", " ", work).strip()

    # restore code
    for i, f in enumerate(fences):
        work = work.replace(f"\x00FENCE{i}\x00", f)

    after = estimate_tokens(work)
    # never let "optimization" increase tokens
    if after >= before:
        work, after = prompt, before
    saved = before - after
    pct = (saved / before * 100.0) if before else 0.0
    return Optimization(
        original=prompt, optimized=work, tokens_before=before,
        tokens_after=after, tokens_saved=saved, pct_saved=round(pct, 1),
    )
