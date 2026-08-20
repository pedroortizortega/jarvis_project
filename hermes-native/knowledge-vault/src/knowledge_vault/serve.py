"""Read-only HTTP search bridge over the published vault.

Exposes exactly `POST /search` and `GET /healthz`. Nothing here writes: the
publisher (`publisher.py`) remains the sole writer to the vault and its
index. This module reuses `search_vault()` verbatim rather than calling
`Retriever` directly, so the `MIN_RELEVANCE` filter, per-note dedupe,
frontmatter title lookup, excerpt truncation, and stale-index retry never
drift from the CLI path (design.md D-01).
"""

import hmac
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .search import search_vault

DEFAULT_HOST = "10.42.0.1"
DEFAULT_PORT = 8088
DEFAULT_TIMEOUT_SECONDS = 5
DEFAULT_LIMIT_MAX = 20


def _env_default(name, default, cast=str):
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return cast(value)


def _read_token():
    """Read the bearer token from a systemd credential.

    Never an env var, never a repo file — `$CREDENTIALS_DIRECTORY` is set by
    `LoadCredential=` at unit start. Missing/unreadable credential means no
    request can ever authenticate (fails closed, not fails open).
    """
    directory = os.environ.get("CREDENTIALS_DIRECTORY")
    if not directory:
        return ""
    path = os.path.join(directory, "search-token")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return ""


class SingleFlightSearcher:
    """Runs `search_vault()` in a worker thread, bounded by a deadline.

    D-02/D-03: `future.result(timeout=...)` bounds how long a caller waits,
    but the submitted call is never cancelled — it keeps running in the
    background so the index converges and the *next* request is fast. The
    lock coalesces concurrent identical requests (same query+limit) onto one
    in-flight future instead of starting a second rebuild for each of N
    concurrent callers.
    """

    def __init__(self, vault_directory, index_path, *, timeout_seconds=DEFAULT_TIMEOUT_SECONDS):
        self._vault_directory = vault_directory
        self._index_path = index_path
        self._timeout_seconds = timeout_seconds
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._lock = threading.Lock()
        self._inflight_key = None
        self._inflight_future = None

    def search(self, query, limit):
        key = (query, limit)
        with self._lock:
            future = self._inflight_future
            if future is None or future.done() or self._inflight_key != key:
                future = self._executor.submit(
                    search_vault, query, self._vault_directory, self._index_path, limit
                )
                self._inflight_key = key
                self._inflight_future = future
        try:
            return future.result(timeout=self._timeout_seconds)
        except FutureTimeoutError:
            raise TimeoutError("index rebuild exceeded the deadline") from None


def make_handler(searcher, token, limit_max):
    def _authenticated(handler):
        if not token:
            return False
        auth = handler.headers.get("Authorization", "")
        provided = auth[len("Bearer "):] if auth.startswith("Bearer ") else ""
        if not provided:
            return False
        return hmac.compare_digest(provided, token)

    def _valid_body(body):
        query = body.get("query")
        limit = body.get("limit", 5)
        if not isinstance(query, str) or not query.strip():
            return None
        if isinstance(limit, bool) or not isinstance(limit, int):
            return None
        return query, limit

    class VaultSearchHandler(BaseHTTPRequestHandler):
        server_version = "knowledge-vault-search/0.1"

        def log_message(self, format, *args):  # noqa: A002 - stdlib signature
            pass  # systemd/journald capture stdout separately; keep test output quiet

        def _respond(self, status, payload):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):  # noqa: N802 - stdlib method name
            if self.path != "/search":
                self._respond(404, {"error": "not_found"})
                return
            if not _authenticated(self):
                self._respond(401, {"error": "unauthenticated"})
                return
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._respond(400, {"error": "invalid_body"})
                return
            if not isinstance(body, dict):
                self._respond(400, {"error": "invalid_body"})
                return
            parsed = _valid_body(body)
            if parsed is None:
                self._respond(400, {"error": "invalid_body"})
                return
            query, limit = parsed
            limit = max(1, min(limit, limit_max))
            try:
                hits = searcher.search(query, limit)
            except TimeoutError:
                self._respond(503, {"error": "index_rebuild_timeout"})
                return
            self._respond(
                200,
                {
                    "hits": [
                        {
                            "note": hit.note,
                            "title": hit.title,
                            "excerpt": hit.excerpt,
                            "score": hit.score,
                        }
                        for hit in hits
                    ]
                },
            )

        def do_GET(self):  # noqa: N802 - stdlib method name
            if self.path == "/healthz":
                # Unauthenticated liveness probe; touches no vault file.
                self._respond(200, {"status": "ok"})
                return
            self._respond(404, {"error": "not_found"})

    return VaultSearchHandler


def build_http_server(handler_class, *, host=DEFAULT_HOST, port=DEFAULT_PORT):
    return ThreadingHTTPServer((host, port), handler_class)


def build_default_handler():
    vault_directory = os.environ["KNOWLEDGE_VAULT_DIR"]
    index_path = os.environ["KNOWLEDGE_VAULT_INDEX"]
    timeout_seconds = _env_default(
        "KNOWLEDGE_VAULT_SEARCH_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS, int
    )
    limit_max = _env_default("KNOWLEDGE_VAULT_SEARCH_LIMIT_MAX", DEFAULT_LIMIT_MAX, int)
    token = _read_token()
    searcher = SingleFlightSearcher(vault_directory, index_path, timeout_seconds=timeout_seconds)
    return make_handler(searcher, token, limit_max)


def main():
    host = _env_default("KNOWLEDGE_VAULT_SEARCH_HOST", DEFAULT_HOST)
    port = _env_default("KNOWLEDGE_VAULT_SEARCH_PORT", DEFAULT_PORT, int)
    handler_class = build_default_handler()
    server = build_http_server(handler_class, host=host, port=port)
    server.serve_forever()


if __name__ == "__main__":
    main()
