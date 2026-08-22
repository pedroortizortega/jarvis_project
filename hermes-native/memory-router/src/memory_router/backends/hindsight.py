import functools
import json
import os
import re
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
    StoreRequest,
    StoreResult,
)

# Single revisable wire-format surface (design.md "Interfaces"). Verified
# 2026-08-22 against ghcr.io/vectorize-io/hindsight's real API reference
# and confirmed live against a real `hindsight-api` container. The old
# paths/payload shape were a plausible-looking guess, not the real API —
# real Hindsight is a batch-oriented, tenant-scoped API ("default" tenant
# segment, "memories" not "retain"/"recall" as path segments, PUT not
# POST for bank creation, items wrapped in a list even for a single
# memory). ENDPOINTS["health"] was the only entry that was already
# correct.
ENDPOINTS = {
    "retain": "/v1/default/banks/{bank_id}/memories",
    "recall": "/v1/default/banks/{bank_id}/memories/recall",
    "create": "/v1/default/banks/{bank_id}",
    "health": "/health",
}

_BANK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def _env_default(explicit, env_key: str, fallback: str) -> str:
    """Resolve a config value: explicit constructor arg wins, else env var,
    else fallback. Mirrors engram.py's inline pattern as a pure helper."""
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
    """Minimal JSON-over-HTTP client for the Hindsight wire format. The
    `transport(method, url, headers, body) -> (status, bytes)` seam is
    exactly `spawn` for Engram: swap it in tests, never touch the network
    for real."""

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
            raise BackendUnavailableError("hindsight", str(exc)) from exc
        return status, raw


class HindsightBackend:
    """Second Memory Router adapter, reaching Hindsight over HTTP (not the
    stdio-subprocess transport used by the Engram adapter). See
    design.md for the full interface/data-flow contract."""

    def __init__(
        self,
        *,
        transport=None,
        base_url: str | None = None,
        auth_mode: str | None = None,
        token: str | None = None,
        bank_prefix: str | None = None,
        timeout: int | None = None,
    ):
        self._transport = transport
        self._base_url = _env_default(
            base_url,
            "HINDSIGHT_BASE_URL",
            "http://hindsight.mcps.svc.cluster.local:8080",
        )
        self._token = _env_default(token, "HINDSIGHT_TOKEN", "")

        if auth_mode is not None:
            self._auth_mode = auth_mode
        else:
            env_auth_mode = os.environ.get("HINDSIGHT_AUTH_MODE")
            if env_auth_mode is not None:
                self._auth_mode = env_auth_mode
            else:
                self._auth_mode = "bearer" if self._token else "none"

        self._bank_prefix = _env_default(bank_prefix, "HINDSIGHT_BANK_PREFIX", "")

        if timeout is not None:
            self._timeout = timeout
        else:
            self._timeout = int(os.environ.get("HINDSIGHT_TIMEOUT_SECONDS", "10"))

    def capabilities(self) -> Capabilities:
        return Capabilities(
            name="hindsight",
            verbs=frozenset({"store", "search"}),
            namespaces=("/projects/*",),
            hierarchical_search=True,
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
            raise BackendUnavailableError("hindsight", f"malformed response: {exc}") from exc

    def _create_bank(self, client: _HttpJsonClient, bank_id: str) -> None:
        # PUT /v1/default/banks/{bank_id}, not POST /v1/banks with bank_id
        # in the body — the real endpoint is "create or update", scoped by
        # the URL path, and takes agent-config fields (mission/disposition/
        # ...), not a bank_id field (verified live, see ENDPOINTS comment).
        path = ENDPOINTS["create"].format(bank_id=bank_id)
        status, raw = client.request("PUT", path, {"mission": f"memory-router bank for {bank_id}"})
        if status < 200 or status >= 300:
            reason = self._decode_error_reason(raw)
            raise BackendUnavailableError(
                "hindsight", f"bank create failed with status {status}: {reason}"
            )

    @staticmethod
    def _decode_error_reason(raw: bytes) -> str:
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return raw.decode("utf-8", errors="replace") if raw else ""
        return str(payload.get("error", payload))

    def _bank_id(self, namespace: str) -> str:
        # Flatten a validated namespace into a Hindsight bank id: strip the
        # leading "/", replace remaining "/" with "-", lowercase, prepend
        # the optional configured prefix. Re-validated against the bank id
        # charset — fail closed on anything that would not survive as a
        # single legal path segment (namespaces.py already rejects
        # traversal/wildcards upstream; this is defense in depth).
        flattened = namespace.lstrip("/").replace("/", "-").lower()
        candidate = f"{self._bank_prefix.lower()}{flattened}"
        if not _BANK_ID_RE.match(candidate):
            raise ValueError(f"namespace does not yield a legal bank id: {namespace!r}")
        return candidate

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

    def store(self, req: StoreRequest) -> StoreResult:
        client = self._client()
        bank_id = self._bank_id(req.namespace)
        path = ENDPOINTS["retain"].format(bank_id=bank_id)
        # Real retain is batch-shaped — a single memory is a one-item list,
        # never a bare {"content": ...} body (verified live).
        payload = {"items": [{"content": req.content, "metadata": req.metadata}]}

        status, raw = client.request("POST", path, payload)
        if status == 404:
            self._create_bank(client, bank_id)
            status, raw = client.request("POST", path, payload)

        if status < 200 or status >= 300:
            raise BackendUnavailableError(
                "hindsight",
                f"retain failed with status {status}: {self._decode_error_reason(raw)}",
            )

        result = self._decode(raw)
        # Real synchronous retain never returns a per-item id — only
        # {"success", "bank_id", "items_count", "async", "usage"}
        # (verified live). `id` stays "" here; StoreResult.id is `str`,
        # not Optional[str], so an empty string is the honest value, not
        # a bug to paper over with a fabricated id.
        return StoreResult(
            status="committed", backend="hindsight", id=str(result.get("id", ""))
        )

    def search(self, req: SearchRequest) -> SearchResult:
        client = self._client()
        bank_id = self._bank_id(req.namespace)
        path = ENDPOINTS["recall"].format(bank_id=bank_id)
        payload = {"query": req.query}

        status, raw = client.request("POST", path, payload)
        if status < 200 or status >= 300:
            raise BackendUnavailableError(
                "hindsight",
                f"recall failed with status {status}: {self._decode_error_reason(raw)}",
            )

        result = self._decode(raw)
        hits = tuple(
            SearchHit(
                namespace=req.namespace,
                backend="hindsight",
                # Real recall response: `results[].text`, not `.content`;
                # `results[].scores.final`, not a top-level `.score`
                # (verified live).
                content=item.get("text", ""),
                score=float((item.get("scores") or {}).get("final", 0.0)),
            )
            for item in result.get("results", [])
        )
        return SearchResult(hits=hits)
