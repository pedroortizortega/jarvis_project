"""The ONNX embedder adapter (D-01/D-02/D-11).

`fastembed` is imported lazily, only inside `load_embedder`, so this
module — like the pure `app.embeddings` core it wraps — imports cleanly
on a machine (or unit-test run) with no `fastembed` installed. The HTTP
layer (`app/main.py`, PR 2) is the only caller in production; unit tests
inject a fake `Embedder` instead and never reach this module's ONNX path.
"""
from __future__ import annotations

import math
from typing import Any

from app.embeddings import MODEL_ID

# D-02: baked at build time, owned by root, readable by the runtime uid.
CACHE_DIR = "/opt/models/fastembed"


class FastEmbedAdapter:
    """Wraps a loaded `fastembed.TextEmbedding` behind the pure-core
    `Embedder` protocol (`embed`, `count_tokens`)."""

    def __init__(self, text_embedding: Any) -> None:
        self._text_embedding = text_embedding

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [vector.tolist() for vector in self._text_embedding.embed(texts)]

    def count_tokens(self, texts: list[str]) -> list[int]:
        return [self._count_one(text) for text in texts]

    def _count_one(self, text: str) -> int:
        """D-11: exact tokenizer count when reachable, else a documented
        `ceil(len(text)/4)` fallback. A guarded `getattr` chain must never
        be able to turn a valid request into a 500."""
        try:
            tokenizer = getattr(self._text_embedding, "tokenizer", None)
            if tokenizer is not None and hasattr(tokenizer, "encode"):
                encoded = tokenizer.encode(text)
                ids = getattr(encoded, "ids", encoded)
                return len(ids)
        except Exception:
            pass
        return max(1, math.ceil(len(text) / 4))


def load_embedder() -> FastEmbedAdapter:
    """Load the pinned ONNX model from the baked, offline cache dir.

    The `import fastembed` here — not at module level — is what makes
    `app.model` importable without the dependency installed (D-01, task
    2.1): only calling this function requires `fastembed` to exist.
    """
    from fastembed import TextEmbedding  # lazy import — keep at call site

    text_embedding = TextEmbedding(MODEL_ID, cache_dir=CACHE_DIR)
    return FastEmbedAdapter(text_embedding)
