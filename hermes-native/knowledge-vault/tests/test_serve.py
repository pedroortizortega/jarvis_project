import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from knowledge_vault import serve
from knowledge_vault.layout import knowledge_root

NOTE = (
    "---\ntype: infra-fact\nid: 1\ntitle: Longhorn no esta instalado\n---\n"
    "# Longhorn no esta instalado\n\nEl unico storage class es local-path.\n"
)


def vault_with(root, notes):
    vault = Path(root) / "vault"
    knowledge_root(vault).mkdir(parents=True, exist_ok=True)
    for name, text in notes.items():
        (knowledge_root(vault) / name).write_text(text, encoding="utf-8")
    return vault


class ServeTestCase(unittest.TestCase):
    """Spins up a real serve.py handler in-process, over a temp vault."""

    token = "s3cr3t"

    def _start(self, *, vault_dir=None, index_path=None, timeout_seconds=5,
               limit_max=20, token="s3cr3t", searcher=None):
        if vault_dir is None or index_path is None:
            self._tempdir = tempfile.TemporaryDirectory()
            root = self._tempdir.name
            if vault_dir is None:
                vault_dir = vault_with(root, {"note.md": NOTE})
            if index_path is None:
                index_path = Path(root) / "index.json"
        if searcher is None:
            searcher = serve.SingleFlightSearcher(
                str(vault_dir), str(index_path), timeout_seconds=timeout_seconds
            )
        handler_class = serve.make_handler(searcher, token, limit_max)
        self.server = serve.build_http_server(handler_class, host="127.0.0.1", port=0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.vault_dir = Path(vault_dir)
        self.index_path = Path(index_path)
        return searcher

    def tearDown(self):
        if hasattr(self, "server"):
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=5)
        if hasattr(self, "_tempdir"):
            self._tempdir.cleanup()

    def _post(self, path, body=None, token=None, raw_body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port)
        hdrs = dict(headers or {})
        if token is not None:
            hdrs["Authorization"] = f"Bearer {token}"
        data = raw_body if raw_body is not None else (
            json.dumps(body).encode("utf-8") if body is not None else b""
        )
        connection.request("POST", path, body=data, headers=hdrs)
        response = connection.getresponse()
        raw = response.read()
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except json.JSONDecodeError:
            payload = None
        connection.close()
        return response.status, payload

    def _get(self, path):
        connection = http.client.HTTPConnection("127.0.0.1", self.port)
        connection.request("GET", path)
        response = connection.getresponse()
        raw = response.read()
        payload = json.loads(raw.decode("utf-8")) if raw else {}
        connection.close()
        return response.status, payload

    def _other_method(self, method, path):
        connection = http.client.HTTPConnection("127.0.0.1", self.port)
        connection.request(method, path, body=b"{}")
        response = connection.getresponse()
        response.read()
        connection.close()
        return response.status


class AuthTests(ServeTestCase):
    def setUp(self):
        self.calls = []
        self._original = serve.search_vault

        def spy(*args, **kwargs):
            self.calls.append(args)
            return self._original(*args, **kwargs)

        serve.search_vault = spy
        self._start()

    def tearDown(self):
        serve.search_vault = self._original
        super().tearDown()

    def test_missing_authorization_header_is_rejected(self):
        status, payload = self._post("/search", {"query": "storage", "limit": 5})
        self.assertEqual(401, status)
        self.assertEqual([], self.calls)

    def test_wrong_token_is_rejected(self):
        status, payload = self._post(
            "/search", {"query": "storage", "limit": 5}, token="wrong"
        )
        self.assertEqual(401, status)
        self.assertEqual([], self.calls)

    def test_empty_bearer_token_is_rejected(self):
        status, payload = self._post("/search", {"query": "storage", "limit": 5}, token="")
        self.assertEqual(401, status)
        self.assertEqual([], self.calls)

    def test_valid_token_is_accepted(self):
        status, payload = self._post(
            "/search", {"query": "storage", "limit": 5}, token=self.token
        )
        self.assertEqual(200, status)
        self.assertEqual(1, len(self.calls))


class RouteTests(ServeTestCase):
    def setUp(self):
        self._start()

    def test_get_search_is_not_routable(self):
        status = self._other_method("GET", "/search")
        self.assertIn(status, (404, 405, 501))

    def test_put_delete_patch_on_search_are_rejected(self):
        for method in ("PUT", "DELETE", "PATCH"):
            status = self._other_method(method, "/search")
            self.assertIn(status, (404, 405, 501))

    def test_post_to_unknown_path_is_404(self):
        status, _ = self._post("/publish", {"query": "x"}, token=self.token)
        self.assertEqual(404, status)

    def test_post_root_is_404(self):
        status, _ = self._post("/", {"query": "x"}, token=self.token)
        self.assertEqual(404, status)

    def test_handler_defines_no_mutating_methods(self):
        handler_class = serve.make_handler(
            serve.SingleFlightSearcher("dummy", "dummy"), self.token, 20
        )
        self.assertNotIn("do_PUT", dir(handler_class))
        self.assertNotIn("do_DELETE", dir(handler_class))
        self.assertNotIn("do_PATCH", dir(handler_class))


class ResponseShapeTests(ServeTestCase):
    def setUp(self):
        self._start()

    def test_search_returns_non_zero_score_for_a_matching_note(self):
        status, payload = self._post(
            "/search", {"query": "storage class local-path", "limit": 5}, token=self.token
        )
        self.assertEqual(200, status)
        self.assertEqual(1, len(payload["hits"]))
        hit = payload["hits"][0]
        self.assertEqual("note.md", hit["note"])
        self.assertIn("title", hit)
        self.assertIn("excerpt", hit)
        self.assertNotEqual(0.0, hit["score"])


class EmptyResultTests(ServeTestCase):
    def test_empty_vault_returns_200_empty_hits(self):
        with tempfile.TemporaryDirectory() as root:
            vault = Path(root) / "vault"
            vault.mkdir()
            self._start(vault_dir=vault, index_path=Path(root) / "index.json")
            status, payload = self._post(
                "/search", {"query": "anything", "limit": 5}, token=self.token
            )
        self.assertEqual(200, status)
        self.assertEqual([], payload["hits"])

    def test_query_below_relevance_threshold_returns_200_empty_hits(self):
        self._start()
        status, payload = self._post(
            "/search",
            {"query": "completely unrelated martian topic", "limit": 5},
            token=self.token,
        )
        self.assertEqual(200, status)
        self.assertEqual([], payload["hits"])


class BoundedRebuildTests(unittest.TestCase):
    def setUp(self):
        self._original = serve.search_vault

    def tearDown(self):
        serve.search_vault = self._original

    def test_slow_rebuild_returns_503_within_deadline(self):
        calls = []

        def slow(*args, **kwargs):
            calls.append(1)
            time.sleep(0.5)
            return []

        serve.search_vault = slow
        searcher = serve.SingleFlightSearcher("v", "i", timeout_seconds=0.1)
        handler_class = serve.make_handler(searcher, "tok", 20)
        server = serve.build_http_server(handler_class, host="127.0.0.1", port=0)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", port)
            start = time.monotonic()
            connection.request(
                "POST",
                "/search",
                body=json.dumps({"query": "x", "limit": 5}).encode("utf-8"),
                headers={"Authorization": "Bearer tok"},
            )
            response = connection.getresponse()
            elapsed = time.monotonic() - start
            payload = json.loads(response.read().decode("utf-8"))
            connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        self.assertEqual(503, response.status)
        self.assertEqual("index_rebuild_timeout", payload["error"])
        self.assertLess(elapsed, 0.5)

    def test_concurrent_requests_trigger_only_one_rebuild(self):
        calls = []

        def slow(*args, **kwargs):
            calls.append(1)
            time.sleep(0.3)
            return []

        serve.search_vault = slow
        searcher = serve.SingleFlightSearcher("v", "i", timeout_seconds=5)
        handler_class = serve.make_handler(searcher, "tok", 20)
        server = serve.build_http_server(handler_class, host="127.0.0.1", port=0)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def call():
            connection = http.client.HTTPConnection("127.0.0.1", port)
            connection.request(
                "POST",
                "/search",
                body=json.dumps({"query": "same query", "limit": 5}).encode("utf-8"),
                headers={"Authorization": "Bearer tok"},
            )
            response = connection.getresponse()
            response.read()
            connection.close()

        try:
            workers = [threading.Thread(target=call) for _ in range(3)]
            for worker in workers:
                worker.start()
            time.sleep(0.05)
            for worker in workers:
                worker.join(timeout=5)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        self.assertEqual(1, len(calls))


class ReadOnlyTests(ServeTestCase):
    def test_mtimes_unchanged_after_a_successful_non_rebuild_search(self):
        self._start()
        status, _ = self._post(
            "/search", {"query": "storage", "limit": 5}, token=self.token
        )
        self.assertEqual(200, status)
        note_before = (knowledge_root(self.vault_dir) / "note.md").stat().st_mtime_ns
        index_before = self.index_path.stat().st_mtime_ns

        status, _ = self._post(
            "/search", {"query": "storage", "limit": 5}, token=self.token
        )
        self.assertEqual(200, status)
        note_after = (knowledge_root(self.vault_dir) / "note.md").stat().st_mtime_ns
        index_after = self.index_path.stat().st_mtime_ns

        self.assertEqual(note_before, note_after)
        self.assertEqual(index_before, index_after)


class IndexUnavailableTests(unittest.TestCase):
    """D-07: search_vault() raising IndexUnavailable maps to a 503, not a 500."""

    def setUp(self):
        self._original = serve.search_vault

    def tearDown(self):
        serve.search_vault = self._original

    def test_index_unavailable_maps_to_503(self):
        def unavailable(*args, **kwargs):
            raise serve.IndexUnavailable("index is unavailable")

        serve.search_vault = unavailable
        searcher = serve.SingleFlightSearcher("v", "i", timeout_seconds=5)
        handler_class = serve.make_handler(searcher, "tok", 20)
        server = serve.build_http_server(handler_class, host="127.0.0.1", port=0)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", port)
            connection.request(
                "POST",
                "/search",
                body=json.dumps({"query": "x", "limit": 5}).encode("utf-8"),
                headers={"Authorization": "Bearer tok"},
            )
            response = connection.getresponse()
            payload = json.loads(response.read().decode("utf-8"))
            connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        self.assertEqual(503, response.status)
        self.assertEqual("index_unavailable", payload["error"])


class SecretHandlingTests(ServeTestCase):
    def setUp(self):
        self._start(token="super-secret-token")

    def test_token_never_appears_in_any_response_body(self):
        status, payload = self._post(
            "/search", {"query": "storage", "limit": 5}, token="wrong-token"
        )
        self.assertEqual(401, status)
        self.assertNotIn("super-secret-token", json.dumps(payload))


if __name__ == "__main__":
    unittest.main()
