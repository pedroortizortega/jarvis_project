# Proposal: Cognee Backend Adapter (Reflect on `/projects/*`)

## Intent

`reflect` works, but only on one namespace. Honcho made `Dispatcher.reflect()` real and gave `/user/master` derived conclusions; every other root still selects zero reflect-capable backends and returns `no_backend`. `/projects/*` is the only namespace+verb pair in the whole matrix with no claimant at all — Engram and Hindsight own `store`/`search` on all four roots, Honcho owns `reflect` on `/user/master` alone.

Cognee (topoteretes, open source) fills exactly that hole. Its ECL pipeline builds a knowledge graph and `GRAPH_COMPLETION` search returns an LLM-synthesized answer over that graph — a derived conclusion, not a retrieval hit, which is `reflect` semantics rather than `search`. Project work is where cross-document synthesis actually pays off.

Unlike honcho-backend, this is **purely additive**: the reflect pipeline already exists. `contracts.py`, `app.py`, and `registry.py` need no functional change.

## Scope

### In Scope
- `CogneeBackend` in `.../memory_router/backends/cognee.py` — HTTP adapter over `/recall` with `search_type=GRAPH_COMPLETION`, injectable `transport(method, url, headers, body)` seam, env-driven config, mirroring `backends/honcho.py`.
- `capabilities()` = name `cognee`, `verbs = frozenset({"reflect"})`, `namespaces = ("/projects/*",)`. No `store`, no `search`.
- `permissions.py`: add `reflect` to `_ROLE_TABLE["scientist"]["projects"]` (currently `{"search"}`) and `_ROLE_TABLE["jarvis"]["projects"]` (currently `{"store", "search"}`). `coder` stays `{"store", "search"}` — no reflect. Mirrors the `user_master` reflect pattern.
- Entry-point line under the existing `memory_router.backends` group.
- Graceful empty/pending handling: an unpopulated graph returns an explicit `ReflectResult`, never a fabricated conclusion.
- Unit tests with a stubbed transport; no live Cognee instance required.

### Out of Scope
- Cognee `store`/`search`. All four roots are claimed for those verbs; a new namespace root is a separate, larger change (`namespaces.py` core edit).
- An ingestion path (`/remember`, add+cognify) that populates Cognee's graph — deferred, same posture as Honcho's deferred ingestion.
- Cognee deployment infra: Postgres, vector DB, LLM API key provisioning, k8s manifests. No live instance exists.
- Cognee's `CHUNKS` mode / raw retrieval — that is search-shaped and out of scope.
- Reflect on `/agents/*` or `/global`.

## Capabilities

### New Capabilities
None.

### Modified Capabilities
- `memory-access-control`: adds `reflect` authorization rows on the `projects` namespace kind for `scientist` and `jarvis`; deny-by-default preserved everywhere else, `coder` unchanged.
- `memory-backend-adapters`: adds the Cognee adapter requirement (HTTP, reflect-only, `/projects/*`-only, `GRAPH_COMPLETION`).

## Approach

Clone the Honcho adapter shape and swap two things: the namespace→backend-identifier mapping and the wire format. `_peer_ref()` becomes a `/projects/{name}` → Cognee dataset/scope resolver with the same fail-closed id-charset revalidation; `ENDPOINTS` points at `/recall` and `/healthz`. One `_HttpJsonClient` isolates the unverified Cognee schema in a single revisable class, exactly as `honcho.py` isolates Dialectic.

No contract extension is needed: `ReflectiveBackend`, `ReflectRequest`, `ReflectResult`, and `Conclusion` already exist and fit. Registry selection is already generic — `fnmatch` matches `/projects/foo` against `/projects/*` with no code change. Multi-backend reflect fan-out already works in `Dispatcher.reflect()`, so Honcho and Cognee coexist without interacting (disjoint namespaces).

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `.../memory_router/backends/cognee.py` | New | HTTP reflect adapter. |
| `.../memory_router/permissions.py` | Modified | `reflect` added to two `projects` rows. |
| `.../memory_router/contracts.py` | Unchanged | Reflect contract already sufficient. |
| `.../memory_router/app.py` | Modified | `Dispatcher.reflect()` docstring fix, plus a 3-line status-mapping addition so `ReflectResult(status="empty")` surfaces as `"empty"` instead of being silently reported as `"no_backend"` (resolved during design, D-06 — required for the approved `empty` status to be observable at all; Honcho's `ready`/`pending` behavior is bit-for-bit unchanged). |
| `.../memory_router/registry.py` | Unchanged | Wildcard selection already generic. |
| `hermes-native/memory-router/pyproject.toml` | Modified | One entry-point line. |
| `tests/test_memory_router_cognee_adapter.py` | New | Stubbed-transport unit tests. |
| `tests/` (permissions) | Modified | New allow/deny assertions for `projects` + `reflect`. |
| `openspec/specs/{memory-access-control,memory-backend-adapters}/` | Modified | Delta specs. |
| `specs/017_cognee_backend.md` | New | Numbered spec companion. |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Cognee `/recall` wire format unverified against a live instance | High | Single revisable client class; stubbed-transport tests; schema treated as provisional. |
| Empty graph (no ingestion path) makes reflect always return nothing | High | Explicit `empty`/`pending` result is the *correct* first-slice behavior; asserted in tests; never fabricate. |
| Cross-project leakage if all projects share one Cognee dataset | Med | Namespace→dataset mapping must be per-project and fail-closed; open question below must be answered before `sdd-design` finalizes. |
| Granting `reflect` on `projects` widens the permission table | Low | Resolved: `scientist` + `jarvis` only; `coder` unchanged; explicit deny test for `coder`. |
| Scope creep into `store`/`search` | Med | `verbs == {"reflect"}` asserted; absence of `store`/`search` asserted. |
| Cognee `GRAPH_COMPLETION` costs an LLM call per reflect | Med | Out-of-scope for this slice (no live instance); note as an ops cost before provisioning. |

## Rollback Plan

Registration-only, unlike honcho-backend. (1) Remove the entry-point line, `backends/cognee.py`, and its test file — `backends_for(verb="reflect", namespace="/projects/x")` then returns empty and `Dispatcher.reflect()` responds `no_backend`, its pre-change behavior. (2) Revert the two `permissions.py` rows. Both are pure code reverts on a feature branch. No data migration, no stored state, no Cognee-side cleanup.

## Dependencies

- honcho-backend merged on `main` (reflect pipeline + `ReflectiveBackend` contract) — satisfied.
- Cognee `/recall` request/response documentation for the `GRAPH_COMPLETION` shape.
- A Cognee endpoint + Postgres + vector DB + LLM key — required for live validation only, not for merging.

## Success Criteria

- [ ] `CogneeBackend.capabilities().verbs == frozenset({"reflect"})`; `"store"` and `"search"` asserted absent.
- [ ] `capabilities().namespaces == ("/projects/*",)`; reflect on `/user/master`, `/agents/*`, `/global` does not select Cognee.
- [ ] Reflect on `/user/master` still selects Honcho only — existing Honcho tests pass unmodified.
- [ ] `contracts.py`, `app.py` (beyond docstring), and `registry.py` have zero functional diff.
- [ ] `jarvis` and `scientist` reflecting on `/projects/x` are authorized; `coder` gets `403 authorization_denied`.
- [ ] Empty or unpopulated graph yields an explicit `empty`/`pending` `ReflectResult` — never a fabricated conclusion, never a generic failure.
- [ ] Transport failure raises `BackendUnavailableError` and surfaces as degraded, not as request failure.
- [ ] A namespace that cannot yield a legal Cognee scope identifier fails closed.

## Proposal question round (open questions for `sdd-design`)

Role grants are **resolved** (`scientist` + `jarvis`) — not re-asked. Remaining genuinely ambiguous product decisions:

1. **Namespace → Cognee scope mapping (blocking for design).** Does `/projects/{name}` map to one Cognee *dataset per project*, or a single shared dataset filtered by node set? Per-project datasets give hard isolation; a shared graph gives cross-project synthesis but risks leaking project B into a reflect on project A. Proposed default: **one dataset per project, fail closed** — isolation over synthesis.
2. **Result status when the graph is empty.** Cognee `/recall` is synchronous, so Honcho's async `pending` may not apply. Proposed: `empty` when the call succeeds with no conclusion, reserving `pending` for an explicit async signal.
3. **Confidence value.** `GRAPH_COMPLETION` returns synthesized prose, not a score. Proposed: one `Conclusion` with `confidence=0.0`, treating absent confidence as "unscored" rather than inventing a number.
4. **Nested namespaces.** `fnmatch` makes `/projects/*` also match `/projects/a/b`. Confirm nested project namespaces should reflect against the parent project's scope, or be rejected.
