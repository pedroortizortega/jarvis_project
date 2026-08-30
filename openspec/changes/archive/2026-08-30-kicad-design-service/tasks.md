# Tasks: KiCad Design Service

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~600–900 (two Deployments, two Services, one ConfigMap, one test module) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 → PR 2 (PR 1: ingestion + tests; PR 2: MCP workloads + rollout) |
| Delivery strategy | ask-on-risk |
| Chain strategy | feature-branch-chain |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: feature-branch-chain
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Ingestion stage + confidence gating + RED tests | PR 1 | `python -m unittest discover -s tests` | `kicad-mcp` pod dry-run against a sample sketch | `kubernetes/mcps/sketch-ingest-config.yaml` + `tests/test_kicad_design_contracts.py` removable without unrelated rollback |
| 2 | KiCad + FreeCAD MCP workloads + rollout | PR 2 | `python -m unittest discover -s tests` | ephemeral cluster with both Deployments | `kubernetes/mcps/kicad-mcp-*.yaml` + `kubernetes/mcps/freecad-mcp-*.yaml` removable without unrelated rollback |

## Phase 1: Foundation

- [x] 1.1 Create `tests/test_kicad_design_contracts.py` with RED tests for ingestion confidence gating and DRC/ERC commit gate.
- [x] 1.2 Create `kubernetes/mcps/sketch-ingest-config.yaml` (ConfigMap) with confidence threshold and pipeline handoff keys.
- [x] 1.3 Confirm `kubernetes/mcps/namespace.yaml` already declares `mcps`; do not edit.

## Phase 2: Core Implementation

- [x] 2.1 Create `kubernetes/mcps/kicad-mcp-deployment.yaml`: pinned `mixelpixx/KiCAD-MCP-Server` build, non-root, read-only root, dropped capabilities, `automountServiceAccountToken: false`.
- [x] 2.2 Create `kubernetes/mcps/kicad-mcp-service.yaml`: ClusterIP-only, no public exposure.
- [x] 2.3 Create `kubernetes/mcps/freecad-mcp-deployment.yaml`: pinned `freecad-robust-mcp` headless build, same hardening.
- [x] 2.4 Create `kubernetes/mcps/freecad-mcp-service.yaml`: ClusterIP-only.

## Phase 3: Testing / Verification

- [x] 3.1 RED: unauthenticated MCP tool call is denied (both workloads).
- [x] 3.2 RED: low-confidence extraction never auto-commits.
- [x] 3.3 RED: DRC/ERC violation blocks plan commit and is reported.
- [x] 3.4 Verify `python -m unittest discover -s tests` passes. (409 tests, OK)

## Phase 4: Rollout / Documentation

- [x] 4.1 Record CNI NetworkPolicy evidence and Hermes `hostNetwork` connectivity evidence before onboarding.
- [ ] 4.2 Update `kubernetes/policy/mcps-networkpolicy.yaml` if a new egress rule is required for the FreeCAD headless run.
- [x] 4.3 Confirm `kubernetes/mcps/brave-search-mcp-deployment.yaml` is byte-identical to its pre-change state. (sha256: 84effee82870bd6189e1cac984cf324a654c45b0d7e05afc5a32753eadad4c91)
