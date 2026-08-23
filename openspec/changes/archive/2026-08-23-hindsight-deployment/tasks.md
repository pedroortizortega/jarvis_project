# Tasks: Hindsight Deployment

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | PR1 ~200, PR2 ~615 |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 (port fix + deployable service) → PR 2 (enforcement + specs) |
| Delivery strategy | ask-on-risk |
| Chain strategy | feature-branch-chain (PR 2 targets PR 1's branch) |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: feature-branch-chain
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Port default fix + deployable manifests + bootstrap wiring | PR 1 | `python -m unittest tests.test_memory_router_hindsight_adapter` | `kubectl apply -f kubernetes/mcps/hindsight-*.yaml` then `kubectl rollout status` | `kubectl delete -f hindsight-{pvc,deployment,service}.yaml`; revert `hindsight.py`/test line |
| 2 | Manifest + cross-manifest enforcement tests, spec closure | PR 2 | `python -m unittest discover -s tests` | N/A — assertion-only, no live cluster call | Revert `tests/test_hindsight_manifest.py` + `specs/015` edit; no runtime effect |

Note: `specs/022_hindsight_deployment.md` and both OpenSpec delta specs (`openspec/changes/hindsight-deployment/specs/{hindsight-service,memory-backend-adapters}/spec.md`) are already written and consistent with design — no task recreates them.

## Phase 1: Adapter Port Fix (PR 1, strict TDD)

- [x] 1.1 RED: edit `tests/test_memory_router_hindsight_adapter.py:42` assertion from `:8080` to `:8888`; run `python -m unittest tests.test_memory_router_hindsight_adapter` and confirm it fails.
- [x] 1.2 GREEN: in `hermes-native/memory-router/src/memory_router/backends/hindsight.py:110`, change the `HINDSIGHT_BASE_URL` default from `:8080` to `:8888`; re-run the test to confirm it passes.
- [x] 1.3 Update `specs/015_hindsight_backend.md` §4 default-port table row to `:8888` and check off the "Despliegue real de una instancia de Hindsight" checklist item, pointing at spec 022.

## Phase 2: Runtime Discovery (PR 1)

- [x] 2.1 Run `docker inspect -f '{{.Config.User}}' ghcr.io/vectorize-io/hindsight:latest` (or `docker run --rm --entrypoint id ...`) to resolve the real numeric uid/gid per D-02; record the value in `specs/022_hindsight_deployment.md`. If root or non-numeric, stop and flag as a design finding — do not fall back to `runAsNonRoot: false`.

## Phase 3: Manifests (PR 1)

- [x] 3.1 Create `kubernetes/mcps/hindsight-pvc.yaml` — RWO, `local-path`, `10Gi`, name `hindsight-data`, header comment noting D-04 (Postgres + model cache share one PVC).
- [x] 3.2 Create `kubernetes/mcps/hindsight-deployment.yaml` — `replicas: 1`, `Recreate`, image + `imagePullPolicy: Always`, D-02 securityContext (discovered uid), D-03 volumes (`home`/`data`/`tmp`), D-11 probes, D-13 `terminationGracePeriodSeconds: 60`, `1`/`2Gi`→`4`/`6Gi`, full env block from design Interfaces section (D-06 LLM model, D-14 onnx embeddings, both secretKeyRefs).
- [x] 3.3 Create `kubernetes/mcps/hindsight-service.yaml` — ClusterIP `hindsight`, `8888→8888`, name `http`, selector `app: hindsight`, comment recording the no-Ingress boundary.
- [x] 3.4 Modify `kubernetes/mcps/memory-router-deployment.yaml` — add `HINDSIGHT_TOKEN` (secretKeyRef `hindsight-tenant-key`/`tenant-api-key`) and `HINDSIGHT_AUTH_MODE: "bearer"`; do not add `HINDSIGHT_BASE_URL` (D-07).

## Phase 4: Bootstrap (PR 1)

- [x] 4.1 Modify `kubernetes/mcps/bootstrap/03-create-secrets.sh` — add block 5 (`hindsight-tenant-key`, `openssl rand -hex 32` cached at `$MR_PKI_DIR/hindsight/tenant-api-key`) and block 6 (`hindsight-codex-shim-key`, copy `llms/codex-shim-key[internal-key]` via `kubectl get ... -o jsonpath | base64 -d`, abort loudly if empty); update the closing log line to 6 secrets.
- [x] 4.2 Modify `kubernetes/mcps/bootstrap/05-deploy-manifests.sh` — append `hindsight-pvc.yaml`, `hindsight-deployment.yaml`, `hindsight-service.yaml` to the ordered apply list plus a second `rollout status` call.
- [x] 4.3 Modify `kubernetes/mcps/bootstrap/00-config.sh` — add `: "${MR_HINDSIGHT_IMAGE:=ghcr.io/vectorize-io/hindsight:latest}"` as a reference value.

## Phase 5: Manifest Enforcement Tests (PR 2)

- [x] 5.1 Create `tests/test_hindsight_manifest.py` (`unittest.TestCase` + `yaml.safe_load_all`, following `kubernetes/local-embeddings/tests/test_local_embeddings_manifest.py`): image/pull policy, container port 8888, Service ClusterIP/port/selector, `replicas: 1`/`Recreate`, PVC size/class/mode, both secretKeyRefs by exact `{name, key}`, no plaintext secret `value:`, `HINDSIGHT_API_LLM_BASE_URL` targets codex-shim, D-02 security context (`runAsNonRoot`, numeric non-zero `runAsUser`, `fsGroup == runAsUser`), `readOnlyRootFilesystem: true`, `capabilities.drop == ["ALL"]`, `startupProbe.failureThreshold >= 60`, no `kind: Ingress` in `hindsight-*.yaml`.
- [x] 5.2 In the same file, add the cross-manifest test: `memory-router-deployment.yaml`'s `HINDSIGHT_TOKEN`/`HINDSIGHT_AUTH_MODE` match the Hindsight Deployment's `HINDSIGHT_API_TENANT_API_KEY` secretKeyRef field-by-field, and confirm no `HINDSIGHT_BASE_URL` is present.
- [x] 5.3 Run `python -m unittest discover -s tests` and confirm the full suite passes.

## Phase 6: Manual Verification (post-merge, not automated)

- [ ] 6.1 Apply order per design Migration/Rollout: secrets → PVC → Deployment → Service → memory-router same window; watch first rollout for D-03/D-11/D-12 verdicts.
- [ ] 6.2 Confirm unauthenticated request rejected, bearer accepted; `store`→`search` round-trip with `backend == "hindsight"`; `kubectl delete pod` survives; codex-shim logs show calls.
