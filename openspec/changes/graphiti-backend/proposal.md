# Proposal: Graphiti Backend Adapter (Reflect on `/global` and `/agents/*`)

## Intent

`reflect` now works on two roots — Honcho on `/user/master`, Cognee on `/projects/*`. `/global` and `/agents/*` still select zero reflect-capable backends and return `no_backend`. They are the last two unclaimed cells in the namespace×verb matrix for this verb.

Graphiti (Zep AI, open source) is a temporal knowledge graph: episodes are ingested, an LLM extracts entities and relationships, and edges carry validity intervals. `search_facts` / `search_nodes` return facts derived across accumulated episodes with a time dimension — a derived conclusion, not a retrieval hit, which is `reflect` semantics. `group_id` namespaces episodes, mapping cleanly onto router namespaces (the same shape Hindsight's `bank_id` uses).

Closing this verb makes the reflect surface explainable as a rule ("every fixed root reflects") instead of a list of exceptions.

## Scope

### In Scope
- `GraphitiBackend` in `.../memory_router/backends/graphiti.py` — HTTP adapter over `search_facts`/`search_nodes`, injectable `transport(method, url, headers, body)` seam, env-driven config, mirroring `backends/cognee.py`.
- `capabilities()` = name `graphiti`, `verbs = frozenset({"reflect"})`, `namespaces = ("/global", "/agents/*")`. No `store`, no `search`.
- Namespace → `group_id` resolution, fail-closed on any namespace that cannot yield a legal group identifier.
- `permissions.py`: `reflect` rows for the `global`, `agents_self`, and `agents_other` namespace kinds (see Approach for the proposed grants). Deny-by-default preserved everywhere else.
- Entry-point line under the existing `memory_router.backends` group.
- Explicit `empty` `ReflectResult` for an unpopulated graph — never a fabricated conclusion.
- Unit tests with a stubbed transport; no live Graphiti instance, no graph DB required to merge.

### Out of Scope
- Graphiti `store`/`search`. Both verbs are fully claimed on all four roots.
- An ingestion path (`add_episode`) that populates the graph — deferred, same posture as Honcho's and Cognee's deferred ingestion.
- Graph-DB and LLM infrastructure: Neo4j/FalkorDB provisioning, k8s manifests, API-key provisioning, cost controls.
- Mutating Graphiti tools: `delete_entity_edge`, `delete_episode`, `clear_graph`. A reflect-only adapter never mutates.
- `get_episodes` raw episode listing — that is search-shaped.
- Exposing temporal-validity intervals as a first-class router field (`Conclusion` has no time dimension today).

## Capabilities

### New Capabilities
None.

### Modified Capabilities
- `memory-access-control`: adds `reflect` authorization on the `global`, `agents_self`, and `agents_other` namespace kinds.
- `memory-backend-adapters`: adds the Graphiti adapter requirement (HTTP, reflect-only, `/global` + `/agents/*`, fact/node search).

## Approach

Clone the Cognee adapter shape; swap the namespace→identifier resolver and the wire format. `_dataset_id()` becomes `_group_id()`: `/global` → one fixed shared group, `/agents/{name}` → one group per agent, with the same fail-closed charset revalidation. One `_HttpJsonClient` isolates the unverified Graphiti wire format in a single revisable class.

Proposed permission grants, consistent with the existing table (`coder` holds `reflect` nowhere today, and only `jarvis` touches `agents_other`):

| Namespace kind | coder | scientist | jarvis |
|---|---|---|---|
| `global` | unchanged | + `reflect` | + `reflect` |
| `agents_self` | unchanged | + `reflect` | + `reflect` |
| `agents_other` | unchanged (denied) | unchanged (denied) | + `reflect` |

No contract extension: `ReflectiveBackend`, `ReflectRequest`, `ReflectResult`, and `Conclusion` already exist and fit. Registry selection is generic — `fnmatch` matches `/agents/foo` against `/agents/*` with no code change. `Dispatcher.reflect()` already fans out across backends and already maps `ready`/`pending`/`empty`/`degraded`, so Honcho, Cognee, and Graphiti coexist on disjoint namespaces with zero core diff.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `.../memory_router/backends/graphiti.py` | New | HTTP reflect adapter. |
| `.../memory_router/permissions.py` | Modified | `reflect` added to `global`, `agents_self`, `agents_other` rows. |
| `.../memory_router/contracts.py` | Unchanged | Reflect contract already sufficient. |
| `.../memory_router/app.py` | Unchanged | Fan-out and status mapping already generic. |
| `.../memory_router/registry.py` | Unchanged | Wildcard selection already generic. |
| `hermes-native/memory-router/pyproject.toml` | Modified | One entry-point line. |
| `tests/test_memory_router_graphiti_adapter.py` | New | Stubbed-transport unit tests. |
| `tests/` (permissions) | Modified | New allow/deny assertions for `global` + `agents_*` reflect. |
| `openspec/specs/{memory-access-control,memory-backend-adapters}/` | Modified | Delta specs. |
| `specs/019_graphiti_backend.md` | New | Numbered spec companion. |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Graphiti wire format unverified against a live instance | High | Single revisable client class; stubbed-transport tests; schema treated as provisional. |
| Empty graph (no ingestion path) makes reflect always return nothing | High | Explicit `empty` result is the correct first-slice behavior; asserted in tests; never fabricate. |
| Cross-agent leakage if all agents share one `group_id` | Med | Per-agent group, fail-closed resolver; `agents_other` reflect restricted to `jarvis`. |
| `/global` reflect exposes cross-domain facts to a broad audience | Med | `coder` denied; explicit deny test. |
| New infra class: graph DB + LLM key, LLM cost on every write | Med | No ingestion in this slice, so no write cost is incurred by this change; flagged as an ops prerequisite before provisioning. |
| Temporal validity discarded when flattened into `Conclusion.content` | Med | First slice keeps the fact text verbatim; a typed time field is deferred, not silently invented. |
| Scope creep into `store`/`search` or mutating tools | Med | `verbs == {"reflect"}` asserted; absence of `store`/`search` asserted; no delete/clear call sites. |

## Rollback Plan

Registration-only. (1) Remove the entry-point line, `backends/graphiti.py`, and its test file — `backends_for(verb="reflect", namespace="/global")` then returns empty and `Dispatcher.reflect()` responds `no_backend`, its pre-change behavior. (2) Revert the `permissions.py` rows. Both are pure code reverts on a feature branch. No data migration, no stored state, no Graphiti-side cleanup (the adapter never writes).

## Dependencies

- honcho-backend and cognee-backend on `main` (reflect pipeline + `ReflectiveBackend` contract) — verified present.
- Graphiti request/response documentation for `search_facts` / `search_nodes`.
- A Graphiti endpoint + graph DB + LLM key — required for live validation only, not for merging.

## Success Criteria

- [ ] `GraphitiBackend.capabilities().verbs == frozenset({"reflect"})`; `"store"` and `"search"` asserted absent.
- [ ] `capabilities().namespaces == ("/global", "/agents/*")`; reflect on `/user/master` and `/projects/x` does not select Graphiti.
- [ ] Reflect on `/user/master` still selects Honcho only and on `/projects/*` still selects Cognee only — existing tests pass unmodified.
- [ ] `contracts.py`, `app.py`, and `registry.py` have zero diff.
- [ ] `jarvis` and `scientist` reflecting on `/global` and their own agent namespace are authorized; `coder` gets `403 authorization_denied` on both.
- [ ] `scientist` reflecting on another agent's namespace is denied; only `jarvis` is allowed.
- [ ] Empty or unpopulated graph yields an explicit `empty` `ReflectResult` — never a fabricated conclusion.
- [ ] Transport failure raises `BackendUnavailableError` and surfaces as `degraded`, not as request failure.
- [ ] A namespace that cannot yield a legal `group_id` fails closed.
- [ ] Every fixed namespace root now has at least one reflect-capable backend.

## Proposal question round (open questions for `sdd-design`)

Reflect-only scope and the two target roots are **resolved** by prior user decision — not re-asked. Permission grants (below) are also now **resolved** by user decision (AskUserQuestion): the Approach table's proposed grants stand as-is — `scientist` + `jarvis` reflect on `global`/`agents_self`, `jarvis`-only on `agents_other` (cross-agent reflect denied to `coder` and `scientist`). Remaining genuinely ambiguous product decisions, left to `sdd-design` with a proposed default each (none blocking — design may adopt the proposed default or flag if it finds a reason not to):
2. **`/agents/*` group mapping.** One `group_id` per agent (isolation) or one shared agents group (cross-agent synthesis)? Proposed default: per-agent, fail closed.
3. **`search_facts` vs `search_nodes`.** Facts are edges (relationship statements, temporally scoped); nodes are entity summaries. Proposed default: facts only in slice one, since a fact reads as a conclusion and a node reads as a record.
4. **Temporal validity.** Facts carry `valid_at`/`invalid_at`. Should expired facts be filtered out, or returned with their interval inlined in `content`? Proposed default: return only currently-valid facts.
5. **Confidence value.** Graphiti returns relevance-ranked facts, not calibrated confidence. Proposed: `confidence=0.0` ("unscored"), matching Cognee, rather than inventing a number.
6. **Nested namespaces.** `fnmatch` makes `/agents/*` also match `/agents/a/b`. Confirm nested agent namespaces reflect against the parent agent's group, or are rejected.
