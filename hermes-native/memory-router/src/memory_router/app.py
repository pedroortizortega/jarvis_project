import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .contracts import BackendUnavailableError, SearchRequest, StoreRequest
from .identity import IdentityError, resolve_identity
from .journal import Journal
from .namespaces import NamespaceError, validate_namespace
from .permissions import AuthorizationError, authorize
from .registry import Registry

# Traefik forwards the verified client-certificate CN via this header.
# TBD/confirm against the live `mcps` ingress config (design.md Open
# Questions) — kept as a single named constant so it is a one-line change.
CLIENT_CN_HEADER = "X-Forwarded-Client-Cert-Cn"


class DispatchError(Exception):
    """Carries an explicit HTTP-status-equivalent code and a distinct,
    non-generic reason. Never collapsed into a single generic failure."""

    def __init__(self, status: int, error: str, detail: str):
        self.status = status
        self.error = error
        self.detail = detail
        super().__init__(f"{status} {error}: {detail}")


class Dispatcher:
    """Transport-agnostic router core: identity -> permissions -> namespace
    -> registry -> backend/journal. Both the REST handler and the MCP stdio
    shim call the same normalization + this dispatcher, so both surfaces
    produce equivalent routing decisions (interfaces spec, MCP/REST parity).
    """

    def __init__(
        self,
        *,
        registry: Registry,
        journal: Journal,
        cn_to_identity: dict[str, str],
        bearer_by_identity: dict[str, str],
    ):
        self._registry = registry
        self._journal = journal
        self._cn_to_identity = cn_to_identity
        self._bearer_by_identity = bearer_by_identity

    def _authenticate(self, cn, bearer):
        try:
            return resolve_identity(
                cn,
                bearer,
                cn_to_identity=self._cn_to_identity,
                bearer_by_identity=self._bearer_by_identity,
            )
        except IdentityError as exc:
            raise DispatchError(401, "identity_rejected", str(exc)) from exc

    def _validate_namespace(self, namespace):
        try:
            return validate_namespace(namespace)
        except NamespaceError as exc:
            raise DispatchError(400, "invalid_namespace", str(exc)) from exc

    def _authorize(self, *, role, identity_name, namespace, verb):
        try:
            authorize(role=role, identity_name=identity_name, namespace=namespace, verb=verb)
        except AuthorizationError as exc:
            message = str(exc)
            if message.startswith("unknown role"):
                raise DispatchError(400, "invalid_role", message) from exc
            if "not permitted for client identity" in message:
                raise DispatchError(403, "role_not_permitted", message) from exc
            raise DispatchError(403, "authorization_denied", message) from exc

    def store(self, *, cn, bearer, role, namespace, content, metadata=None) -> dict:
        identity = self._authenticate(cn, bearer)
        namespace = self._validate_namespace(namespace)
        self._authorize(role=role, identity_name=identity.name, namespace=namespace, verb="store")

        req = StoreRequest(
            namespace=namespace, role=role, content=content, metadata=metadata or {}
        )
        backends = self._registry.backends_for(verb="store", namespace=namespace)

        if not backends:
            return self._queue(req)

        backend = backends[0]
        try:
            result = backend.store(req)
        except BackendUnavailableError:
            return self._queue(req)

        return {"status": result.status, "backend": result.backend, "id": result.id}

    def _queue(self, req: StoreRequest) -> dict:
        queue_id = self._journal.append(
            {
                "namespace": req.namespace,
                "role": req.role,
                "content": req.content,
                "metadata": req.metadata,
            }
        )
        return {"status": "pending", "queue_id": queue_id}

    def _fallback_chain(self, namespace: str, identity_name: str) -> list[str]:
        # Hierarchical search fallback: project -> agent -> global. A
        # namespace already at /agents/{n} falls back only to /global; the
        # store surface never uses this chain (store never falls back).
        if namespace.startswith("/projects/"):
            return [namespace, f"/agents/{identity_name}", "/global"]
        if namespace.startswith("/agents/"):
            return [namespace, "/global"]
        return [namespace]

    def search(self, *, cn, bearer, role, namespace, query) -> dict:
        identity = self._authenticate(cn, bearer)
        namespace = self._validate_namespace(namespace)
        self._authorize(role=role, identity_name=identity.name, namespace=namespace, verb="search")

        hits: list[dict] = []
        unavailable: list[dict] = []

        for candidate in self._fallback_chain(namespace, identity.name):
            if candidate != namespace:
                try:
                    self._authorize(
                        role=role, identity_name=identity.name, namespace=candidate, verb="search"
                    )
                except DispatchError:
                    continue

            req = SearchRequest(namespace=candidate, role=role, query=query)
            candidate_hits: list[dict] = []
            for backend in self._registry.backends_for(verb="search", namespace=candidate):
                try:
                    result = backend.search(req)
                except BackendUnavailableError as exc:
                    unavailable.append({"backend": exc.backend, "reason": exc.reason})
                    continue
                candidate_hits.extend(
                    {
                        "namespace": hit.namespace,
                        "backend": hit.backend,
                        "content": hit.content,
                        "score": hit.score,
                    }
                    for hit in result.hits
                )

            hits.extend(candidate_hits)
            if candidate_hits:
                break

        return {"hits": hits, "unavailable": unavailable}

    def context(self, *, cn, bearer, role, namespace) -> dict:
        """Read-oriented context summary for a single `/agents/{name}` or
        `/projects/{name}` namespace. Goes through the exact same
        identity -> permission -> namespace pipeline as store/search (never
        bypassed), authorizing the "search" verb since this is read-only.
        Unlike `search`, this does not walk the hierarchical fallback chain
        — it is scoped strictly to the requested namespace.
        """
        identity = self._authenticate(cn, bearer)
        namespace = self._validate_namespace(namespace)
        self._authorize(role=role, identity_name=identity.name, namespace=namespace, verb="search")

        req = SearchRequest(namespace=namespace, role=role, query="")
        items: list[dict] = []
        unavailable: list[dict] = []
        for backend in self._registry.backends_for(verb="search", namespace=namespace):
            try:
                result = backend.search(req)
            except BackendUnavailableError as exc:
                unavailable.append({"backend": exc.backend, "reason": exc.reason})
                continue
            items.extend(
                {
                    "namespace": hit.namespace,
                    "backend": hit.backend,
                    "content": hit.content,
                    "score": hit.score,
                }
                for hit in result.hits
            )

        return {"namespace": namespace, "items": items, "unavailable": unavailable}

    def reflect(self, *, cn, bearer, role=None, namespace=None, query=None) -> dict:
        # Decision 1: placeholder in Phase 1. No logic, no backend call.
        self._authenticate(cn, bearer)
        raise DispatchError(
            501, "not_implemented", "reflect is not implemented in Phase 1 (lands with Hindsight)"
        )


def _dispatch_error_payload(error: DispatchError) -> dict:
    payload = {"error": error.error, "detail": error.detail}
    if error.error == "not_implemented":
        payload["phase"] = "hindsight"
    return payload


# --------------------------------------------------------------------------
# REST surface
# --------------------------------------------------------------------------


def _parse_store_body(body: dict) -> dict:
    return {
        "role": body.get("role"),
        "namespace": body.get("namespace"),
        "content": body.get("content"),
        "metadata": body.get("metadata") or {},
    }


def _parse_search_body(body: dict) -> dict:
    return {
        "role": body.get("role"),
        "namespace": body.get("namespace"),
        "query": body.get("query", ""),
    }


# Matches GET /agents/{name}/context and GET /projects/{name}/context (the
# interfaces spec's dual MCP/REST surface). `{name}` is taken verbatim from
# the path segment and re-validated by `validate_namespace` downstream — the
# route match alone never authorizes anything.
_CONTEXT_PATH_RE = re.compile(r"^/(?P<kind>agents|projects)/(?P<name>[^/]+)/context$")


def make_handler(dispatcher: Dispatcher):
    class RouterRequestHandler(BaseHTTPRequestHandler):
        server_version = "memory-router/0.1"

        def log_message(self, format, *args):  # noqa: A002 - stdlib signature
            pass  # keep test/CI output quiet; Traefik/K8s capture stdout separately

        def _identity_headers(self):
            cn = self.headers.get(CLIENT_CN_HEADER)
            auth = self.headers.get("Authorization", "")
            bearer = auth[len("Bearer "):] if auth.startswith("Bearer ") else None
            return cn, bearer

        def _read_json_body(self) -> dict:
            length = int(self.headers.get("Content-Length", 0) or 0)
            if length == 0:
                return {}
            raw = self.rfile.read(length)
            return json.loads(raw.decode("utf-8")) if raw else {}

        def _respond(self, status: int, payload: dict):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _respond_error(self, error: DispatchError):
            self._respond(error.status, _dispatch_error_payload(error))

        def do_POST(self):  # noqa: N802 - stdlib method name
            cn, bearer = self._identity_headers()
            try:
                body = self._read_json_body()
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._respond(400, {"error": "invalid_body", "detail": "body must be JSON"})
                return

            try:
                if self.path == "/memory/store":
                    result = dispatcher.store(cn=cn, bearer=bearer, **_parse_store_body(body))
                    self._respond(202, result)
                elif self.path == "/memory/search":
                    result = dispatcher.search(cn=cn, bearer=bearer, **_parse_search_body(body))
                    self._respond(200, result)
                elif self.path == "/memory/reflect":
                    dispatcher.reflect(cn=cn, bearer=bearer, **body)
                else:
                    self._respond(404, {"error": "not_found", "detail": self.path})
            except DispatchError as exc:
                self._respond_error(exc)

        def do_GET(self):  # noqa: N802 - stdlib method name
            if self.path == "/healthz":
                # Unauthenticated liveness/readiness probe for Kubernetes;
                # never touches identity, permissions, or any backend.
                self._respond(200, {"status": "ok"})
                return

            parsed = urllib.parse.urlsplit(self.path)
            match = _CONTEXT_PATH_RE.match(parsed.path)
            if match:
                cn, bearer = self._identity_headers()
                role = urllib.parse.parse_qs(parsed.query).get("role", [None])[0]
                namespace = f"/{match.group('kind')}/{match.group('name')}"
                try:
                    result = dispatcher.context(cn=cn, bearer=bearer, role=role, namespace=namespace)
                    self._respond(200, result)
                except DispatchError as exc:
                    self._respond_error(exc)
                return

            self._respond(404, {"error": "not_found", "detail": self.path})

    return RouterRequestHandler


def build_http_server(dispatcher: Dispatcher, *, host: str = "0.0.0.0", port: int = 8080):
    handler = make_handler(dispatcher)
    return ThreadingHTTPServer((host, port), handler)


def _load_role_map_from_env() -> tuple[dict[str, str], dict[str, str]]:
    # Defaults mirror design.md's Phase 1 identity map. Real deployments
    # override via the mounted ConfigMap/Secret (see kubernetes/mcps).
    cn_to_identity = {
        "pedro-claude-code": "pedro-claude-code",
        "codex": "codex",
        "opencode": "opencode",
        "hermes-gateway": "hermes-gateway",
    }
    bearer_by_identity = {
        name: os.environ.get(f"MEMORY_ROUTER_BEARER_{name.upper().replace('-', '_')}", "")
        for name in cn_to_identity
    }
    return cn_to_identity, bearer_by_identity


def build_default_dispatcher() -> Dispatcher:
    cn_to_identity, bearer_by_identity = _load_role_map_from_env()
    journal_path = os.environ.get("MEMORY_ROUTER_JOURNAL_PATH", "/data/memory-router/journal.ndjson")
    return Dispatcher(
        registry=Registry(),
        journal=Journal(journal_path),
        cn_to_identity=cn_to_identity,
        bearer_by_identity=bearer_by_identity,
    )


def main() -> None:
    dispatcher = build_default_dispatcher()
    host = os.environ.get("MEMORY_ROUTER_HOST", "0.0.0.0")
    port = int(os.environ.get("MEMORY_ROUTER_PORT", "8080"))
    server = build_http_server(dispatcher, host=host, port=port)
    server.serve_forever()


# --------------------------------------------------------------------------
# MCP stdio shim — thin: normalizes tool calls into the same request shape
# as the REST surface, then calls the REST service over HTTP.
# --------------------------------------------------------------------------


class RestClient:
    """Calls the Memory Router REST surface over HTTP. Used by the MCP
    stdio shim so both surfaces go through the exact same dispatcher."""

    def __init__(self, base_url: str, *, cn: str, bearer: str):
        self._base_url = base_url.rstrip("/")
        self._cn = cn
        self._bearer = bearer

    def _post(self, path: str, body: dict) -> tuple[int, dict]:
        request = urllib.request.Request(
            self._base_url + path,
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._bearer}",
                CLIENT_CN_HEADER: self._cn,
            },
        )
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, json.loads(exc.read().decode("utf-8"))

    def store(self, **kwargs) -> tuple[int, dict]:
        return self._post("/memory/store", _parse_store_body(kwargs))

    def search(self, **kwargs) -> tuple[int, dict]:
        return self._post("/memory/search", _parse_search_body(kwargs))

    def reflect(self, **kwargs) -> tuple[int, dict]:
        return self._post("/memory/reflect", kwargs)


def handle_mcp_tool_call(name: str, arguments: dict, *, client) -> dict:
    """Normalize an MCP tool call to the same request shape the REST surface
    parses, then dispatch through `client` (a RestClient or, for embedded/
    in-process use, a Dispatcher — both expose store/search/reflect)."""
    if name == "memory_store":
        status, payload = client.store(**arguments)
    elif name == "memory_search":
        status, payload = client.search(**arguments)
    elif name == "memory_reflect":
        status, payload = client.reflect(**arguments)
    else:
        return {"error": "unknown_tool", "detail": name}
    return {"status": status, "body": payload}


def mcp_main() -> None:
    base_url = os.environ.get("MEMORY_ROUTER_URL", "http://127.0.0.1:8080")
    cn = os.environ.get("MEMORY_ROUTER_CLIENT_CN", "")
    bearer = os.environ.get("MEMORY_ROUTER_CLIENT_BEARER", "")
    client = RestClient(base_url, cn=cn, bearer=bearer)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request = json.loads(line)
        params = request.get("params", {})
        result = handle_mcp_tool_call(params.get("name", ""), params.get("arguments", {}), client=client)
        response = {"jsonrpc": "2.0", "id": request.get("id"), "result": result}
        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
