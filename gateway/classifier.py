"""
Prompt classification engine (PRD Feature 3).

Every request is classified into one of the intents in `config.INTENTS` *before*
it touches an LLM. The intent drives two decisions:
  1. routing  — which candidate models are even in the running
  2. caching   — which similarity threshold to use

In production this would be a fine-tuned DistilBERT/MiniLM head. Here we use a
hybrid that is fast and dependency-free but genuinely discriminative:

  - a set of natural-language *prototypes* per intent, embedded once into
    centroids; we score a prompt by cosine similarity to each centroid
    (this is nearest-centroid classification — a real, well-understood method)
  - high-precision lexical signals (regex for code fences, math operators,
    "translate to", etc.) that add calibrated boosts

The result is a label plus a full confidence distribution, which the dashboard
visualizes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List

import numpy as np

from .embeddings import Embedder

# Natural-language prototypes. More/﻿better prototypes -> sharper centroids.
_PROTOTYPES: Dict[str, List[str]] = {
    "code": [
        "write a python function to sort a list",
        "fix this javascript bug in my react component",
        "implement a binary search tree in c++",
        "refactor this code and add error handling",
        "write a sql query to join two tables",
        "design a rate limiter using a token bucket algorithm with unit tests",
        "implement an lru cache data structure in go",
        "build a rest api endpoint for user authentication",
        "write unit tests for this function and handle edge cases",
        "design a thread-safe queue and benchmark it",
    ],
    "math": [
        "what is the integral of x squared",
        "solve this system of linear equations",
        "calculate the probability of rolling two sixes",
        "prove that the square root of two is irrational",
        "compute the eigenvalues of this matrix",
    ],
    "reasoning": [
        "explain step by step why this argument is flawed",
        "what are the tradeoffs between these two architectures",
        "analyze the consequences of this business decision",
        "think through the implications of this policy",
        "compare and contrast these strategies and recommend one",
    ],
    "translation": [
        "translate this paragraph into french",
        "how do you say good morning in japanese",
        "convert this english text to spanish",
        "translate the following sentence to german",
    ],
    "summarization": [
        "summarize this article in three bullet points",
        "give me a tldr of this document",
        "condense this email into one sentence",
        "what are the key takeaways from this report",
    ],
    "rag": [
        "based on the attached documents what does the policy say",
        "according to the context above answer the question",
        "using the provided knowledge base find the answer",
        "what do the retrieved passages say about refunds",
    ],
    "qa": [
        "what is the capital of france",
        "who wrote pride and prejudice",
        "when did world war two end",
        "how tall is mount everest",
    ],
    "chat": [
        "hey how are you doing today",
        "tell me a fun fact",
        "what should i cook for dinner",
        "lets just chat for a bit",
    ],
    "classification": [
        "is this review positive or negative",
        "categorize this support ticket",
        "label the sentiment of this tweet",
        "classify this email as spam or not spam",
    ],
}

# High-precision lexical boosts (added to the cosine score before softmax).
_LEXICAL = [
    ("code", re.compile(r"```|def |class |function|import |const |</?\w+>|console\.|=>|System\.")),
    ("code", re.compile(r"\b(python|javascript|typescript|java|rust|golang|c\+\+|sql|regex|api)\b")),
    ("code", re.compile(r"\b(algorithm|data structure|unit test|rate limiter|endpoint|"
                        r"implement|refactor|debug|compile|async|thread-safe|in go|stack trace|"
                        r"linked list|hash map|binary search|recursion|big-o)\b", re.I)),
    ("math", re.compile(r"[∫∑√≤≥≠π]|\bintegral\b|\bderivative\b|\beigen|\bprobability\b|\bsolve\b|\bequation")),
    ("math", re.compile(r"\d+\s*[\+\-\*/\^]\s*\d+")),
    ("translation", re.compile(r"\btranslate\b|\binto (french|spanish|german|japanese|chinese|hindi)\b|\bhow do you say\b")),
    ("summarization", re.compile(r"\bsummar|tl;?dr\b|\bcondense\b|\bkey takeaways\b|\bin (one|three|five) (sentence|bullet)")),
    ("rag", re.compile(r"\b(based on|according to|using the) (the )?(context|document|passage|knowledge base|attached)")),
    ("classification", re.compile(r"\bclassif|\bcategoriz|\blabel\b|positive or negative|spam or not")),
    ("reasoning", re.compile(r"\bstep by step\b|\bwhy\b|\btradeoff|\banalyze\b|\bimplication|\bcompare and contrast\b")),
]

_BOOST = 0.18


@dataclass
class Classification:
    intent: str
    confidence: float
    distribution: Dict[str, float]


class PromptClassifier:
    def __init__(self, embedder: Embedder):
        self.embedder = embedder
        self.intents = list(_PROTOTYPES.keys())
        self.centroids: Dict[str, np.ndarray] = {}
        for intent, protos in _PROTOTYPES.items():
            mat = embedder.embed_batch(protos)
            c = mat.mean(axis=0)
            n = np.linalg.norm(c)
            self.centroids[intent] = c / n if n > 0 else c

    def classify(self, text: str) -> Classification:
        v = self.embedder.embed(text)
        scores = {intent: max(0.0, float(np.dot(v, c))) for intent, c in self.centroids.items()}

        lowered = text.lower()
        matched_intents = set()
        for intent, pat in _LEXICAL:
            if pat.search(lowered):
                scores[intent] = scores.get(intent, 0.0) + _BOOST
                matched_intents.add(intent)

        # Precision gate for translation: it is almost always signalled by an
        # explicit marker ("translate", "in French", "how do you say"). Without
        # that marker we damp the centroid score hard, so generic prose can't be
        # mislabeled as translation (the most jarring failure for a code prompt).
        if "translation" not in matched_intents:
            scores["translation"] = scores.get("translation", 0.0) * 0.35

        # temperature-scaled softmax over the (sharpened) scores for a calibrated
        # confidence distribution
        keys = list(scores.keys())
        arr = np.array([scores[k] for k in keys], dtype=np.float64)
        arr = arr / 0.12  # temperature
        arr -= arr.max()
        probs = np.exp(arr)
        probs /= probs.sum()
        dist = {k: float(p) for k, p in zip(keys, probs)}

        best = max(dist, key=dist.get)
        return Classification(intent=best, confidence=dist[best], distribution=dist)
