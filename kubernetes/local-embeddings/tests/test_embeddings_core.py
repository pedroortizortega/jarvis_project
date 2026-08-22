"""Pure-core tests for `app/embeddings.py` and the lazy adapter seam in
`app/model.py` (D-15 — `unittest.TestCase` so this file is collectible by
both `unittest discover` and `pytest`; bridged into the repo-root enforced
suite by `tests/test_local_embeddings.py` in PR 3).

Every case here uses an injected fake embedder. No fastembed, no network,
no model download — this file must import and pass with only the stdlib.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.embeddings import (  # noqa: E402
    DIMENSION,
    MODEL_ID,
    EmbeddingError,
    build_response,
    validate_request,
)


class FakeEmbedder:
    """Deterministic in-memory embedder — no ONNX, no I/O.

    Mirrors the `Embedder` protocol (`embed`, `count_tokens`) and records
    every call so tests can assert exactly what text reached inference.
    """

    def __init__(self, vector_length: int = DIMENSION) -> None:
        self.vector_length = vector_length
        self.embed_calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.embed_calls.append(list(texts))
        return [[float(i)] * self.vector_length for i in range(len(texts))]

    def count_tokens(self, texts: list[str]) -> list[int]:
        return [len(t.split()) for t in texts]


class ValidateRequestSingleAndListTests(unittest.TestCase):
    def test_single_string_input_produces_one_text(self):
        req = validate_request({"input": "hola mundo"})
        self.assertEqual(req.texts, ["hola mundo"])

    def test_list_input_preserves_order(self):
        items = ["a", "b", "c", "d", "e"]
        req = validate_request({"input": items})
        self.assertEqual(req.texts, items)


class BuildResponseOrderingTests(unittest.TestCase):
    def test_build_response_assigns_index_in_input_order(self):
        embedder = FakeEmbedder()
        req = validate_request({"input": ["a", "b", "c", "d", "e"]})
        vectors = embedder.embed(req.texts)
        token_counts = embedder.count_tokens(req.texts)

        resp = build_response(req, vectors, token_counts)

        self.assertEqual(len(resp["data"]), 5)
        self.assertEqual([entry["index"] for entry in resp["data"]], [0, 1, 2, 3, 4])
        self.assertEqual(resp["object"], "list")


class BatchCeilingTests(unittest.TestCase):
    def test_256_inputs_pass(self):
        items = [f"text-{i}" for i in range(256)]
        req = validate_request({"input": items})
        self.assertEqual(len(req.texts), 256)

    def test_257_inputs_rejected(self):
        items = [f"text-{i}" for i in range(257)]
        with self.assertRaises(EmbeddingError) as ctx:
            validate_request({"input": items})
        self.assertEqual(ctx.exception.code, "batch_size_exceeded")
        self.assertEqual(ctx.exception.param, "input")


class EncodingFormatTests(unittest.TestCase):
    def test_base64_rejected(self):
        with self.assertRaises(EmbeddingError) as ctx:
            validate_request({"input": "hola", "encoding_format": "base64"})
        self.assertEqual(ctx.exception.code, "unsupported_encoding_format")
        self.assertEqual(ctx.exception.param, "encoding_format")

    def test_explicit_float_proceeds(self):
        req = validate_request({"input": "hola", "encoding_format": "float"})
        self.assertEqual(req.texts, ["hola"])

    def test_absent_encoding_format_proceeds(self):
        req = validate_request({"input": "hola"})
        self.assertEqual(req.texts, ["hola"])


class DimensionsTests(unittest.TestCase):
    def test_1536_rejected(self):
        # 1536 is OpenAI's default embedding dimension — the exact wrong
        # value a consumer coded against OpenAI would naively send.
        with self.assertRaises(EmbeddingError) as ctx:
            validate_request({"input": "hola", "dimensions": 1536})
        self.assertEqual(ctx.exception.code, "dimension_mismatch")
        self.assertEqual(ctx.exception.param, "dimensions")

    def test_1024_proceeds(self):
        req = validate_request({"input": "hola", "dimensions": 1024})
        self.assertEqual(req.texts, ["hola"])

    def test_absent_dimensions_proceeds(self):
        req = validate_request({"input": "hola"})
        self.assertEqual(req.texts, ["hola"])


class NeverPadTruncateInvariantTests(unittest.TestCase):
    def test_wrong_length_vector_raises_instead_of_truncating(self):
        embedder = FakeEmbedder(vector_length=1536)
        req = validate_request({"input": "hola"})
        vectors = embedder.embed(req.texts)
        self.assertEqual(len(vectors[0]), 1536)  # sanity: the fake really misbehaved
        token_counts = embedder.count_tokens(req.texts)

        with self.assertRaises(EmbeddingError) as ctx:
            build_response(req, vectors, token_counts)
        self.assertEqual(ctx.exception.status, 500)


class ModelNameEchoTests(unittest.TestCase):
    def test_unknown_model_name_is_echoed_verbatim_in_response(self):
        embedder = FakeEmbedder()
        req = validate_request({"input": "hola", "model": "text-embedding-3-small"})
        self.assertEqual(req.echo_model, "text-embedding-3-small")

        vectors = embedder.embed(req.texts)
        token_counts = embedder.count_tokens(req.texts)
        resp = build_response(req, vectors, token_counts)

        self.assertEqual(resp["model"], "text-embedding-3-small")
        # Inference always targets the pinned model id, never the client string.
        self.assertEqual(MODEL_ID, "intfloat/multilingual-e5-large")


class InputTypePrefixTests(unittest.TestCase):
    """D-10, decided 2026-08-21: opt-in `input_type` query/passage prefixing."""

    def test_omitted_input_type_embeds_verbatim(self):
        embedder = FakeEmbedder()
        req = validate_request({"input": "hola mundo"})
        embedder.embed(req.texts)
        self.assertEqual(embedder.embed_calls[-1], ["hola mundo"])

    def test_query_input_type_prepends_query_prefix(self):
        embedder = FakeEmbedder()
        req = validate_request({"input": "hola mundo", "input_type": "query"})
        embedder.embed(req.texts)
        self.assertEqual(embedder.embed_calls[-1], ["query: hola mundo"])

    def test_passage_input_type_prepends_passage_prefix(self):
        embedder = FakeEmbedder()
        req = validate_request({"input": "hola mundo", "input_type": "passage"})
        embedder.embed(req.texts)
        self.assertEqual(embedder.embed_calls[-1], ["passage: hola mundo"])

    def test_unknown_input_type_rejected_never_falls_back_to_verbatim(self):
        with self.assertRaises(EmbeddingError) as ctx:
            validate_request({"input": "hola mundo", "input_type": "doc"})
        self.assertEqual(ctx.exception.code, "invalid_input_type")
        self.assertEqual(ctx.exception.param, "input_type")


class UsageTests(unittest.TestCase):
    def test_usage_is_nonzero_with_deterministic_token_counts(self):
        embedder = FakeEmbedder()
        req = validate_request({"input": ["hola mundo", "adios"]})
        vectors = embedder.embed(req.texts)
        token_counts = embedder.count_tokens(req.texts)

        resp = build_response(req, vectors, token_counts)

        self.assertEqual(resp["usage"]["prompt_tokens"], 3)  # 2 + 1 tokens
        self.assertEqual(resp["usage"]["total_tokens"], 3)

    def test_empty_string_input_rejected(self):
        with self.assertRaises(EmbeddingError) as ctx:
            validate_request({"input": ""})
        self.assertEqual(ctx.exception.code, "invalid_input")

    def test_empty_list_input_rejected(self):
        with self.assertRaises(EmbeddingError) as ctx:
            validate_request({"input": []})
        self.assertEqual(ctx.exception.code, "invalid_input")


class ModelAdapterLazyImportTests(unittest.TestCase):
    """Phase 2 (2.1): `app/model.py` must import cleanly even when
    `fastembed` cannot be imported — proving the import is lazy, scoped
    inside `load_embedder`, not at module level."""

    def _block_fastembed(self):
        backup = sys.modules.copy()
        sys.modules["fastembed"] = None  # forces `import fastembed` to raise
        sys.modules.pop("app.model", None)
        return backup

    def _restore(self, backup):
        sys.modules.clear()
        sys.modules.update(backup)

    def test_model_module_imports_with_fastembed_unavailable(self):
        backup = self._block_fastembed()
        try:
            import app.model as model_module

            self.assertTrue(callable(model_module.load_embedder))
        finally:
            self._restore(backup)

    def test_load_embedder_raises_only_when_actually_called(self):
        backup = self._block_fastembed()
        try:
            import app.model as model_module

            with self.assertRaises(ImportError):
                model_module.load_embedder()
        finally:
            self._restore(backup)


if __name__ == "__main__":
    unittest.main()
