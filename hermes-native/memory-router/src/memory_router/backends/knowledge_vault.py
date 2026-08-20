import json
import os
import urllib.error
import urllib.request

from ..contracts import (
    BackendUnavailableError,
    Capabilities,
    Health,
    HealthStatus,
    SearchHit,
    SearchRequest,
    SearchResult,
)

# Single revisable wire-format surface (design.md "Interfaces"), matching the
# knowledge-vault-search-bridge spec's request/response shape.
ENDPOINTS = {"search": "/search", "health": "/healthz"}
NAMESPACE = "/global"


def _env_default(explicit, env_key: str, fallback: str) -> str:
    """Resolve a config value: explicit constructor arg wins, else env var,
    else fallback. Mirrors honcho.py's/cognee.py's pattern."""
    if explicit is not None:
        return explicit
    return os.environ.get(env_key, fallback)


def _default_transport(method: str, url: str, headers: dict, body: bytes | None):
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


class _HttpJsonClient:
    """Minimal JSON-over-HTTP client for the knowledge-vault search bridge
    wire format. The `transport(method, url, headers, body) -> (status,
    bytes)` seam is exactly `honcho.py`'s/`cognee.py`'s: swap it in tests,
    never touch the network for real."""

    def __init__(self, *, transport=None, base_url: str, timeout: int, headers: dict | None = None):
        self._transport = transport or _default_transport
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._headers = dict(headers or {})

    def request(self, method: str, path: str, payload: dict | None = None):
        url = f"{self._base_url}{path}"
        headers = dict(self._headers)
        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        try:
            status, raw = self._transport(method, url, headers, body)
        except (OSError, urllib.error.URLError) as exc:
            raise BackendUnavailableError("knowledge-vault", str(exc)) from exc
        return status, raw


class KnowledgeVaultBackend:
    """Search-only adapter reaching the knowledge-vault search bridge
    (`knowledge_vault/serve.py` on the host) over HTTP. Implements
    `SearchOnlyBackend`, NOT `MemoryBackend` — it declares no `store`/
    `reflect`. See design.md for the full interface/data-flow contract."""

    def __init__(
        self,
        *,
        transport=None,
        base_url: str | None = None,
        auth_mode: str | None = None,
        token: str | None = None,
        limit: int | None = None,
        timeout: int | None = None,
    ):
        self._transport = transport
        self._base_url = _env_default(
            base_url,
            "KNOWLEDGE_VAULT_BASE_URL",
            "http://knowledge-vault-search.mcps.svc.cluster.local:8088",
        )
        self._token = _env_default(token, "KNOWLEDGE_VAULT_TOKEN", "")

        if auth_mode is not None:
            self._auth_mode = auth_mode
        else:
            env_auth_mode = os.environ.get("KNOWLEDGE_VAULT_AUTH_MODE")
            if env_auth_mode is not None:
                self._auth_mode = env_auth_mode
            else:
                self._auth_mode = "bearer" if self._token else "none"

        if limit is not None:
            self._limit = limit
        else:
            self._limit = int(os.environ.get("KNOWLEDGE_VAULT_LIMIT", "5"))

        if timeout is not None:
            self._timeout = timeout
        else:
            self._timeout = int(os.environ.get("KNOWLEDGE_VAULT_TIMEOUT_SECONDS", "10"))

    def capabilities(self) -> Capabilities:
        return Capabilities(
            name="knowledge-vault",
            verbs=frozenset({"search"}),
            namespaces=("/global",),
            hierarchical_search=False,
        )

    def _client(self) -> _HttpJsonClient:
        headers = {}
        if self._auth_mode == "bearer" and self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return _HttpJsonClient(
            transport=self._transport,
            base_url=self._base_url,
            timeout=self._timeout,
            headers=headers,
        )

    @staticmethod
    def _decode(raw: bytes) -> dict:
        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError as exc:
            raise BackendUnavailableError(
                "knowledge-vault", f"malformed response: {exc}"
            ) from exc

    @staticmethod
    def _decode_error_reason(raw: bytes) -> str:
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return raw.decode("utf-8", errors="replace") if raw else ""
        return str(payload.get("error", payload))

    def health(self) -> Health:
        try:
            client = self._client()
            status, raw = client.request("GET", ENDPOINTS["health"])
        except BackendUnavailableError as exc:
            return Health(status=HealthStatus.DOWN, reason=exc.reason)
        if 200 <= status < 300:
            return Health(status=HealthStatus.OK)
        return Health(
            status=HealthStatus.DOWN,
            reason=f"status {status}: {self._decode_error_reason(raw)}",
        )

    def search(self, req: SearchRequest) -> SearchResult:
        # D-05: fail closed on anything other than exactly "/global" — zero
        # HTTP calls. Redundant with `capabilities().namespaces` under
        # `Registry`, but the adapter is also directly constructible in
        # tests and by future callers.
        if req.namespace != NAMESPACE:
            raise BackendUnavailableError(
                "knowledge-vault", "namespace is not the global namespace"
            )

        client = self._client()
        payload = {"query": req.query, "limit": self._limit}

        status, raw = client.request("POST", ENDPOINTS["search"], payload)
        if status < 200 or status >= 300:
            raise BackendUnavailableError(
                "knowledge-vault",
                f"search failed with status {status}: {self._decode_error_reason(raw)}",
            )

        result = self._decode(raw)
        hits = tuple(
            SearchHit(
                namespace="/global",
                backend="knowledge-vault",
                # D-04: embed the note id + title in content — the only
                # route the note id has to a caller since SearchHit has no
                # metadata field.
                content=f"{hit['note']} — {hit['title']}\n{hit['excerpt']}",
                score=float(hit.get("score", 0.0)),
            )
            for hit in result.get("hits", [])
        )
        return SearchResult(hits=hits)
