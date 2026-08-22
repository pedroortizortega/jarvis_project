"""HTTP-layer tests for `app/main.py` (D-15 — pytest only, `TestClient`;
never bridged into the root `unittest` suite, unlike `test_embeddings_core.py`).

Every case injects a fake `Embedder` via `create_app(embedder=fake)` — no
fastembed, no network, no model download. See design.md's Data Flow and
Testing Strategy sections for the contract this file verifies.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.embeddings import DIMENSION, MODEL_ID  # noqa: E402
from app.main import create_app  # noqa: E402


class FakeEmbedder:
    """Deterministic in-memory embedder mirroring the `Embedder` protocol."""

    def __init__(self, vector_length: int = DIMENSION, delay: float = 0.0) -> None:
        self.vector_length = vector_length
        self.delay = delay
        self.embed_calls: list[list[str]] = []
        self.in_flight = 0
        self.max_in_flight = 0
        self._lock = threading.Lock()

    def embed(self, texts: list[str]) -> list[list[float]]:
        with self._lock:
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            self.embed_calls.append(list(texts))
            if self.delay:
                time.sleep(self.delay)
            return [[float(i)] * self.vector_length for i in range(len(texts))]
        finally:
            with self._lock:
                self.in_flight -= 1

    def count_tokens(self, texts: list[str]) -> list[int]:
        return [len(t.split()) for t in texts]


@pytest.fixture()
def fake_embedder() -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture()
def client(fake_embedder: FakeEmbedder) -> TestClient:
    app = create_app(embedder=fake_embedder)
    with TestClient(app) as test_client:
        yield test_client


class TestEmbeddingsRoundTrip:
    def test_single_string_round_trip(self, client: TestClient) -> None:
        resp = client.post(
            "/v1/embeddings",
            json={"input": "hola mundo", "model": "text-embedding-3-small"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["object"] == "list"
        assert len(body["data"]) == 1
        assert body["data"][0]["index"] == 0
        assert len(body["data"][0]["embedding"]) == DIMENSION
        assert body["model"] == "text-embedding-3-small"
        assert body["usage"]["prompt_tokens"] > 0

    def test_batch_size_exceeded_uses_openai_error_envelope(self, client: TestClient) -> None:
        resp = client.post("/v1/embeddings", json={"input": ["x"] * 257})
        assert resp.status_code == 400
        body = resp.json()
        assert "detail" not in body
        assert body["error"]["code"] == "batch_size_exceeded"
        assert body["error"]["param"] == "input"
        assert body["error"]["type"] == "invalid_request_error"


class TestMalformedRequestValidationError:
    def test_malformed_json_uses_openai_error_envelope(self, client: TestClient) -> None:
        resp = client.post(
            "/v1/embeddings",
            content=b"{not valid json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert "detail" not in body
        assert "error" in body
        assert body["error"]["type"] == "invalid_request_error"

    def test_missing_input_field_uses_openai_error_envelope(self, client: TestClient) -> None:
        resp = client.post("/v1/embeddings", json={})
        assert resp.status_code in (400, 422)
        body = resp.json()
        assert "detail" not in body
        assert "error" in body


class TestHealthz:
    def test_healthz_returns_503_before_model_loaded(self) -> None:
        # No `with` block: the lifespan (which would call model.load_embedder())
        # never runs, so `app.state.embedder` stays at its pre-load `None`.
        app = create_app(embedder=None)
        test_client = TestClient(app)
        resp = test_client.get("/healthz")
        assert resp.status_code == 503

    def test_healthz_returns_200_once_loaded(self, client: TestClient) -> None:
        resp = client.get("/healthz")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["model"] == MODEL_ID
        assert body["dimension"] == DIMENSION


class TestModels:
    def test_get_models_returns_pinned_id_without_touching_embedder(
        self, client: TestClient, fake_embedder: FakeEmbedder
    ) -> None:
        resp = client.get("/v1/models")
        assert resp.status_code == 200
        body = resp.json()
        assert body["object"] == "list"
        assert body["data"][0]["id"] == MODEL_ID
        assert body["data"][0]["object"] == "model"
        assert fake_embedder.embed_calls == []


class TestAuthorizationPassthrough:
    def test_dummy_bearer_token_is_accepted_and_ignored(self, client: TestClient) -> None:
        resp = client.post(
            "/v1/embeddings",
            json={"input": "hola"},
            headers={"Authorization": "Bearer sk-dummy"},
        )
        assert resp.status_code == 200


class TestConcurrencySerialization:
    def test_concurrent_requests_are_serialized_through_the_semaphore(self) -> None:
        fake = FakeEmbedder(delay=0.05)
        app = create_app(embedder=fake)
        with TestClient(app) as test_client:
            results: list[int] = []
            errors: list[Exception] = []

            def do_request() -> None:
                try:
                    resp = test_client.post("/v1/embeddings", json={"input": "hola"})
                    results.append(resp.status_code)
                except Exception as exc:  # pragma: no cover - defensive
                    errors.append(exc)

            threads = [threading.Thread(target=do_request) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5)

        assert not errors
        assert results == [200, 200]
        assert fake.max_in_flight == 1
