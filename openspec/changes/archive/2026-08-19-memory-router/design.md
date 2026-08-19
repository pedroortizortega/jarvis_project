# Design: Memory Router (Phase 1)

## Technical Approach

A stateless-routing Python service in `mcps`: authenticate identity -> validate declared role -> validate caller-declared namespace -> authorize verb -> dispatch to backends selected by declared capability. Backends are entry-point plugins behind one `MemoryBackend` protocol; Phase 1 registers only Engram. Degraded writes go to a durable journal; degraded searches return partial results with per-backend markers.

## Component Architecture

```text
client (mTLS + bearer, Tailnet)
  -> Traefik ingress (RequireAndVerifyClientCert, TLSOption mcps-memory-router-mtls)
  -> [identity resolver] -> [role validator] -> [namespace validator] -> [permission engine]
  -> [backend registry: capability match] -> [dispatcher]
       |-> healthy: adapter.store/search
       |-> degraded (store): durable journal -> drainer -> adapter
       |-> degraded (search): omit + "unavailable" marker
  -> Engram adapter -> `engram mcp` stdio subprocess -> engram-cloud.mcps.svc:8080
```

MCP surface is a thin stdio shim (`memory-router-mcp` console script) that clients spawn locally and that calls the REST service. Engram exposes no MCP-over-HTTP (`/mcp` returns 404 from the app, spec 011 §6), so no HTTP-MCP route is assumed anywhere.

## Backend Adapter Contract

```python
@dataclass(frozen=True)
class Capabilities:
    name: str
    verbs: frozenset[str]          # {"store","search"}; "reflect" absent in Phase 1
    namespaces: tuple[str, ...]    # glob patterns this backend accepts
    hierarchical_search: bool

class MemoryBackend(Protocol):
    def capabilities(self) -> Capabilities: ...
    def health(self) -> Health: ...                       # ok | degraded | down
    def store(self, req: StoreRequest) -> StoreResult: ...  # committed | pending
    def search(self, req: SearchRequest) -> SearchResult: ...
```

Registration is a setuptools entry point group `memory_router.backends`, mirroring `hermes_agent.plugins` in `hermes-native/memory-router/pyproject.toml`. Adding backend #2 = new package + entry point + config row; no router code change.

**Engram reference adapter**: spawns `engram mcp --tools=agent` with `ENGRAM_CLOUD_SERVER=http://engram-cloud.mcps.svc.cluster.local:8080`, per-identity `ENGRAM_CLOUD_TOKEN`, `ENGRAM_CLOUD_AUTOSYNC=1` — the access path proven in spec 011. Engram has no namespaces, so the adapter encodes namespace as a reserved `topic_key` prefix (`ns:/projects/foo/...`) inside project `jarvis_project`; `store`->`mem_save`, `search`->`mem_search` + `mem_get_observation`. `reflect` is absent from its capabilities.

## Data Flow

| Flow | Behavior |
|---|---|
| store (healthy) | authz -> adapter.store -> `202 {status: "committed", backend, id}` |
| store (degraded) | authz -> journal append+fsync -> `202 {status: "pending", queue_id}`; drainer retries with backoff; never dropped |
| search (healthy) | resolve namespace -> query; if empty and namespace is `/projects/{n}` or `/agents/{n}`, fall back `project -> agent -> global`, tagging each hit with its source namespace |
| search (degraded) | return hits from healthy backends plus `unavailable: [{backend, reason}]`; HTTP 200, never a whole-request failure |
| reflect | `501 {error: "not_implemented", phase: "hindsight"}` — no logic, no backend call |

## Auth & Permissions

Traefik terminates mTLS and forwards the client DN; the router maps `CN` -> identity, then verifies the per-identity bearer. The request declares `role`; the router checks it against the server-side `identity -> allowed_roles` map (config, never caller-asserted), then evaluates namespace+verb. Deny-by-default at a single enforcement point before any adapter call.

| Role | `/global` | `/user/master` | `/projects/{n}` | `/agents/{self}` | `/agents/{other}` | `admin/*` |
|---|---|---|---|---|---|---|
| coder | search | deny | store+search | store+search | deny | deny |
| scientist | store+search | search | search | store+search | deny | deny |
| jarvis | store+search | store+search | store+search | store+search | store+search | deny |

Identity map: `pedro-claude-code`->{coder}, `codex`->{coder}, `opencode`->{coder,scientist}, `hermes-gateway`->{jarvis}.

## Architecture Decisions

| Decision | Options / tradeoff | Choice and rationale |
|---|---|---|
| Adapter plugin mechanism | Hardcoded registry (simple, needs router edits) vs entry points (indirection) | Entry points — the proposal requires backend #2 to be registration-only, and the repo already uses this pattern. |
| Router statefulness | Fully stateless (no durable queue) vs stateful | Request handling is stateless; only the write journal and Engram's local db are durable. Keeps auth/routing trivially testable. |
| Queue mechanism | In-memory deque (fast, **drops on restart — violates "never dropped"**) vs Redis/Postgres (new dependency) vs on-disk journal | Append-only NDJSON journal with fsync on a PVC; `replicas: 1` + `Recreate` keeps a single writer. |
| Role mapping storage | External store (dynamic, new dependency) vs ConfigMap | ConfigMap + PyYAML, like `hermes-native/orchestration/policy.yaml`. Role changes are reviewable config, per decision 5. |
| Engram transport | Assumed MCP-over-HTTP (404 upstream) vs cloud HTTP sync API (unverified verbs) vs `engram mcp` stdio | stdio subprocess — the only proven store/search surface. |
| Framework | FastAPI/uvicorn (new ASGI stack) vs stdlib | Python 3.11 + `ThreadingHTTPServer` + PyYAML, matching this repo's stdlib-only setuptools convention; 4 Tailnet clients need no ASGI. |

## File Changes

| File | Action | Description |
|---|---|---|
| `hermes-native/memory-router/src/memory_router/{app,identity,permissions,namespaces,registry,journal,contracts}.py` | Create | Router core. |
| `hermes-native/memory-router/src/memory_router/backends/engram.py` | Create | Reference adapter. |
| `hermes-native/memory-router/pyproject.toml` | Modify | Add `memory_router.backends` group + console scripts. |
| `kubernetes/mcps/memory-router-{configmap,deployment,service,pvc,ingress,tlsoption}.yaml` | Create | Third `mcps` tenant, ClusterIP:8080, `automountServiceAccountToken: false`, non-root, read-only rootfs, caps dropped. |
| `tests/test_memory_router_*.py` | Create | RED tests. |
| `specs/014_memory_router.md` | Create | Numbered spec companion. |

## Testing Strategy

| Layer | What | How |
|---|---|---|
| Unit | Permission table, namespace validation, role-assertion rejection, capability matching, journal durability across restart, hierarchical fallback, partial-search markers, 501 reflect | `python -m unittest discover -s tests` |
| Integration | Engram adapter against a live `engram mcp` subprocess | Manual/ephemeral; not in the unit command |
| E2E | Four clients over Tailnet with mTLS | Runbook, gated on deployment prerequisites |

## Threat Matrix

| Boundary | Applicability | Design response | Planned RED tests |
|---|---|---|---|
| Documentation-like paths | N/A — no file classification or execution of repo content | — | None |
| Git repository selection | N/A — no Git operations | — | None |
| Commit state | N/A — no commits | — | None |
| Push state | N/A — no pushes | — | None |
| PR commands | N/A — no PR automation | — | None |
| Namespace routing selection (added) | Applicable — caller declares the namespace | Accept only the four literal namespace shapes; reject traversal (`..`), absolute/relative escapes, wildcards, and unknown roots before authorization; fail closed | Traversal, wildcard, unknown-root, cross-agent namespace |
| Adapter subprocess (added) | Applicable — `engram mcp` is spawned | Fixed argv list, no shell, no caller-controlled argv or env; secrets from mounted Secret only; crash -> `degraded`, not a request failure | Caller input never reaches argv/env; subprocess death degrades cleanly |

## Migration / Rollout

No data migration. Deploy alongside existing direct Engram access; once healthy the router is the **default** path and direct access is rollback-only (decision 4). Rollback removes only `memory-router` resources and revokes its onboarding.

## Open Questions

- [ ] **Deployment prerequisite (blocking)**: Engram Cloud's `mcps` manifests are untracked with undocumented origin (spec 011 §0). Confirm ownership and a reproducible manifest source before deploying a dependent tenant — not assumed solved.
- [ ] Does the router hold one shared Engram identity or proxy each client's token? Phase 1 assumes one router identity; attribution then lives in the namespace, not in Engram principals.
- [ ] Journal retention/alerting when a backend stays down beyond N hours.
- [ ] Traefik client-DN header name to confirm against the live `mcps` ingress config.
- [ ] **ConfigMap + PyYAML wiring deferred**: `kubernetes/mcps/memory-router-configmap.yaml`'s `identity-roles.yaml` documents the intended identity->role map, but Phase 1 code does not read it — `permissions.py::IDENTITY_ROLES` and `app.py::_load_role_map_from_env()` hardcode the equivalent map directly (CN identities in code, bearer tokens from env vars). Actually wiring the ConfigMap through PyYAML (this doc's originally stated architecture) is deferred to a later phase; until then, a change to the ConfigMap's `identity-roles.yaml` has no runtime effect and must be mirrored by hand into `permissions.py`/`app.py`.
