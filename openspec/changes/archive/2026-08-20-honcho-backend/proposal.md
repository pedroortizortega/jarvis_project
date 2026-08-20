# Proposal: Honcho Backend Adapter (Reflect Verb)

## Intent

`reflect` is the Memory Router's third declared verb and it is dead. `Dispatcher.reflect()` authenticates and then unconditionally raises `501 not_implemented`, with a stale comment claiming it "lands with Hindsight" — false, Hindsight explicitly excluded `reflect`. `_ROLE_TABLE` has zero `reflect` rows for any role, so deny-by-default would block it even if the dispatcher worked, and the `MemoryBackend` Protocol has no `reflect()` method for an adapter to implement. Three layers are simultaneously unfinished, so the verb cannot be exercised at all.

Honcho (Plastic Labs, open source) closes it. Its Dialectic API runs async jobs that derive *conclusions* about a user — theory-of-mind style beliefs, distinct from the raw conversation history Engram stores. That is exactly the semantics `reflect` was reserved for, and it is a user-modeling system, so `/user/master` is its natural and only first-slice namespace.

This is the first non-additive core-contract change since Phase 1. Hindsight proved the adapter seam under a purely additive constraint; this change deliberately spends the core edits Hindsight refused, on the narrowest possible surface.

## Scope

### In Scope
- `HonchoBackend` in `hermes-native/memory-router/src/memory_router/backends/honcho.py` — HTTP transport with an injectable `transport(method, url, headers, body)` seam, config-driven auth via env, mirroring `hindsight.py`'s shape.
- `capabilities()` = name `honcho`, `verbs = frozenset({"reflect"})`, `namespaces = ("/user/master",)`. No `store`, no `search` — Engram keeps sole ownership of store/search on `/user/master`; this is verb-scoped and non-colliding.
- `contracts.py`: new `ReflectRequest` / `ReflectResult` dataclasses, plus a reflect-capable backend contract that leaves `MemoryBackend` conformance intact for Engram and Hindsight (neither implements `reflect`).
- `app.py`: rewrite `Dispatcher.reflect()` to run the real identity → namespace → permission → `backends_for(verb="reflect", ...)` pipeline, with `BackendUnavailableError` degradation. The stale Hindsight comment and the `"phase": "hindsight"` hint in `_dispatch_error_payload` go with it.
- `permissions.py`: explicit `reflect` rows in `_ROLE_TABLE` (see Open Questions for the proposed defaults).
- Entry-point registration under the existing `memory_router.backends` group.
- Unit tests with a stubbed transport; no live Honcho instance required.

### Out of Scope
- `reflect` on `/projects/*`, `/agents/*`, or `/global`. `/user/master` only.
- Graphiti, Cognee, Obsidian adapters.
- Honcho account provisioning, API-key issuance, deployment manifests, or the hosted-vs-self-hosted decision.
- Giving Honcho `store` or `search`, or any cross-backend merge/dedup with Engram.
- Write-back of derived conclusions into Engram.

## Capabilities

### New Capabilities
None.

### Modified Capabilities
- `memory-router-interfaces`: replaces "Reflect Endpoint Returns Not Implemented" with a real routed-reflect requirement, including the behavior when no reflect-capable backend is registered.
- `memory-access-control`: adds explicit per-role `reflect` authorization rows on the `user_master` namespace kind, preserving deny-by-default everywhere else.
- `memory-backend-adapters`: adds the Honcho adapter requirement (HTTP, reflect-only verbs, `/user/master`-only) and the reflect-capable contract extension that must not break existing adapters' conformance.

## Approach

Extend the contract by capability, not by widening the base Protocol. Preferred direction for `sdd-design` to confirm: a separate narrow `ReflectiveBackend` Protocol alongside the unchanged `MemoryBackend`, with the dispatcher reaching `reflect()` only on backends whose `capabilities().verbs` already contains `"reflect"`. Registry selection is therefore the gate and `isinstance(backend, MemoryBackend)` keeps passing for Engram and Hindsight untouched. Adding a default no-op `reflect()` to `MemoryBackend` is rejected: it silently makes every backend claim a verb it cannot serve.

`Dispatcher.reflect()` mirrors `context()` rather than `search()` — single namespace, no hierarchical fallback chain, since `/user/master` has no parent. Adapter maps the namespace to a Honcho peer/workspace identifier the way `hindsight.py` maps namespace to `bank_id`, isolating the wire format in one client class so the unverified Dialectic schema is revisable in one place.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `.../memory_router/backends/honcho.py` | New | HTTP reflect adapter. |
| `.../memory_router/contracts.py` | Modified | `ReflectRequest`/`ReflectResult` + reflect-capable Protocol. |
| `.../memory_router/app.py` | Modified | Real `Dispatcher.reflect()`; stale comment and `phase` hint removed. |
| `.../memory_router/permissions.py` | Modified | `reflect` rows in `_ROLE_TABLE`. |
| `.../memory_router/registry.py` | Unchanged | Verb/namespace selection already generic. |
| `hermes-native/memory-router/pyproject.toml` | Modified | One entry-point line. |
| `tests/test_memory_router_honcho_adapter.py` | New | Stubbed-transport unit tests. |
| `tests/` (dispatcher, permissions) | Modified | 501 assertions replaced with routed-reflect assertions. |
| `openspec/specs/{memory-router-interfaces,memory-access-control,memory-backend-adapters}/` | Modified | Delta specs. |
| `specs/016_honcho_backend.md` | New | Numbered spec companion. |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Contract change breaks Engram/Hindsight `MemoryBackend` conformance | Med | Capability-gated separate Protocol; existing `isinstance` conformance tests must stay green unmodified. |
| Honcho Dialectic wire format unverified against a live instance | High | Isolate in one client class; stubbed-transport tests; treat schema as revisable. |
| Dialectic is async — conclusions may not be ready when `reflect` is called | High | Adapter returns an explicit empty/pending `ReflectResult` rather than blocking; never fabricate a conclusion. |
| Reflect permission defaults leak personal user modeling to the wrong role | Med | Deny-by-default preserved; only rows explicitly confirmed by the user are added (Open Questions). |
| Personal derived beliefs leave the cluster to a hosted third party | Med | Flag as an explicit ops/privacy decision before any real key is provisioned; adapter must support a self-hosted base URL. |
| Scope creep into store/search on `/user/master` | Med | `verbs == {"reflect"}` asserted in tests; store/search absence asserted. |

## Rollback Plan

Two-part, because this is not registration-only. (1) Remove the entry-point line, `backends/honcho.py`, and its test file — this alone disables reflect routing, since `backends_for(verb="reflect")` then returns empty. (2) Revert the `contracts.py` / `app.py` / `permissions.py` commits to restore the `501` stub. Both parts are pure code reverts on a feature branch; no data migration, no stored state, no Honcho-side cleanup required beyond deleting an unused workspace.

## Dependencies

- Hindsight backend merged on `main` (PR #26) — satisfied.
- Honcho Dialectic API documentation for the reflect request/response shape.
- A Honcho endpoint (hosted `mcp.honcho.dev` or self-hosted) and credential — required for live validation only, not for merging the code-level change.
- User confirmation of the per-role reflect permission defaults (see Open Questions).

## Success Criteria

- [ ] `HonchoBackend.capabilities().verbs == frozenset({"reflect"})` and tests assert `"store"`/`"search"` are absent.
- [ ] `capabilities().namespaces == ("/user/master",)` and reflect on `/projects/*` or `/agents/*` selects no backend.
- [ ] `isinstance(EngramBackend(), MemoryBackend)` and `isinstance(HindsightBackend(), MemoryBackend)` still pass with their conformance tests unmodified.
- [ ] `POST /memory/reflect` no longer returns `501`; it runs identity → namespace → permission → registry and returns a `ReflectResult` payload.
- [ ] An unauthorized role reflecting on `/user/master` gets `403 authorization_denied`, not `501`.
- [ ] Reflect with no reflect-capable backend registered returns an explicit empty result or a distinct error — never a generic failure, never a silent success.
- [ ] Transport failure raises `BackendUnavailableError` and surfaces as degraded, not as a request failure.
- [ ] No occurrence of `"lands with Hindsight"` or `"phase": "hindsight"` remains in `app.py`.
- [ ] MCP and REST surfaces produce equivalent reflect routing decisions.

## Open Questions (need user decision before `sdd-spec`)

1. **Per-role `reflect` on `/user/master` (blocking).** Proposed defaults, following the existing table's conservatism:
   - `jarvis`: **allow** — already holds `store`+`search` on `user_master`; reflection is its core purpose.
   - `scientist`: **allow** — already holds `search` on `user_master`; reflect is read-oriented derived insight. *This is the genuinely ambiguous one:* derived beliefs about the user are qualitatively more sensitive than raw stored items, so denying scientist is equally defensible.
   - `coder`: **deny** — currently has zero verbs on `user_master`; granting reflect would newly expose personal user modeling to coding clients.
2. **Does `reflect` also ingest?** This change treats `reflect` as read-only query over Honcho-derived conclusions. If Honcho must first be fed conversation content to derive anything, that ingestion path is a separate future change — confirm that deferral is acceptable, or the first slice returns empty conclusions in practice.
3. **Honcho endpoint and credential.** Hosted `mcp.honcho.dev` vs self-hosted. Not a merge blocker (stubbed-transport tests), but it blocks live validation and carries the privacy tradeoff above.
