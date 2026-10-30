"""Embedding providers.

`openai` is the real embedding provider. `local` is a deterministic hashing
embedder used only in DEMO/OFFLINE mode: it keeps the RAG pipeline runnable with
no API key while never pretending to be semantic-quality. The active provider is
always reported by /api/ai/status so the mode is never ambiguous.
"""
from __future__ import annotations

import hashlib
import math
import re
from abc import ABC, abstractmethod

import httpx

from app.core.config import settings

TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_+\-.]*")
STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "is", "are", "for", "with",
    "on", "by", "as", "at", "be", "that", "this", "it", "from", "which", "can",
    "will", "shall", "may", "we", "you", "they", "their", "its", "into", "than",
}


class EmbeddingError(RuntimeError):
    pass


class EmbeddingProvider(ABC):
    name: str
    model: str

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]: ...

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]


class LocalHashingEmbedder(EmbeddingProvider):
    name = "local-hashing-demo"
    model = "local-hashing-v1"

    def __init__(self, dim: int | None = None) -> None:
        self.dim = dim or settings.EMBEDDING_DIM

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vector = [0.0] * self.dim
            tokens = [tok for tok in TOKEN_RE.findall(text.lower()) if tok not in STOPWORDS]
            for index, token in enumerate(tokens):
                digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
                slot = int.from_bytes(digest[:4], "big") % self.dim
                sign = 1.0 if digest[4] % 2 == 0 else -1.0
                weight = 1.0 + math.log1p(len(token)) + (0.3 if index < 12 else 0.0)
                vector[slot] += sign * weight
            norm = math.sqrt(sum(value * value for value in vector)) or 1.0
            vectors.append([round(value / norm, 6) for value in vector])
        return vectors


class OpenAIEmbedder(EmbeddingProvider):
    name = "openai"
    model = settings.EMBEDDING_MODEL

    def __init__(self) -> None:
        if not settings.LLM_API_KEY:
            raise EmbeddingError("LLM_API_KEY is required for OpenAI embeddings")
        self.base_url = settings.LLM_BASE_URL.rstrip("/")

    def embed(self, texts: list[str]) -> list[list[float]]:
        payload: dict = {"model": self.model, "input": texts}
        if settings.EMBEDDING_API_DIMENSIONS:
            payload["dimensions"] = settings.EMBEDDING_API_DIMENSIONS
        try:
            with httpx.Client(timeout=45) as client:
                response = client.post(
                    f"{self.base_url}/embeddings",
                    headers={"Authorization": f"Bearer {settings.LLM_API_KEY}"},
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()["data"]
        except Exception as exc:  # noqa: BLE001
            raise EmbeddingError("Embedding provider request failed") from exc
        ordered = sorted(data, key=lambda item: item.get("index", 0))
        return [item["embedding"] for item in ordered]


_provider: EmbeddingProvider | None = None


def get_embedder() -> EmbeddingProvider:
    global _provider
    if _provider is None:
        _provider = (
            OpenAIEmbedder()
            if settings.effective_embedding_provider == "openai"
            else LocalHashingEmbedder()
        )
    return _provider


def reset_embedder() -> None:  # test helper
    global _provider
    _provider = None


def cosine(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    size = min(len(left), len(right))
    dot = sum(left[i] * right[i] for i in range(size))
    norm_left = math.sqrt(sum(value * value for value in left[:size]))
    norm_right = math.sqrt(sum(value * value for value in right[:size]))
    if not norm_left or not norm_right:
        return 0.0
    return dot / (norm_left * norm_right)
