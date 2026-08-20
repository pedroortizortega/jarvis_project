# Tasks: Graphiti Backend Adapter (Reflect on `/global` and `/agents/*`)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~1,050 (graphiti.py ~210, tests ~430, permissions ~5, pyproject 1, delta specs ~90, specs/019 ~330) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes (user decision, overriding the single-PR/size:exception default) |
| Suggested split | 2 chained PRs |
| Delivery strategy | chained-PR, fresh-PR-against-main pattern (per honcho-backend/obsidian-backend precedent — avoid the PR-auto-close-on-merge trap) |
| Chain strategy | 2-slice, per design.md's documented fallback |

Decision needed before apply: No — resolved by explicit user confirmation (AskUserQuestion): chained PRs, not single-PR exception.

Rationale for the split (from design.md's documented fallback, in dependency order): PR1 is self-contained and mergeable alone (adapter has no effect until permissions grant reflect on it — `backends_for` would select it, but `_authorize` still returns 403 for everyone since no permission rows exist yet); PR2 depends on PR1's entry-point registration.

### Work Units (chained)

| Unit | Goal | PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----|----------------------|-----------------|-------------------|
| 1 | `backends/graphiti.py` adapter + entry-point line + adapter unit tests (Phases 1-6, 9) | PR 1 (base=main) | `python -m unittest tests.test_memory_router_graphiti_adapter` | N/A — stubbed-transport unit tests only | Remove `backends/graphiti.py`, its entry-point line, its test file |
| 2 | `permissions.py` 5 rows + permission/dispatcher/coexistence tests + delta specs + `specs/019` + zero-diff verification (Phases 7-8, 10-11) | PR 2 (base=main, opened fresh after PR1 merges — never stacked on PR1's branch) | `python -m unittest discover -s tests` (full suite) | N/A | Revert the 5 `permissions.py` rows |

PR2 must be opened as a **fresh branch off `main` after PR1 is merged**, `base=main` directly — not stacked on PR1's branch. This is the established fix for the PR-auto-close bug hit during honcho-backend (merging PR1 with `--delete-branch` while PR2 is stacked on it causes GitHub to auto-close PR2, and a closed PR's base cannot be changed back).

## Phase 1: Foundation — Graphiti Adapter Skeleton

- [x] 1.1 RED: `tests/test_memory_router_graphiti_adapter.py` — protocol conformance: `isinstance(GraphitiBackend(), ReflectiveBackend)` true, `isinstance(GraphitiBackend(), MemoryBackend)` false, zero-arg construction succeeds (fails: module doesn't exist)
- [x] 1.2 GREEN: create `hermes-native/memory-router/src/memory_router/backends/graphiti.py` — clone `backends/cognee.py`'s shape: `_env_default`, `_default_transport`, `_HttpJsonClient`, `GraphitiBackend.__init__` (transport, base_url, auth_mode, token, group_prefix, timeout) reading `GRAPHITI_BASE_URL`/`GRAPHITI_AUTH_MODE`/`GRAPHITI_TOKEN`/`GRAPHITI_GROUP_PREFIX`(default `jarvis-`)/`GRAPHITI_TIMEOUT_SECONDS`(default `10`)/`GRAPHITI_MAX_FACTS`(default `10`)
- [x] 1.3 GREEN: define `ENDPOINTS = {"search_facts": "/search/facts", "health": "/healthz"}`, `MAX_FACTS = 10`, `_GROUP_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")`
- [x] 1.4 GREEN: implement `capabilities()` returning `name="graphiti"`, `verbs=frozenset({"reflect"})`, `namespaces=("/global", "/agents/*")`, `hierarchical_search=False`
- [x] 1.5 REFACTOR: confirm test 1.1 passes; diff `graphiti.py` against `cognee.py` shape for consistency

## Phase 2: `_group_id()` Mapping (D-01, D-02, D-03, D-04, D-08)

- [x] 2.1 RED: `/global` → `jarvis-global` (default prefix); `/agents/scientist` → `jarvis-agent-scientist`; prefix override honored via `GRAPHITI_GROUP_PREFIX`
- [x] 2.2 GREEN: implement `_group_id(namespace)` per design.md Interfaces — `/global` → `global` suffix, `/agents/{name}` → `agent-{name}` suffix (infix keeps mapping injective), reject-never-rewrite, `BackendUnavailableError("graphiti", …)` on failure (not `ValueError`)
- [x] 2.3 RED: `/agents/global` group id does **not** collide with `/global`'s group id (D-02 injectivity)
- [x] 2.4 RED: fail-closed cases each raise `BackendUnavailableError("graphiti", …)` and issue **no HTTP call**: `..` traversal, `*` wildcard, `?`, embedded `/`, empty agent name, uppercase (`Foo`), dot (`a.b`)
- [x] 2.5 RED: malformed `GRAPHITI_GROUP_PREFIX` (e.g. contains uppercase) also fails closed after prefixing+revalidation against `_GROUP_RE`
- [x] 2.6 GREEN: confirm 2.3/2.4/2.5 pass against 2.2's implementation
- [x] 2.7 RED: two distinct namespaces never yield one group id (injective-by-rejection); case/dot variants fail closed rather than collide
- [x] 2.8 RED: `namespace == "/user/master"` or any namespace outside `/global`/`/agents/*` raises `BackendUnavailableError("graphiti", …)` from `_group_id`

## Phase 3: Reflect Round-Trip, Temporal Filter, Empty, Degradation (D-05, D-06, D-07, D-09)

- [x] 3.1 RED: 2xx + N currently-valid facts → `status == "ready"`, N conclusions in response order, `confidence == 0.0`, `namespace` echoed, `backend == "graphiti"`; POST body carries `query`, exactly one id in `group_ids`, and `max_facts == MAX_FACTS`
- [x] 3.2 GREEN: implement `reflect(req)` — `_group_id(req.namespace)`, build payload, POST `/search/facts`, decode `{"facts": [...]}`
- [x] 3.3 RED: mixed valid/`invalid_at` facts → only currently-valid fact text included, expired fact excluded entirely
- [x] 3.4 GREEN: filter `live = [f for f in facts if not f.get("invalid_at")]` before building conclusions (D-06)
- [x] 3.5 RED: all facts expired → `status == "empty"`, `conclusions == ()`; assert **not** `"ready"` and **not** `"pending"` (D-06 sub-decision: never a `ready` with an empty tuple)
- [x] 3.6 RED: `{"facts": []}` or absent `facts` key → `status == "empty"`; no fabricated content anywhere in the payload
- [x] 3.7 RED: blank/whitespace-only `fact` text is dropped; if dropping empties the tuple, result is `empty`
- [x] 3.8 GREEN: branch `reflect()` — return `ReflectResult(status="empty", backend="graphiti")` when no live facts or all fact text is blank; otherwise build one `Conclusion(confidence=0.0)` per surviving fact (D-07, D-09)
- [x] 3.9 RED: connection error (`OSError`/`URLError`), non-2xx status, malformed JSON each raise `BackendUnavailableError("graphiti", …)` only
- [x] 3.10 GREEN: confirm `_HttpJsonClient.request` + `reflect()` error paths satisfy 3.9 (mirrors `cognee.py`'s decode/status-check pattern)
- [x] 3.11 RED: `health()` — GET `/healthz`, 2xx → OK, non-2xx/transport-error → DOWN(reason); never raises
- [x] 3.12 GREEN: implement `health()` mirroring `cognee.py`

## Phase 4: Secrets and Outbound Construction

- [x] 4.1 RED: `GRAPHITI_TOKEN` substring absent from every raised `BackendUnavailableError.reason`; `Authorization: Bearer …` header present in `bearer` mode, absent in `none` mode
- [x] 4.2 GREEN: confirm header-building logic in `_client()` satisfies 4.1 (mirrors `cognee.py`)
- [x] 4.3 RED: hostile `query` (e.g. containing `?`, `&`, control chars) appears only in the JSON POST body — never in URL, never in headers; timeout always set on the transport call
- [x] 4.4 GREEN: confirm request construction satisfies 4.3

## Phase 5: Namespace Selection and Three-Way Coexistence

- [x] 5.1 RED: `Registry([GraphitiBackend()]).backends_for(verb="reflect", namespace="/global")` and `namespace="/agents/foo"` select Graphiti; empty for `/user/master`, `/projects/x`
- [x] 5.2 GREEN: confirm 5.1 passes with no `registry.py` changes (capabilities-only gating already generic)
- [x] 5.3 RED: `Registry([HonchoBackend(), CogneeBackend(), GraphitiBackend()])`: for every validated namespace, `backends_for(verb="reflect", ...)` returns at most one of the three adapters (disjoint patterns)
- [x] 5.4 GREEN: confirm coexistence via disjoint namespace patterns, no shared-state interaction
- [x] 5.5 Run existing `tests/test_memory_router_honcho_adapter.py` and `tests/test_memory_router_cognee_adapter.py` unmodified — confirm all pass (no regression)

## Phase 6: Namespace Validation — Nested `/agents/*` (F-2, confirms design's no-op)

- [x] 6.1 RED: `validate_namespace("/agents/a/b")` raises `NamespaceError` (test in `tests/test_memory_router_namespaces.py` or dispatcher-level 400 test), proving nesting is rejected at the existing validation layer — no `namespaces.py` code change
- [x] 6.2 RED: dispatcher-level — reflect request with `namespace="/agents/a/b"` returns `400 invalid_namespace`
- [x] 6.3 Confirm no `namespaces.py` diff is needed; tests document/lock the F-2 finding rather than adding adapter-level nested-namespace handling

## Phase 7: Permissions

- [x] 7.1 RED: `tests/test_memory_router_permissions.py` — `scientist` and `jarvis` allowed `reflect` on `/global` and on their own agent namespace (`agents_self`)
- [x] 7.2 RED: `coder` raises `AuthorizationError` for `reflect` on `/global` and on its own agent namespace
- [x] 7.3 RED: `jarvis` allowed `reflect` on another agent's namespace (`agents_other`); `scientist` and `coder` denied `reflect` on `agents_other`
- [x] 7.4 RED: `coder`'s existing verb grants on `global`/`agents_self`/`agents_other` are unaffected (regression check)
- [x] 7.5 GREEN: edit `hermes-native/memory-router/src/memory_router/permissions.py` — exactly 5 row edits, `+reflect` added to: `scientist.global`, `scientist.agents_self`, `jarvis.global`, `jarvis.agents_self`, `jarvis.agents_other`; `coder` rows byte-unchanged
- [x] 7.6 Diff `permissions.py` — confirm only the five target rows changed, `coder` untouched

## Phase 8: Zero-Diff Verification (contracts.py, app.py, registry.py)

- [x] 8.1 `git diff --stat origin/main -- hermes-native/memory-router/src/memory_router/contracts.py hermes-native/memory-router/src/memory_router/app.py hermes-native/memory-router/src/memory_router/registry.py` — confirm empty output (zero diff on all three files)
- [x] 8.2 Assert this in a test or recorded check, not skipped: `app.py`'s existing `empty` status mapping (F-1) and `registry.py`'s `fnmatch`-based selection (F-3) already serve Graphiti with no code change

## Phase 9: Wiring

- [x] 9.1 GREEN: add `graphiti = "memory_router.backends.graphiti:GraphitiBackend"` to `hermes-native/memory-router/pyproject.toml` under `[project.entry-points."memory_router.backends"]`
- [x] 9.2 RED/GREEN: registry entry-point loading test exercises `graphiti` alongside `engram`/`hindsight`/`honcho`/`cognee` without error

## Phase 10: Matrix Closure and Spec Companion Doc

- [x] 10.1 RED: registry injection test — every fixed namespace root (`/user/master`, `/projects/*`, `/global`, `/agents/*`) now has at least one reflect-capable backend under the full registry (`HonchoBackend`, `CogneeBackend`, `GraphitiBackend`)
- [x] 10.2 Create `specs/019_graphiti_backend.md` following `specs/017_cognee_backend.md`'s format and language (Spanish, matching precedent) — sections: why it exists, scope, architecture, `ReflectiveBackend` reuse (no contract change), adapter contract, `_group_id()` mapping (D-01–D-04, D-08), reflect round-trip/temporal-filter/empty/degradation semantics (D-05–D-07, D-09), config table, permissions table, threats considered, coexistence/rollback, implementation checklist, references

## Phase 11: Final Verification

- [x] 11.1 Run full suite: `python -m unittest discover -s tests` (run from repo root of this worktree) — all green, no failures/errors
- [x] 11.2 `git diff origin/main -- hermes-native/memory-router/src/memory_router/permissions.py` — confirm exactly 5 rows changed (`scientist.global`, `scientist.agents_self`, `jarvis.global`, `jarvis.agents_self`, `jarvis.agents_other`), `coder` byte-unchanged
- [x] 11.3 Re-run `tests/test_memory_router_honcho_adapter.py` and `tests/test_memory_router_cognee_adapter.py` in isolation — confirm all pass unmodified (no regression)
- [x] 11.4 Confirm `GraphitiBackend.capabilities().verbs == frozenset({"reflect"})` exactly and `"store"`/`"search"` absent (equality assertion, not membership)
- [x] 11.5 Confirm `GraphitiBackend.capabilities().namespaces == ("/global", "/agents/*")` exactly (equality, not membership)
