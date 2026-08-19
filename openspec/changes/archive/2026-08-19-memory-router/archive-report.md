# Archive Report: Memory Router (Phase 1)

**Change**: `memory-router`
**Archived**: 2026-08-19
**Status**: COMPLETE
**Mode**: hybrid (OpenSpec filesystem + Engram persistence)

## Executive Summary

Memory Router Phase 1 has been successfully completed, implemented, verified, and archived. The router skeleton with MCP + REST surfaces and a single Engram backend adapter has been fully delivered with all 32 tasks checked off, 80/80 tests passing, and sdd-verify confirming PASS (0 CRITICAL, 1 non-blocking SUGGESTION). All four new capability specifications have been synced to main specs. Kubernetes manifests for `memory-router` are authored and YAML-validated but NOT applied to any cluster pending resolution of an external prerequisite: Engram Cloud's own namespace manifests are untracked and of undocumented origin (see Design Open Questions). This is called out prominently as a follow-up blocker and is NOT treated as implemented. ConfigMap-to-PyYAML wiring is intentionally deferred to a later phase per design; Phase 1 hardcodes the equivalent identity/role map.

## Implementation Status

### Scope Delivered: Phase 1 (Router Skeleton + Engram)

| Component | Status | Details |
|-----------|--------|---------|
| Router core (contracts, namespaces, identity, permissions, journal, registry, app) | Complete | 7 modules, 80/80 tests passing |
| Engram adapter | Complete | Single reference implementation, namespace→topic_key prefix mapping, degraded → partial results |
| Dual surfaces (REST + MCP stdio shim) | Complete | `/memory/store`, `/memory/search`, `/memory/reflect`, `/agents/{name}/context`, `/projects/{name}/context` |
| Kubernetes manifests | Authored | 6 YAML files (configmap, deployment, service, PVC, ingress, tlsoption); NOT APPLIED (see blocker below) |
| Numbered spec companion | Complete | `specs/014_memory_router.md` |
| Test suite | Complete | 80/80 unit tests passing (`python -m unittest discover -s tests`) |

### Scope Explicitly Deferred

| Item | Reason | Next Phase / Reference |
|------|--------|------------------------|
| Backends 2–6 (Hindsight, Graphiti, Honcho, Cognee, Obsidian) | Out of scope per proposal; architecture proven with Engram only | Separate SDD changes per backend |
| ConfigMap + PyYAML wiring | Intended architecture documented; implementation deferred to later phase | Design Open Questions, `permissions.py`/`app.py` currently hardcode the equivalent map |
| Cross-backend merge/ranking, write fan-out, dedup, memory migration | Out of scope per proposal | Future phases |
| Engram protocol changes or new transports | Out of scope per proposal | Assumes existing MCP-stdio path only |

## Artifacts

### Delta Specs (Merged to Main)

Four new capability specifications created in phase; all merged to `openspec/specs/` (primary source of truth):

| Domain | File | Action | Details |
|--------|------|--------|---------|
| `memory-router-interfaces` | `openspec/specs/memory-router-interfaces/spec.md` | Created (full spec) | MCP and REST surfaces, request/response contracts, error semantics; 5 requirements (dual surface, reflect 501, explicit error codes, default entry point, rollback path) |
| `memory-namespace-routing` | `openspec/specs/memory-namespace-routing/spec.md` | Created (full spec) | Namespace model (`/global`, `/user/master`, `/projects/{name}`, `/agents/{name}`), explicit declaration, hierarchical search fallback; 3 requirements |
| `memory-backend-adapters` | `openspec/specs/memory-backend-adapters/spec.md` | Created (full spec) | Adapter contract (capabilities, store, search, health), Phase 1 Engram adapter, degraded-store queuing, degraded-search partial results; 3 requirements |
| `memory-access-control` | `openspec/specs/memory-access-control/spec.md` | Created (full spec) | Phase 1 role set (coder, scientist, jarvis), server-side identity→role mapping, per-role namespace+verb authorization, deny-by-default; 3 requirements |

All four specs contain requirements backed by Scenario-driven test coverage per the SDD phase work.

### Archive Folder Contents

All original SDD artifacts archived to `openspec/changes/archive/2026-08-19-memory-router/`:

- `proposal.md` ✅ (intent, scope, capabilities, approach, risk assessment, rollback, dependencies, resolved decisions, success criteria)
- `design.md` ✅ (technical approach, component architecture, backend contract, data flow, auth & permissions, architecture decisions, file changes, testing strategy, threat matrix, migration/rollout, open questions)
- `tasks.md` ✅ (8 phases, 32 tasks total, all `[x]` checked)
- `specs/` ✅ (4 domain specs, copied for audit trail)

## Task Completion

### Final State: 32/32 Tasks Complete

| Phase | Count | Status |
|-------|-------|--------|
| Phase 1: Contracts | 2 | ✅ Complete |
| Phase 2: Namespace validation | 2 | ✅ Complete |
| Phase 3: Identity & permissions | 4 | ✅ Complete |
| Phase 4: Journal | 2 | ✅ Complete |
| Phase 5: Registry & Engram adapter | 4 | ✅ Complete |
| Phase 6: App — dispatcher, REST, MCP shim | 10 | ✅ Complete (includes fix-pass tasks 6.6–6.10, 7.7) |
| Phase 7: Deployment manifests | 7 | ✅ Complete (manifests authored, not applied) |
| Phase 8: Spec companion | 1 | ✅ Complete |
| **TOTAL** | **32** | ✅ **COMPLETE** |

All implementation tasks verified by orchestrator as complete post-apply. No stale unchecked tasks remain in archived `tasks.md`.

## Verification and Testing

### Test Evidence (Final State)

- **Unit test suite**: 80/80 passing (`python -m unittest discover -s tests`)
- **Verified twice by orchestrator**: both runs confirmed full pass
- **sdd-verify final verdict**: **PASS**
  - Critical issues: 0
  - Warnings: 0
  - Suggestions: 1 (non-blocking query-param log-hygiene note; no action required for archive)

### Verification Report State

Per sdd-verify final report (per launch prompt final-state facts):
- All 26 original implementation tasks verified complete
- All 6 fix-pass tasks (6.6–6.10, 7.7) verified complete and committed
- 12 commits total on `feat/memory-router` branch, all on top of `main`, nothing pushed
- No CRITICAL or blocking issues identified

## Critical Blocker: Kubernetes Deployment Prerequisite

**Status**: NOT RESOLVED — blocks actual cluster deployment.

The Memory Router's Kubernetes manifests (`kubernetes/mcps/memory-router-*.yaml`) have been authored and are YAML-validated (via `kubectl apply --dry-run=client`), but **cluster apply is blocked on an unresolved external prerequisite**:

**Issue**: Engram Cloud's own `mcps` namespace manifests are untracked in this repository with undocumented origin and ownership.

**Documented In**:
- Design: "Open Questions" section, first item (blocking)
- Proposal: Risk Assessment, "Engram Cloud manifests untracked / out-of-band origin" (High likelihood)

**What Must Be Done Before Cluster Deploy**:
1. Identify the current owner of Engram Cloud's `mcps` manifests (are they in a separate repo? generated? undocumented?)
2. Document a reproducible manifest source / generation process
3. Confirm whether Memory Router's manifests and Engram's manifests share a namespace and how they coexist
4. Once ownership and origin are clear, these manifests may be applied

**This is NOT a bug in the router code** — it is an infrastructure coordination gap. The router implementation is sound and ready; the blocker is external to this change.

**Note**: All 6 YAML files contain header comments reminding the deployer not to `kubectl apply` until this prerequisite is resolved.

## Known Deferred Work (Not a Blocker)

### ConfigMap + PyYAML Wiring (Phase 1 Intentional Deferral)

**Issue**: `kubernetes/mcps/memory-router-configmap.yaml` documents the intended identity→role map (`identity-roles.yaml`), but Phase 1 code does not read it from ConfigMap via PyYAML. Instead:

- `permissions.py::IDENTITY_ROLES` hardcodes the equivalent role-based permission table
- `app.py::_load_role_map_from_env()` hardcodes the identity→role mapping for the four onboarded clients

**Why It's Deferred**: Simpler Phase 1 scope, proven correctness before adding ConfigMap YAML parsing

**Impact**: Until the PyYAML wiring is completed, changes to the ConfigMap's `identity-roles.yaml` have **no runtime effect**. Any role or identity change must be mirrored by hand into the code.

**Documented In**: Design Open Questions (last item, explicitly marked "deferred")

**Path Forward**: A later SDD phase will wire ConfigMap loading through `permissions.py` without changing the permission enforcement logic.

## Git State

- **Branch**: `feat/memory-router` (all work committed)
- **Commits**: 12 total (9 original implementation + 3 fix-pass)
- **Base**: all commits on top of `main`
- **Push state**: nothing pushed, no PR opened
- **Git user**: pedro

The orchestrator will handle PR and delivery strategy separately. The change is ready for review and delivery.

## Specification Summary

### Main Specs Now Updated

Four new capabilities are now the source of truth in `openspec/specs/`:

1. **`memory-router-interfaces`** — MCP and REST surface contracts, explicit error distinction, fallback paths
2. **`memory-namespace-routing`** — Fixed 4-root namespace model, explicit declaration requirement, hierarchical search fallback
3. **`memory-backend-adapters`** — Adapter contract, Phase 1 Engram adapter, degraded-store queuing, degraded-search partial results
4. **`memory-access-control`** — 3-role Phase 1 model (coder, scientist, jarvis), server-side identity→role mapping, deny-by-default authorization

All requirements in these specs are backed by scenario-driven tests (80/80 passing).

### Phase 1 Design Characteristics

- **Router behavior**: stateless request handling, durable NDJSON journal for degraded writes, per-backend health/capability tracking
- **Engram adapter**: stdio subprocess, namespace→`topic_key` prefix encoding, `store`→`mem_save`, `search`→`mem_search` + `mem_get_observation` per result
- **Permissions**: deny-by-default, role-based (not identity-based), configured server-side, enforced before any adapter call
- **Degraded semantics**: store never drops, queues to journal and retries; search returns partial results + unavailable markers

## Authority and Traceability

This archive report is the terminal record of the Memory Router Phase 1 SDD cycle per the Final-State Authority hierarchy:

- **Native review authority**: none (review not yet run on delivery; candidate awaits delivery strategy user decision per launch prompt)
- **Persisted tasks artifact**: `openspec/changes/archive/2026-08-19-memory-router/tasks.md` — all 32 tasks `[x]` checked
- **Explicit final-state facts from launch prompt**: incorporated above (32 tasks, 80/80 tests, PASS verify, 12 commits, K8s manifests authored but blocked, ConfigMap PyYAML deferred)
- **Intermediate snapshots** (verify-report, apply-progress): superseded by launch-prompt facts and tasks artifact

**Rules applied**:
- Only the higher-ranked sources (tasks artifact + launch prompt facts) are reported as final state
- The K8s deployment blocker (Engram Cloud manifests) is called out prominently with full context, not mentioned in passing
- The ConfigMap PyYAML deferral (intentional Phase 1 scope) is documented as a known deferred item, not a regression

## Next Steps (User Decision)

1. **PR delivery strategy** (user decision, not automatic per proposal): The tasks artifact forecasts ~1200–1600 changed lines (High risk). The proposal recommendation was chained/stacked PRs (PR1–PR7 per unit). Awaiting user delivery strategy decision.
2. **Kubernetes deployment** (after resolution): Once Engram Cloud manifests are identified and documented, the Memory Router manifests may be applied to a test cluster.
3. **Backends 2–6** (separate SDD changes): Each backend integration is a separate change, reusing the proven adapter contract.
4. **ConfigMap PyYAML wiring** (separate SDD phase): A later phase will complete the intended architecture (ConfigMap → PyYAML → permissions.py).

## Compliance

- ✅ All implementation tasks complete and verified
- ✅ All 80 unit tests passing
- ✅ sdd-verify PASS (0 CRITICAL)
- ✅ All specs merged to main `openspec/specs/` source of truth
- ✅ All change artifacts moved to archive with date prefix
- ✅ Archive folder contains complete audit trail (proposal, design, tasks, specs)
- ✅ Kubernetes manifests authored and YAML-validated (not applied pending blocker)
- ✅ Phase 1 scope closed; out-of-scope items (backends 2–6, PyYAML wiring) documented as future work
- ✅ Critical blocker (Engram Cloud manifests) prominently documented

The Memory Router Phase 1 SDD cycle is COMPLETE and ARCHIVED.
