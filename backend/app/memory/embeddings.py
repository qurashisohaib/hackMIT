"""Text embedders for the memory graph.

``HashedEmbedder`` is a dependency-free, deterministic feature-hashing embedder (word unigrams
+ bigrams, 256 dims, sign hashing, L2 normalised). ``OpenAIEmbedder`` uses the configured
OpenAI embedding model and silently degrades to the hashed embedder on any failure so the demo
never depends on the network.
"""
from __future__ import annotations

import hashlib
import logging
import re
from typing import Any, Protocol, runtime_checkable

import numpy as np

from app.config import settings

log = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:\.[0-9]+)?")


@runtime_checkable
class Embedder(Protocol):
    """Anything that turns text into a fixed-size float vector."""

    name: str
    dims: int

    def embed(self, text: str) -> list[float]: ...


def cosine(a: list[float] | np.ndarray, b: list[float] | np.ndarray) -> float:
    """Cosine similarity in [-1, 1]; 0.0 when either vector is empty, zero or of different length."""
    va = np.asarray(a, dtype=np.float64)
    vb = np.asarray(b, dtype=np.float64)
    if va.size == 0 or vb.size == 0 or va.shape != vb.shape:
        return 0.0
    na = float(np.linalg.norm(va))
    nb = float(np.linalg.norm(vb))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))


def tokenize(text: str) -> list[str]:
    """Lower-cased alphanumeric tokens (keeps decimals like ``2.5`` together)."""
    return _TOKEN_RE.findall(text.lower())


class HashedEmbedder:
    """Deterministic feature-hashing embedder (unigrams + bigrams, sign hashing)."""

    name = "hashed"

    def __init__(self, dims: int = 256) -> None:
        if dims <= 0:
            raise ValueError("dims must be positive")
        self.dims = dims

    @staticmethod
    def _hash(feature: str) -> int:
        return int.from_bytes(hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest(), "big")

    def features(self, text: str) -> list[str]:
        """Unigram and bigram features of ``text``."""
        toks = tokenize(text)
        feats = list(toks)
        feats.extend(f"{a}_{b}" for a, b in zip(toks, toks[1:]))
        return feats

    def embed(self, text: str) -> list[float]:
        """Embed ``text`` into an L2-normalised vector of ``dims`` floats (zeros for empty text)."""
        vec = np.zeros(self.dims, dtype=np.float64)
        for feat in self.features(text):
            h = self._hash(feat)
            idx = h % self.dims
            sign = 1.0 if (h >> 63) & 1 == 0 else -1.0
            vec[idx] += sign
        norm = float(np.linalg.norm(vec))
        if norm > 0.0:
            vec /= norm
        return vec.tolist()


class OpenAIEmbedder:
    """OpenAI embeddings with a hashed fallback that is remembered after the first failure."""

    name = "openai"

    def __init__(self, model: str | None = None, fallback: HashedEmbedder | None = None) -> None:
        self.model = model or settings.openai_embedding_model
        self.fallback = fallback or HashedEmbedder()
        self.dims = self.fallback.dims
        self.failed = False
        self.last_error: str | None = None
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            from openai import OpenAI  # imported lazily: optional at runtime

            self._client = OpenAI(api_key=settings.openai_api_key)
        return self._client

    def embed(self, text: str) -> list[float]:
        """Embed via OpenAI; after any error use (and keep using) the hashed embedder."""
        if self.failed or not settings.llm_available or not text.strip():
            return self.fallback.embed(text)
        try:
            response = self._get_client().embeddings.create(model=self.model, input=text)
            vector = [float(x) for x in response.data[0].embedding]
            if not vector:
                raise ValueError("empty embedding returned")
            self.dims = len(vector)
            return vector
        except Exception as exc:  # noqa: BLE001 - any failure must degrade gracefully
            self.failed = True
            self.last_error = f"{type(exc).__name__}: {exc}"
            self.dims = self.fallback.dims
            log.warning("OpenAI embeddings unavailable (%s); falling back to hashed embedder", self.last_error)
            return self.fallback.embed(text)


def get_embedder() -> Embedder:
    """``OpenAIEmbedder`` when an API key is configured and LLM use is enabled, else ``HashedEmbedder``."""
    if settings.llm_available:
        return OpenAIEmbedder()
    return HashedEmbedder()
