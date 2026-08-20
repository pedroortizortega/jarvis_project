# Archive Report: Knowledge-Vault Backend Adapter (Search on `/global`)

**Change**: `obsidian-backend`
**Archived**: 2026-08-20
**Status**: COMPLETE
**Mode**: hybrid (OpenSpec filesystem + Engram persistence)

## Executive Summary

Knowledge-Vault Backend Adapter has been successfully completed, implemented, verified, and archived. The search-only backend has been operationalized via a KnowledgeVaultBackend adapter, adding read-only HTTP search capability to the `/global` namespace alongside Engram. All 11 phases with implementation tasks are checked off (11.1–11.4 final verification complete), 363/363 tests passing (250 router + 113 host), and sdd-verify confirms PASS (0 CRITICAL, 0 WARNING, 0 SUGGESTION). The three delta specs for `knowledge-vault-search-bridge` (NEW), `memory-router-interfaces`, and `memory-backend-adapters` have been synced to main specs, adding the search-only HTTP bridge specification and establishing SearchOnlyBackend as a first-class contract separate from MemoryBackend. All change artifacts have been archived with complete audit trail intact.

## Implementation Status

### Scope Delivered: Knowledge-Vault Backend Adapter

| Component | Status | Details |
|-----------|--------|---------|
| `KnowledgeVaultBackend` adapter class | Complete | Implements `SearchOnlyBackend`; HTTP transport via stdlib `urllib.request`; default-constructible zero-arg constructor |
| `SearchOnlyBackend` Protocol | Complete | New capability-gated contract with `capabilities()`/`health()`/`search()` only; `store()` deliberately absent |
| HTTP transport client (`_HttpJsonClient`) | Complete | Bearer token auth; config-driven via environment; injectable transport for testing |
| Config-driven auth | Complete | Environment variables `KNOWLEDGE_VAULT_BASE_URL`, `KNOWLEDGE_VAULT_AUTH_MODE`, `KNOWLEDGE_VAULT_TOKEN`, `KNOWLEDGE_VAULT_LIMIT`, `KNOWLEDGE_VAULT_TIMEOUT_SECONDS` |
| Declared verbs | Complete | `frozenset({"search"})` exactly; `"store"`/`"reflect"` explicitly excluded and asserted in tests |
| Namespace declaration | Complete | `("/global",)` only; search on `/projects/*`, `/agents/*`, `/user/master` selects no knowledge-vault backend |
| Score bearing hits | Complete | `SearchHit.score` carries relevance from bridge; `backend="knowledge-vault"` distinguishes from Engram |
| Degraded-backend integration | Complete | HTTP failure → `BackendUnavailableError("knowledge-vault", ...)` → existing dispatcher degradation semantics |
| Entry-point registration | Complete | One line in `pyproject.toml` under `[project.entry-points."memory_router.backends"]` |
| `Dispatcher.search()` fan-out | Complete | Engram and knowledge-vault coexist on `/global`; hits merged by existing dispatcher; zero core changes required |
| Host-side HTTP bridge | Complete | `serve.py` ThreadingHTTPServer with bearer auth, bounded rebuild (timeout D-02), read-only mount |
| VaultHit score field | Complete | `VaultHit.score` added to `search.py`; populated from `RetrievalHit.score` (D-01) |
| Systemd unit | Complete | `knowledge-vault-search.service` with `ReadOnlyPaths=` vault/index, no `ReadWritePaths=`, `LoadCredential=`, `After=cni0.device` (D-06) |
| Knowledge-vault search bridge spec | NEW | `openspec/specs/knowledge-vault-search-bridge/spec.md` created (NEW capability; no existing main spec) |
| Unit test suite host | Complete | 113 tests (host package) all passing |
| Unit test suite router | Complete | 250 tests (router package) all passing |
| Router-core changes | Verified minimal | Only `contracts.py` (SearchOnlyBackend Protocol); `app.py`, `registry.py`, `permissions.py` byte-unmodified per test assertions |
| Numbered spec companion | Complete | `specs/018_knowledge_vault_backend.md` |

### Scope Explicitly Deferred

| Item | Reason | Next Phase / Reference |
|------|--------|------------------------|
| Live knowledge-vault search service deployment | Out of scope; memory-router itself undeployed | Operational follow-up: deploy service to host, verify reachability from k8s pod at 10.42.0.1:8088 |
| Score normalization across backends | Deliberately deferred; scores not on common scale | Future change if caller-side ranking becomes critical |
| Reflect or store for knowledge-vault | Out of scope; read-only search only | Future phase if vault write access is needed (stays behind publish pipeline) |
| Search on namespaces other than `/global` | Out of scope; Phase 1 is `/global`-only | Future phase if product expands to vault-per-project model |

## Artifacts

### Delta Specs (Merged to Main)

Three capability specifications created and extended; all merged to `openspec/specs/` (primary source of truth):

| Domain | File | Action | Details |
|--------|------|--------|---------|
| `knowledge-vault-search-bridge` | `openspec/specs/knowledge-vault-search-bridge/spec.md` | NEW | New capability: read-only HTTP search surface on host exposing `search_vault()` over bearer-authenticated `/search` and `/healthz`. 6 requirements: Search-Only HTTP Surface, Bearer-Token Authentication, Local-Interface Binding, Read-Only Vault and Index Mount, Bounded Inline Index Rebuild, Search Response Shape. All scenario-driven. |
| `memory-router-interfaces` | `openspec/specs/memory-router-interfaces/spec.md` | Modified + Extended | 1 new requirement added: "Search-Only Backend Contract Is Separate From MemoryBackend". Establishes `SearchOnlyBackend` Protocol (capabilities/health/search) as first-class contract mirroring `ReflectiveBackend` precedent. `MemoryBackend` unchanged; dispatcher selects only via `capabilities().verbs` gating. |
| `memory-backend-adapters` | `openspec/specs/memory-backend-adapters/spec.md` | Modified + Extended | 1 MODIFIED requirement: "Degraded Backend — Search Returns Partial Results" now explicitly includes knowledge-vault alongside Engram/Hindsight. 4 new requirements: (1) "Knowledge-Vault Adapter" with HTTP transport, config-driven bearer auth, `/global`-only namespaces, search-only verbs; (2) "Knowledge-Vault Transport Failure Integrates With Degraded-Backend Handling" for HTTP failures; (3) "Knowledge-Vault Empty or Unavailable Index Never Fabricates Hits"; (4) "Knowledge-Vault Hits Are Attributed and Score-Bearing" with backend attribution and score preservation. |

All specs contain requirements backed by scenario-driven test coverage per the SDD phase work (363/363 tests passing).

### Archive Folder Contents

All original SDD artifacts archived to `openspec/changes/archive/2026-08-20-obsidian-backend/`:

- `proposal.md` ✅ (intent, scope, capabilities, approach, affected areas, risks, rollback, dependencies, success criteria, proposal questions)
- `design.md` ✅ (technical approach, verified findings, architecture decisions D-01 through D-08, interfaces/contracts, config surface, data flow, file changes, testing strategy, threat matrix, migration/rollout, open questions)
- `tasks.md` ✅ (11 phases, all implementation tasks checked; workload forecast; final verification complete)
- `specs/` ✅ (3 domain spec sources: knowledge-vault-search-bridge NEW, memory-router-interfaces merged, memory-backend-adapters merged; all copies are final merged state for audit trail)

## Task Completion

### Final State: All Phases Complete

| Phase | Count | Status |
|-------|-------|--------|
| Phase 1: Host — VaultHit score field (D-01) | 3 | ✅ Complete |
| Phase 2: Host — serve.py HTTP surface | 9 | ✅ Complete |
| Phase 3: Host — systemd unit | 2 | ✅ Complete |
| Phase 4: Router — contracts.py Protocol | 2 | ✅ Complete |
| Phase 5: Router — backends/knowledge_vault.py | 9 | ✅ Complete |
| Phase 6: Router — entry point | 1 | ✅ Complete |
| Phase 7: Cross-cutting — coexistence (headline test) | 3 | ✅ Complete |
| Phase 8: Deployment artifact | 1 | ✅ Complete |
| Phase 9: Docs | 1 | ✅ Complete |
| Phase 10: Spec companion | 1 | ✅ Complete |
| Phase 11: Final verification | 4 | ✅ Complete |
| **TOTAL** | **36** | ✅ **COMPLETE** |

All implementation tasks verified by orchestrator as complete post-apply. No stale unchecked tasks remain in archived `tasks.md`.

## Verification and Testing

### Test Evidence (Final State)

- **Unit test suite host**: 113/113 passing (`python -m unittest discover -s tests -v` in hermes-native/knowledge-vault)
- **Unit test suite router**: 250/250 passing (`python -m unittest discover -s tests` in hermes-native/memory-router)
- **Total**: 363/363 tests passing (baseline pre-existing + new knowledge-vault tests)
- **Test structure**: Phase-structured RED/GREEN/REFACTOR across 11 phases
  - Phase 1 (Host score field): RED/GREEN/REFACTOR on `VaultHit.score` field population
  - Phase 2 (Host HTTP surface): Bearer auth rejection, route table limits, response shape with non-zero score, bounded rebuild timeout, empty vault honest response, read-only verification
  - Phase 3 (Host systemd): Unit file structure, hardening, environment script
  - Phase 4 (Router Protocol): `SearchOnlyBackend` mechanics, `MemoryBackend` byte-diff
  - Phase 5 (Router adapter): Capabilities exact equality, namespace isolation, round-trip with score, error handling, secret absence, outbound construction defense
  - Phase 6 (Router entry-point): Registration exercise
  - Phase 7 (Coexistence): Registry injection with fake Engram, dispatcher fan-out to both backends, Engram tests regression pass
  - Phase 8 (k8s manifest): Service + EndpointSlice creation validation
  - Phase 9 (Docs): Knowledge-vault.md update
  - Phase 10 (Spec): 018_knowledge_vault_backend.md presence
  - Phase 11 (Final): Full suite green, `app.py`/`registry.py`/`permissions.py` byte-unmodified assertions, coexistence verification, no secret substring assertions
- **sdd-verify final verdict**: **PASS**
  - Critical issues: 0
  - Warnings: 0
  - Suggestions: 0

### Verification Report State

Per sdd-verify final report (per launch prompt final-state facts):
- All implementation tasks verified complete per tasks.md checkboxes
- 363 tests passing (113 host + 250 router)
- Zero router-core file edits beyond `contracts.py` (SearchOnlyBackend addition only)
- `app.py`, `registry.py`, `permissions.py` byte-unmodified (search fan-out and registry already support non-MemoryBackend)
- No CRITICAL, WARNING, or blocking issues identified

## Acceptance Criteria Verification

### Per Proposal Success Criteria

| Criterion | Evidence | Status |
|-----------|----------|--------|
| `KnowledgeVaultBackend.capabilities().verbs == {"search"}` and `"store"`/`"reflect"` absent | Phase 5.1 capabilities test + Phase 11.4 exact equality assertion | ✅ Pass |
| `capabilities().namespaces == ("/global",)` exactly; search on `/projects/x`/`/agents/x`/`/user/master` selects no backend | Phase 5.2 + Phase 7 registry injection tests | ✅ Pass |
| `isinstance(backend, SearchOnlyBackend)` true; `isinstance(backend, MemoryBackend)` false | Phase 4 protocol mechanics test | ✅ Pass |
| `/global` search selects both Engram and knowledge-vault; hits merged; existing Engram tests pass unmodified | Phase 7.1–7.3 coexistence tests | ✅ Pass |
| `app.py`, `registry.py`, `permissions.py` byte-identical | Phase 11.3 diff verification | ✅ Pass |
| Empty vault or unavailable index yields zero hits with no error | Phase 2.7 + Phase 5 tests | ✅ Pass |
| Transport failure raises `BackendUnavailableError` and surfaces as degraded | Phase 5.5 + Phase 7 degradation tests | ✅ Pass |
| HTTP surface exposes no write verb; unauthenticated request rejected | Phase 2.2 + Phase 2.1 tests | ✅ Pass |
| Each `SearchHit` carries `backend="knowledge-vault"` and non-zero score | Phase 5.4 round-trip test | ✅ Pass |

## Architecture Decisions Confirmed

### Design Choices Validated by Implementation

| Decision | Design Rationale | Implementation Outcome |
|----------|------------------|------------------------|
| Score surfacing via `VaultHit.score` field (D-01) | Alternative calling `Retriever` directly would duplicate five behaviors (MIN_RELEVANCE filter, dedupe, title lookup, excerpt truncation, retry); field is backward compatible | ✅ Implemented; field defaulted to 0.0, only populated on successful search; existing CLI path unaffected |
| Thread pool executor for bounded rebuild (D-02) | In-loop deadline would require editing `build_index`; subprocess adds fork cost on every query; thread allows self-healing on timeout | ✅ Implemented; `ThreadPoolExecutor` + single-flight lock ensures at most one rebuild concurrent; timeout set per config |
| 503 on rebuild timeout (D-03) | `200` empty is indistinguishable from "corpus has no answer"; 503 maps to dispatcher's `BackendUnavailableError` degradation | ✅ Implemented; timeout yields 503 with reason `index_rebuild_timeout` |
| Embed note id in content (D-04) | `SearchHit` dataclass has no metadata field; extending it would cascade to all backends; embedding in content preserves citable id | ✅ Implemented; `content = f"{note} — {title}\n{excerpt}"` puts id on first line |
| Namespace guard with `BackendUnavailableError` (D-05) | No mapper needed (one fixed namespace); `ValueError` would escape unhandled; `BackendUnavailableError` routes to degradation | ✅ Implemented; guard is two lines raising `BackendUnavailableError` on non-`/global` request |
| Bind to 10.42.0.1:8088 (D-06) | Tightest address reachable only from pods on this node; not LAN IP (unpublishes knowledge to 192.168.100.0/24); not loopback (unreachable from pod via cni0) | ✅ Implemented; unit configuration + selector-less Service+EndpointSlice; manifest shows explicit coupling comment for future mTLS trigger |
| `urllib.request` (D-07) | Zero new dependencies; mirrors Honcho/Cognee/Hindsight pattern; transport seam handles testing | ✅ Implemented; no new imports beyond stdlib |
| `SearchOnlyBackend` only, not extending `MemoryBackend` (D-08) | Adapter declares `verbs={"search"}` alone; `isinstance(backend, MemoryBackend)` returns `False` and is asserted; prevents accidental store selection | ✅ Implemented; class structure proof; `MemoryBackend` untouched |

### No Rearchitecting Required

The existing dispatcher search fan-out and registry verb-based selection proved adequate without further modifications. This confirms:
- The dispatcher's `Dispatcher.search()` already iterates all backends and merges hits
- The registry's `backends_for(verb="search", namespace=...)` already filters on capabilities
- The degradation handler (`BackendUnavailableError`) already integrates new adapters
- Future search-only backends can reuse the `SearchOnlyBackend` contract identically

## Known Deferred Work (Not a Blocker)

### Live Service Deployment (Operational Follow-Up)

**Status**: Not performed — memory-router itself is undeployed; service structure (host-side wrapper) is untested on live instance.

The adapter and host-side HTTP bridge are complete and tested via stubbed transport and in-process host handlers. Before any production deployment of memory-router:

1. Deploy `knowledge-vault-search.service` to `trantor` host
2. Verify reachability from in-cluster pod at `10.42.0.1:8088` (D-06 binding)
3. Confirm bearer token is supplied correctly via `LoadCredential=` systemd mechanism
4. Load-test rebuild timeout behavior under real vault size

**Impact**: The service will report unavailable via `BackendUnavailableError` until deployment; search still succeeds via Engram alone (degraded, not broken).

### Score Normalization (Explicitly Deferred)

**Status**: Out of scope per proposal.

Engram scores and vault lexical/semantic scores are not on a common scale. `SearchHit.backend` discriminator allows callers to distinguish sources; normalizing scores is a future caller-side or backend-coordination concern.

### Write Access and Reflect (Explicitly Deferred)

Vault writes stay behind the existing propose/review/approve/publish pipeline. Reflect is out of scope for this backend (search-only).

## Git State

- **Branch**: `feat/obsidian-backend` (all work committed)
- **Base**: all commits on top of `main`
- **Push state**: nothing pushed, no PR opened
- **Git user**: pedro

The orchestrator will handle PR and delivery strategy separately. The change is ready for review and delivery.

## Specification Summary

### Main Specs Now Updated

Three capabilities created/extended in `openspec/specs/`:

1. **`knowledge-vault-search-bridge`** (NEW):
   - **6 requirements**: Search-Only HTTP Surface, Bearer-Token Authentication, Local-Interface Binding, Read-Only Vault and Index Mount, Bounded Inline Index Rebuild, Search Response Shape
   - Scenario-driven; covers host-side HTTP wrapper surface including auth, timeout, and read-only guarantees

2. **`memory-router-interfaces`** (1 new requirement):
   - **New requirement**: "Search-Only Backend Contract Is Separate From MemoryBackend"
     - Introduces `SearchOnlyBackend` Protocol (capabilities/health/search)
     - Mirrors `ReflectiveBackend` precedent; `MemoryBackend` unchanged
     - Dispatcher gates via `capabilities().verbs` containing `"search"`
     - `isinstance` checks prevent accidental store selection
   - **4 scenarios**: Protocol distinction, adapter conformance, existing adapter coexistence, registry gating

3. **`memory-backend-adapters`** (1 modified + 4 new requirements):
   - **Modified requirement**: "Degraded Backend — Search Returns Partial Results"
     - Previously scoped to Engram/Hindsight; now explicitly includes knowledge-vault
     - Added scenario: `/global` search fans out to both Engram and knowledge-vault
   - **New requirement 1**: "Knowledge-Vault Adapter"
     - HTTP transport to search bridge with `/global`-only namespace and search-only verbs
     - Config-driven auth (bearer)
     - Default-constructible zero-arg construction
   - **New requirement 2**: "Knowledge-Vault Transport Failure Integrates With Degraded-Backend Handling"
     - HTTP failures raise `BackendUnavailableError` → degradation semantics
   - **New requirement 3**: "Knowledge-Vault Empty or Unavailable Index Never Fabricates Hits"
     - Returns empty hits (not `503`, not fabricated) when vault/index unavailable
   - **New requirement 4**: "Knowledge-Vault Hits Are Attributed and Score-Bearing"
     - Each hit carries `backend="knowledge-vault"` and non-zero score from bridge

All requirements backed by scenario-driven tests (363/363 passing).

### Phase 1 Architecture Characteristics

- **Dual search-capable backends**: Engram (session memory) + knowledge-vault (curated corpus) both serve `/global`
- **Capability-gated dispatch**: `SearchOnlyBackend` extends protocol landscape; registry selects via `verbs` gating
- **Namespace isolation**: knowledge-vault declares `/global` only; no collision with Engram's `/global` overlap (coexistence is the design point)
- **Auth flexibility**: Bearer token sourced from environment; injectable transport for testing
- **Degraded semantics**: Knowledge-vault uses existing dispatcher behavior (no new handler); HTTP failure → explicit unavailable marker
- **Read-only guarantee**: Host-side systemd unit enforces `ReadOnlyPaths=` mount; no write verb on HTTP surface
- **Minimal core edits**: Only `contracts.py` (SearchOnlyBackend Protocol); `app.py`, `registry.py`, `permissions.py` unchanged
- **Plugin seam proven**: New standalone adapter registered via entry-point; zero registry changes needed
- **Score preservation**: Each hit carries score from bridge; caller can distinguish source via `backend` field
- **Dual-adapter precedence**: No conflict because both serve `/global` intentionally (fan-out, not dual-dispatch guard)

## Critical Verifications Performed

### Per the Launch Prompt CRITICAL Section

✅ **Verification 1: `git rm` and original folder deletion**
After copying to archive, the original `openspec/changes/obsidian-backend/` folder is removed from git tracking via:
```bash
git rm -r openspec/changes/obsidian-backend/
```
Result: Original folder shows as deleted in `git status`; no untracked archive folder alongside original.

✅ **Verification 2: Archived specs are byte-identical to merged main specs**
Each archived spec file at `openspec/changes/archive/2026-08-20-obsidian-backend/specs/*/spec.md` is verified byte-identical to its corresponding merged main spec at `openspec/specs/*/spec.md` via diff. All three copies (knowledge-vault-search-bridge NEW, memory-router-interfaces merged, memory-backend-adapters merged) verified as final merged state.

Both verifications PASSED. Archive is complete and correct.

## Authority and Traceability

This archive report is the terminal record of the Knowledge-Vault Backend Adapter SDD cycle per the Final-State Authority hierarchy:

- **Native review authority**: None yet (delivery strategy decision pending)
- **Persisted tasks artifact**: `openspec/changes/archive/2026-08-20-obsidian-backend/tasks.md` — all implementation tasks checked off
- **Explicit final-state facts from launch prompt**: incorporated above (363/363 tests passing, PASS verify with 0 issues, `app.py`/`registry.py`/`permissions.py` byte-unmodified, verifications performed)
- **Intermediate snapshots** (verify-report, apply-progress): superseded by launch-prompt facts and tasks artifact

**Rules applied**:
- Only higher-ranked sources (tasks artifact + launch prompt facts) are reported as final state
- The live deployment deferral (intentional Phase 1 scope, no deployment environment available) is documented as an explicit follow-up, not a regression
- The protocol separation proof (SearchOnlyBackend coexistence, Engram byte-for-byte unchanged) is prominently documented as successful validation
- Critical verifications (git rm, diff archive vs merged specs) performed and passed before reporting closure

## Next Steps (User Decision)

1. **PR delivery strategy** (user decision, not automatic): Awaiting user delivery strategy decision (chained PRs recommended per tasks forecast).
2. **Live service deployment** (operational follow-up): Deploy `knowledge-vault-search.service` to host, verify reachability from k8s cluster at 10.42.0.1:8088.
3. **Score normalization** (future change if needed): Implement cross-backend score alignment if caller-side ranking becomes critical.
4. **Backends 5+** (separate SDD changes): Other search-only backends (Elasticsearch, Milvus, etc.), reusing the `SearchOnlyBackend` contract.
5. **Reflect or store** (future change): Extend knowledge-vault adapter if write access becomes needed (remains out of scope per Phase 1).
6. **Search on other namespaces** (future change): Expand `/global`-only scope to `/projects/*` or `/agents/*` if product requires per-project knowledge graphs.

## Compliance

- ✅ All implementation tasks complete and verified (all checked in tasks.md)
- ✅ All 363 unit tests passing (113 host + 250 router)
- ✅ sdd-verify PASS (0 CRITICAL, 0 WARNING, 0 SUGGESTION)
- ✅ Delta specs merged to main `openspec/specs/` source of truth (3 domains, 1 NEW + 1 modified + 4 new requirements)
- ✅ All change artifacts moved to archive with date prefix (2026-08-20)
- ✅ Archive folder contains complete audit trail (proposal, design, tasks, merged specs)
- ✅ `SearchOnlyBackend` Protocol established as first-class contract separate from `MemoryBackend`
- ✅ Engram search-on-`/global` conformance tests re-run unmodified and passing
- ✅ `app.py`, `registry.py`, `permissions.py` byte-unmodified (search fan-out and registry already support non-MemoryBackend)
- ✅ Dispatcher fan-out confirmed; Engram and knowledge-vault coexist on `/global` seamlessly
- ✅ Host-side HTTP bridge with bearer auth, bounded rebuild, read-only mount completed
- ✅ `VaultHit.score` field added to preserve relevance scoring through HTTP bridge
- ✅ Phase 1 scope closed; live deployment, score normalization, and future namespace expansion documented as follow-up work
- ✅ Critical verifications performed: git rm confirmed original folder deleted, diff confirmed archived specs byte-identical to merged main specs
- ✅ No blockers; ready for PR and delivery

The Knowledge-Vault Backend Adapter (Search on `/global`) SDD cycle is COMPLETE and ARCHIVED.
