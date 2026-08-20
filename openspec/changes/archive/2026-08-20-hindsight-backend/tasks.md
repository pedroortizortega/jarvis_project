# Tasks: Hindsight Backend Adapter

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~550-650 (adapter ~180, tests ~250, spec doc ~130, 2x 1-line diffs) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 (adapter + tests + registration + engram narrowing) → PR 2 (spec doc + final verification) |
| Delivery strategy | ask-on-risk |
| Chain strategy | feature-branch-chain |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: feature-branch-chain
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | `HindsightBackend` adapter, stubbed-transport tests, entry-point registration, Engram namespace narrowing | PR 1 (base: `feat/hindsight-backend`) | `python -m unittest tests.test_memory_router_hindsight_adapter -v` | N/A — no live Hindsight instance; unit-level proof only, per design.md Testing Strategy | Delete `backends/hindsight.py`, its test file, revert the `pyproject.toml` entry-point line and the `engram.py` namespace line |
| 2 | `specs/015_hindsight_backend.md` numbered companion + final router-core-untouched verification | PR 2 (base: PR 1 branch) | N/A — doc only | `git diff --stat -- app.py registry.py contracts.py permissions.py namespaces.py identity.py journal.py` (expect empty) | Delete `specs/015_hindsight_backend.md` |

## Phase 1: Config & Skeleton

- [x] 1.1 RED: `tests/test_memory_router_hindsight_adapter.py` — zero-arg construction, env-var config resolution (`HINDSIGHT_BASE_URL`, `HINDSIGHT_AUTH_MODE`, `HINDSIGHT_TOKEN`, `HINDSIGHT_BANK_PREFIX`, `HINDSIGHT_TIMEOUT_SECONDS`) fail (module doesn't exist).
- [x] 1.2 GREEN: create `backends/hindsight.py` — `HindsightBackend.__init__(*, transport=None, base_url=None, auth_mode=None, token=None, bank_prefix=None, timeout=None)` with env-default resolution mirroring `engram.py`.
- [x] 1.3 REFACTOR: extract env-default resolution into a small pure helper.

## Phase 2: Transport & Namespace Mapping

- [x] 2.1 RED: `_HttpJsonClient` construction test; `_bank_id(namespace)` sanitizer tests (flatten `/projects/lector-ine` → `projects-lector-ine`; traversal/wildcard input never yields an illegal path segment, re-validated against `^[a-z0-9][a-z0-9_-]*$`).
- [x] 2.2 GREEN: implement `_HttpJsonClient` (stdlib `urllib.request`, injectable `transport(method,url,headers,body)->(status,bytes)`), `_bank_id()`, `ENDPOINTS` dict.
- [x] 2.3 REFACTOR: isolate `ENDPOINTS` as the single revisable wire-format surface.

## Phase 3: Store / Search / Health

- [x] 3.1 RED: `store()`/`search()` round-trip via stubbed transport (retain→`StoreResult("committed","hindsight",id)`; recall→`SearchHit(namespace,backend="hindsight",...)`).
- [x] 3.2 GREEN: implement `store()`/`search()`.
- [x] 3.3 RED: lazy create-on-404 test (retain 404 → POST create → retry retain once → success).
- [x] 3.4 GREEN: implement create-then-retry-once.
- [x] 3.5 RED: `health()` — 2xx→OK, else→DOWN(reason), never raises.
- [x] 3.6 GREEN: implement `health()`.

## Phase 4: Auth & Security

- [x] 4.1 RED: `Authorization` header present (`Bearer <token>`) when `HINDSIGHT_AUTH_MODE=bearer`/token set, absent when `none`.
- [x] 4.2 GREEN: implement auth-mode header injection (default `bearer` iff token non-empty). (already satisfied by `_client()` from Phase 3; tests pass immediately — confirmed, no additional code needed.)
- [x] 4.3 RED: security test — malicious `content`/`metadata` in `StoreRequest`/`SearchRequest` never appear in URL or headers; header key set is fixed.
- [x] 4.4 REFACTOR: confirm `_HttpJsonClient` call sites pass only config+sanitized bank id into URL/headers. (verified: URL built only from `self._base_url` + `ENDPOINTS[...].format(bank_id=self._bank_id(...))`; headers built only from `self._auth_mode`/`self._token`; `req.content`/`req.metadata`/`req.query` only ever go into the JSON body.)

## Phase 5: Degradation & Secrets

- [x] 5.1 RED: connection error, non-2xx status, malformed JSON response each raise `BackendUnavailableError("hindsight", ...)` and nothing else (3 cases).
- [x] 5.2 GREEN: implement error mapping in `_HttpJsonClient`/`store`/`search`. (already satisfied by Phase 3 implementation; all 4 cases pass immediately, confirmed.)
- [x] 5.3 RED: token never appears inside a raised `BackendUnavailableError.reason` string.
- [x] 5.4 REFACTOR: confirm reason strings never interpolate `self._token`. (verified: all `BackendUnavailableError`/`Health(reason=...)` construction sites in `hindsight.py` interpolate only `status`, `exc`, or `_decode_error_reason(raw)` — never `self._token`.)

## Phase 6: Protocol Conformance & Namespace Non-Overlap

- [x] 6.1 RED: `isinstance(HindsightBackend(), MemoryBackend)` passes (runtime-checkable Protocol).
- [x] 6.2 RED: `capabilities().verbs == {"store","search"}` and `"reflect" not in verbs`.
- [x] 6.3 RED: `HindsightBackend().capabilities().namespaces` and `EngramBackend().capabilities().namespaces` share zero entries.
- [x] 6.4 GREEN: finalize `capabilities(namespaces=("/projects/*",))`. (already the value set in Phase 1/2; 6.3 GREEN achieved via Phase 7.1's Engram narrowing.)

## Phase 7: Engram Narrowing & Registration

- [x] 7.1 GREEN: `backends/engram.py` — narrow `namespaces` tuple to `("/global", "/user/master", "/agents/*")` (drop `/projects/*`); confirms 6.3.
- [x] 7.2 GREEN: `pyproject.toml` — add `hindsight = "memory_router.backends.hindsight:HindsightBackend"` under `[project.entry-points."memory_router.backends"]`.
- [x] 7.3 Manual verify: reinstall package (`pip install -e hermes-native/memory-router`), confirm `Registry().all_backends()` returns two adapters. (verified in isolated venv `/tmp/claude-1000/hb-verify-venv`: `Registry().all_backends()` → `['engram', 'hindsight']`.)

## Phase 8: Docs & Final Verification

- [x] 8.1 Write `specs/015_hindsight_backend.md`, numbered companion following `specs/014_memory_router.md`'s format/language.
- [x] 8.2 Run full suite: `python -m unittest discover -s tests`. Result: `Ran 121 tests in 5.016s — OK` (was 90 before this change; +31 new Hindsight adapter tests).
- [x] 8.3 Verify `git diff --stat -- app.py registry.py contracts.py permissions.py namespaces.py identity.py journal.py` is empty (zero router-core edits). Confirmed: command produced no output.
