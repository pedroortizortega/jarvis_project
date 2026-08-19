# Proposal: Memory Router

## Intent

Agent memory is fragmented: Engram is the only live backend, reachable only via MCP-over-stdio per client, with no namespacing and no per-agent permissions. Five further stores (Hindsight, Graphiti, Honcho, Cognee, Obsidian) are planned, each with its own protocol. Without a single routing layer, every agent would integrate six backends itself. Memory Router centralizes memory access behind one namespaced, permissioned surface deployed in `mcps`, and proves the architecture end to end with Engram before any second backend exists.

## Scope

### In Scope
- `memory-router` service (name deliberately distinct from `llama-router` and the Hermes intent router), deployed as a third `mcps` tenant, Tailnet-only.
- Dual surface: MCP server plus REST (`POST /memory/store`, `/memory/search`, `/memory/reflect`, `/agents/context`, `/projects/context`).
- Namespaces from day one: `/global`, `/user/master`, `/projects/{name}`, `/agents/{name}`; hierarchical search fallback project -> agent -> global.
- Backend adapter contract (capability declaration, store/search/health) with exactly one implementation: Engram, reached through its existing supported access path.
- Per-identity/role permission model (`coder`, `scientist`, `jarvis`, ...) as namespace+verb rules, layered on the existing `mcps` mTLS/identity-proxy plus per-identity bearer convention.
- Client onboarding for `pedro-claude-code`, `codex`, `opencode`, `hermes-gateway` reusing the established pattern.

### Out of Scope
- Implementing Hindsight, Graphiti, Honcho, Cognee, or Obsidian adapters; their protocols stay open research per backend.
- Cross-backend merge/ranking, write fan-out, dedup, and memory migration.
- Changing Engram's own deployment, storage, or transport; public Internet exposure.

## Capabilities

### New Capabilities
- `memory-router-interfaces`: MCP and REST surfaces, request/response contracts, error semantics.
- `memory-namespace-routing`: namespace model, routing decision, hierarchical search fallback.
- `memory-backend-adapters`: adapter contract plus the Engram adapter and degraded/unavailable behavior.
- `memory-access-control`: identity resolution and per-role namespace/verb authorization over `mcps` auth.

### Modified Capabilities
None — no OpenSpec capability specifications exist yet.

## Approach

Thin stateless router: authenticate identity -> resolve namespace -> authorize verb -> select backends by declared capability -> delegate to adapters. Backends are plugins behind one interface, so adapters 2–6 land without reshaping the router. Phase 1 wires only Engram; the routing table stays data-driven so adding a backend is registration, not surgery. Permissions are enforced at the router, never trusted from the caller.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `kubernetes/mcps/` | New | `memory-router` deployment, service, policy. |
| `hermes-native/memory-router/` | New | Router, adapters, permission engine. |
| `tests/` | New | Unit tests (`python -m unittest discover -s tests`). |
| `specs/014_memory_router.md` | New | Numbered spec companion. |
| `openspec/changes/memory-router/` | New | SDD artifacts. |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Engram MCP-over-HTTP unavailable upstream | High | Adapter targets Engram's existing supported access path; no HTTP-MCP assumption. |
| Engram Cloud manifests untracked / out-of-band origin | High | Treat as coordination prerequisite; confirm owner and access path before deploy. |
| Adapter contract overfit to Engram | Med | Validate contract against a paper design of at least one non-Engram backend before freezing. |
| Router becomes single point of failure for all memory | Med | Stateless, health-checked; agents retain direct Engram access during rollout. |
| Permission model too coarse or too granular | Med | Ship the three stated roles only; deny-by-default. |

## Rollback Plan

Remove only `memory-router` resources from `mcps` and revoke its client onboarding. Engram, Brave, and Graphify are untouched; agents fall back to their current direct Engram access. No data migration occurs, so no restore step is required.

## Dependencies

- Existing `mcps` mTLS/identity-proxy and per-identity bearer token issuance.
- Documented, reachable Engram access path and ownership of its untracked manifests.
- Tailnet connectivity for the four onboarded clients.

## Resolved Decisions (user, post-proposal)

1. **`/memory/reflect` semantics**: placeholder in Phase 1. Not implemented against Engram (Engram has no "experience/lesson" concept). Returns `501 Not Implemented` / explicit stub. Real semantics (resumen sobre lo recuperado) land when Hindsight is integrated.
2. **Namespace authority**: caller declares the namespace explicitly on every request (`/projects/{name}`, `/agents/{name}`, etc.). Router does not infer it from identity/context. Misattribution is a caller error, validated against the caller's permitted namespaces (see access control), not silently corrected by the router.
3. **Degraded-backend behavior**: `store` queues (buffers) when the target backend is unavailable rather than failing loudly — no memory is dropped, but the caller must be told the write is pending, not committed. `search` returns partial results plus an explicit "backend unavailable" marker per omitted backend, rather than failing the whole request.
4. **Rollout coexistence**: agents may keep direct Engram access during rollout for rollback safety, but once the Memory Router is active it becomes the **default path** — new integrations and normal operation go through the router; direct Engram access is a rollback/fallback only, not a parallel steady-state option.
5. **Permission subject**: role-based (`coder`, `scientist`, `jarvis`, ...), matching the original design intent. The mapping from onboarded client identity (`pedro-claude-code`, `codex`, `opencode`, `hermes-gateway`) to its permitted role(s) is fixed server-side in router configuration — a client cannot self-declare an arbitrary role (e.g. assert `jarvis` for full access). Adding/changing a client's allowed roles is a router-config change, not a caller-side capability.

## Success Criteria

- [ ] Both MCP and REST surfaces serve store/search/reflect/context against Engram.
- [ ] Namespaced writes are isolated; search falls back project -> agent -> global.
- [ ] A `coder` identity is denied `admin/*` and a `scientist` identity is denied another agent's namespace.
- [ ] Adding a second backend requires only a new adapter plus registration — no router change.
- [ ] All four clients reach the router over Tailnet with mutual authentication.
