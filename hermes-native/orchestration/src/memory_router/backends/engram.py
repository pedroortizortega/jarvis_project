import json
import os
import subprocess

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

# Fixed argv — never built from caller input. This is the only proven
# store/search access path for Engram (spec 011): MCP-over-stdio.
ARGV = ["engram", "mcp", "--tools=agent"]
NAMESPACE_TOPIC_PREFIX = "ns:"


def _default_spawn(argv, env):
    return subprocess.Popen(  # noqa: S603 - fixed argv, no shell, see ARGV above
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
    )


class _StdioRpcClient:
    """Minimal JSON-RPC-over-stdio client for `engram mcp --tools=agent`."""

    def __init__(self, process):
        self._process = process
        self._next_id = 1

    def call_tool(self, name: str, arguments: dict) -> dict:
        request_id = self._next_id
        self._next_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        try:
            self._process.stdin.write(json.dumps(request) + "\n")
            self._process.stdin.flush()
            line = self._process.stdout.readline()
        except (BrokenPipeError, OSError) as exc:
            raise BackendUnavailableError("engram", str(exc)) from exc

        if not line:
            raise BackendUnavailableError(
                "engram", "subprocess produced no output (likely crashed)"
            )
        try:
            response = json.loads(line)
        except json.JSONDecodeError as exc:
            raise BackendUnavailableError("engram", f"malformed response: {exc}") from exc

        if "error" in response:
            raise BackendUnavailableError("engram", str(response["error"]))
        return response.get("result", {})

    def close(self) -> None:
        try:
            self._process.terminate()
        except OSError:
            pass


class EngramBackend:
    """Phase 1 reference adapter: the only backend registered in this phase.

    Reaches Engram through its existing supported access path
    (`engram mcp --tools=agent` over stdio) — no HTTP-MCP transport is
    assumed. Engram has no namespace concept, so the namespace is encoded
    as a reserved `topic_key` prefix inside a single project.
    """

    def __init__(
        self,
        *,
        spawn=None,
        engram_server: str | None = None,
        engram_token: str | None = None,
        project: str = "jarvis_project",
    ):
        self._spawn = spawn or _default_spawn
        self._engram_server = (
            engram_server
            if engram_server is not None
            else os.environ.get(
                "ENGRAM_CLOUD_SERVER",
                "http://engram-cloud.mcps.svc.cluster.local:8080",
            )
        )
        self._engram_token = (
            engram_token
            if engram_token is not None
            else os.environ.get("ENGRAM_CLOUD_TOKEN", "")
        )
        self._project = project

    def capabilities(self) -> Capabilities:
        return Capabilities(
            name="engram",
            verbs=frozenset({"store", "search"}),
            namespaces=("/global", "/user/master", "/projects/*", "/agents/*"),
            hierarchical_search=True,
        )

    def _fixed_env(self) -> dict:
        # Only the router's own configured credentials and PATH are passed.
        # No request field (namespace/content/metadata) ever reaches env.
        return {
            "PATH": os.environ.get("PATH", ""),
            "ENGRAM_CLOUD_SERVER": self._engram_server,
            "ENGRAM_CLOUD_TOKEN": self._engram_token,
            "ENGRAM_CLOUD_AUTOSYNC": "1",
        }

    def _connect(self) -> _StdioRpcClient:
        try:
            process = self._spawn(ARGV, self._fixed_env())
        except OSError as exc:
            raise BackendUnavailableError("engram", str(exc)) from exc
        return _StdioRpcClient(process)

    def _topic_key(self, namespace: str) -> str:
        return f"{NAMESPACE_TOPIC_PREFIX}{namespace}"

    def health(self) -> Health:
        try:
            client = self._connect()
            client.close()
        except BackendUnavailableError as exc:
            return Health(status=HealthStatus.DOWN, reason=exc.reason)
        return Health(status=HealthStatus.OK)

    def store(self, req: StoreRequest) -> StoreResult:
        client = self._connect()
        try:
            result = client.call_tool(
                "mem_save",
                {
                    "title": req.content[:100],
                    "topic_key": self._topic_key(req.namespace),
                    "content": req.content,
                    "project": self._project,
                    "type": "note",
                },
            )
        finally:
            client.close()
        return StoreResult(
            status="committed", backend="engram", id=str(result.get("id", ""))
        )

    def search(self, req: SearchRequest) -> SearchResult:
        client = self._connect()
        try:
            result = client.call_tool(
                "mem_search",
                {
                    "query": req.query,
                    "project": self._project,
                    "topic_key_prefix": self._topic_key(req.namespace),
                },
            )
        finally:
            client.close()
        hits = tuple(
            SearchHit(
                namespace=req.namespace,
                backend="engram",
                content=item.get("content", ""),
                score=float(item.get("score", 0.0)),
            )
            for item in result.get("results", [])
        )
        return SearchResult(hits=hits)
