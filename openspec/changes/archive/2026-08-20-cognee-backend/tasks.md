# Tasks: Cognee Backend Adapter (Reflect on `/projects/*`)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~430-480 (cognee.py ~165, tests ~230, permissions 2, app.py ~9, pyproject 1, spec doc ~230 excluded from authored-risk count as doc) |
| 400-line budget risk | Medium |
| Chained PRs recommended | No |
| Suggested split | Single PR |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending |

Decision needed before apply: Yes
Chained PRs recommended: No
Chain strategy: pending
400-line budget risk: Medium

Rationale: code-only diff (cognee.py + app.py + permissions.py + pyproject.toml + adapter test file) lands near ~400 authored lines, similar footprint to honcho-backend's single PR. Below the honcho precedent's scale (that touched contracts.py/registry.py too); this change does not. `specs/017_cognee_backend.md` is generated documentation and excluded from authored risk. Recommend single PR; ask user to confirm given proximity to the 400-line budget.

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Full change: adapter + dispatcher fix + permissions + entry point + tests + spec doc | PR 1 | `python -m unittest discover -s tests` (run from repo root of this worktree) | N/A — no live Cognee instance; stubbed-transport unit tests only | Remove `backends/cognee.py`, its entry-point line, its test file, revert the 2 `permissions.py` rows and the `app.py` 3-line mapping + docstring |

## Phase 1: Foundation — Cognee Adapter Skeleton

- [x] 1.1 RED: `tests/test_memory_router_cognee_adapter.py` — protocol conformance: `isinstance(CogneeBackend(), ReflectiveBackend)` true, `isinstance(CogneeBackend(), MemoryBackend)` false, zero-arg construction succeeds (fails: module doesn't exist)
- [x] 1.2 GREEN: create `hermes-native/memory-router/src/memory_router/backends/cognee.py` — `_env_default`, `_default_transport`, `_HttpJsonClient` (copied from `honcho.py`, backend name `"cognee"`), `ENDPOINTS = {"recall": "/recall", "health": "/healthz"}`, `SEARCH_TYPE = "GRAPH_COMPLETION"`, `_DATASET_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")`, `CogneeBackend.__init__` (transport, base_url, auth_mode, token, dataset_prefix, timeout) reading `COGNEE_BASE_URL`/`COGNEE_AUTH_MODE`/`COGNEE_TOKEN`/`COGNEE_DATASET_PREFIX`(default `jarvis-`)/`COGNEE_TIMEOUT_SECONDS`(default `10`)
- [x] 1.3 GREEN: implement `capabilities()` returning `name="cognee"`, `verbs=frozenset({"reflect"})`, `namespaces=("/projects/*",)`, `hierarchical_search=False`
- [x] 1.4 REFACTOR: confirm test 1.1 passes; diff `cognee.py` against `honcho.py` shape for consistency

## Phase 2: `_dataset_id()` Mapping (D-01, D-03, D-04)

- [x] 2.1 RED: test `/projects/hermes` → `jarvis-hermes` (default prefix); prefix override honored via `COGNEE_DATASET_PREFIX`
- [x] 2.2 GREEN: implement `_dataset_id(namespace)` per design.md Interfaces — reject-never-rewrite, `BackendUnavailableError("cognee", …)` on failure (not `ValueError`, per D-04)
- [x] 2.3 RED: fail-closed cases each raise `BackendUnavailableError("cognee", …)` and issue **no HTTP call**: `..` traversal, `*` wildcard, `?`, embedded `/`, leading `-`, uppercase (`Foo`), dot (`a.b`), empty project name
- [x] 2.4 RED: malformed `COGNEE_DATASET_PREFIX` (e.g. contains uppercase) also fails closed after prefixing+revalidation
- [x] 2.5 GREEN: confirm 2.3/2.4 pass against 2.2's implementation (revalidate full `dataset` string against `_DATASET_RE` after prefixing)
- [x] 2.6 RED: two distinct namespaces never yield one dataset id (injective-by-rejection); case/dot variants fail closed rather than collide

## Phase 3: Reflect Round-Trip, Empty, Degradation (D-02, D-05)

- [x] 3.1 RED: 2xx + non-empty answer → `status == "ready"`, one `Conclusion`, `confidence == 0.0`, `namespace` echoed, `backend == "cognee"`; POST body carries `search_type == "GRAPH_COMPLETION"` and exactly one dataset in `datasets`
- [x] 3.2 GREEN: implement `reflect(req)` — build payload, POST `/recall`, decode response, construct `Conclusion(confidence=0.0)` on non-empty answer
- [x] 3.3 RED: 2xx with empty/absent/whitespace answer → `status == "empty"`, `conclusions == ()`; assert **not** `"pending"` and **not** `"ready"`; no fabricated content
- [x] 3.4 GREEN: branch `reflect()` to return `ReflectResult(status="empty", backend="cognee")` on empty answer (never `"pending"`)
- [x] 3.5 RED: connection error (`OSError`/`URLError`), non-2xx status, malformed JSON each raise `BackendUnavailableError("cognee", …)` only
- [x] 3.6 GREEN: confirm `_HttpJsonClient.request` + `reflect()` error paths satisfy 3.5 (mirrors `honcho.py`'s `_decode`/status-check pattern)
- [x] 3.7 RED: `health()` — GET `/healthz`, 2xx → OK, non-2xx/transport-error → DOWN(reason); never raises
- [x] 3.8 GREEN: implement `health()` mirroring `honcho.py`

## Phase 4: Secrets and Outbound Construction

- [x] 4.1 RED: `COGNEE_TOKEN` substring absent from every raised `BackendUnavailableError.reason`; `Authorization: Bearer …` header present in `bearer` mode, absent in `none` mode
- [x] 4.2 GREEN: confirm header-building logic in `_client()` satisfies 4.1 (mirrors `honcho.py`)
- [x] 4.3 RED: hostile `query` (e.g. containing `?`, `&`, control chars) appears only in the JSON POST body — never in URL, never in headers; timeout always set on the transport call
- [x] 4.4 GREEN: confirm request construction satisfies 4.3

## Phase 5: Namespace Selection and Coexistence

- [x] 5.1 RED: `Registry([CogneeBackend()]).backends_for(verb="reflect", namespace="/projects/foo")` selects Cognee; empty for `/user/master`, `/global`, `/agents/x`
- [x] 5.2 GREEN: confirm 5.1 passes with no registry.py changes (capabilities-only gating already generic)
- [x] 5.3 RED: `Registry([HonchoBackend(), CogneeBackend()])`: `/user/master` → only Honcho; `/projects/foo` → only Cognee
- [x] 5.4 GREEN: confirm coexistence via disjoint namespace patterns, no shared-state interaction
- [x] 5.5 Run existing `tests/test_memory_router_honcho_adapter.py` unmodified — confirm all pass (no regression)

## Phase 6: Dispatcher `empty` Status Mapping (D-06)

- [x] 6.1 RED: add to `tests/test_memory_router_app.py` `DispatcherReflectTests` — reflect on `/projects/x` with a `FakeReflectiveBackend(status="empty", namespaces=("/projects/*",))` reports `result["status"] == "empty"`, not `"no_backend"`
- [x] 6.2 RED: same test class — confirm precedence: a backend returning `ready` still wins over one returning `empty` when both are registered (status stays `"ready"`)
- [x] 6.3 GREEN: edit `hermes-native/memory-router/src/memory_router/app.py` `Dispatcher.reflect()` — add exactly the 3-line mapping after the `pending` branch: `elif result.status == "empty" and status not in ("ready", "pending"): status = "empty"`
- [x] 6.4 GREEN: update `Dispatcher.reflect()` docstring per design.md (replace `app.py:199-204` wording — remove the `/user/master`-only / "no parent" phrasing, state the real invariant that conclusions are never inherited regardless of namespace)
- [x] 6.5 Run `DispatcherReflectTests.test_pending_backend_returns_pending_status` and `test_authorized_role_gets_routed_reflect_result_never_501` unmodified — confirm Honcho's existing `ready`/`pending` behavior is bit-for-bit unchanged

## Phase 7: Permissions

- [x] 7.1 RED: `tests/test_memory_router_permissions.py` — `scientist` allowed `reflect` on `/projects/x`; `jarvis` allowed `reflect` on `/projects/x`; `coder` raises `AuthorizationError` for `reflect` on `/projects/x`
- [x] 7.2 RED: `reflect` still denied on `/global`, `/agents/*` for all three roles (deny-by-default unaffected)
- [x] 7.3 RED: `coder`'s existing `{"store", "search"}` on `projects` still allowed (unaffected regression check)
- [x] 7.4 GREEN: edit `hermes-native/memory-router/src/memory_router/permissions.py` — `_ROLE_TABLE["scientist"]["projects"]` → `frozenset({"search", "reflect"})`; `_ROLE_TABLE["jarvis"]["projects"]` → `frozenset({"store", "search", "reflect"})`; leave `coder`'s `projects` row byte-unchanged
- [x] 7.5 Diff `permissions.py` — confirm only the two target lines changed, `coder` row untouched

## Phase 8: Namespace Validation (F-1)

- [x] 8.1 RED: `validate_namespace("/projects/a/b")` raises `NamespaceError` (test in `tests/test_memory_router_namespaces.py` or dispatcher-level 400 test), proving nesting is rejected at the validation layer — not just asserted in a comment
- [x] 8.2 RED: dispatcher-level — reflect request with `namespace="/projects/a/b"` returns `400 invalid_namespace`
- [x] 8.3 Confirm no `namespaces.py` code change is needed (F-1 pre-existing behavior); tests document/lock the finding

## Phase 9: Wiring

- [x] 9.1 GREEN: add `cognee = "memory_router.backends.cognee:CogneeBackend"` to `hermes-native/memory-router/pyproject.toml` under `[project.entry-points."memory_router.backends"]`
- [x] 9.2 RED/GREEN: registry entry-point loading test (if one exists in `tests/test_memory_router_registry.py`) exercises `cognee` alongside `engram`/`hindsight`/`honcho` without error

## Phase 10: Spec Companion Doc

- [x] 10.1 Create `specs/017_cognee_backend.md` following `specs/016_honcho_backend.md`'s format and language (Spanish, matching precedent) — sections: why it exists, scope, architecture, `ReflectiveBackend` reuse (no contract change), adapter contract, `_dataset_id()` mapping, reflect round-trip/empty/degradation semantics, config table, dispatcher pipeline incl. D-06 empty mapping, permissions table, threats considered, coexistence/rollback, implementation checklist, references

## Phase 11: Final Verification

- [x] 11.1 Run full suite: `python -m unittest discover -s tests` (run from repo root of this worktree) — all green, no failures/errors
- [x] 11.2 `git diff --stat origin/main -- hermes-native/memory-router/src/memory_router/contracts.py hermes-native/memory-router/src/memory_router/registry.py` — confirm zero diff on both files
- [x] 11.3 `git diff origin/main -- hermes-native/memory-router/src/memory_router/permissions.py` — confirm `coder`'s `projects` row is byte-unchanged, only the two target rows (`scientist`, `jarvis`) modified
- [x] 11.4 Re-run `tests/test_memory_router_honcho_adapter.py` in isolation — confirm all pass unmodified (no regression from the `app.py` dispatcher change)
- [x] 11.5 Confirm `CogneeBackend.capabilities().verbs == frozenset({"reflect"})` exactly and `"store"`/`"search"` absent (equality assertion, not membership)
