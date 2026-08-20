import json
import os
import re
import urllib.error
import urllib.request

from ..contracts import (
    BackendUnavailableError,
    Capabilities,
    Conclusion,
    Health,
    HealthStatus,
    ReflectRequest,
    ReflectResult,
)

# Single revisable wire-format surface (design.md "Interfaces"). Unverified
# against a live Cognee instance or authoritative /recall docs — see
# design.md Open Questions.
ENDPOINTS = {
    "recall": "/recall",     # POST {query, search_type, datasets:[id]} -> {result|answer: str}
    "health": "/healthz",
}
SEARCH_TYPE = "GRAPH_COMPLETION"   # graph-synthesized answer, not CHUNKS retrieval

_DATASET_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def _env_default(explicit, env_key: str, fallback: str) -> str:
    """Resolve a config value: explicit constructor arg wins, else env var,
    else fallback. Mirrors honcho.py's pattern."""
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
    """Minimal JSON-over-HTTP client for the Cognee `/recall` wire format.
    The `transport(method, url, headers, body) -> (status, bytes)` seam is
    exactly `honcho.py`'s: swap it in tests, never touch the network for
    real."""

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
            raise BackendUnavailableError("cognee", str(exc)) from exc
        return status, raw


class CogneeBackend:
    """Reflect-only adapter reaching Cognee's `/recall` (GRAPH_COMPLETION)
    endpoint over HTTP. Implements `ReflectiveBackend`, NOT `MemoryBackend`
    — it declares no `store`/`search`. See design.md for the full
    interface/data-flow contract."""

    def __init__(
        self,
        *,
        transport=None,
        base_url: str | None = None,
        auth_mode: str | None = None,
        token: str | None = None,
        dataset_prefix: str | None = None,
        timeout: int | None = None,
    ):
        self._transport = transport
        self._base_url = _env_default(
            base_url,
            "COGNEE_BASE_URL",
            "http://cognee.mcps.svc.cluster.local:8000",
        )
        self._token = _env_default(token, "COGNEE_TOKEN", "")

        if auth_mode is not None:
            self._auth_mode = auth_mode
        else:
            env_auth_mode = os.environ.get("COGNEE_AUTH_MODE")
            if env_auth_mode is not None:
                self._auth_mode = env_auth_mode
            else:
                self._auth_mode = "bearer" if self._token else "none"

        self._dataset_prefix = _env_default(dataset_prefix, "COGNEE_DATASET_PREFIX", "jarvis-")

        if timeout is not None:
            self._timeout = timeout
        else:
            self._timeout = int(os.environ.get("COGNEE_TIMEOUT_SECONDS", "10"))

    def capabilities(self) -> Capabilities:
        return Capabilities(
            name="cognee",
            verbs=frozenset({"reflect"}),
            namespaces=("/projects/*",),
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
            raise BackendUnavailableError("cognee", f"malformed response: {exc}") from exc

    @staticmethod
    def _decode_error_reason(raw: bytes) -> str:
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return raw.decode("utf-8", errors="replace") if raw else ""
        return str(payload.get("error", payload))

    def _dataset_id(self, namespace: str) -> str:
        # Fail closed. namespaces.py already rejects traversal, wildcards and
        # embedded "/" (design F-1); this is defense in depth, mirroring
        # honcho.py's _peer_ref and hindsight.py's _bank_id.
        prefix = "/projects/"
        if not namespace.startswith(prefix):
            raise BackendUnavailableError("cognee", "namespace is not a project namespace")
        project = namespace[len(prefix):]
        if not project or "/" in project or ".." in project or "*" in project or "?" in project:
            raise BackendUnavailableError("cognee", "namespace does not yield a legal dataset id")
        dataset = f"{self._dataset_prefix}{project}"      # NO case-folding, NO substitution (D-03)
        if not _DATASET_RE.match(dataset):
            raise BackendUnavailableError("cognee", "namespace does not yield a legal dataset id")
        return dataset

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

    def reflect(self, req: ReflectRequest) -> ReflectResult:
        dataset = self._dataset_id(req.namespace)
        client = self._client()
        payload = {
            "query": req.query,
            "search_type": SEARCH_TYPE,
            "datasets": [dataset],
        }

        status, raw = client.request("POST", ENDPOINTS["recall"], payload)
        if status < 200 or status >= 300:
            raise BackendUnavailableError(
                "cognee",
                f"recall query failed with status {status}: {self._decode_error_reason(raw)}",
            )

        result = self._decode(raw)
        content = result.get("result") or result.get("answer")
        if not content or not str(content).strip():
            return ReflectResult(status="empty", backend="cognee")

        conclusion = Conclusion(
            namespace=req.namespace,
            backend="cognee",
            content=content,
            confidence=0.0,
        )
        return ReflectResult(status="ready", backend="cognee", conclusions=(conclusion,))
