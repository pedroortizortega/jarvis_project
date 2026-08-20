# Tasks: Honcho Backend Adapter (Reflect Verb)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~950-1050 (contracts +25, honcho.py +160, app.py ~70 diff, permissions.py ~10 diff, pyproject +1, new adapter test +380, existing test edits ~60, specs/016 +240) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 -> PR 2 |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending (ask user: stacked-to-main or feature-branch-chain) |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: pending
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | `contracts.py` additive Protocol + `HonchoBackend` adapter + entry point + full adapter test suite | PR 1 | `python -m unittest tests.test_memory_router_honcho_adapter tests.test_memory_router_hindsight_adapter tests.test_memory_router_engram_adapter -v` | N/A — stubbed transport only, no live Honcho instance | delete `backends/honcho.py`, its test file, and the `pyproject.toml` entry-point line; `contracts.py` additions are inert until referenced |
| 2 | `app.py` reflect pipeline rewrite + `permissions.py` role rows + existing dispatcher/permissions test updates + `specs/016_honcho_backend.md` | PR 2 | `python -m unittest discover -s tests` | N/A — in-process `Dispatcher`/`RestClient` HTTP loopback test server, no live Honcho | revert the `app.py`/`permissions.py` commits to restore the `501` stub (documented in design.md Migration/Rollout) |

## Phase 1: Contracts (Additive)

- [x] 1.1 RED: in `tests/test_memory_router_honcho_adapter.py`, add a `ContractConformanceTests` case asserting `isinstance(EngramBackend(spawn=...), MemoryBackend)` and `isinstance(HindsightBackend(base_url="http://x"), MemoryBackend)` still hold (run against current code to confirm it already passes, as a baseline).
- [x] 1.2 RED: add `test_reflective_backend_is_separate_protocol` asserting `ReflectiveBackend` exists, `MemoryBackend` has no `reflect` attribute, and `not isinstance(HindsightBackend(base_url="http://x"), ReflectiveBackend)` (fails: `ReflectiveBackend`/`ReflectRequest`/`ReflectResult`/`Conclusion` don't exist yet).
- [x] 1.3 GREEN: in `hermes-native/memory-router/src/memory_router/contracts.py`, add `ReflectRequest`, `ReflectResult`, `Conclusion` frozen dataclasses and `@runtime_checkable class ReflectiveBackend(Protocol)` exactly per design.md Interfaces. Do not touch `MemoryBackend`.
- [x] 1.4 REFACTOR: confirm no import cycle; re-run 1.1/1.2 green.

## Phase 2: HonchoBackend Adapter

- [x] 2.1 RED: in `tests/test_memory_router_honcho_adapter.py`, add config tests mirroring `HindsightAdapterConfigTests` (zero-arg construction, env defaults, env override, explicit-arg override, bearer-mode default) for `HONCHO_BASE_URL`/`HONCHO_AUTH_MODE`/`HONCHO_TOKEN`/`HONCHO_WORKSPACE_ID`/`HONCHO_TIMEOUT_SECONDS`.
- [x] 2.2 RED: add `capabilities()` tests: `verbs == frozenset({"reflect"})` exactly, `"store"`/`"search"` absent, `namespaces == ("/user/master",)`.
- [x] 2.3 RED: add `_peer_ref`/namespace-mapping tests analogous to `BankIdSanitizerTests` — valid `/user/master` mapping, traversal/wildcard namespace raises `ValueError`, charset regex match.
- [x] 2.4 RED: add stubbed-transport adapter tests: dialectic 2xx+content -> `ReflectResult(status="ready", ...)` with `conclusions`; 202/empty body -> `status="pending"`, never fabricated; auth header present (bearer) / absent (none).
- [x] 2.5 RED: add degradation tests: connection error, non-2xx, malformed JSON each raise `BackendUnavailableError("honcho", ...)` and no other exception type escapes; `health()` never raises, returns OK/DOWN.
- [x] 2.6 RED: add secret-handling test — `HONCHO_TOKEN` never appears in any raised `BackendUnavailableError.reason` or `Health.reason`.
- [x] 2.7 GREEN: create `hermes-native/memory-router/src/memory_router/backends/honcho.py` — `ENDPOINTS` dict, `_env_default`, `_default_transport`, `_HttpJsonClient`, `_peer_ref(namespace) -> (workspace_id, peer_id)`, `HonchoBackend` implementing `capabilities/health/reflect` per design.md Data Flow (mirrors `hindsight.py` shape; implements `ReflectiveBackend`, not `MemoryBackend`).
- [x] 2.8 GREEN: run all Phase 2 tests, confirm green.
- [x] 2.9 REFACTOR: dedupe `_decode`/`_decode_error_reason` style against `hindsight.py` pattern; keep `ENDPOINTS` the single revisable wire surface.

## Phase 3: Dispatcher Reflect Pipeline (`app.py`)

- [x] 3.1 RED: in `tests/test_memory_router_app.py`, replace `test_reflect_returns_501_and_never_calls_registry_or_journal` with `DispatcherReflectTests` asserting: authorized role -> real registry dispatch (via injected `Registry(backends=[...])`) returns a routed `ReflectResult` payload, never `501`; unauthorized role -> `403 authorization_denied`; no reflect-capable backend registered -> `200 {"status": "no_backend", "conclusions": [], "unavailable": []}`; all backends raise `BackendUnavailableError` -> `status: "degraded"`, `unavailable` populated.
- [x] 3.2 RED: replace `test_rest_reflect_returns_501_no_backend_call` with a REST assertion that `POST /memory/reflect` actually writes a response body (today it calls `dispatcher.reflect` and returns nothing) and is never `501`.
- [x] 3.3 RED: replace `test_mcp_reflect_also_returns_501` with an MCP/REST parity assertion for `memory_reflect` matching `memory_search`'s existing parity pattern.
- [x] 3.4 RED: add an assertion (string search over `app.py` source) that neither `"lands with Hindsight"` nor `"phase": "hindsight"` occurs anywhere in the file.
- [x] 3.5 GREEN: in `app.py`, add `_parse_reflect_body(body)` helper (role/namespace/query — fixes the `**body` `TypeError` bug) mirroring `_parse_search_body`.
- [x] 3.6 GREEN: rewrite `Dispatcher.reflect()` mirroring `context()` — single namespace via `_validate_namespace`, `_authorize(verb="reflect")`, `registry.backends_for(verb="reflect", namespace=namespace)`, no `_fallback_chain`; iterate results per design.md Data Flow (`ready`/`pending`/`BackendUnavailableError`/no-backend), return `{"namespace", "status", "conclusions", "unavailable"}`.
- [x] 3.7 GREEN: fix `do_POST`'s `/memory/reflect` branch to call `dispatcher.reflect(cn=cn, bearer=bearer, **_parse_reflect_body(body))` and `self._respond(200, result)` (today it never responds).
- [x] 3.8 GREEN: fix `RestClient.reflect` to normalize via `_parse_reflect_body` like `store`/`search` instead of passing raw `**kwargs`.
- [x] 3.9 GREEN: delete the `"lands with Hindsight"` docstring/comment and the `if error.error == "not_implemented": payload["phase"] = "hindsight"` branch in `_dispatch_error_payload`.
- [x] 3.10 GREEN: run Phase 3 tests, confirm green.
- [x] 3.11 REFACTOR: confirm `reflect()` reads symmetrically with `context()`; no leftover `501`/`not_implemented` reflect-specific code paths.

## Phase 4: Permissions

- [x] 4.1 RED: in `tests/test_memory_router_permissions.py`, add table-driven tests: `jarvis` and `scientist` allow `reflect` on `user_master`; `coder` denies `reflect` on `user_master`; all three roles deny `reflect` on `global`/`projects`/`agents_self`/`agents_other`.
- [x] 4.2 GREEN: in `hermes-native/memory-router/src/memory_router/permissions.py`, add `reflect` to `_ROLE_TABLE["jarvis"]["user_master"]` and `_ROLE_TABLE["scientist"]["user_master"]`; leave `_ROLE_TABLE["coder"]["user_master"]` as `frozenset()`.
- [x] 4.3 GREEN: run Phase 4 tests, confirm green.

## Phase 5: Registration and Cross-Cutting Verification

- [x] 5.1 In `hermes-native/memory-router/pyproject.toml`, add `honcho = "memory_router.backends.honcho:HonchoBackend"` under `[project.entry-points."memory_router.backends"]`.
- [x] 5.2 Run full suite: `python -m unittest discover -s tests` from the repo root — confirm all green, including Phase 1-4 additions.
- [x] 5.3 Confirm `isinstance(EngramBackend(...), MemoryBackend)` and `isinstance(HindsightBackend(...), MemoryBackend)` conformance tests are byte-unmodified from pre-change and still pass.
- [x] 5.4 Grep `app.py` for `"lands with Hindsight"` and `"phase": "hindsight"` — confirm zero occurrences.
- [x] 5.5 Confirm `registry.py` is byte-unmodified (`git diff` shows no changes to `registry.py`).

## Phase 6: Spec Companion Doc

- [x] 6.1 Create `specs/016_honcho_backend.md` following `specs/015_hindsight_backend.md`'s structure/format (sections 0-10: why, scope, architecture, contract-separation rationale, adapter contract, degraded semantics, threat matrix, coexistence/rollback, open questions, implementation checklist, references). Reference `openspec/changes/honcho-backend/{proposal,design}.md` and the delta specs.
