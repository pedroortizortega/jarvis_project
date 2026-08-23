import json
import sys
import unittest
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "hermes-native" / "memory-router" / "src")
)

from memory_router.backends.engram import EngramBackend
from memory_router.backends.hindsight import HindsightBackend
from memory_router.contracts import (
    BackendUnavailableError,
    HealthStatus,
    MemoryBackend,
    SearchRequest,
    StoreRequest,
)


class HindsightAdapterConfigTests(unittest.TestCase):
    def test_zero_arg_construction_succeeds(self):
        backend = HindsightBackend()
        self.assertIsInstance(backend, HindsightBackend)

    def test_defaults_when_no_env_and_no_explicit_args(self, monkeypatch=None):
        import os

        saved = {
            key: os.environ.pop(key, None)
            for key in (
                "HINDSIGHT_BASE_URL",
                "HINDSIGHT_AUTH_MODE",
                "HINDSIGHT_TOKEN",
                "HINDSIGHT_BANK_PREFIX",
                "HINDSIGHT_TIMEOUT_SECONDS",
            )
        }
        try:
            backend = HindsightBackend()
            self.assertEqual(
                "http://hindsight.mcps.svc.cluster.local:8888", backend._base_url
            )
            self.assertEqual("none", backend._auth_mode)
            self.assertEqual("", backend._token)
            self.assertEqual("", backend._bank_prefix)
            self.assertEqual(10, backend._timeout)
        finally:
            for key, value in saved.items():
                if value is not None:
                    os.environ[key] = value

    def test_env_vars_are_resolved_when_no_explicit_args(self):
        import os

        saved = {
            key: os.environ.get(key)
            for key in (
                "HINDSIGHT_BASE_URL",
                "HINDSIGHT_AUTH_MODE",
                "HINDSIGHT_TOKEN",
                "HINDSIGHT_BANK_PREFIX",
                "HINDSIGHT_TIMEOUT_SECONDS",
            )
        }
        os.environ["HINDSIGHT_BASE_URL"] = "http://env-host:9000"
        os.environ["HINDSIGHT_AUTH_MODE"] = "bearer"
        os.environ["HINDSIGHT_TOKEN"] = "env-token"
        os.environ["HINDSIGHT_BANK_PREFIX"] = "jarvis-"
        os.environ["HINDSIGHT_TIMEOUT_SECONDS"] = "5"
        try:
            backend = HindsightBackend()
            self.assertEqual("http://env-host:9000", backend._base_url)
            self.assertEqual("bearer", backend._auth_mode)
            self.assertEqual("env-token", backend._token)
            self.assertEqual("jarvis-", backend._bank_prefix)
            self.assertEqual(5, backend._timeout)
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_explicit_args_override_env(self):
        import os

        saved = os.environ.get("HINDSIGHT_BASE_URL")
        os.environ["HINDSIGHT_BASE_URL"] = "http://env-host:9000"
        try:
            backend = HindsightBackend(base_url="http://explicit:1234")
            self.assertEqual("http://explicit:1234", backend._base_url)
        finally:
            if saved is None:
                os.environ.pop("HINDSIGHT_BASE_URL", None)
            else:
                os.environ["HINDSIGHT_BASE_URL"] = saved

    def test_auth_mode_defaults_to_bearer_when_token_present_no_explicit_mode(self):
        backend = HindsightBackend(base_url="http://x", token="tok", auth_mode=None)
        self.assertEqual("bearer", backend._auth_mode)


class HttpJsonClientTests(unittest.TestCase):
    def test_construction_with_injected_transport(self):
        from memory_router.backends.hindsight import _HttpJsonClient

        client = _HttpJsonClient(
            transport=lambda method, url, headers, body: (200, b"{}"),
            base_url="http://x",
            timeout=10,
        )
        self.assertIsNotNone(client)


class BankIdSanitizerTests(unittest.TestCase):
    def test_flattens_project_namespace_to_bank_id(self):
        backend = HindsightBackend(base_url="http://x")
        self.assertEqual("projects-lector-ine", backend._bank_id("/projects/lector-ine"))

    def test_applies_bank_prefix_when_configured(self):
        backend = HindsightBackend(base_url="http://x", bank_prefix="jarvis-")
        self.assertEqual(
            "jarvis-projects-lector-ine", backend._bank_id("/projects/lector-ine")
        )

    def test_lowercases_namespace(self):
        backend = HindsightBackend(base_url="http://x")
        self.assertEqual("projects-lectorine", backend._bank_id("/projects/LectorINE"))

    def test_traversal_namespace_never_yields_illegal_segment(self):
        backend = HindsightBackend(base_url="http://x")
        with self.assertRaises(ValueError):
            backend._bank_id("/projects/../../etc/passwd")

    def test_wildcard_namespace_never_yields_illegal_segment(self):
        backend = HindsightBackend(base_url="http://x")
        with self.assertRaises(ValueError):
            backend._bank_id("/projects/*")

    def test_result_matches_bank_id_charset(self):
        import re

        backend = HindsightBackend(base_url="http://x", bank_prefix="Jarvis_")
        bank_id = backend._bank_id("/projects/foo")
        self.assertRegex(bank_id, r"^[a-z0-9][a-z0-9_-]*$")


class StubTransport:
    """Records every (method, url, headers, body) call and answers from a
    queue of (status, payload) responses, one per call."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        status, payload = self._responses.pop(0)
        if isinstance(payload, (bytes, type(None))):
            raw = payload or b""
        else:
            raw = json.dumps(payload).encode("utf-8")
        return status, raw


class HindsightAdapterStoreSearchTests(unittest.TestCase):
    def test_store_returns_committed_result_on_success(self):
        transport = StubTransport([(200, {"id": "mem-1"})])
        backend = HindsightBackend(transport=transport, base_url="http://x")

        result = backend.store(
            StoreRequest(namespace="/projects/lector-ine", role="coder", content="hi")
        )

        self.assertEqual("committed", result.status)
        self.assertEqual("hindsight", result.backend)
        self.assertEqual("mem-1", result.id)
        method, url, _headers, body = transport.calls[0]
        self.assertEqual("POST", method)
        self.assertIn("/v1/default/banks/projects-lector-ine/memories", url)
        self.assertEqual("hi", json.loads(body)["items"][0]["content"])

    def test_search_returns_hits_from_results(self):
        transport = StubTransport(
            [(200, {"results": [{"text": "found", "scores": {"final": 0.9}}]})]
        )
        backend = HindsightBackend(transport=transport, base_url="http://x")

        result = backend.search(
            SearchRequest(namespace="/projects/lector-ine", role="coder", query="deploy")
        )

        self.assertEqual(1, len(result.hits))
        self.assertEqual("found", result.hits[0].content)
        self.assertEqual("hindsight", result.hits[0].backend)
        self.assertEqual("/projects/lector-ine", result.hits[0].namespace)
        method, url, _headers, body = transport.calls[0]
        self.assertEqual("POST", method)
        self.assertIn("/v1/default/banks/projects-lector-ine/memories/recall", url)
        self.assertEqual("deploy", json.loads(body)["query"])

    def test_store_lazy_creates_bank_on_404_then_retries_once(self):
        transport = StubTransport(
            [
                (404, {"error": "bank not found"}),
                (200, {"bank_id": "projects-lector-ine"}),
                (200, {"id": "mem-2"}),
            ]
        )
        backend = HindsightBackend(transport=transport, base_url="http://x")

        result = backend.store(
            StoreRequest(namespace="/projects/lector-ine", role="coder", content="hi")
        )

        self.assertEqual("committed", result.status)
        self.assertEqual("mem-2", result.id)
        methods_and_urls = [(m, u) for m, u, _h, _b in transport.calls]
        self.assertEqual(
            [
                ("POST", "http://x/v1/default/banks/projects-lector-ine/memories"),
                ("PUT", "http://x/v1/default/banks/projects-lector-ine"),
                ("POST", "http://x/v1/default/banks/projects-lector-ine/memories"),
            ],
            methods_and_urls,
        )

    def test_health_returns_ok_on_2xx(self):
        transport = StubTransport([(200, {})])
        backend = HindsightBackend(transport=transport, base_url="http://x")
        health = backend.health()
        self.assertEqual(HealthStatus.OK, health.status)

    def test_health_returns_down_on_non_2xx_and_never_raises(self):
        transport = StubTransport([(503, {"error": "down"})])
        backend = HindsightBackend(transport=transport, base_url="http://x")
        health = backend.health()
        self.assertEqual(HealthStatus.DOWN, health.status)


class HindsightAdapterAuthTests(unittest.TestCase):
    def test_bearer_header_present_when_auth_mode_bearer(self):
        transport = StubTransport([(200, {"id": "mem-1"})])
        backend = HindsightBackend(
            transport=transport, base_url="http://x", auth_mode="bearer", token="secret-tok"
        )
        backend.store(StoreRequest(namespace="/projects/foo", role="coder", content="hi"))
        _method, _url, headers, _body = transport.calls[0]
        self.assertEqual("Bearer secret-tok", headers.get("Authorization"))

    def test_authorization_header_absent_when_auth_mode_none(self):
        transport = StubTransport([(200, {"id": "mem-1"})])
        backend = HindsightBackend(
            transport=transport, base_url="http://x", auth_mode="none", token=""
        )
        backend.store(StoreRequest(namespace="/projects/foo", role="coder", content="hi"))
        _method, _url, headers, _body = transport.calls[0]
        self.assertNotIn("Authorization", headers)


class HindsightAdapterSecurityTests(unittest.TestCase):
    def test_malicious_content_and_metadata_never_reach_url_or_headers(self):
        transport = StubTransport([(200, {"id": "mem-1"})])
        backend = HindsightBackend(
            transport=transport, base_url="http://x", auth_mode="bearer", token="tok"
        )
        malicious = StoreRequest(
            namespace="/projects/foo",
            role="coder",
            content="; rm -rf / #",
            metadata={"Authorization": "Bearer evil", "url": "http://evil"},
        )
        backend.store(malicious)
        _method, url, headers, _body = transport.calls[0]
        self.assertNotIn("evil", url)
        self.assertNotIn("rm -rf", url)
        self.assertEqual({"Content-Type", "Authorization"}, set(headers.keys()))
        self.assertEqual("Bearer tok", headers["Authorization"])

    def test_header_key_set_is_fixed_across_requests(self):
        transport = StubTransport(
            [(200, {"results": []})]
        )
        backend = HindsightBackend(
            transport=transport, base_url="http://x", auth_mode="none", token=""
        )
        backend.search(SearchRequest(namespace="/projects/foo", role="coder", query="q"))
        _method, _url, headers, _body = transport.calls[0]
        self.assertEqual({"Content-Type"}, set(headers.keys()))


class HindsightAdapterDegradationTests(unittest.TestCase):
    def test_connection_error_raises_backend_unavailable(self):
        def failing_transport(method, url, headers, body):
            raise OSError("connection refused")

        backend = HindsightBackend(transport=failing_transport, base_url="http://x")
        with self.assertRaises(BackendUnavailableError):
            backend.store(StoreRequest(namespace="/projects/foo", role="coder", content="hi"))

    def test_non_2xx_status_raises_backend_unavailable(self):
        transport = StubTransport([(500, {"error": "boom"})])
        backend = HindsightBackend(transport=transport, base_url="http://x")
        with self.assertRaises(BackendUnavailableError):
            backend.store(StoreRequest(namespace="/projects/foo", role="coder", content="hi"))

    def test_malformed_json_response_raises_backend_unavailable(self):
        def transport(method, url, headers, body):
            return 200, b"not json{{"

        backend = HindsightBackend(transport=transport, base_url="http://x")
        with self.assertRaises(BackendUnavailableError):
            backend.store(StoreRequest(namespace="/projects/foo", role="coder", content="hi"))

    def test_no_other_exception_type_escapes_on_failure(self):
        transport = StubTransport([(500, {"error": "boom"})])
        backend = HindsightBackend(transport=transport, base_url="http://x")
        try:
            backend.store(StoreRequest(namespace="/projects/foo", role="coder", content="hi"))
            self.fail("expected BackendUnavailableError")
        except BackendUnavailableError:
            pass
        except Exception as exc:  # noqa: BLE001
            self.fail(f"unexpected exception type escaped: {type(exc)}: {exc}")


class HindsightAdapterSecretHandlingTests(unittest.TestCase):
    def test_token_never_appears_in_backend_unavailable_reason(self):
        transport = StubTransport([(500, {"error": "boom"})])
        backend = HindsightBackend(
            transport=transport, base_url="http://x", auth_mode="bearer", token="super-secret-token"
        )
        try:
            backend.store(StoreRequest(namespace="/projects/foo", role="coder", content="hi"))
            self.fail("expected BackendUnavailableError")
        except BackendUnavailableError as exc:
            self.assertNotIn("super-secret-token", exc.reason)

    def test_token_never_appears_in_health_down_reason(self):
        transport = StubTransport([(503, {"error": "down"})])
        backend = HindsightBackend(
            transport=transport, base_url="http://x", auth_mode="bearer", token="super-secret-token"
        )
        health = backend.health()
        self.assertNotIn("super-secret-token", health.reason)


class HindsightAdapterProtocolConformanceTests(unittest.TestCase):
    def test_isinstance_memory_backend_protocol(self):
        backend = HindsightBackend(base_url="http://x")
        self.assertIsInstance(backend, MemoryBackend)

    def test_capabilities_verbs_exclude_reflect(self):
        backend = HindsightBackend(base_url="http://x")
        capabilities = backend.capabilities()
        self.assertEqual({"store", "search"}, set(capabilities.verbs))
        self.assertNotIn("reflect", capabilities.verbs)

    def test_hindsight_and_engram_namespaces_share_zero_entries(self):
        hindsight_ns = set(HindsightBackend(base_url="http://x").capabilities().namespaces)
        engram_ns = set(
            EngramBackend(spawn=lambda argv, env: None).capabilities().namespaces
        )
        self.assertEqual(set(), hindsight_ns & engram_ns)

    def test_capabilities_namespaces_is_projects_only(self):
        backend = HindsightBackend(base_url="http://x")
        self.assertEqual(("/projects/*",), backend.capabilities().namespaces)


if __name__ == "__main__":
    unittest.main()
