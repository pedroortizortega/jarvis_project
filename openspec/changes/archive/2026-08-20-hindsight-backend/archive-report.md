# Archive Report: Hindsight Backend Adapter

**Change**: `hindsight-backend`
**Archived**: 2026-08-20
**Status**: COMPLETE
**Mode**: hybrid (OpenSpec filesystem + Engram persistence)

## Executive Summary

Hindsight Backend Adapter has been successfully completed, implemented, verified, and archived. Phase 1's claim that "adding a second backend requires only a new adapter plus registration — no router change" has been proven true. The `HindsightBackend` adapter over HTTP transport has been fully delivered with all 29 tasks checked off, 121/121 tests passing (31 new Hindsight adapter tests added to the existing 90), and sdd-verify confirming PASS (0 CRITICAL, 0 WARNING, 1 non-blocking SUGGESTION). The delta spec for `memory-backend-adapters` has been synced to main specs, promoting the "exactly one adapter" requirement to "multi-adapter support" and adding five new requirements specific to Hindsight. All change artifacts have been archived with audit trail intact.

## Implementation Status

### Scope Delivered: Hindsight Backend Adapter

| Component | Status | Details |
|-----------|--------|---------|
| `HindsightBackend` adapter class | Complete | Implements `MemoryBackend` Protocol; default-constructible zero-arg constructor; injectable transport for testing |
| HTTP transport client (`_HttpJsonClient`) | Complete | Stdlib `urllib.request`; namespace→`bank_id` flattening; lazy bank creation on 404; no new dependencies |
| Config-driven auth | Complete | Environment variables `HINDSIGHT_AUTH_MODE` (none/bearer), `HINDSIGHT_TOKEN`, `HINDSIGHT_BASE_URL`, `HINDSIGHT_BANK_PREFIX`, `HINDSIGHT_TIMEOUT_SECONDS` |
| Declared verbs | Complete | `frozenset({"store", "search"})`; `"reflect"` explicitly excluded and asserted in tests |
| Namespace-to-bank mapping | Complete | `/projects/*` ownership (non-overlapping with narrowed Engram `/global`, `/user/master`, `/agents/*`) |
| Degraded-backend integration | Complete | HTTP failure → `BackendUnavailableError("hindsight", ...)` → existing dispatcher (pending store, partial search) |
| Entry-point registration | Complete | One line in `pyproject.toml` under `[project.entry-points."memory_router.backends"]` |
| Unit test suite | Complete | 31 new tests (phase-structured RED/GREEN/REFACTOR); all passing |
| Router-core changes | Verified empty | Zero edits to `app.py`, `registry.py`, `contracts.py`, `permissions.py`, `namespaces.py`, `identity.py`, `journal.py` |
| Engram narrowing | Complete | `engram.py` narrowed to `("/global", "/user/master", "/agents/*")`; frees `/projects/*` for Hindsight |
| Numbered spec companion | Complete | `specs/015_hindsight_backend.md` |
| Test suite | Complete | 121/121 unit tests passing (was 90 before; +31 new Hindsight adapter tests) |

### Scope Explicitly Deferred

| Item | Reason | Next Phase / Reference |
|------|--------|------------------------|
| Live Hindsight instance integration | Out of scope per proposal; no instance exists | Explicit follow-up: validate `ENDPOINTS` dict and payload schema against live Hindsight |
| Kubernetes deployment | No live instance to deploy against; design open question unresolved | Deferred to operational phase after integration validation |
| `/memory/reflect` wiring | Out of scope per proposal; needs product/security decisions; would require router-core changes | Future phase if product decides to expose reflect semantics end-to-end |
| Cross-backend merge, ranking, dedup, fan-out | Out of scope per proposal | Future phases after both adapters are production-validated |

## Artifacts

### Delta Specs (Merged to Main)

One capability specification modified and extended; merged to `openspec/specs/` (primary source of truth):

| Domain | File | Action | Details |
|--------|------|--------|---------|
| `memory-backend-adapters` | `openspec/specs/memory-backend-adapters/spec.md` | Modified + Extended | "Phase 1 Engram Adapter" renamed to "Multi-Adapter Backend Support" (1 modified requirement); 5 new requirements added (Hindsight Adapter, Hindsight Declared Verbs Exclude Reflect, Hindsight Namespace-to-Bank Mapping Without Cross-Backend Overlap, Hindsight Config-Driven Auth, Hindsight Transport Failure Integrates With Degraded-Backend Handling) |

All specs contain requirements backed by scenario-driven test coverage per the SDD phase work.

### Archive Folder Contents

All original SDD artifacts archived to `openspec/changes/archive/2026-08-20-hindsight-backend/`:

- `proposal.md` ✅ (intent, scope, capabilities, approach, risk assessment, rollback, dependencies, success criteria)
- `design.md` ✅ (technical approach, namespace ownership, architecture decisions, config surface, interfaces, data flow, file changes, testing strategy, threat matrix, migration/rollout, open questions)
- `tasks.md` ✅ (8 phases, 29 tasks total, all `[x]` checked)
- `specs/` ✅ (1 domain spec delta, merged to main, copied for audit trail)

## Task Completion

### Final State: 29/29 Tasks Complete

| Phase | Count | Status |
|-------|-------|--------|
| Phase 1: Config & Skeleton | 3 | ✅ Complete |
| Phase 2: Transport & Namespace Mapping | 3 | ✅ Complete |
| Phase 3: Store / Search / Health | 6 | ✅ Complete |
| Phase 4: Auth & Security | 4 | ✅ Complete |
| Phase 5: Degradation & Secrets | 4 | ✅ Complete |
| Phase 6: Protocol Conformance & Namespace Non-Overlap | 4 | ✅ Complete |
| Phase 7: Engram Narrowing & Registration | 3 | ✅ Complete |
| Phase 8: Docs & Final Verification | 3 | ✅ Complete |
| **TOTAL** | **29** | ✅ **COMPLETE** |

All implementation tasks verified by orchestrator as complete post-apply. No stale unchecked tasks remain in archived `tasks.md`.

## Verification and Testing

### Test Evidence (Final State)

- **Unit test suite**: 121/121 passing (`python -m unittest discover -s tests`)
  - Baseline (Memory Router Phase 1): 90 tests
  - New Hindsight adapter tests: 31 tests
  - Delta: +31 tests
- **Test structure**: Phase-structured RED/GREEN/REFACTOR across 8 phases
  - Phase 1 (Config & Skeleton): zero-arg construction, env-var resolution
  - Phase 2 (Transport & Namespace Mapping): `_HttpJsonClient`, `_bank_id()` sanitizer, traversal/wildcard protection
  - Phase 3 (Store / Search / Health): retain/recall round-trip, lazy create-on-404, health endpoint
  - Phase 4 (Auth & Security): Authorization header presence/absence per mode, malicious payload never reaches URL/headers
  - Phase 5 (Degradation & Secrets): all three failure modes raise `BackendUnavailableError`, token never logged
  - Phase 6 (Protocol Conformance & Namespace Non-Overlap): `isinstance` check, verbs assertion, namespace non-overlap verification
  - Phase 7 (Engram Narrowing & Registration): entry-point registration verified, two-adapter discovery confirmed
  - Phase 8 (Docs & Final Verification): spec companion written, full suite passing, zero router-core changes confirmed
- **sdd-verify final verdict**: **PASS**
  - Critical issues: 0
  - Warnings: 0
  - Suggestions: 1 (non-blocking; addressed)

### Verification Report State

Per sdd-verify final report (per launch prompt final-state facts):
- All 29 implementation tasks verified complete
- 121 tests passing (31 new Hindsight tests + 90 existing Memory Router tests)
- Zero router-core edits confirmed
- Entry-point registration verified (two adapters discovered: `['engram', 'hindsight']`)
- Engram namespace narrowing verified (`/projects/*` released to Hindsight)
- No CRITICAL or blocking issues identified

## Acceptance Criteria Verification

### Per Proposal Success Criteria

| Criterion | Evidence | Status |
|-----------|----------|--------|
| `HindsightBackend` satisfies `MemoryBackend` Protocol (`isinstance` check passes) | Phase 6.1 RED test | ✅ Pass |
| `capabilities().verbs == {"store","search"}` and `"reflect" not in verbs` | Phase 6.2 RED test | ✅ Pass |
| `Registry` discovers two backends via entry points; zero changes to `registry.py` | Phase 7.3 manual verify; 7.2 entry-point registration | ✅ Pass |
| `store` / `search` round-trip with stubbed HTTP transport, namespace → `bank_id` mapping | Phase 3.1-3.4 RED/GREEN tests | ✅ Pass |
| Transport failure raises `BackendUnavailableError` → pending-store / partial-search via existing dispatcher | Phase 5.1-5.2 tests; Phase 3 tests | ✅ Pass |
| `git diff` shows zero modification to router core files | Phase 8.3 verification | ✅ Pass |
| Auth mode selectable by config (no-auth and bearer token) | Phase 4.1-4.2 tests; design config surface | ✅ Pass |

## Architecture Decisions Confirmed

### Design Choices Validated by Implementation

| Decision | Design Rationale | Implementation Outcome |
|----------|------------------|------------------------|
| HTTP client via stdlib `urllib.request` | Zero new dependencies; proposal preference stated | ✅ Implemented; no additional imports beyond stdlib |
| Namespace → bank flattening (`/projects/lector-ine` → `projects-lector-ine`) | Human-debuggable; injective over owned root | ✅ Implemented; re-validated against `^[a-z0-9][a-z0-9_-]*$` |
| Lazy bank auto-create on 404 | Simpler than pre-provisioning | ✅ Implemented; create-then-retry-once logic tested |
| `frozenset({"store","search"})` verbs only | `reflect` out of scope; avoids wiring burden | ✅ Implemented; test asserts absence |
| Engram narrowed to `("/global", "/user/master", "/agents/*")` | Only way to achieve non-overlap; undeployed router has no migrations | ✅ Implemented; zero migrations needed |
| Zero router-core edits | Acceptance criterion; proves plugin seam | ✅ Verified; `git diff --stat` on core files is empty |
| Entry-point registration only | Registry already supports dynamic loading | ✅ Verified; one `pyproject.toml` line sufficient |

### No Rearchitecting Required

The `MemoryBackend` Protocol and entry-point seam proved adequate without modification. This confirms:
- The protocol is not Engram-shaped or transport-specific
- The registry's plugin loader is general-purpose and requires no changes
- The dispatcher's degraded-backend handling (pending store, partial search) integrates new adapters unchanged
- Future backends (Graphiti, Honcho, Cognee, Obsidian) can follow the same pattern

## Known Deferred Work (Not a Blocker)

### Live Hindsight Validation (Explicit Follow-Up)

**Status**: Not performed — no live instance available.

The `ENDPOINTS` dictionary documents the assumed Hindsight wire format:

```python
ENDPOINTS = {
    "retain": "/v1/banks/{bank_id}/retain",
    "recall": "/v1/banks/{bank_id}/recall",
    "create": "/v1/banks",
    "health": "/health",
}
```

**Before any production deployment**, this must be validated against:
1. Authoritative Hindsight API documentation
2. A live Hindsight instance (if available)
3. Request/response payload schemas (especially `recall` score normalization)

**Impact**: Until validated, treat the wire format as revisable. The adapter is structured to isolate this surface in one class (`_HttpJsonClient`), making schema updates straightforward.

**Path Forward**: A follow-up integration task will confirm or update `ENDPOINTS` and related payload handling.

## Git State

- **Branch**: `feat/hindsight-backend` (all work committed)
- **Base**: all commits on top of `main`
- **Push state**: nothing pushed, no PR opened
- **Git user**: pedro

The orchestrator will handle PR and delivery strategy separately. The change is ready for review and delivery.

## Specification Summary

### Main Specs Now Updated

One capability is now extended in `openspec/specs/`:

1. **`memory-backend-adapters`** (modified + extended):
   - **Modified requirement**: "Phase 1 Engram Adapter" → "Multi-Adapter Backend Support"
     - Engram remains the reference implementation
     - Hindsight added as a second production adapter
     - Both register through `memory_router.backends` entry-point group
     - Each must be default-constructible (zero required arguments)
   - **New requirements** (5 total):
     1. **Hindsight Adapter**: HTTP transport to Hindsight (not stdio subprocess)
     2. **Hindsight Declared Verbs Exclude Reflect**: `verbs == {"store", "search"}`; no `reflect`
     3. **Hindsight Namespace-to-Bank Mapping Without Cross-Backend Overlap**: `/projects/*` ownership; zero overlap with narrowed Engram
     4. **Hindsight Config-Driven Auth**: Bearer token (Hindsight Cloud) or no-auth (local) via env vars
     5. **Hindsight Transport Failure Integrates With Degraded-Backend Handling**: HTTP failure → `BackendUnavailableError("hindsight", ...)` → existing dispatcher behavior

All requirements in these specs are backed by scenario-driven tests (121/121 passing).

### Phase 2 Architecture Characteristics

- **Multi-adapter support**: Proven by entry-point plugin seam; both Engram and Hindsight discovered and loaded dynamically
- **Transport variety**: Engram (stdio subprocess), Hindsight (HTTP); no transport assumption in protocol or router core
- **Namespace isolation**: Engram (`/global`, `/user/master`, `/agents/*`) + Hindsight (`/projects/*`) = complete coverage, zero overlap
- **Auth flexibility**: Each adapter sources config independently from environment; no hardcoded mode
- **Degraded semantics**: Both adapters use existing dispatcher behavior (pending store, partial search); no new handling required
- **No core changes**: Registry, contracts, app dispatcher, permissions, journal, identity, namespaces all unchanged; plugin seam proven

## Authority and Traceability

This archive report is the terminal record of the Hindsight Backend Adapter SDD cycle per the Final-State Authority hierarchy:

- **Native review authority**: None yet (delivery strategy decision pending)
- **Persisted tasks artifact**: `openspec/changes/archive/2026-08-20-hindsight-backend/tasks.md` — all 29 tasks `[x]` checked
- **Explicit final-state facts from launch prompt**: incorporated above (29 tasks, 121/121 tests, PASS verify with 1 non-blocking suggestion, zero router-core edits, entry-point registration verified)
- **Intermediate snapshots** (verify-report, apply-progress): superseded by launch-prompt facts and tasks artifact

**Rules applied**:
- Only the higher-ranked sources (tasks artifact + launch prompt facts) are reported as final state
- The live Hindsight validation deferral (intentional Phase 2 scope, no instance available) is documented as an explicit follow-up, not a regression
- The protocol and entry-point seam proof (core acceptance criterion) is prominently documented as successful validation

## Next Steps (User Decision)

1. **PR delivery strategy** (user decision, not automatic per proposal): The tasks artifact forecasts ~550-650 changed lines (High risk, recommended chained/stacked PRs). Awaiting user delivery strategy decision.
2. **Live Hindsight validation** (integration follow-up): Confirm `ENDPOINTS` schema and payload shapes against a live instance or authoritative docs before production deployment.
3. **Backends 3–5** (separate SDD changes): Graphiti, Honcho, Cognee, Obsidian each as separate changes, reusing the proven adapter contract and plugin seam.

## Compliance

- ✅ All implementation tasks complete and verified (29/29)
- ✅ All 121 unit tests passing (90 baseline + 31 new Hindsight tests)
- ✅ sdd-verify PASS (0 CRITICAL, 0 WARNING, 1 non-blocking suggestion)
- ✅ Delta spec merged to main `openspec/specs/` source of truth
- ✅ All change artifacts moved to archive with date prefix
- ✅ Archive folder contains complete audit trail (proposal, design, tasks, specs)
- ✅ Plugin seam proven: two adapters registered via entry points, zero router-core edits
- ✅ Namespace non-overlap verified: Engram narrowed, Hindsight owns `/projects/*`
- ✅ Entry-point registration validated: `Registry().all_backends()` returns `['engram', 'hindsight']`
- ✅ Phase 2 scope closed; live validation and future backends documented as follow-up work
- ✅ No blockers; ready for PR and delivery

The Hindsight Backend Adapter SDD cycle is COMPLETE and ARCHIVED.
