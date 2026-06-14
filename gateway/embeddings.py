"""
Embeddings.

The semantic cache and the classifier both need to turn text into a vector so we
can measure "how close are these two prompts?" In production you'd plug in a
sentence-transformer (MiniLM / BGE) or a hosted embedding endpoint. To keep this
project fully self-contained and runnable on any machine with zero model
downloads, the default embedder is a *real* feature-hashing vectorizer:

  - tokenizes text into word unigrams, word bigrams, and character trigrams
  - hashes each feature into a fixed-dimensional space (the hashing trick)
  - applies a signed-hash to avoid systematic collision bias
  - sublinear term-frequency weighting + L2 normalization

This is the same family of technique behind classic high-throughput text search
(it's what scikit-learn's HashingVectorizer does). It captures lexical and
near-paraphrase similarity well — enough for a convincing semantic cache — and it
is deterministic across processes (uses blake2b, not Python's salted hash).

The `Embedder` interface is intentionally swappable: `MiniLMEmbedder` is provided
as a drop-in that activates automatically if `sentence-transformers` is installed.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import List

import numpy as np

from .config import EMBED_DIM

_WORD_RE = re.compile(r"[a-z0-9]+")


def _blake_int(s: str) -> int:
    """Deterministic 64-bit hash (stable across processes, unlike hash())."""
    return int.from_bytes(hashlib.blake2b(s.encode("utf-8"), digest_size=8).digest(), "big")


class Embedder:
    """Base interface."""

    dim: int = EMBED_DIM

    def embed(self, text: str) -> np.ndarray:  # pragma: no cover - interface
        raise NotImplementedError

    def embed_batch(self, texts: List[str]) -> np.ndarray:
        return np.vstack([self.embed(t) for t in texts]) if texts else np.zeros((0, self.dim))


class HashingEmbedder(Embedder):
    """Dependency-free feature-hashing embedder. Real, fast, deterministic."""

    def __init__(self, dim: int = EMBED_DIM):
        self.dim = dim

    def _features(self, text: str):
        text = text.lower().strip()
        words = _WORD_RE.findall(text)
        feats: List[str] = []
        # word unigrams + bigrams
        feats.extend(words)
        feats.extend(f"{a}_{b}" for a, b in zip(words, words[1:]))
        # character trigrams over the normalized stream — captures typos &
        # morphology so "reset" ~ "resetting" still share signal
        stream = " ".join(words)
        feats.extend(stream[i:i + 3] for i in range(max(0, len(stream) - 2)))
        return feats

    def embed(self, text: str) -> np.ndarray:
        vec = np.zeros(self.dim, dtype=np.float32)
        counts: dict[int, float] = {}
        signs: dict[int, float] = {}
        for f in self._features(text):
            h = _blake_int(f)
            idx = h % self.dim
            sign = 1.0 if (h >> 17) & 1 else -1.0
            counts[idx] = counts.get(idx, 0.0) + 1.0
            signs[idx] = sign
        for idx, c in counts.items():
            # sublinear tf damping so a repeated word doesn't dominate
            vec[idx] = signs[idx] * (1.0 + math.log(c))
        norm = float(np.linalg.norm(vec))
        if norm > 0:
            vec /= norm
        return vec


class MiniLMEmbedder(Embedder):  # pragma: no cover - optional dependency path
    """Auto-activated upgrade if sentence-transformers is available."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        from sentence_transformers import SentenceTransformer  # type: ignore
        self._model = SentenceTransformer(model_name)
        self.dim = self._model.get_sentence_embedding_dimension()

    def embed(self, text: str) -> np.ndarray:
        v = self._model.encode([text], normalize_embeddings=True)[0]
        return np.asarray(v, dtype=np.float32)

    def embed_batch(self, texts: List[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return np.asarray(
            self._model.encode(texts, normalize_embeddings=True), dtype=np.float32
        )


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity for already-L2-normalized vectors (falls back safely)."""
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)


def build_default_embedder() -> Embedder:
    """Prefer a real sentence-transformer; fall back to the hashing embedder."""
    try:
        emb = MiniLMEmbedder()
        return emb
    except Exception:
        return HashingEmbedder()
