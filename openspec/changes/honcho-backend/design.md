# Design: Honcho Backend Adapter (Reflect Verb)

## Technical Approach

Three layers land together, each minimal. `contracts.py` gains `ReflectRequest`/`ReflectResult` plus a **separate** `@runtime_checkable ReflectiveBackend` Protocol; `MemoryBackend` is byte-identical, so `isinstance(EngramBackend(), MemoryBackend)` and `isinstance(HindsightBackend(), MemoryBackend)` keep passing with their conformance tests unmodified (neither adapter implements `ReflectiveBackend`, and `runtime_checkable` checks method presence only). `Dispatcher.reflect()` is rewritten to mirror `context()` — single namespace, no `_fallback_chain` — because `/user/master` has no parent. `HonchoBackend` copies `hindsight.py`'s HTTP shape (`_default_transport`, `_env_default`, `_HttpJsonClient`, one `ENDPOINTS` dict) but implements `reflect()` instead of `store`/`search`. `registry.py` is untouched: `backends_for` only calls `capabilities()` and never `isinstance`-checks, so a non-`MemoryBackend` adapter routes correctly today; its `list[MemoryBackend]` annotation is advisory and unenforced.

## Architecture Decisions

| Decision | Options / tradeoff | Choice and rationale |
|---|---|---|
| Reflect contract | Default no-op `reflect()` on `MemoryBackend` (one Protocol, but every backend silently claims a verb it cannot serve) vs ABC inheritance (forces adapters to subclass, breaking the structural-typing convention) vs separate narrow Protocol | Separate `ReflectiveBackend`. Registry verb selection is already the gate; base Protocol conformance for Engram/Hindsight is provably untouched. Confirms the proposal's rejection. |
| Dispatch gate | `hasattr(backend, "reflect")` alone vs `isinstance` alone vs capability-first | `capabilities().verbs` (via `backends_for(verb="reflect")`) is the primary gate; `isinstance(backend, ReflectiveBackend)` is a fail-closed second check. A backend advertising `reflect` without the method is skipped and reported, never crashed on. |
| `reflect()` shape | Mirror `search()` (hierarchical chain) vs mirror `context()` | Mirror `context()`. `_fallback_chain("/user/master", …)` already returns `[namespace]`; walking it would imply a parent that does not exist and would silently authorize other namespaces. |
| No reflect-capable backend | 501 (indistinguishable from the old stub) vs generic failure vs fabricated success vs explicit typed result | `200` with `{"status": "no_backend", "conclusions": [], "unavailable": []}` — a distinct, machine-readable state. |
| Async Dialectic | Block/poll until conclusions exist vs synthesize a placeholder conclusion | Adapter returns `ReflectResult(status="pending")` on `202`/empty-body responses. Never blocks, never fabricates. |
| HTTP client | `httpx`/`requests` (new dependency) vs stdlib `urllib.request` | `urllib.request`, identical to `hindsight.py`; package keeps zero runtime deps. |

## Interfaces / Contracts

```python
# contracts.py — additive only
@dataclass(frozen=True)
class ReflectRequest:
    namespace: str
    role: str
    query: str = ""

@dataclass(frozen=True)
class ReflectResult:
    status: str              # "ready" | "pending" | "empty"
    backend: str
    conclusions: tuple = ()   # tuple[Conclusion]
    reason: str = ""

@dataclass(frozen=True)
class Conclusion:
    namespace: str
    backend: str
    content: str
    confidence: float = 0.0

@runtime_checkable
class ReflectiveBackend(Protocol):
    def capabilities(self) -> Capabilities: ...
    def health(self) -> Health: ...
    def reflect(self, req: ReflectRequest) -> ReflectResult: ...
```

```python
# backends/honcho.py — single revisable wire surface (UNVERIFIED, see Open Questions)
ENDPOINTS = {
    "dialectic": "/v2/workspaces/{workspace_id}/peers/{peer_id}/chat",  # POST {query} -> {content}|202
    "health": "/healthz",
}

class HonchoBackend:   # implements ReflectiveBackend, NOT MemoryBackend
    def __init__(self, *, transport=None, base_url=None, auth_mode=None,
                 token=None, workspace_id=None, timeout=None): ...
    def capabilities(self): return Capabilities(
        name="honcho", verbs=frozenset({"reflect"}),
        namespaces=("/user/master",), hierarchical_search=False)
```

`transport(method, url, headers, body) -> (status, bytes)` is the test seam, exactly as in `hindsight.py`.

## Config Surface

| Env var | Default |
|---|---|
| `HONCHO_BASE_URL` | `http://honcho.mcps.svc.cluster.local:8000` |
| `HONCHO_AUTH_MODE` | `bearer` if token set, else `none` |
| `HONCHO_TOKEN` | `""` |
| `HONCHO_WORKSPACE_ID` | `jarvis` |
| `HONCHO_TIMEOUT_SECONDS` | `10` |

## Data Flow

    POST /memory/reflect
      -> _parse_reflect_body(body)            # role, namespace, query — no **body TypeError
      -> _authenticate -> _validate_namespace -> _authorize(verb="reflect")
      -> registry.backends_for(verb="reflect", namespace)   # single namespace, no fallback chain
           |- []                        -> {"status": "no_backend", "conclusions": [], "unavailable": []}
           |- backend not ReflectiveBackend -> skip + unavailable[{backend, "not reflect-capable"}]
           |- ReflectResult.status "ready"   -> conclusions[]
           |- ReflectResult.status "pending" -> {"status": "pending", "conclusions": []}
           `- BackendUnavailableError        -> unavailable[] ; status "degraded" if all failed
      -> 200 {"namespace", "status", "conclusions", "unavailable"}

    HonchoBackend.reflect -> _peer_ref(ns) -> POST dialectic
        2xx + content -> ReflectResult("ready", "honcho", conclusions)
        2xx empty / 202 -> ReflectResult("pending", "honcho")
        non-2xx / transport / decode error -> BackendUnavailableError("honcho", reason)
    health -> GET /healthz -> OK | DOWN(reason)   # never raises

Namespace `/user/master` maps to `(HONCHO_WORKSPACE_ID, "master")`, re-validated against `^[a-z0-9][a-z0-9_-]*$` and fail-closed — the `_bank_id` pattern from `hindsight.py`.

## Permissions

`_ROLE_TABLE` additions on `user_master` only (approved defaults):

| Role | `user_master` verbs after change |
|---|---|
| `jarvis` | `{"store", "search", "reflect"}` |
| `scientist` | `{"search", "reflect"}` |
| `coder` | `frozenset()` — unchanged, reflect denied |

No `reflect` row is added to `global`, `projects`, `agents_self`, `agents_other`, or `other`. `authorize()` already resolves an absent verb through `_ROLE_TABLE.get(...).get(kind, frozenset())`, so reflect on any other namespace kind is denied by default with no new code — asserted explicitly in tests rather than assumed.

## File Changes

| File | Action | Description |
|---|---|---|
| `.../memory_router/backends/honcho.py` | Create | `HonchoBackend` + `_HttpJsonClient` + `ENDPOINTS`. |
| `.../memory_router/contracts.py` | Modify | `ReflectRequest`/`ReflectResult`/`Conclusion`/`ReflectiveBackend`. `MemoryBackend` untouched. |
| `.../memory_router/app.py` | Modify | Real `Dispatcher.reflect()`; `_parse_reflect_body`; REST handler now responds (today it calls `reflect` and writes nothing); `RestClient.reflect` normalizes; delete `"lands with Hindsight"` and the `"phase": "hindsight"` branch in `_dispatch_error_payload`. |
| `.../memory_router/permissions.py` | Modify | Three `reflect` entries on `user_master`. |
| `hermes-native/memory-router/pyproject.toml` | Modify | One entry-point line. |
| `tests/test_memory_router_honcho_adapter.py` | Create | Stubbed-transport RED tests. |
| `tests/` dispatcher + permissions | Modify | Replace 501 assertions with routed-reflect assertions. |
| `specs/016_honcho_backend.md` | Create | Numbered spec companion. |

## Testing Strategy

| Layer | What to test | Approach |
|---|---|---|
| Unit (contract) | `isinstance(HonchoBackend(), ReflectiveBackend)`; `not isinstance(HonchoBackend(), MemoryBackend)`; Engram/Hindsight `MemoryBackend` conformance tests re-run **unmodified** | `python -m unittest discover -s tests` |
| Unit (capabilities) | `verbs == frozenset({"reflect"})` exactly; `"store"`/`"search"` absent; `namespaces == ("/user/master",)`; zero-arg construction | Direct assertions |
| Unit (adapter) | Dialectic round-trip → `ready`; `202`/empty → `pending` (never fabricated); peer/workspace mapping; auth header present/absent per mode | Stub transport |
| Unit (degradation) | Connection error, non-2xx, malformed JSON each raise `BackendUnavailableError("honcho", …)` | Raising/garbage stub |
| Unit (secrets) | `HONCHO_TOKEN` never appears in any `BackendUnavailableError.reason` or dispatcher payload | Token-substring assertion over raised reasons |
| Unit (dispatcher) | Authorized role → routed reflect, not 501; unauthorized role → `403 authorization_denied`, not 501; no reflect-capable backend → `status: "no_backend"`, not generic failure; all-backends-down → `unavailable` populated; no `"lands with Hindsight"`/`"phase"` string remains | `Registry(backends=[...])` injection |
| Unit (permissions) | `jarvis`/`scientist` allow, `coder` deny on `user_master`; reflect denied on `/global`, `/projects/*`, `/agents/*` for all three roles | Table-driven |
| Unit (parity) | MCP `memory_reflect` and REST `/memory/reflect` produce identical routing decisions | `handle_mcp_tool_call` against a `Dispatcher` |
| Integration | Live Honcho instance | **Not performed** — no instance/credential; explicit follow-up. |

## Threat Matrix

| Boundary | Applicability | Design response | Planned RED tests |
|---|---|---|---|
| Namespace routing selection | Applicable | Reflect scoped to a single validated namespace, no fallback chain; workspace/peer ids re-validated, fail closed | Traversal/wildcard namespace yields no peer ref; reflect on `/projects/x` selects no backend |
| Authorization | Applicable | Explicit rows only on `user_master`; deny-by-default elsewhere unchanged | `coder` denied; reflect denied on every other namespace kind |
| Outbound request construction | Applicable | URL from router-owned config + sanitized ids only; caller `query` travels in the JSON body; fixed header set; timeout always set | Malicious query absent from URL and headers |
| Secret handling | Applicable | Token from env only, never logged, never echoed into error reasons | Token absent from raised reasons and responses |
| Subprocess / VCS / PR automation / executable classification | N/A — HTTP only, no shell, no VCS | — | None |

## Migration / Rollout

No data migration, no stored state. Adding the entry point activates reflect routing; removing `honcho.py`, its test, and the entry-point line disables it (`backends_for(verb="reflect")` returns empty and reflect degrades to the explicit `no_backend` result — not a crash). Reverting the `contracts.py`/`app.py`/`permissions.py` commits restores the 501 stub.

## Open Questions

- [ ] Honcho Dialectic wire format (path, request key, response shape, async/pending signal) is **unverified** against a live instance or authoritative docs; `ENDPOINTS` plus `_peer_ref` are the single revisable surface.
- [ ] Hosted `mcp.honcho.dev` vs self-hosted endpoint, and the privacy tradeoff of shipping derived personal beliefs off-cluster — deferred, blocks live validation only.
- [ ] Does reflect ever need an ingestion path (feeding Honcho conversation content)? Deferred per the proposal; until then a real deployment plausibly returns `pending`/`empty` in practice.
- [ ] Should `registry.py`'s `list[MemoryBackend]` annotation widen to a `RoutableBackend` union? Advisory only — no runtime behavior depends on it; deliberately left unchanged to keep the diff minimal.
- [ ] Confidence/score normalization across Honcho conclusions and Engram hits, if reflect results are ever merged with search.
