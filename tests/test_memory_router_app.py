import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "hermes-native" / "memory-router" / "src")
)

from memory_router.app import (
    CLIENT_CN_HEADER,
    Dispatcher,
    DispatchError,
    RestClient,
    build_http_server,
    handle_mcp_tool_call,
)
from memory_router.contracts import (
    BackendUnavailableError,
    Capabilities,
    Conclusion,
    Health,
    HealthStatus,
    ReflectResult,
    SearchHit,
    SearchResult,
    StoreResult,
)
from memory_router.journal import Journal
from memory_router.registry import Registry

CN_TO_IDENTITY = {"codex": "codex", "hermes-gateway": "hermes-gateway"}
BEARER_BY_IDENTITY = {"codex": "token-codex", "hermes-gateway": "token-hg"}


class FakeBackend:
    def __init__(
        self,
        *,
        name="engram",
        namespaces=("/global", "/projects/*", "/agents/*"),
        hits=None,
        hits_by_namespace=None,
        fail=False,
    ):
        self._name = name
        self._namespaces = namespaces
        self._hits = hits or []
        self._hits_by_namespace = hits_by_namespace
        self._fail = fail
        self.store_calls = []
        self.search_calls = []

    def capabilities(self) -> Capabilities:
        return Capabilities(
            name=self._name, verbs=frozenset({"store", "search"}), namespaces=self._namespaces
        )

    def health(self) -> Health:
        return Health(status=HealthStatus.DOWN if self._fail else HealthStatus.OK)

    def store(self, req):
        self.store_calls.append(req)
        if self._fail:
            raise BackendUnavailableError(self._name, "simulated crash")
        return StoreResult(status="committed", backend=self._name, id="note-1")

    def search(self, req):
        self.search_calls.append(req)
        if self._fail:
            raise BackendUnavailableError(self._name, "simulated crash")
        if self._hits_by_namespace is not None:
            texts = self._hits_by_namespace.get(req.namespace, [])
        else:
            texts = self._hits
        hits = tuple(
            SearchHit(namespace=req.namespace, backend=self._name, content=text)
            for text in texts
        )
        return SearchResult(hits=hits)


class FakeReflectiveBackend:
    def __init__(self, *, name="honcho", namespaces=("/user/master",), status="ready", fail=False):
        self._name = name
        self._namespaces = namespaces
        self._status = status
        self._fail = fail
        self.reflect_calls = []

    def capabilities(self) -> Capabilities:
        return Capabilities(
            name=self._name, verbs=frozenset({"reflect"}), namespaces=self._namespaces
        )

    def health(self) -> Health:
        return Health(status=HealthStatus.DOWN if self._fail else HealthStatus.OK)

    def reflect(self, req):
        self.reflect_calls.append(req)
        if self._fail:
            raise BackendUnavailableError(self._name, "simulated crash")
        if self._status == "pending":
            return ReflectResult(status="pending", backend=self._name)
        conclusion = Conclusion(
            namespace=req.namespace, backend=self._name, content="derived belief"
        )
        return ReflectResult(status="ready", backend=self._name, conclusions=(conclusion,))


def make_dispatcher(backend, *, journal_dir):
    registry = Registry(backends=[backend] if backend else [])
    journal = Journal(Path(journal_dir) / "journal.ndjson")
    return Dispatcher(
        registry=registry,
        journal=journal,
        cn_to_identity=CN_TO_IDENTITY,
        bearer_by_identity=BEARER_BY_IDENTITY,
    )


class DispatcherStoreTests(unittest.TestCase):
    def test_healthy_store_returns_committed(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = FakeBackend()
            dispatcher = make_dispatcher(backend, journal_dir=directory)
            result = dispatcher.store(
                cn="codex", bearer="token-codex", role="coder",
                namespace="/projects/lector-ine", content="hello",
            )
        self.assertEqual("committed", result["status"])
        self.assertEqual("engram", result["backend"])

    def test_degraded_store_queues_and_is_never_dropped(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = FakeBackend(fail=True)
            dispatcher = make_dispatcher(backend, journal_dir=directory)
            result = dispatcher.store(
                cn="codex", bearer="token-codex", role="coder",
                namespace="/projects/lector-ine", content="important",
            )
            self.assertEqual("pending", result["status"])
            self.assertIn("queue_id", result)

            journal = Journal(Path(directory) / "journal.ndjson")
            entries = journal.replay()
        self.assertEqual(1, len(entries))
        self.assertEqual("important", entries[0]["content"])

    def test_store_with_no_capable_backend_queues_pending(self):
        with tempfile.TemporaryDirectory() as directory:
            dispatcher = make_dispatcher(None, journal_dir=directory)
            result = dispatcher.store(
                cn="codex", bearer="token-codex", role="coder",
                namespace="/projects/lector-ine", content="x",
            )
        self.assertEqual("pending", result["status"])

    def test_store_never_falls_back_to_another_namespace(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = FakeBackend()
            dispatcher = make_dispatcher(backend, journal_dir=directory)
            dispatcher.store(
                cn="codex", bearer="token-codex", role="coder",
                namespace="/projects/lector-ine", content="x",
            )
        self.assertEqual(1, len(backend.store_calls))
        self.assertEqual("/projects/lector-ine", backend.store_calls[0].namespace)

    def test_store_denied_by_permissions_raises_dispatch_error(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = FakeBackend()
            dispatcher = make_dispatcher(backend, journal_dir=directory)
            with self.assertRaises(DispatchError) as ctx:
                dispatcher.store(
                    cn="codex", bearer="token-codex", role="coder",
                    namespace="/user/master", content="x",
                )
        self.assertEqual(403, ctx.exception.status)


class DispatcherSearchTests(unittest.TestCase):
    def test_hierarchical_fallback_project_to_agent_to_global(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = FakeBackend(hits_by_namespace={"/global": ["found in global"]})
            dispatcher = make_dispatcher(backend, journal_dir=directory)
            result = dispatcher.search(
                cn="hermes-gateway", bearer="token-hg", role="jarvis",
                namespace="/projects/lector-ine", query="deploy",
            )
        namespaces_hit = {hit["namespace"] for hit in result["hits"]}
        self.assertEqual({"/global"}, namespaces_hit)
        searched_namespaces = [req.namespace for req in backend.search_calls]
        self.assertEqual(
            ["/projects/lector-ine", "/agents/hermes-gateway", "/global"], searched_namespaces
        )

    def test_search_stops_at_first_namespace_with_hits(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = FakeBackend(hits=["found here"])
            dispatcher = make_dispatcher(backend, journal_dir=directory)
            result = dispatcher.search(
                cn="hermes-gateway", bearer="token-hg", role="jarvis",
                namespace="/global", query="deploy",
            )
        self.assertEqual(1, len(backend.search_calls))
        self.assertEqual(1, len(result["hits"]))

    def test_degraded_search_returns_partial_with_unavailable_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = FakeBackend(fail=True)
            dispatcher = make_dispatcher(backend, journal_dir=directory)
            result = dispatcher.search(
                cn="hermes-gateway", bearer="token-hg", role="jarvis",
                namespace="/global", query="deploy",
            )
        self.assertEqual([], result["hits"])
        self.assertEqual(1, len(result["unavailable"]))
        self.assertEqual("engram", result["unavailable"][0]["backend"])

    def test_search_denied_by_permissions_raises_dispatch_error(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = FakeBackend()
            dispatcher = make_dispatcher(backend, journal_dir=directory)
            with self.assertRaises(DispatchError) as ctx:
                dispatcher.search(
                    cn="codex", bearer="token-codex", role="coder",
                    namespace="/agents/hermes-gateway", query="x",
                )
        self.assertEqual(403, ctx.exception.status)


class ExplodingJournal:
    def append(self, entry):
        raise AssertionError("reflect must never touch the journal")


def make_reflect_dispatcher(backends, *, journal_dir):
    registry = Registry(backends=list(backends))
    journal = Journal(Path(journal_dir) / "journal.ndjson")
    return Dispatcher(
        registry=registry,
        journal=journal,
        cn_to_identity=CN_TO_IDENTITY,
        bearer_by_identity=BEARER_BY_IDENTITY,
    )


class DispatcherReflectTests(unittest.TestCase):
    def test_authorized_role_gets_routed_reflect_result_never_501(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = FakeReflectiveBackend()
            dispatcher = make_reflect_dispatcher([backend], journal_dir=directory)
            result = dispatcher.reflect(
                cn="hermes-gateway", bearer="token-hg", role="jarvis",
                namespace="/user/master", query="preferences?",
            )
        self.assertEqual("ready", result["status"])
        self.assertEqual(1, len(result["conclusions"]))
        self.assertEqual("derived belief", result["conclusions"][0]["content"])
        self.assertEqual([], result["unavailable"])
        self.assertEqual(1, len(backend.reflect_calls))

    def test_unauthorized_role_gets_403_not_501(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = FakeReflectiveBackend()
            dispatcher = make_reflect_dispatcher([backend], journal_dir=directory)
            with self.assertRaises(DispatchError) as ctx:
                dispatcher.reflect(
                    cn="codex", bearer="token-codex", role="coder",
                    namespace="/user/master", query="x",
                )
        self.assertEqual(403, ctx.exception.status)
        self.assertEqual("authorization_denied", ctx.exception.error)
        self.assertEqual(0, len(backend.reflect_calls))

    def test_no_reflect_capable_backend_returns_explicit_no_backend_status(self):
        with tempfile.TemporaryDirectory() as directory:
            # Only a store/search backend is registered — no ReflectiveBackend.
            store_backend = FakeBackend()
            dispatcher = make_reflect_dispatcher([store_backend], journal_dir=directory)
            result = dispatcher.reflect(
                cn="hermes-gateway", bearer="token-hg", role="jarvis",
                namespace="/user/master", query="x",
            )
        self.assertEqual("no_backend", result["status"])
        self.assertEqual([], result["conclusions"])
        self.assertEqual([], result["unavailable"])

    def test_all_backends_down_returns_degraded_status(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = FakeReflectiveBackend(fail=True)
            dispatcher = make_reflect_dispatcher([backend], journal_dir=directory)
            result = dispatcher.reflect(
                cn="hermes-gateway", bearer="token-hg", role="jarvis",
                namespace="/user/master", query="x",
            )
        self.assertEqual("degraded", result["status"])
        self.assertEqual([], result["conclusions"])
        self.assertEqual(1, len(result["unavailable"]))
        self.assertEqual("honcho", result["unavailable"][0]["backend"])

    def test_pending_backend_returns_pending_status(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = FakeReflectiveBackend(status="pending")
            dispatcher = make_reflect_dispatcher([backend], journal_dir=directory)
            result = dispatcher.reflect(
                cn="hermes-gateway", bearer="token-hg", role="jarvis",
                namespace="/user/master", query="x",
            )
        self.assertEqual("pending", result["status"])
        self.assertEqual([], result["conclusions"])

    def test_reflect_never_touches_journal(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = FakeReflectiveBackend()
            registry = Registry(backends=[backend])
            dispatcher = Dispatcher(
                registry=registry,
                journal=ExplodingJournal(),
                cn_to_identity=CN_TO_IDENTITY,
                bearer_by_identity=BEARER_BY_IDENTITY,
            )
            dispatcher.reflect(
                cn="hermes-gateway", bearer="token-hg", role="jarvis",
                namespace="/user/master", query="x",
            )


class AppSourceHygieneTests(unittest.TestCase):
    def test_no_stale_hindsight_reflect_references_remain(self):
        app_source = Path(
            Path(__file__).resolve().parent.parent
            / "hermes-native" / "memory-router" / "src" / "memory_router" / "app.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("lands with Hindsight", app_source)
        self.assertNotIn('"phase": "hindsight"', app_source)


class RestAndMcpParityTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.backend = FakeBackend(hits=["hit-a"])
        self.reflective_backend = FakeReflectiveBackend()
        registry = Registry(backends=[self.backend, self.reflective_backend])
        journal = Journal(Path(self._directory.name) / "journal.ndjson")
        self.dispatcher = Dispatcher(
            registry=registry,
            journal=journal,
            cn_to_identity=CN_TO_IDENTITY,
            bearer_by_identity=BEARER_BY_IDENTITY,
        )
        self.server = build_http_server(self.dispatcher, host="127.0.0.1", port=0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self._directory.cleanup()

    def _post(self, path, body, cn="codex", bearer="token-codex"):
        connection = http.client.HTTPConnection("127.0.0.1", self.port)
        connection.request(
            "POST",
            path,
            body=json.dumps(body),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {bearer}",
                CLIENT_CN_HEADER: cn,
            },
        )
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status, payload

    def test_rest_store_accepted(self):
        status, payload = self._post(
            "/memory/store",
            {"role": "coder", "namespace": "/projects/lector-ine", "content": "hi"},
        )
        self.assertEqual(202, status)
        self.assertEqual("committed", payload["status"])

    def test_rest_reflect_writes_a_routed_response_body_never_501(self):
        status, payload = self._post(
            "/memory/reflect",
            {"role": "jarvis", "namespace": "/user/master", "query": "preferences?"},
            cn="hermes-gateway",
            bearer="token-hg",
        )
        self.assertNotEqual(501, status)
        self.assertEqual(200, status)
        self.assertEqual("ready", payload["status"])
        self.assertEqual(1, len(payload["conclusions"]))
        self.assertEqual(0, len(self.backend.store_calls))
        self.assertEqual(0, len(self.backend.search_calls))

    def test_mcp_and_rest_produce_equivalent_store_result(self):
        rest_status, rest_payload = self._post(
            "/memory/store",
            {"role": "coder", "namespace": "/projects/lector-ine", "content": "same"},
        )

        client = RestClient(f"http://127.0.0.1:{self.port}", cn="codex", bearer="token-codex")
        mcp_result = handle_mcp_tool_call(
            "memory_store",
            {"role": "coder", "namespace": "/projects/lector-ine", "content": "same"},
            client=client,
        )

        self.assertEqual(rest_status, mcp_result["status"])
        self.assertEqual(rest_payload["status"], mcp_result["body"]["status"])
        self.assertEqual(rest_payload["backend"], mcp_result["body"]["backend"])

    def test_mcp_and_rest_produce_equivalent_reflect_result(self):
        rest_status, rest_payload = self._post(
            "/memory/reflect",
            {"role": "jarvis", "namespace": "/user/master", "query": "same?"},
            cn="hermes-gateway",
            bearer="token-hg",
        )

        client = RestClient(
            f"http://127.0.0.1:{self.port}", cn="hermes-gateway", bearer="token-hg"
        )
        mcp_result = handle_mcp_tool_call(
            "memory_reflect",
            {"role": "jarvis", "namespace": "/user/master", "query": "same?"},
            client=client,
        )

        self.assertEqual(rest_status, mcp_result["status"])
        self.assertNotEqual(501, mcp_result["status"])
        self.assertEqual(rest_payload["status"], mcp_result["body"]["status"])
        self.assertEqual(
            len(rest_payload["conclusions"]), len(mcp_result["body"]["conclusions"])
        )

    def test_healthz_requires_no_auth_for_kubernetes_probes(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.port)
        connection.request("GET", "/healthz")
        response = connection.getresponse()
        response.read()
        connection.close()
        self.assertEqual(200, response.status)

    def _get(self, path, cn=None, bearer=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port)
        headers = {}
        if cn is not None:
            headers[CLIENT_CN_HEADER] = cn
        if bearer is not None:
            headers["Authorization"] = f"Bearer {bearer}"
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status, payload

    def test_agents_context_requires_authentication(self):
        status, payload = self._get("/agents/hermes-gateway/context?role=jarvis")
        self.assertEqual(401, status)
        self.assertEqual("identity_rejected", payload["error"])

    def test_agents_context_returns_content_for_permitted_namespace(self):
        status, payload = self._get(
            "/agents/hermes-gateway/context?role=jarvis",
            cn="hermes-gateway",
            bearer="token-hg",
        )
        self.assertEqual(200, status)
        self.assertEqual("/agents/hermes-gateway", payload["namespace"])
        self.assertEqual(1, len(payload["items"]))
        self.assertEqual("hit-a", payload["items"][0]["content"])

    def test_projects_context_returns_content_for_permitted_namespace(self):
        status, payload = self._get(
            "/projects/lector-ine/context?role=coder",
            cn="codex",
            bearer="token-codex",
        )
        self.assertEqual(200, status)
        self.assertEqual("/projects/lector-ine", payload["namespace"])
        self.assertEqual(1, len(payload["items"]))

    def test_agents_context_denied_for_namespace_outside_permitted_role(self):
        status, payload = self._get(
            "/agents/hermes-gateway/context?role=coder",
            cn="codex",
            bearer="token-codex",
        )
        self.assertEqual(403, status)
        self.assertEqual("authorization_denied", payload["error"])

    def test_context_malformed_namespace_segment_rejected(self):
        status, payload = self._get(
            "/agents/../context?role=coder",
            cn="codex",
            bearer="token-codex",
        )
        self.assertEqual(400, status)
        self.assertEqual("invalid_namespace", payload["error"])


class DispatcherContextTests(unittest.TestCase):
    def test_context_goes_through_identity_permission_and_namespace_pipeline(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = FakeBackend(hits=["ctx-hit"])
            dispatcher = make_dispatcher(backend, journal_dir=directory)
            result = dispatcher.context(
                cn="hermes-gateway", bearer="token-hg", role="jarvis",
                namespace="/projects/lector-ine",
            )
        self.assertEqual(1, len(result["items"]))
        self.assertEqual("ctx-hit", result["items"][0]["content"])

    def test_context_denied_by_permissions_raises_dispatch_error(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = FakeBackend()
            dispatcher = make_dispatcher(backend, journal_dir=directory)
            with self.assertRaises(DispatchError) as ctx:
                dispatcher.context(
                    cn="codex", bearer="token-codex", role="coder",
                    namespace="/agents/hermes-gateway",
                )
        self.assertEqual(403, ctx.exception.status)

    def test_context_unauthenticated_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = FakeBackend()
            dispatcher = make_dispatcher(backend, journal_dir=directory)
            with self.assertRaises(DispatchError) as ctx:
                dispatcher.context(
                    cn=None, bearer=None, role="coder", namespace="/global",
                )
        self.assertEqual(401, ctx.exception.status)


if __name__ == "__main__":
    unittest.main()
