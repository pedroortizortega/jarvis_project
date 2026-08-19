# Tasks: Memory Router (Phase 1 — router skeleton + Engram adapter)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~1200–1600 (7 prod modules + 6 k8s YAML + 8 test files + spec doc) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR1→PR7 below |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending (user decision required) |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: pending
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | `contracts.py` + `namespaces.py` | PR 1 | `python -m unittest tests.test_memory_router_contracts tests.test_memory_router_namespaces` | N/A — pure functions, no runtime deps | Delete both modules + tests |
| 2 | `identity.py` + `permissions.py` | PR 2 | `python -m unittest tests.test_memory_router_identity tests.test_memory_router_permissions` | N/A — pure logic over config dict | Delete both modules + tests |
| 3 | `journal.py` | PR 3 | `python -m unittest tests.test_memory_router_journal` | Local tmp-dir NDJSON file (real fs, no cluster) | Delete module + tests |
| 4 | `registry.py` + `backends/engram.py` | PR 4 | `python -m unittest tests.test_memory_router_registry tests.test_memory_router_engram_adapter` | Mocked `engram mcp` subprocess (unit-level); manual live run vs `engram-cloud.mcps` deferred | Delete module + tests |
| 5 | `app.py` + `pyproject.toml` entry points | PR 5 | `python -m unittest tests.test_memory_router_app` | `python -m memory_router.app` against Phase 1–4 modules | Revert `app.py`, `pyproject.toml` diff |
| 6 | `kubernetes/mcps/memory-router-*.yaml` | PR 6 | N/A — manifests only, no unit test | `kubectl apply --dry-run=client -f kubernetes/mcps/` | Delete the 6 YAML files; not applied to cluster until manifest-ownership blocker resolved |
| 7 | `specs/012_memory_router.md` | PR 7 | N/A — doc only | N/A | Delete file |

**Blocker (separate from authoring)**: PR 6 YAML may be written now, but `kubectl apply` is gated on resolving Engram Cloud manifest ownership/origin (design.md Open Questions — untracked upstream manifests, undocumented owner).

## Phase 1: Contracts (foundation)

- [x] 1.1 RED `tests/test_memory_router_contracts.py`: `Capabilities`, `Health`, `StoreRequest/Result`, `SearchRequest/Result` shapes and `MemoryBackend` protocol conformance
- [x] 1.2 GREEN `src/memory_router/contracts.py`: implement dataclasses/enum/Protocol

## Phase 2: Namespace validation

- [x] 2.1 RED `tests/test_memory_router_namespaces.py`: accepts the 4 fixed roots; rejects traversal (`..`), wildcards, unknown roots, missing namespace
- [x] 2.2 GREEN `src/memory_router/namespaces.py`: `validate_namespace()`, fail-closed

## Phase 3: Identity & permissions

- [x] 3.1 RED `tests/test_memory_router_identity.py`: CN→identity resolution; bearer mismatch rejected
- [x] 3.2 GREEN `src/memory_router/identity.py`
- [x] 3.3 RED `tests/test_memory_router_permissions.py`: 3-role table incl. deny-by-default (`admin/*`), unknown role rejected, role outside client's permitted set rejected
- [x] 3.4 GREEN `src/memory_router/permissions.py`

## Phase 4: Journal

- [x] 4.1 RED `tests/test_memory_router_journal.py`: append+fsync, re-open after simulated restart replays queued entries durably
- [x] 4.2 GREEN `src/memory_router/journal.py`: NDJSON append-only journal

## Phase 5: Registry & Engram adapter

- [x] 5.1 RED `tests/test_memory_router_registry.py`: capability-based adapter selection (search-only adapter excluded from store dispatch)
- [x] 5.2 GREEN `src/memory_router/registry.py`: entry-point loading (`memory_router.backends`)
- [x] 5.3 RED `tests/test_memory_router_engram_adapter.py`: fixed argv/no shell/no caller-controlled env; subprocess crash → `degraded`, not request failure
- [x] 5.4 GREEN `src/memory_router/backends/engram.py`: `engram mcp --tools=agent` adapter, namespace→`topic_key` prefix mapping

## Phase 6: App — dispatcher, REST, MCP shim

- [x] 6.1 RED `tests/test_memory_router_app.py`: healthy store commit; degraded store → pending via journal, never dropped; hierarchical search fallback project→agent→global; store never falls back; degraded search returns partial results + unavailable marker
- [x] 6.2 GREEN `src/memory_router/app.py`: dispatcher wiring identity→permissions→namespaces→registry
- [x] 6.3 RED (extend `test_memory_router_app.py`): `/memory/reflect` returns `501`, no backend call; MCP and REST parity for store/search
- [x] 6.4 GREEN `app.py`: reflect stub route + MCP stdio shim entry point
- [x] 6.5 Modify `pyproject.toml`: add `memory_router.backends` entry-point group; console scripts for REST service and `memory-router-mcp`

## Phase 7: Deployment manifests

- [x] 7.1 Create `kubernetes/mcps/memory-router-configmap.yaml`: role map, namespace roots
- [x] 7.2 Create `kubernetes/mcps/memory-router-deployment.yaml`: `replicas: 1`, `Recreate`, non-root, read-only rootfs, caps dropped, `automountServiceAccountToken: false`, PVC-mounted journal
- [x] 7.3 Create `kubernetes/mcps/memory-router-service.yaml`: ClusterIP:8080
- [x] 7.4 Create `kubernetes/mcps/memory-router-pvc.yaml`: journal storage
- [x] 7.5 Create `kubernetes/mcps/memory-router-ingress.yaml` + `memory-router-tlsoption.yaml`: Traefik mTLS, mirroring existing `mcps` tenants
- [x] 7.6 Note in manifest PR description: cluster apply blocked on Engram Cloud manifest-ownership prerequisite (design.md Open Questions) — see the header comment repeated in all 6 files in this phase; **do not `kubectl apply` any of these until that prerequisite is resolved**.

## Phase 8: Spec companion

- [x] 8.1 Create `specs/012_memory_router.md` following the `specs/011_engram_cloud_centralized.md` numbering/format convention; document Phase 1 scope, contracts, roles, namespaces, degraded-backend semantics
