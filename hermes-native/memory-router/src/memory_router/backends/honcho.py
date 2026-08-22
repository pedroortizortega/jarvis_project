import functools
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

# Single revisable wire-format surface (design.md "Interfaces"). Verified
# 2026-08-21 against plastic-labs/honcho's server source (src/main.py's
# router prefix, src/routers/peers.py's chat route, src/schemas/api.py's
# DialecticOptions/DialecticResponse) and confirmed live against a real
# `honcho` server. `query` matched what this adapter already sent; the
# prefix and health path were wrong. Also confirmed: `DialecticResponse`
# has no `confidence` field at all — `result.get("confidence", 0.0)` below
# always silently defaults, Honcho never sends it, not a bug but worth
# knowing.
ENDPOINTS = {
    "dialectic": "/v3/workspaces/{workspace_id}/peers/{peer_id}/chat",
    "health": "/health",
}

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def _env_default(explicit, env_key: str, fallback: str) -> str:
    """Resolve a config value: explicit constructor arg wins, else env var,
    else fallback. Mirrors hindsight.py's pattern."""
    if explicit is not None:
        return explicit
    return os.environ.get(env_key, fallback)


def _default_transport(
    method: str, url: str, headers: dict, body: bytes | None, *, timeout: int = 10
):
    # `timeout` is keyword-only with a default so the 4-positional-arg
    # `transport(method, url, headers, body)` seam stays exactly what tests
    # stub — only the real default transport needs the configured timeout,
    # bound via functools.partial where it's constructed below. Previously
    # this hardcoded 10 regardless of the configured `timeout`/`*_TIMEOUT_SECONDS`
    # env var, silently ignoring it (found validating against a live
    # backend that legitimately took longer than 10s to respond).
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


class _HttpJsonClient:
    """Minimal JSON-over-HTTP client for the Honcho Dialectic wire format.
    The `transport(method, url, headers, body) -> (status, bytes)` seam is
    exactly `hindsight.py`'s: swap it in tests, never touch the network for
    real."""

    def __init__(self, *, transport=None, base_url: str, timeout: int, headers: dict | None = None):
        self._transport = transport or functools.partial(_default_transport, timeout=timeout)
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
            raise BackendUnavailableError("honcho", str(exc)) from exc
        return status, raw


class HonchoBackend:
    """Reflect-only adapter reaching Honcho's Dialectic API over HTTP.
    Implements `ReflectiveBackend`, NOT `MemoryBackend` — it declares no
    `store`/`search`. See design.md for the full interface/data-flow
    contract."""

    def __init__(
        self,
        *,
        transport=None,
        base_url: str | None = None,
        auth_mode: str | None = None,
        token: str | None = None,
        workspace_id: str | None = None,
        timeout: int | None = None,
    ):
        self._transport = transport
        self._base_url = _env_default(
            base_url,
            "HONCHO_BASE_URL",
            "http://honcho.mcps.svc.cluster.local:8000",
        )
        self._token = _env_default(token, "HONCHO_TOKEN", "")

        if auth_mode is not None:
            self._auth_mode = auth_mode
        else:
            env_auth_mode = os.environ.get("HONCHO_AUTH_MODE")
            if env_auth_mode is not None:
                self._auth_mode = env_auth_mode
            else:
                self._auth_mode = "bearer" if self._token else "none"

        self._workspace_id = _env_default(workspace_id, "HONCHO_WORKSPACE_ID", "jarvis")

        if timeout is not None:
            self._timeout = timeout
        else:
            self._timeout = int(os.environ.get("HONCHO_TIMEOUT_SECONDS", "10"))

    def capabilities(self) -> Capabilities:
        return Capabilities(
            name="honcho",
            verbs=frozenset({"reflect"}),
            namespaces=("/user/master",),
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
            raise BackendUnavailableError("honcho", f"malformed response: {exc}") from exc

    @staticmethod
    def _decode_error_reason(raw: bytes) -> str:
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return raw.decode("utf-8", errors="replace") if raw else ""
        return str(payload.get("error", payload))

    def _peer_ref(self, namespace: str) -> tuple[str, str]:
        # Maps the single supported namespace ("/user/master") to a
        # (workspace_id, peer_id) pair. Re-validated against the id
        # charset — fail closed on anything that would not survive as a
        # single legal path segment (namespaces.py already rejects
        # traversal/wildcards upstream; this is defense in depth), mirrors
        # hindsight.py's `_bank_id`.
        flattened = namespace.lstrip("/").replace("/", "-").lower()
        if not flattened.startswith("user-"):
            raise ValueError(f"namespace does not yield a legal peer ref: {namespace!r}")
        peer_id = flattened[len("user-"):]
        workspace_id = self._workspace_id.lower()
        if not _ID_RE.match(workspace_id) or not _ID_RE.match(peer_id):
            raise ValueError(f"namespace does not yield a legal peer ref: {namespace!r}")
        return workspace_id, peer_id

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
        client = self._client()
        workspace_id, peer_id = self._peer_ref(req.namespace)
        path = ENDPOINTS["dialectic"].format(workspace_id=workspace_id, peer_id=peer_id)
        payload = {"query": req.query}

        status, raw = client.request("POST", path, payload)
        if status < 200 or status >= 300:
            raise BackendUnavailableError(
                "honcho",
                f"dialectic query failed with status {status}: {self._decode_error_reason(raw)}",
            )

        if status == 202 or not raw:
            return ReflectResult(status="pending", backend="honcho")

        result = self._decode(raw)
        content = result.get("content")
        if not content:
            return ReflectResult(status="pending", backend="honcho")

        conclusion = Conclusion(
            namespace=req.namespace,
            backend="honcho",
            content=content,
            confidence=float(result.get("confidence", 0.0)),
        )
        return ReflectResult(status="ready", backend="honcho", conclusions=(conclusion,))
