# Design: Hindsight Deployment

## Technical Approach

Four flat manifests in `kubernetes/mcps/` (`hindsight-deployment.yaml`, `hindsight-service.yaml`, `hindsight-pvc.yaml` — **no ConfigMap**, see D-05; **no `kustomization.yaml`**, that directory is flat-file + `05-deploy-manifests.sh`, unlike `kubernetes/local-embeddings/`), plus a two-line code fix, two new Secrets owned by the bootstrap script, two env additions on `memory-router-deployment.yaml`, and one root-suite manifest test.

Security shape is copied from `kubernetes/local-embeddings/deployment.yaml` (pod-level `runAsNonRoot` + numeric uid/gid + `fsGroup` + `fsGroupChangePolicy: OnRootMismatch` + `seccompProfile: RuntimeDefault`; container-level `allowPrivilegeEscalation: false`, `readOnlyRootFilesystem: true`, `capabilities.drop: [ALL]`; `automountServiceAccountToken: false`, `enableServiceLinks: false`; `startupProbe` → tight readiness/liveness). Statefulness shape is copied from `memory-router-deployment.yaml` (`replicas: 1` + `Recreate` + RWO `local-path` PVC).

All five proposal questions are settled inputs: `1`/`2Gi` requests, `4`/`6Gi` limits, `10Gi` PVC, `:latest` + `imagePullPolicy: Always`, one-shot auth rollout, and **bundled `onnx` embeddings with `intfloat/multilingual-e5-small`** (amended from the original `bge-small-en-v1.5` plan after live investigation of Hindsight's embedding-provider config surface — see D-14; `local-embeddings` wiring was evaluated and rejected for this backend, not deferred).

## Verified Findings (read from this repo, not assumed)

- **F-1 — codex-shim's secret is `codex-shim-key`, key `internal-key`, namespace `llms`** (`kubernetes/codex-shim/deployment.yaml:48-52`, also consumed by LiteLLM at `kubernetes/proxy/litellm-config.yaml:201-205`). That is the exact secret/key pair the `mcps` copy must reproduce.
- **F-2 — the real model alias is `gpt-5.6-sol`**, not `cloud`. `cloud` is a **LiteLLM** alias (`litellm-config.yaml:140`); Hindsight talks to codex-shim **directly**, and codex-shim's own `/v1/models` advertises `CODEX_CLOUD_MODEL` = `gpt-5.6-sol` (`app/proxy.py:35,109-123`, `codex-shim/deployment.yaml:46-47`). `POST /v1/chat/completions` exists (`app/proxy.py:273`) and `_check_internal_bearer` guards every `/v1/*` route.
- **F-3 — codex-shim ignores the request's `model` field for the upstream call** (`app/proxy.py:280,289` — it always sends `CODEX_CLOUD_MODEL`). Sending `gpt-5.6-sol` is therefore the honest, self-documenting value rather than a load-bearing one.
- **F-4 — `kubernetes/mcps/` has zero tests today** (no `tests/`, no `pytest.ini`; only manifests + `bootstrap/`). This change sets the precedent — see D-08.
- **F-5 — `03-create-secrets.sh` is idempotent by `kubectl create ... --dry-run=client -o yaml | apply_secret`**, generates randoms with `openssl rand -hex 32` cached under `$MR_PKI_DIR/`, and already reads a value out of a live secret via `kubectl get secret ... -o jsonpath='{.data.X}' | base64 -d` (lines 25-32). Both new secrets fit those two existing shapes exactly; no new mechanism is needed.
- **F-6 — the Hindsight image's runtime UID is not verifiable from this repo.** It is a public upstream image with no Dockerfile here, and spec 015 §9.1's live validation ran it in Docker without recording a uid. `10001` is **this repo's own convention for images it builds**; assuming it for an upstream image is exactly the class of guess that D-14 in the local-embeddings design got burned by. See D-02.
- **F-7 — root suite `tests/` is `unittest.TestCase`** under `python -m unittest discover -s tests`, and `tests/test_local_embeddings.py` already pulled PyYAML into it (local-embeddings D-15). A root manifest test needs no new precedent, only the same one.
- **F-8 — Hindsight's embedding config has a `local`/`onnx`/`openai`/... provider selector**, verified against `github.com/vectorize-io/hindsight` `hindsight-docs/docs/developer/configuration.md`. `local` (SentenceTransformers) defaults to `BAAI/bge-small-en-v1.5`, English-only. `onnx` supports `HINDSIGHT_API_EMBEDDINGS_ONNX_MODEL_ID` for a HuggingFace swap — including `intfloat/multilingual-e5-small` (multilingual, 384-dim) — and natively prepends `HINDSIGHT_API_EMBEDDINGS_ONNX_QUERY_PREFIX`/`_PASSAGE_PREFIX` (defaults `"query: "`/`"passage: "`) **inside Hindsight itself**, before the model call. This is the fact that decides D-14: memory-router never touches embeddings directly (it only sends plain text to Hindsight's REST API); Hindsight is the sole caller of its embedding provider for both `retain` and `recall`. Routing that call through `local-embeddings`'s OpenAI-compatible endpoint (no `input_type` field in the OpenAI schema) would silently drop prefixing on both sides — symmetric but degraded. The `onnx` provider has no such gap.

## Architecture Decisions

| # | Decision | Options / tradeoff | Choice and rationale |
|---|---|---|---|
| D-01 | File layout | `kustomization.yaml` (local-embeddings style) vs flat `hindsight-*.yaml` (mcps style) | **Flat `hindsight-*.yaml`.** `kubernetes/mcps/` has no kustomization at all; `bootstrap/05-deploy-manifests.sh` applies an explicit ordered file list. Adding one kustomization for one service would split that directory across two deployment conventions. The three new files are appended to the `05` list (PVC → Deployment → Service), matching its existing dependency ordering. |
| D-02 | Runtime UID (F-6) | Assume `10001` vs assume `1000` vs verify then pin | **Verify, then pin.** The implementation task runs `docker run --rm --entrypoint id ghcr.io/vectorize-io/hindsight:latest` (or `docker inspect -f '{{.Config.User}}'`) and writes the real numeric uid/gid into `runAsUser`/`runAsGroup`/`fsGroup`, with the discovered value recorded in `specs/022`. The manifest test asserts `runAsNonRoot: true`, that `runAsUser` is **present, numeric and non-zero**, and that `fsGroup == runAsUser` — invariants that survive whichever uid is real, instead of freezing a guess into an assertion. Rationale: `runAsNonRoot` with a **non-numeric** image `USER` makes the kubelet refuse the container outright (the exact bug `memory-router-deployment.yaml:35-37` documents), so a numeric value is mandatory — but a *wrong* numeric value means the PVC is owned by a uid the process does not have. If the image turns out to run as root, that is a blocking finding, not a silent `runAsNonRoot: false`. |
| D-03 | `readOnlyRootFilesystem` (proposal's flagged risk) | `false` + documented exception vs `true` + writable-mount design | **`true`, kept.** The write targets are known and finite, so the control does not need dropping: an **emptyDir at `/home/hindsight`** (writable home for stray `.config`/lock/socket writes), with the PVC mounted **nested inside it** at `/home/hindsight/.pg0` (`subPath: pg`) for the embedded Postgres data and at `/home/hindsight/.cache` (`subPath: cache`) for the HF model cache, plus the usual **emptyDir at `/tmp`**. `HOME=/home/hindsight` explicitly. Nested mounts under an emptyDir are legal and kubelet orders them by path depth. This is the only design in the change that could fail on first apply; if it does, the failure is a loud CrashLoop at startup with the offending path in the log, and the recorded fallback is a spec-022 amendment naming the exact path — not a pre-emptive blanket exception. Rejected: `readOnlyRootFilesystem: false` day one, which would make Hindsight the **only** service in this repo without the control, on a prediction nobody had verified. |
| D-04 | Model cache persistence | Cache on the `/tmp` emptyDir vs on the PVC | **On the PVC (`.cache` subPath).** The proposal's risk row promises the model download is a one-time cost, not per-restart; an emptyDir cache silently breaks that promise on every reschedule and re-imposes the startup egress. One PVC, two subPaths — no second volume, no second storage decision. |
| D-05 | ConfigMap | `hindsight-configmap.yaml` (proposal's Approach line) vs inline `env` | **Inline `env`, no ConfigMap.** Hindsight is configured purely by env vars — there is no config *file* to mount, which is the only reason `memory-router-configmap.yaml` exists. A ConfigMap holding five literal values adds a second place to look and a second object to keep in sync for zero gain. Documented divergence from the proposal's Approach paragraph. |
| D-06 | LLM model value (F-2/F-3) | `cloud` vs `gpt-5.6-sol` vs unset | **`HINDSIGHT_API_LLM_MODEL=gpt-5.6-sol`.** `cloud` only exists inside LiteLLM's `model_list`; sending it to codex-shim would be a name no component advertises. `gpt-5.6-sol` matches codex-shim's `/v1/models` id exactly, so a curious operator's `curl` and the manifest agree. |
| D-07 | `HINDSIGHT_BASE_URL` on memory-router | Explicit override vs rely on the fixed default | **No override — confirmed consistent.** After the code fix the default is `http://hindsight.mcps.svc.cluster.local:8888`; the Service is `name: hindsight`, `namespace: mcps`, port `8888`, and memory-router runs in `mcps`. The default resolves correctly with zero env. Adding a redundant override would reintroduce exactly the "the default is only correct because everyone overrides it" failure this change exists to kill. Only `HINDSIGHT_TOKEN` + `HINDSIGHT_AUTH_MODE=bearer` are added. |
| D-08 | Manifest test placement (F-4/F-7) | New `kubernetes/mcps/tests/` + `pytest.ini` (sibling-service style) vs root `tests/` bridge vs root `tests/` directly | **Root `tests/test_hindsight_manifest.py`, `unittest.TestCase`, direct — no bridge.** `kubernetes/mcps/` has no service source tree, so there is nothing for a local test dir to sit next to and nothing pytest-only to keep out; a `tests/` + `pytest.ini` pair there would land straight in F-1-of-local-embeddings territory (invisible to `unittest discover -s tests`). Writing it in the enforced root suite is the shortest path that is actually run by the strict-TDD gate. PyYAML in the root suite is already precedent (`tests/test_local_embeddings.py`). |
| D-09 | Secret shape | Two keys in one secret vs two separate secrets | **Two separate Secrets**, `hindsight-tenant-key` (key `tenant-api-key`) and `hindsight-codex-shim-key` (key `internal-key`). Different lifecycles and different owners: one is generated here and rotates freely; the other is a **mirror** whose value is dictated by `llms/codex-shim-key` and must never be rotated independently. Same key name `internal-key` as the source so the copy is a byte-identical extraction with no field renaming to get wrong. The tenant secret is referenced by **both** Deployments — one object, two env names, never two literals. |
| D-10 | Bootstrap idempotency (F-5) | New helper vs reuse the two existing shapes | **Reuse.** Tenant key: `openssl rand -hex 32` cached at `$MR_PKI_DIR/hindsight/tenant-api-key` (mode 600), so re-runs reuse rather than rotate — identical to the `bearers/` loop. codex-shim mirror: `kubectl -n llms get secret codex-shim-key -o jsonpath='{.data.internal-key}' | base64 -d`, aborting loudly if empty (the `MR_ENGRAM_TOKEN` pattern, lines 25-32) — **copied, never regenerated**, because a regenerated value is a 401 from codex-shim. Both then go through the existing `apply_secret`. |
| D-11 | Probes | Sibling tight probes only vs `startupProbe` first | **`startupProbe` on `/health` (`periodSeconds: 5`, `timeoutSeconds: 3`, `failureThreshold: 120` ≈ 10 min), then readiness `5s×3` and liveness `15s×4`.** Two serial cold-start costs stack here — embedded Postgres initdb *and* a first-run download of bge-small + the `ms-marco-MiniLM-L-6-v2` reranker over the network — which is a strictly worse worst case than local-embeddings' baked-in model (D-13 there: `60` ≈ 5 min). A too-tight budget CrashLoops the pod forever *before* the download completes, and the PVC cache means the long path is paid once. `/health` is the endpoint the adapter already uses (`hindsight.py` `ENDPOINTS["health"]`). |
| D-12 | Probe auth | Assume `/health` is public vs plan for 401 | **Assume public, verify on first apply.** `/health` is unauthenticated on the ephemeral instance validated in spec 015 §9.1 (the adapter's `health()` succeeded there), but `HINDSIGHT_API_TENANT_API_KEY` was not set in that run. If enabling the tenant key also guards `/health`, every probe returns 401 and the pod never becomes Ready — a loud, immediate, unambiguous failure. Fallback if it happens: `exec` probe (`/bin/true`-class liveness) or an `httpHeaders` Authorization entry sourced from the same secret. Recorded as an open question, not silently assumed away. |
| D-13 | `terminationGracePeriodSeconds` | Default `30` vs longer | **`60`.** Postgres needs a clean shutdown to avoid the corruption risk the proposal calls out; 30s is the sibling default sized for stateless HTTP pods. Doubling it is cheap and only ever costs time on delete. |
| D-14 | Embedding provider (F-8, supersedes the proposal's original bundled-`bge-small` plan) | `local`/`bge-small-en-v1.5` (proposal default) vs `local-embeddings` wiring (project policy) vs `onnx`/`multilingual-e5-small` | **`HINDSIGHT_API_EMBEDDINGS_PROVIDER=onnx` + `HINDSIGHT_API_EMBEDDINGS_ONNX_MODEL_ID=intfloat/multilingual-e5-small`.** Dominates both alternatives: multilingual (unlike bundled `bge-small`) and correctly prefixed (unlike `local-embeddings`, whose OpenAI-shaped endpoint gives Hindsight no `input_type` signal to distinguish `retain` from `recall` calls — see F-8). Also avoids adding `local-embeddings` as a runtime dependency of Hindsight, keeping the cross-namespace footprint to codex-shim alone. Confirmed exception to the project's "new/changed memory backends use `local-embeddings`" policy, made deliberately for this backend after live verification, not by default. |

## Data Flow

    memory-router (mcps)                                Hindsight pod (mcps)
      HINDSIGHT_TOKEN ─┐                                  HINDSIGHT_API_TENANT_API_KEY ─┐
      HINDSIGHT_AUTH_MODE=bearer                                                        │
      base_url default (no override, D-07)                                              │
        └─► POST http://hindsight.mcps.svc.cluster.local:8888/v1/default/banks/{id}/memories
             Authorization: Bearer <tenant-api-key> ───────── compared against ─────────┘
                                    (same Secret `hindsight-tenant-key`, two env names)

    Inside the pod:
      HTTP :8888 ──► fact extraction ──► POST http://codex-shim.llms.svc.cluster.local:8080/v1/chat/completions
                                          Authorization: Bearer <internal-key>   (mcps copy of llms/codex-shim-key)
                                          model: gpt-5.6-sol                     (D-06; codex-shim overrides anyway, F-3)
                  ──► embeddings + rerank ──► in-pod onnx: multilingual-e5-small / ms-marco-MiniLM-L-6-v2 (D-14)
                  ──► storage ──► embedded Postgres ──► /home/hindsight/.pg0  (PVC, subPath pg)
                      model weights ────────────────► /home/hindsight/.cache (PVC, subPath cache, D-04)
                      stray home / temp writes ─────► emptyDir /home/hindsight, emptyDir /tmp   (D-03)

    Bootstrap (03-create-secrets.sh):
      openssl rand -hex 32 ──► $MR_PKI_DIR/hindsight/tenant-api-key ──► Secret hindsight-tenant-key
      llms/codex-shim-key[internal-key] ──(copy, never regenerate)──► mcps/hindsight-codex-shim-key[internal-key]

## File Changes

| File | Action | Description |
|---|---|---|
| `kubernetes/mcps/hindsight-pvc.yaml` | Create | RWO, `local-path`, `10Gi`, name `hindsight-data`. Same header-comment style as `memory-router-pvc.yaml`, with the D-04 note that it holds Postgres data *and* the model cache. |
| `kubernetes/mcps/hindsight-deployment.yaml` | Create | `replicas: 1`, `Recreate`, `ghcr.io/vectorize-io/hindsight:latest` + `imagePullPolicy: Always`, D-02 securityContext, D-03 volumes, D-11 probes, D-13 grace period, `1`/`2Gi` → `4`/`6Gi`, all env per below. |
| `kubernetes/mcps/hindsight-service.yaml` | Create | ClusterIP `hindsight`, `8888 → 8888`, name `http`, selector `app: hindsight`; comment recording the deliberate no-Ingress boundary. |
| `kubernetes/mcps/bootstrap/03-create-secrets.sh` | Modify | Two new blocks (5 and 6) per D-10; closing `log` line updated from "All 4 secrets" to 6. |
| `kubernetes/mcps/bootstrap/05-deploy-manifests.sh` | Modify | Append `hindsight-pvc.yaml`, `hindsight-deployment.yaml`, `hindsight-service.yaml` to the ordered apply list + a second `rollout status`. |
| `kubernetes/mcps/bootstrap/00-config.sh` | Modify | `: "${MR_HINDSIGHT_IMAGE:=ghcr.io/vectorize-io/hindsight:latest}"` — reference only, mirroring how `MR_IMAGE_TAG` documents a value the flat manifest hardcodes. |
| `kubernetes/mcps/memory-router-deployment.yaml` | Modify | `+HINDSIGHT_TOKEN` (secretKeyRef `hindsight-tenant-key`/`tenant-api-key`), `+HINDSIGHT_AUTH_MODE: "bearer"`. No `HINDSIGHT_BASE_URL` (D-07). |
| `hermes-native/memory-router/src/memory_router/backends/hindsight.py` | Modify | Line 110: `:8080` → `:8888`. One line. |
| `tests/test_memory_router_hindsight_adapter.py` | Modify | Line 42 assertion: `:8080` → `:8888`. One line. |
| `tests/test_hindsight_manifest.py` | Create | D-08 manifest suite. |
| `specs/015_hindsight_backend.md` | Modify | §4 table line 159 default → `:8888`; §8 checklist "Despliegue real…" → `[x]` with a pointer to spec 022. |
| `specs/022_hindsight_deployment.md` | Create | Numbered companion: deployed contract, the verified UID (D-02), the bundled-embedding one-way door, the two documented duplications, the `local-embeddings` follow-up. |
| `openspec/specs/hindsight-service/spec.md` | Create | New capability. |
| `openspec/specs/memory-backend-adapters/spec.md` | Modify | Delta: default port `8888`, deployed auth mode `bearer`. |
| `kubernetes/policy/` | **Unchanged** | Explicit no-op (proposal decision). |

## Interfaces / Contracts

```yaml
# hindsight-deployment.yaml — env block (secrets by ref only, never literals)
- {name: HINDSIGHT_API_HOST,         value: "0.0.0.0"}
- {name: HINDSIGHT_API_PORT,         value: "8888"}
- {name: HOME,                       value: "/home/hindsight"}          # D-03
- {name: HINDSIGHT_API_LLM_PROVIDER, value: "openai"}
- {name: HINDSIGHT_API_LLM_MODEL,    value: "gpt-5.6-sol"}              # D-06 / F-2
- {name: HINDSIGHT_API_LLM_BASE_URL, value: "http://codex-shim.llms.svc.cluster.local:8080/v1"}
- name: HINDSIGHT_API_LLM_API_KEY
  valueFrom: {secretKeyRef: {name: hindsight-codex-shim-key, key: internal-key}}   # F-1 mirror
- name: HINDSIGHT_API_TENANT_API_KEY
  valueFrom: {secretKeyRef: {name: hindsight-tenant-key,     key: tenant-api-key}}
- {name: HINDSIGHT_API_EMBEDDINGS_PROVIDER,  value: "onnx"}                         # D-14 / F-8
- {name: HINDSIGHT_API_EMBEDDINGS_ONNX_MODEL_ID, value: "intfloat/multilingual-e5-small"}
```

```yaml
# hindsight-deployment.yaml — volumes (D-03/D-04)
volumeMounts:
  - {name: home, mountPath: /home/hindsight}
  - {name: data, mountPath: /home/hindsight/.pg0,   subPath: pg}
  - {name: data, mountPath: /home/hindsight/.cache, subPath: cache}
  - {name: tmp,  mountPath: /tmp}
volumes:
  - {name: home, emptyDir: {}}
  - {name: data, persistentVolumeClaim: {claimName: hindsight-data}}
  - {name: tmp,  emptyDir: {}}
```

```yaml
# memory-router-deployment.yaml — added env (no HINDSIGHT_BASE_URL, D-07)
- name: HINDSIGHT_TOKEN
  valueFrom: {secretKeyRef: {name: hindsight-tenant-key, key: tenant-api-key}}
- {name: HINDSIGHT_AUTH_MODE, value: "bearer"}
```

```python
# hindsight.py:107-111 — the whole code change
self._base_url = _env_default(
    base_url,
    "HINDSIGHT_BASE_URL",
-   "http://hindsight.mcps.svc.cluster.local:8080",
+   "http://hindsight.mcps.svc.cluster.local:8888",
)
```

## Testing Strategy

| Layer | What to test | Approach |
|---|---|---|
| Unit — adapter (enforced, RED first) | Zero-arg `HindsightBackend()` with the five `HINDSIGHT_*` env vars cleared resolves `http://hindsight.mcps.svc.cluster.local:8888` | Edit the existing `test_defaults_when_no_env_and_no_explicit_args` assertion (`tests/test_memory_router_hindsight_adapter.py:42`) — it fails before the one-line source fix and passes after |
| Unit — manifest (enforced, new) | image == `ghcr.io/vectorize-io/hindsight:latest` and `imagePullPolicy: Always`; `containerPort: 8888`; Service `type: ClusterIP`, `port`/`targetPort` 8888, selector matches the pod labels; `replicas: 1`; `strategy.type == Recreate`; a volumeMount at `/home/hindsight/.pg0` backed by the `hindsight-data` PVC; PVC `10Gi` / `local-path` / `ReadWriteOnce`; **both** secretKeyRefs present with exact `{name, key}` pairs; `HINDSIGHT_API_LLM_BASE_URL` points at codex-shim; **no plaintext secret value anywhere** (no bare `value:` under either secret env); `runAsNonRoot: true` + numeric non-zero `runAsUser` + `fsGroup == runAsUser` (D-02); `readOnlyRootFilesystem: true`; `capabilities.drop == ["ALL"]`; `allowPrivilegeEscalation: false`; `seccompProfile: RuntimeDefault`; `automountServiceAccountToken: false`; startupProbe present with `failureThreshold >= 60`; **no `kind: Ingress` anywhere in `kubernetes/mcps/hindsight-*.yaml`** | `tests/test_hindsight_manifest.py`, `unittest.TestCase` + `yaml.safe_load_all`, following `kubernetes/local-embeddings/tests/test_local_embeddings_manifest.py` |
| Unit — cross-manifest (enforced) | `memory-router-deployment.yaml` carries `HINDSIGHT_AUTH_MODE == "bearer"` and a `HINDSIGHT_TOKEN` secretKeyRef pointing at the **same** `{hindsight-tenant-key, tenant-api-key}` pair as the Hindsight Deployment's `HINDSIGHT_API_TENANT_API_KEY`, and carries **no** `HINDSIGHT_BASE_URL` (D-07) | Same file; parse both manifests and compare the two refs field-by-field — this is the "secret drifts between the two Deployments" risk turned into an assertion |
| Integration | Adapter against the live pod | Not automated. Manual, per the proposal's success criteria: unauthenticated request rejected / bearer accepted; `store` then `search` round-trip with `backend == "hindsight"`; `kubectl delete pod` → memories survive; codex-shim logs show Hindsight's calls |
| E2E | Full bootstrap replay | Not automated. `03-create-secrets.sh` run twice must be a no-op (same tenant value, same mirrored value); `05-deploy-manifests.sh` reaches `rollout status` success |

The bootstrap script is not unit-tested — it is not tested today either (F-5), and the value it produces is only verifiable against a live cluster. Its correctness is enforced at the *consumption* end instead: the manifest tests pin the exact secret names and keys the script must create.

## Threat Matrix

| Boundary | Applicability | Design response | Planned RED test |
|---|---|---|---|
| Shell / subprocess | **Applicable** — `03-create-secrets.sh` gains a `kubectl get ... \| base64 -d` pipeline | No caller-supplied input reaches the command line; both new blocks use fixed literal namespace/secret/key names; `set -euo pipefail` is already in force; the extracted value is quoted at every expansion and aborts loudly when empty rather than creating an empty secret | Manual: run with `codex-shim-key` absent → script exits non-zero **without** applying an empty secret |
| Secret handling | Applicable | Secrets exist only as `secretKeyRef`; no value is ever written into a manifest, a ConfigMap, or a log line; the generated tenant key is cached mode-600 outside the repo under `$MR_PKI_DIR` | Manifest test asserts neither secret env has an inline `value:` |
| Cross-namespace trust | Applicable | Exactly one cross-namespace dependency (`llms/codex-shim`), reached by ClusterIP DNS with a bearer; the secret is a deliberate copy because k8s Secrets are namespace-scoped, and the bootstrap script is its single writer | Manual: rotate `llms/codex-shim-key`, re-run the script, confirm both copies match |
| Auth / unauthenticated access | Applicable | Tenant bearer required from day one; no Ingress, ClusterIP only; a mismatch is a loud 401, no permissive fallback (settled input 4) | Manual: in-cluster `curl` without the header must be rejected |
| Filesystem write under `readOnlyRootFilesystem` | Applicable — D-03 | Writable home emptyDir + PVC subPaths + `/tmp` emptyDir; `HOME` set explicitly | Manifest assertions; first-apply CrashLoop is the runtime check |
| Outbound network / egress | Applicable | Two egress paths only: `ghcr.io` at pull time and the HF model download on first start (cached on the PVC thereafter, D-04). No NetworkPolicy — explicit proposal decision, ClusterIP + no Ingress is the boundary | None (manifest asserts no Ingress) |
| Data integrity | Applicable — the one-way door | `intfloat/multilingual-e5-small` @ 384-dim (D-14), multilingual, binds every vector written; any future model/dimension swap is a re-embed, not a config flip. Recorded in `specs/022`, exactly as spec 021 recorded its own — materially lower risk than the original English-only plan since no known Spanish-recall gap remains to motivate a near-term migration | None automatable; spec text is the artifact |
| Resource exhaustion | Applicable | `1`/`2Gi` requests, `4`/`6Gi` limits; undersizing surfaces as OOMKill, which is loud | Manifest asserts requests and limits are both set |
| Routing / VCS or PR automation / executable-file classification | N/A | No routing change, no VCS automation, no executable classification | None |

## Migration / Rollout

No data migration — Hindsight starts empty. Order matters and is single-shot (settled input 4):

1. `03-create-secrets.sh` (both secrets must exist before either Deployment rolls).
2. `kubectl apply -f hindsight-pvc.yaml`, then `hindsight-deployment.yaml`, then `hindsight-service.yaml`.
3. `kubectl apply -f memory-router-deployment.yaml` — **same apply window**. Between steps 2 and 3 memory-router has no token and every `/projects/*` store gets 401; the router already degrades over an unavailable Hindsight (`memory-backend-adapters` partial-unavailability requirement), so the window is safe but must be short. No grace period with auth off, by decision.
4. Watch the first rollout closely: the D-11 startup budget, the D-03 read-only assumption and the D-12 probe-auth assumption all get their real verdict here.

Rollback is the proposal's split plan, unchanged. The genuine one-way door is the first vector persisted, not the manifests.

## Delivery Forecast

| Slice | Authored lines (est.) |
|---|---|
| **PR 1 — port fix + deployable service** (`hindsight.py` 1, adapter test 1, three manifests ~150, memory-router env ~10, bootstrap ~40) | **~200** |
| **PR 2 — enforcement + specs** (`tests/test_hindsight_manifest.py` ~180, `specs/022` ~300, `openspec/specs/hindsight-service/` ~130, `specs/015` edits ~5) | **~615** |

`Decision needed before apply: Yes`
`Chained PRs recommended: Yes`
`400-line budget risk: High`

Recommendation: two chained PRs on the deploy/document seam (PR 2 targets PR 1's branch), matching how the archived local-embeddings change split. PR 1 fits inside the 400-line budget on its own and is independently verifiable (adapter test goes green; manifests apply and roll out). PR 2 exceeds the budget and will need an explicit `size:exception`, consistent with the accepted exceptions on the memory-backend changes; if declined, it splits at tests-vs-specs. Strict TDD ordering is preserved *within* PR 1 — the adapter assertion is edited to `8888` and observed failing before the source line changes. `sdd-tasks` owns the binding guard lines.

## Open Questions

- [ ] **D-02 — the image's real UID.** Blocking for the Deployment manifest, resolvable in one command (`docker inspect -f '{{.Config.User}}' ghcr.io/vectorize-io/hindsight:latest`) during implementation. If it resolves to root or to a non-numeric name with no numeric mapping, that is a design finding that must come back here, not be papered over with `runAsNonRoot: false`.
- [ ] **D-03 — `readOnlyRootFilesystem: true` survives first start.** Designed for, not proven. Verdict arrives on first apply; the fallback is an amendment naming the exact offending path.
- [ ] **D-12 — whether `/health` stays unauthenticated once `HINDSIGHT_API_TENANT_API_KEY` is set.** If not, probes need an `httpHeaders` Authorization entry or an exec probe.
- [ ] **D-11 — the 10-minute startup budget is an estimate**, not a measurement. Two models plus initdb over an unmeasured link; generous on purpose, tune down after the first real cold start is timed.
- [ ] Whether Hindsight's fact-extraction calls tolerate codex-shim's `model` override (F-3) and its Chat-Completions→Responses translation for tool/JSON-mode requests. Spec 015 §9.1 validated this path live for the retain flow, so the risk is low but not zero for prompt shapes that validation did not exercise.
- [ ] `:latest` remains unpinned. Named follow-up: pin a digest once a known-good version is validated.
- [ ] **Superseded, closed during design.** Wiring Hindsight to `local-embeddings` (spec 021) was evaluated as an alternative to the original bundled-`bge-small` plan and rejected in favor of D-14 (`onnx` + `multilingual-e5-small`), which resolves the multilingual gap without `local-embeddings`'s prefix-signal limitation (F-8). No longer an open follow-up for this backend.
- [ ] Whether Hindsight's `onnx`-provider first-run model download (`multilingual-e5-small`, HuggingFace) shares the same PVC-cache treatment as the LLM/reranker path (D-04) — needs confirming at `sdd-tasks`/`sdd-apply` time that the ONNX model cache directory lands under `/home/hindsight/.cache` alongside the reranker, not a separate uncached path.
