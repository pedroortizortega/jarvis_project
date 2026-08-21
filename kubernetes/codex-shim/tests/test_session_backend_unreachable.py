"""RED->GREEN tests for Phases 1-6: `StoreUnreachable` classification,
sanitized reason (2.6), `session.py` state wiring, `main.py` response shape,
and `proxy.py` widening. See design.md D-01/D-02/D-04/D-05/D-06/D-07.
"""

from __future__ import annotations

import asyncio
import base64
import time

import httpx
import pytest

from app import codex_auth
from app.store import SecretNotFound, StoreUnreachable, TokenStore
from tests.conftest import FakeCoreV1Api, jwt_with_exp, mock_token_transport


# --- Fake exceptions the K8s client family might raise ----------------------


class FakeApiException(Exception):
    """Stand-in for kubernetes.client.exceptions.ApiException — carries a
    `.status` int and, deliberately, a body that would leak if `str(exc)`
    were ever read (it must not be, per D-04)."""

    def __init__(self, status: int, body: str = "") -> None:
        self.status = status
        self.body = body
        super().__init__(f"({status})\nReason: boom\nHTTP response body: {body}")


class FakeMaxRetryError(Exception):
    """Stand-in for urllib3.exceptions.MaxRetryError — no `.status` attr."""


class FakeSocketTimeout(Exception):
    """Stand-in for socket.timeout — no `.status` attr."""


class RaisingCoreV1Api(FakeCoreV1Api):
    """A CoreV1Api stub whose read/patch calls raise a configured exception
    instead of returning/storing data."""

    def __init__(self, read_exc=None, patch_exc=None, **kwargs):
        super().__init__(**kwargs)
        self._read_exc = read_exc
        self._patch_exc = patch_exc

    def read_namespaced_secret(self, name: str, namespace: str):
        if self._read_exc is not None:
            raise self._read_exc
        return super().read_namespaced_secret(name, namespace)

    def patch_namespaced_secret(self, name: str, namespace: str, body):
        if self._patch_exc is not None:
            raise self._patch_exc
        return super().patch_namespaced_secret(name, namespace, body)


# --- Phase 1: TokenStore classification -------------------------------------


def test_read_apiexception_raises_store_unreachable_with_k8s_api_code():
    fake = RaisingCoreV1Api(read_exc=FakeApiException(500, body="sk-secret-should-not-leak"))
    store = TokenStore(k8s_core_v1=fake)

    with pytest.raises(StoreUnreachable) as excinfo:
        store.read()

    assert excinfo.value.code == "k8s_api_500"


@pytest.mark.parametrize(
    "exc",
    [FakeMaxRetryError("connect failed"), FakeSocketTimeout("timed out"), OSError("network unreachable")],
)
def test_read_transport_exceptions_raise_store_unreachable_with_k8s_transport_code(exc):
    fake = RaisingCoreV1Api(read_exc=exc)
    store = TokenStore(k8s_core_v1=fake)

    with pytest.raises(StoreUnreachable) as excinfo:
        store.read()

    assert excinfo.value.code == "k8s_transport"


def test_read_404_still_raises_secret_not_found_unmodified():
    """Regression guard (D-03, task 1.3): 404 must still map to
    SecretNotFound, never StoreUnreachable."""
    fake = FakeCoreV1Api()  # no secret seeded -> read_namespaced_secret raises status=404
    store = TokenStore(k8s_core_v1=fake)

    with pytest.raises(SecretNotFound):
        store.read()


def test_write_apiexception_raises_store_unreachable_with_same_code_family():
    """Task 1.4: write() classifies the same exception family identically
    to read()."""
    fake = RaisingCoreV1Api(patch_exc=FakeApiException(503))
    store = TokenStore(k8s_core_v1=fake)

    with pytest.raises(StoreUnreachable) as excinfo:
        store.write({"access_token": "not-a-jwt", "refresh_token": "rt"})

    assert excinfo.value.code == "k8s_api_503"


def test_write_transport_exception_raises_store_unreachable_k8s_transport():
    fake = RaisingCoreV1Api(patch_exc=FakeMaxRetryError("connect failed"))
    store = TokenStore(k8s_core_v1=fake)

    with pytest.raises(StoreUnreachable) as excinfo:
        store.write({"access_token": "not-a-jwt", "refresh_token": "rt"})

    assert excinfo.value.code == "k8s_transport"


# --- Phase 2: sanitized reason / no-token-material (2.6) --------------------


def test_reason_never_contains_exception_text_even_when_it_carries_secrets():
    """RED 2.1: a crafted exception whose str() contains secret-like text and
    a traceback-shaped body must never leak into `reason`, `code`, or
    `str(exc)` derived output — the reason is templated only from the code."""
    poison = "sk-secret-ACCESS-TOKEN\nTraceback (most recent call last):\n  File 'x.py'"
    exc = FakeApiException(403, body=poison)
    fake = RaisingCoreV1Api(read_exc=exc)
    store = TokenStore(k8s_core_v1=fake)

    with pytest.raises(StoreUnreachable) as excinfo:
        store.read()

    su = excinfo.value
    assert poison not in su.reason
    assert poison not in su.code
    assert su.reason == "kubernetes API secret read failed (k8s_api_403)"
    assert su.code == "k8s_api_403"


def test_all_seven_session_states_never_leak_token_material_via_internal_session():
    """RED 2.3: audit the /internal/session body across all seven
    SessionState values for token material / raw Secret leakage."""
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.session import SessionManager

    access_secret = "SECRET-ACCESS-TOKEN-VALUE"
    refresh_secret = "SECRET-REFRESH-TOKEN-VALUE"
    access_jwt = jwt_with_exp(time.time() + 3600)

    def build(fake_core_v1, responder=None, seed=True):
        store = TokenStore(k8s_core_v1=fake_core_v1)
        if seed:
            fake_core_v1.seed(
                "codex-shim-auth", "llms", {"access_token": access_jwt, "refresh_token": refresh_secret}
            )
        transport = mock_token_transport(responder) if responder else None
        import functools

        refresh_fn = (
            functools.partial(codex_auth.refresh_codex_oauth_pure, transport=transport)
            if transport
            else codex_auth.refresh_codex_oauth_pure
        )
        manager = SessionManager(store=store, refresh_fn=refresh_fn)
        return create_app(session_manager=manager)

    cases = []

    # not_configured
    cases.append(("not_configured", build(FakeCoreV1Api(), seed=False)))

    # valid
    cases.append(("valid", build(FakeCoreV1Api())))

    # backend_unreachable
    fake_unreachable = RaisingCoreV1Api(read_exc=FakeApiException(500, body=access_secret))
    cases.append(("backend_unreachable", build(fake_unreachable, seed=False)))

    # refresh_failed / rate_limited / expired_needs_relogin exercised via a
    # near-expired token forcing a refresh attempt.
    near_expiry_jwt = jwt_with_exp(time.time() - 10)

    def build_refresh(status_code, fake_core_v1=None):
        fake_core_v1 = fake_core_v1 or FakeCoreV1Api()
        store = TokenStore(k8s_core_v1=fake_core_v1)
        fake_core_v1.seed(
            "codex-shim-auth", "llms", {"access_token": near_expiry_jwt, "refresh_token": refresh_secret}
        )

        def responder(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code, json={})

        transport = mock_token_transport(responder)
        import functools

        refresh_fn = functools.partial(codex_auth.refresh_codex_oauth_pure, transport=transport)
        manager = SessionManager(store=store, refresh_fn=refresh_fn)
        return create_app(session_manager=manager)

    cases.append(("refresh_failed", build_refresh(503)))
    cases.append(("rate_limited", build_refresh(429)))
    cases.append(("expired_needs_relogin", build_refresh(401)))

    # expiring_soon: token within skew window but not yet expired -> still
    # reported as "valid" by ensure_fresh's proactive path unless refresh is
    # attempted; use a manager with a huge skew so the token is "expiring".
    expiring_jwt = jwt_with_exp(time.time() + 60)
    fake_expiring = FakeCoreV1Api()
    fake_expiring.seed("codex-shim-auth", "llms", {"access_token": expiring_jwt, "refresh_token": refresh_secret})
    store_expiring = TokenStore(k8s_core_v1=fake_expiring)

    def responder_ok(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"access_token": jwt_with_exp(time.time() + 3600), "refresh_token": refresh_secret}
        )

    import functools

    refresh_fn_ok = functools.partial(codex_auth.refresh_codex_oauth_pure, transport=mock_token_transport(responder_ok))
    manager_expiring = SessionManager(store=store_expiring, refresh_fn=refresh_fn_ok, skew_seconds=99999)
    cases.append(("expiring_soon_or_valid", create_app(session_manager=manager_expiring)))

    for label, app in cases:
        with TestClient(app) as client:
            resp = client.get("/internal/session")
        assert resp.status_code == 200, label
        body_text = resp.text
        assert access_secret not in body_text, label
        assert refresh_secret not in body_text, label
        assert "access_token" not in resp.json(), label
        assert "refresh_token" not in resp.json(), label


# --- Phase 3: session.py state wiring ----------------------------------------


def test_load_cached_classifies_backend_unreachable_and_reraises():
    fake = RaisingCoreV1Api(read_exc=FakeApiException(500))
    store = TokenStore(k8s_core_v1=fake)

    from app.session import SessionManager

    manager = SessionManager(store=store)

    with pytest.raises(StoreUnreachable):
        manager._load_cached()

    status = manager.status()
    assert status["state"] == "backend_unreachable"
    assert status["last_error_code"] == "k8s_api_500"
    assert status["reason"] == "kubernetes API secret read failed (k8s_api_500)"


def test_do_refresh_locked_classifies_backend_unreachable_on_write_failure():
    fake = RaisingCoreV1Api(patch_exc=FakeApiException(500))
    fake.seed(
        "codex-shim-auth", "llms", {"access_token": jwt_with_exp(time.time() - 10), "refresh_token": "rt"}
    )
    store = TokenStore(k8s_core_v1=fake)

    def responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"access_token": jwt_with_exp(time.time() + 3600), "refresh_token": "rt-new"}
        )

    import functools

    from app.session import SessionManager

    refresh_fn = functools.partial(codex_auth.refresh_codex_oauth_pure, transport=mock_token_transport(responder))
    manager = SessionManager(store=store, refresh_fn=refresh_fn)

    with pytest.raises(StoreUnreachable):
        asyncio.run(manager.refresh())

    assert manager.status()["state"] == "backend_unreachable"


def test_cached_not_dropped_on_store_unreachable_d07():
    """Task 3.3: once a token is cached, a subsequent read failure must not
    invalidate `_cached` — the cache stays authoritative until a refresh
    (write) is actually attempted."""
    fake = FakeCoreV1Api()
    fake.seed("codex-shim-auth", "llms", {"access_token": jwt_with_exp(time.time() + 3600), "refresh_token": "rt"})
    store = TokenStore(k8s_core_v1=fake)

    from app.session import SessionManager

    manager = SessionManager(store=store)
    record = manager._load_cached()
    assert record is not None

    # Now break the underlying client — a fresh read would fail, but
    # `_load_cached()` should short-circuit via `_cached` without ever
    # calling into the (now broken) store again.
    fake.read_namespaced_secret = lambda *a, **k: (_ for _ in ()).throw(FakeApiException(500))

    record_again = manager._load_cached()
    assert record_again is record


# --- Phase 4: main.py response shape ----------------------------------------


def test_internal_session_returns_200_backend_unreachable_not_500():
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.session import SessionManager

    fake = RaisingCoreV1Api(read_exc=FakeApiException(500))
    store = TokenStore(k8s_core_v1=fake)
    manager = SessionManager(store=store)
    app = create_app(session_manager=manager)

    with TestClient(app) as client:
        resp = client.get("/internal/session")

    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "backend_unreachable"
    assert set(body.keys()) == {"state", "expires_at", "last_refresh", "last_error_code", "reason"}


# --- Phase 5: proxy.py widening (D-06) ---------------------------------------


def _build_proxy_app(fake_core_v1, internal_key="test-key"):
    import os

    from app.main import create_app
    from app.session import SessionManager

    os.environ["CODEX_SHIM_INTERNAL_KEY"] = internal_key
    store = TokenStore(k8s_core_v1=fake_core_v1)
    manager = SessionManager(store=store)
    return create_app(session_manager=manager)


def test_chat_completions_non_streaming_returns_503_backend_unreachable_not_500():
    from fastapi.testclient import TestClient

    fake = RaisingCoreV1Api(read_exc=FakeApiException(500))
    app = _build_proxy_app(fake)

    with TestClient(app) as client:
        resp = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer test-key"},
            json={"model": "x", "messages": [], "stream": False},
        )

    assert resp.status_code == 503
    assert resp.json()["error"]["state"] == "backend_unreachable"


def test_chat_completions_streaming_returns_503_backend_unreachable_not_500():
    from fastapi.testclient import TestClient

    fake = RaisingCoreV1Api(read_exc=FakeApiException(500))
    app = _build_proxy_app(fake)

    with TestClient(app) as client:
        resp = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer test-key"},
            json={"model": "x", "messages": [], "stream": True},
        )

    assert resp.status_code == 503
    assert resp.json()["error"]["state"] == "backend_unreachable"


def test_responses_passthrough_returns_503_backend_unreachable_not_500():
    from fastapi.testclient import TestClient

    fake = RaisingCoreV1Api(read_exc=FakeApiException(500))
    app = _build_proxy_app(fake)

    with TestClient(app) as client:
        resp = client.post(
            "/v1/responses",
            headers={"Authorization": "Bearer test-key"},
            json={"model": "x", "input": [], "stream": False},
        )

    assert resp.status_code == 503
    assert resp.json()["error"]["state"] == "backend_unreachable"


# --- Phase 6: manifest zero-diff boundary check ------------------------------


def test_codex_shim_deployment_manifest_has_no_webhook_secret_reference():
    """Task 6.2: codex-shim/deployment.yaml must have zero diff — no
    signing-secret / webhook reference of any kind (D-19 boundary)."""
    from pathlib import Path

    manifest_path = Path(__file__).resolve().parents[1] / "deployment.yaml"
    text = manifest_path.read_text()

    assert "model-panel-webhook" not in text
    assert "HERMES_WEBHOOK_URL" not in text
    assert "MODEL_PANEL_WEBHOOK_SECRET" not in text
