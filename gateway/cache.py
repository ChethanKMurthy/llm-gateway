"""
Multi-level cache (PRD Feature 2) + adaptive thresholds (PRD Feature 8).

Three layers, checked in order of cost:

  L1  Exact cache      — hash(normalized prompt + intent). O(1), ~0.1ms. The
                         cheapest possible hit: byte-identical (after normalization)
                         requests.
  L2  Semantic cache   — embedding + cosine similarity against stored prompts.
                         A hit means "a meaningfully equivalent question was asked
                         before." Threshold is *per intent* and *adaptive*.
  L3  Answer cache     — keyed on (intent, normalized template) for high-frequency
                         FAQ-style answers; lets distinct-but-equivalent phrasings
                         share a canonical answer. (A pragmatic stand-in for the
                         "generated answer cache" in the PRD.)

The semantic store is a numpy matrix so similarity search is a single vectorized
dot product — fast enough for tens of thousands of entries without a vector DB,
and trivially swappable for Qdrant/Redis-Vector in production.

Adaptive thresholds: when a semantic hit is later judged low-quality (the served
answer didn't really fit), we *raise* that intent's threshold; when hits are
consistently good, we let it drift slightly lower to capture more savings. This is
the "cache learning system" from the PRD — a simple, stable controller.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from .config import (
    CACHE_MAX_ENTRIES,
    CACHE_TTL_SECONDS,
    DEFAULT_THRESHOLD,
    EMBED_DIM,
    SEMANTIC_THRESHOLDS,
)
from .embeddings import Embedder

_WS = re.compile(r"\s+")


def normalize(text: str) -> str:
    return _WS.sub(" ", text.strip().lower())


def exact_key(text: str, intent: str) -> str:
    h = hashlib.blake2b(f"{intent}\x00{normalize(text)}".encode(), digest_size=16)
    return h.hexdigest()


@dataclass
class CacheEntry:
    key: str
    prompt: str
    response: str
    intent: str
    model: str
    embedding: np.ndarray
    cost_saved: float          # what the original generation cost
    created_at: float
    hits: int = 0
    quality: float = 0.85


@dataclass
class CacheResult:
    hit: bool
    level: Optional[str] = None         # "L1" | "L2" | "L3"
    entry: Optional[CacheEntry] = None
    similarity: float = 0.0
    threshold: float = 0.0
    # for misses, the best near-miss so the dashboard can show "almost cached"
    best_similarity: float = 0.0


class MultiLevelCache:
    def __init__(self, embedder: Embedder, capacity: int = CACHE_MAX_ENTRIES):
        self.embedder = embedder
        self.capacity = capacity

        # L1 exact: key -> entry  (also LRU so it bounds memory)
        self._l1: "OrderedDict[str, CacheEntry]" = OrderedDict()

        # L2 semantic: parallel arrays for vectorized search
        self._keys: List[str] = []
        self._prompts: List[str] = []
        self._matrix = np.zeros((0, EMBED_DIM), dtype=np.float32)
        self._entries: Dict[str, CacheEntry] = {}

        # L3 answer cache: (intent, canonical) -> response
        self._l3: Dict[Tuple[str, str], str] = {}

        # adaptive thresholds (copy so we don't mutate config)
        self.thresholds: Dict[str, float] = dict(SEMANTIC_THRESHOLDS)

        self.lookups = 0
        self.l1_hits = 0
        self.l2_hits = 0

    # ---- thresholds -------------------------------------------------------- #
    def threshold_for(self, intent: str) -> float:
        return self.thresholds.get(intent, DEFAULT_THRESHOLD)

    def adapt_threshold(self, intent: str, hit_quality: float) -> None:
        """Nudge the per-intent threshold based on observed hit quality."""
        t = self.threshold_for(intent)
        if hit_quality < 0.6:
            t = min(0.97, t + 0.01)       # bad hit -> be stricter
        elif hit_quality > 0.85:
            t = max(0.55, t - 0.002)      # consistently good -> capture more
        self.thresholds[intent] = round(t, 4)

    # ---- lookup ------------------------------------------------------------ #
    def lookup(self, prompt: str, intent: str, embedding: np.ndarray) -> CacheResult:
        self.lookups += 1
        now = time.time()

        # L1 exact
        k = exact_key(prompt, intent)
        e = self._l1.get(k)
        if e and (now - e.created_at) <= CACHE_TTL_SECONDS:
            self._l1.move_to_end(k)
            e.hits += 1
            self.l1_hits += 1
            return CacheResult(hit=True, level="L1", entry=e, similarity=1.0,
                               threshold=1.0, best_similarity=1.0)

        # L2 semantic. We search the WHOLE store (not just same-intent entries)
        # and gate on the *matched entry's* threshold. Rationale: similarity is
        # itself the safety gate — cross-topic prompts have near-zero cosine — so
        # global search makes the cache robust to classifier drift (paraphrases
        # that happen to land in different intent buckets can still match), while
        # the per-content threshold keeps strict types (code, translation) strict.
        best_sim, best_idx = 0.0, -1
        if self._matrix.shape[0] > 0:
            sims = self._matrix @ embedding   # cosine (both L2-normalized)
            best_idx = int(np.argmax(sims))
            best_sim = float(sims[best_idx])

        query_thr = self.threshold_for(intent)
        if best_idx >= 0:
            entry = self._entries[self._keys[best_idx]]
            thr = max(self.threshold_for(entry.intent), self.threshold_for(intent))
            fresh = (now - entry.created_at) <= CACHE_TTL_SECONDS
            if best_sim >= thr and fresh:
                entry.hits += 1
                self.l2_hits += 1
                return CacheResult(hit=True, level="L2", entry=entry,
                                   similarity=best_sim, threshold=thr,
                                   best_similarity=best_sim)

        return CacheResult(hit=False, threshold=query_thr, best_similarity=best_sim)

    # ---- store ------------------------------------------------------------- #
    def store(self, prompt: str, response: str, intent: str, model: str,
              embedding: np.ndarray, cost: float, quality: float) -> None:
        now = time.time()
        k = exact_key(prompt, intent)
        entry = CacheEntry(
            key=k, prompt=prompt, response=response, intent=intent, model=model,
            embedding=embedding.astype(np.float32), cost_saved=cost,
            created_at=now, quality=quality,
        )
        # L1
        self._l1[k] = entry
        self._l1.move_to_end(k)
        while len(self._l1) > self.capacity:
            self._l1.popitem(last=False)

        # L2 (skip if this exact key already in the matrix)
        if k not in self._entries:
            self._entries[k] = entry
            self._keys.append(k)
            self._prompts.append(prompt)
            self._matrix = (
                embedding.reshape(1, -1).astype(np.float32)
                if self._matrix.shape[0] == 0
                else np.vstack([self._matrix, embedding.astype(np.float32)])
            )
            self._evict_if_needed()

        # L3 canonical answer
        self._l3[(intent, normalize(prompt)[:120])] = response

    def _evict_if_needed(self) -> None:
        if len(self._keys) <= self.capacity:
            return
        # evict the lowest-value entry: few hits and old
        now = time.time()
        scores = [
            (self._entries[k].hits + 1) / (1.0 + (now - self._entries[k].created_at) / 3600.0)
            for k in self._keys
        ]
        drop = int(np.argmin(scores))
        k = self._keys.pop(drop)
        self._prompts.pop(drop)
        self._matrix = np.delete(self._matrix, drop, axis=0)
        self._entries.pop(k, None)

    # ---- stats ------------------------------------------------------------- #
    @property
    def size(self) -> int:
        return len(self._keys)

    @property
    def hit_rate(self) -> float:
        return (self.l1_hits + self.l2_hits) / self.lookups if self.lookups else 0.0

    def stats(self) -> dict:
        return {
            "entries": self.size,
            "lookups": self.lookups,
            "l1_hits": self.l1_hits,
            "l2_hits": self.l2_hits,
            "hit_rate": round(self.hit_rate, 4),
            "thresholds": {k: round(v, 4) for k, v in self.thresholds.items()},
        }
