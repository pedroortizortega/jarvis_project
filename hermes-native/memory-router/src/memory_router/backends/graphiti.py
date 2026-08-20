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
# against a live Graphiti instance or authoritative search_facts docs — see
# design.md Open Questions.
ENDPOINTS = {
    "search_facts": "/search/facts",   # POST {query, group_ids:[id], max_facts} -> {facts: [...]}
    "health": "/healthz",
}
MAX_FACTS = 10

_GROUP_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def _env_default(explicit, env_key: str, fallback: str) -> str:
    """Resolve a config value: explicit constructor arg wins, else env var,
    else fallback. Mirrors cognee.py's pattern."""
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
    """Minimal JSON-over-HTTP client for the Graphiti `/search/facts` wire
    format. The `transport(method, url, headers, body) -> (status, bytes)`
    seam is exactly `cognee.py`'s: swap it in tests, never touch the
    network for real."""

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
            raise BackendUnavailableError("graphiti", str(exc)) from exc
        return status, raw


class GraphitiBackend:
    """Reflect-only adapter reaching Graphiti's `/search/facts` endpoint
    over HTTP. Implements `ReflectiveBackend`, NOT `MemoryBackend` — it
    declares no `store`/`search`. See design.md for the full
    interface/data-flow contract."""

    def __init__(
        self,
        *,
        transport=None,
        base_url: str | None = None,
        auth_mode: str | None = None,
        token: str | None = None,
        group_prefix: str | None = None,
        timeout: int | None = None,
    ):
        self._transport = transport
        self._base_url = _env_default(
            base_url,
            "GRAPHITI_BASE_URL",
            "http://graphiti.mcps.svc.cluster.local:8000",
        )
        self._token = _env_default(token, "GRAPHITI_TOKEN", "")

        if auth_mode is not None:
            self._auth_mode = auth_mode
        else:
            env_auth_mode = os.environ.get("GRAPHITI_AUTH_MODE")
            if env_auth_mode is not None:
                self._auth_mode = env_auth_mode
            else:
                self._auth_mode = "bearer" if self._token else "none"

        self._group_prefix = _env_default(group_prefix, "GRAPHITI_GROUP_PREFIX", "jarvis-")

        if timeout is not None:
            self._timeout = timeout
        else:
            self._timeout = int(os.environ.get("GRAPHITI_TIMEOUT_SECONDS", "10"))

        self._max_facts = int(os.environ.get("GRAPHITI_MAX_FACTS", str(MAX_FACTS)))

    def capabilities(self) -> Capabilities:
        return Capabilities(
            name="graphiti",
            verbs=frozenset({"reflect"}),
            namespaces=("/global", "/agents/*"),
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
            raise BackendUnavailableError("graphiti", f"malformed response: {exc}") from exc

    @staticmethod
    def _decode_error_reason(raw: bytes) -> str:
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return raw.decode("utf-8", errors="replace") if raw else ""
        return str(payload.get("error", payload))

    def _group_id(self, namespace: str) -> str:
        # Fail closed. namespaces.py already rejects traversal, wildcards and
        # embedded "/" for the whole namespace shape; this is defense in
        # depth, mirroring cognee.py's _dataset_id.
        if namespace == "/global":
            suffix = "global"
        elif namespace.startswith("/agents/"):
            agent = namespace[len("/agents/"):]
            if not agent or "/" in agent or ".." in agent or "*" in agent or "?" in agent:
                raise BackendUnavailableError(
                    "graphiti", "namespace does not yield a legal group id"
                )
            suffix = f"agent-{agent}"          # infix keeps the mapping injective (D-02)
        else:
            raise BackendUnavailableError(
                "graphiti", "namespace is not reflect-capable for graphiti"
            )

        group = f"{self._group_prefix}{suffix}"   # NO case-folding, NO substitution (D-03)
        if not _GROUP_RE.match(group):
            raise BackendUnavailableError("graphiti", "namespace does not yield a legal group id")
        return group

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
        group = self._group_id(req.namespace)
        client = self._client()
        payload = {
            "query": req.query,
            "group_ids": [group],
            "max_facts": self._max_facts,
        }

        status, raw = client.request("POST", ENDPOINTS["search_facts"], payload)
        if status < 200 or status >= 300:
            raise BackendUnavailableError(
                "graphiti",
                f"search_facts query failed with status {status}: {self._decode_error_reason(raw)}",
            )

        result = self._decode(raw)
        facts = result.get("facts") or []
        live = [f for f in facts if not f.get("invalid_at")]
        conclusions = tuple(
            Conclusion(
                namespace=req.namespace,
                backend="graphiti",
                content=str(f.get("fact") or "").strip(),
                confidence=0.0,
            )
            for f in live
            if str(f.get("fact") or "").strip()
        )
        if not conclusions:
            return ReflectResult(status="empty", backend="graphiti")

        return ReflectResult(status="ready", backend="graphiti", conclusions=conclusions)
