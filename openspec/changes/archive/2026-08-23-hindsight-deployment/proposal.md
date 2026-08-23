# Proposal: Hindsight Deployment (real in-cluster instance for the `mcps` namespace)

## Intent

`HindsightBackend` ships, is entry-point registered, and is unit-tested against a fake transport — but nothing answers at `hindsight.mcps.svc.cluster.local`. The adapter is dead code in production: `/projects/*` stores and searches route to a backend that is permanently unavailable, so memory-router silently degrades and no Hindsight memory is ever validated end-to-end. `specs/015_hindsight_backend.md`'s open checklist item "Despliegue real de una instancia de Hindsight en el clúster" is the exact gap.

Worse, the adapter's hardcoded default `HINDSIGHT_BASE_URL` uses port **8080**, while Hindsight's real `HINDSIGHT_API_PORT` default is **8888**. Left alone, this becomes a permanent deploy-time footgun where every future operator must remember an override to compensate for a wrong default in code.

Goal: a real, self-contained, authenticated Hindsight pod in `mcps`, plus the code fix that makes the adapter's own default correct.

## Scope

### In Scope
- New manifests in `kubernetes/mcps/` following that dir's flat `hindsight-*.yaml` convention (sibling to `memory-router-*.yaml`): `hindsight-deployment.yaml`, `hindsight-service.yaml`, `hindsight-pvc.yaml`.
- Image `ghcr.io/vectorize-io/hindsight:latest` pulled from a public registry — **first image in this repo with no local Dockerfile**.
- Embedded Postgres persisted via PVC mounted at `/home/hindsight/.pg0`, `storageClassName: local-path`, matching `memory-router-pvc.yaml`.
- LLM wiring to `codex-shim` at `http://codex-shim.llms.svc.cluster.local:8080/v1`, authenticated with a **duplicated copy of codex-shim's internal-bearer secret into the `mcps` namespace** (k8s Secrets are namespace-scoped; the original lives in `llms`).
- Embeddings via Hindsight's own bundled `onnx` provider (`HINDSIGHT_API_EMBEDDINGS_PROVIDER=onnx`, `HINDSIGHT_API_EMBEDDINGS_ONNX_MODEL_ID=intfloat/multilingual-e5-small`) + its reranker, fully in-pod. **Amended from the original `BAAI/bge-small-en-v1.5` (English-only) plan** — the `onnx` provider supports a multilingual model swap via env var, and handles `query:`/`passage:` E5 prefixing natively inside Hindsight itself (`HINDSIGHT_API_EMBEDDINGS_ONNX_QUERY_PREFIX`/`_PASSAGE_PREFIX`, defaults `"query: "`/`"passage: "`) — a correctness property that routing through the external `local-embeddings` service (OpenAI-shaped, no `input_type` signal) cannot provide. See Decisions.
- **Bearer auth from day one**: a generated shared secret set as `HINDSIGHT_API_TENANT_API_KEY` on the server and as `HINDSIGHT_TOKEN` + `HINDSIGHT_AUTH_MODE=bearer` on memory-router, referenced by both Deployments.
- **Port fix in code**: change `hermes-native/memory-router/src/memory_router/backends/hindsight.py`'s default base_url port `8080` → `8888`; update `tests/test_memory_router_hindsight_adapter.py:42` which asserts the old default; update `specs/015_hindsight_backend.md` §4's config table.
- Bootstrap-script coverage for the new secrets, consistent with `kubernetes/mcps/bootstrap/03-create-secrets.sh`.
- Manifest tests asserting parsed YAML (image, port `8888`, PVC mount path, secret refs, ClusterIP, no Ingress), per the local-embeddings/model-panel manifest-test pattern.
- `specs/022_hindsight_deployment.md`.

### Out of Scope
- Wiring Hindsight to the external `local-embeddings` service (spec 021). Evaluated and explicitly **rejected** for this backend (not merely deferred): Hindsight's own bundled `onnx` provider already covers the multilingual need with correct native query/passage prefixing, which routing through `local-embeddings`'s OpenAI-compatible endpoint cannot provide (no `input_type` signal reaches the server). Consolidating onto one cluster embeddings endpoint remains a theoretical future option but is no longer the obvious next step once the bundled path is this capable.
- Any Ingress or external exposure. In-cluster consumer only.
- Building or forking a Hindsight image.
- External Postgres, HA, replicas > 1, backups of the embedded DB.
- Changing the adapter's request/response wire format, verbs, or bank mapping — only the default port changes.
- Per-tenant / multi-tenant key management beyond the single tenant key.

## Capabilities

### New Capabilities
- `hindsight-service`: the deployed Hindsight instance contract — in-cluster DNS/port, bearer-auth requirement, persistence guarantee across restarts, self-contained LLM/embeddings dependencies, and the no-Ingress boundary.

### Modified Capabilities
- `memory-backend-adapters`: the Hindsight Adapter requirement's default `HINDSIGHT_BASE_URL` changes port `8080` → `8888`, and the deployed configuration is now `bearer` auth rather than the unauthenticated default.

## Approach

Single-pod, single-Deployment, `Recreate` strategy (RWO PVC + embedded Postgres cannot tolerate two writers). Hindsight is configured entirely through env vars — no ConfigMap file mounting needed beyond a small `hindsight-configmap.yaml` for non-secret values, keeping secrets strictly in Secret refs.

Everything stateful lives on one PVC so a pod restart is a no-op for memory content. Everything non-local (the LLM) goes to `codex-shim`, which already fronts the cluster's models; everything else (Postgres, embeddings, reranking) stays inside the pod, so Hindsight has exactly one cross-namespace dependency and no external egress.

The port fix is deliberately made in code rather than papered over with a Deployment env override: a wrong default that is only correct because every caller overrides it is a latent bug, and the adapter's default should be the value that actually works.

## Decisions

| Decision | Resolution |
|---|---|
| Port mismatch | Fixed in **code** (`8888`), plus test and `specs/015` §4 table. No env override compensating for a wrong default. |
| Auth | **Enabled day one.** Shared generated secret: `HINDSIGHT_API_TENANT_API_KEY` (server) ↔ `HINDSIGHT_TOKEN` + `HINDSIGHT_AUTH_MODE=bearer` (client). |
| codex-shim bearer secret | **Duplicated into `mcps`.** Secrets are namespace-scoped; no cross-namespace reference exists. Duplication is deliberate, and the bootstrap script owns it so the two copies stay in sync. |
| Embeddings source | Hindsight's bundled `onnx` provider with `intfloat/multilingual-e5-small` (multilingual, native query/passage prefixing), not `local-embeddings` and not the original English-only `bge-small-en-v1.5`. Fewer moving parts (no cross-namespace `mcps`→`llms` dependency), correct prefix handling, multilingual day one — no known reason left to prefer `local-embeddings` for this backend. |
| NetworkPolicy for `mcps` | **None added.** Same reasoning as `local-embeddings`: no default-deny exists in `mcps`, ClusterIP + no Ingress is the real boundary, and broad in-namespace traffic between memory-router and Hindsight is the whole point. Recorded deliberately, not omitted. |
| Resource sizing | No precedent to copy — embedded Postgres + bge-small + reranker is a different profile than local-embeddings' e5-large-only pod. Sized from first principles, conservative, then monitored. Proposed starting point below. |
| Image tag `:latest` | Accepted for day one with `imagePullPolicy: Always`; pinning a digest is a follow-up once a known-good version is validated. |

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `kubernetes/mcps/hindsight-*.yaml` | New | Deployment, Service, PVC, ConfigMap. |
| `kubernetes/mcps/bootstrap/03-create-secrets.sh` | Modified | Generate the tenant key; duplicate codex-shim's bearer secret into `mcps`. |
| `kubernetes/mcps/memory-router-deployment.yaml` | Modified | `HINDSIGHT_TOKEN`, `HINDSIGHT_AUTH_MODE=bearer` env (secret refs). |
| `hermes-native/memory-router/src/memory_router/backends/hindsight.py` | Modified | Default base_url port `8080` → `8888`. |
| `tests/test_memory_router_hindsight_adapter.py` | Modified | Default-base_url assertion (line 42). |
| `specs/015_hindsight_backend.md` | Modified | §4 config table default; close the deployment checklist item. |
| `specs/022_hindsight_deployment.md` | New | Numbered spec companion. |
| `openspec/specs/hindsight-service/` | New | Full capability spec. |
| `kubernetes/policy/` | Unchanged | Explicit no-op NetworkPolicy decision. |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| `:latest` silently ships a breaking Hindsight version | **Med/High impact** | `imagePullPolicy: Always` plus a follow-up to pin a digest once validated; manifest test asserts the image ref so a change is never accidental. |
| Embedded Postgres corrupted by a non-graceful restart / two writers | Med | `Recreate` strategy, `replicas: 1`, RWO PVC; generous `terminationGracePeriodSeconds` so Postgres shuts down cleanly. |
| Resource sizing is guesswork (Postgres + 2 models in one pod) | High | Conservative requests, headroom-generous limits, then measure. Undersizing manifests as OOMKill, which is loud, not silent. |
| Bearer secret drifts between the two Deployments | Med | One Secret, referenced by both — never two literals. Bootstrap script is the single generator. |
| Duplicated codex-shim secret drifts from the `llms` original | Med | Bootstrap script copies from the source of truth rather than re-generating; documented in `specs/022` as a known duplication. |
| First model download at startup needs egress and delays readiness | Med | Long `startupProbe` failure threshold; models cached on the PVC so it is a one-time cost, not per-restart. |
| Embedding model/dimension is still a one-way door (any future model swap needs a re-embed) | Low | `intfloat/multilingual-e5-small` (384-dim, multilingual, native prefixing) is the day-one choice; record it in `specs/022` as the stated commitment, exactly as spec 021 recorded its own. Materially lower risk than the original `bge-small-en-v1.5` plan since Spanish recall no longer needs a future migration to fix. |
| `readOnlyRootFilesystem` breaks Postgres / model cache writes | Med | Writable PVC mount + `HOME` on the PVC; if the upstream image cannot run read-only, record the deviation explicitly rather than silently dropping the control. |

## Rollback Plan

Split rollback, because this change has two independent halves.

**Manifests (additive, isolated):** `kubectl delete deployment/service/configmap hindsight -n mcps` returns the cluster to today's state — memory-router's Hindsight backend goes back to "unavailable", which the router already degrades over gracefully (`memory-backend-adapters` partial-unavailability requirement). Keep or delete the PVC independently; keeping it preserves stored memories for a retry. Removing `HINDSIGHT_TOKEN`/`HINDSIGHT_AUTH_MODE` from memory-router restores `none` auth.

**Code (port default):** reverting the one-line default plus its test restores `8080`. Safe in isolation because nothing was listening on `8080` in the first place; the only way this breaks someone is if they had already deployed with an explicit `HINDSIGHT_BASE_URL` override, which continues to win over the default either way.

Reverting the commit removes the manifests, the spec, and the port fix together. Persisted Hindsight memories are the only non-reversible artifact, and they are new data, not a migration of existing data.

## Dependencies

- `codex-shim` running and reachable at `codex-shim.llms.svc.cluster.local:8080` with its internal-bearer secret available to copy.
- Cluster node headroom in `mcps` for embedded Postgres + two resident models.
- `local-path` storageClass available (already used by `memory-router-journal`).
- Registry egress to `ghcr.io` at pull time, and model-download egress on first start.

## Success Criteria

- [x] `HindsightBackend()` with no arguments and no env resolves to `http://hindsight.mcps.svc.cluster.local:8888`.
- [x] The pod reaches Ready and its health endpoint answers on port 8888 inside the cluster.
- [x] An unauthenticated in-cluster request is **rejected**; the same request with the tenant bearer token succeeds. (Required a follow-up fix, PR #58 — `HINDSIGHT_API_TENANT_API_KEY` alone had no effect; see `specs/022` §8.1 Bug 1.)
- [x] memory-router `store` to a `/projects/*` namespace reaches Hindsight and returns success — not a degraded/unavailable marker. (Required a second follow-up — the deployed `memory-router` image was stale, never rebuilt after the port fix merged; see `specs/022` §8.1 Bug 2.)
- [x] A subsequent `search` returns the stored memory with `backend == "hindsight"`.
- [x] Deleting the pod and letting it reschedule preserves previously stored memories (PVC persistence proven, not assumed).
- [x] Hindsight's LLM calls resolve through `codex-shim` — verified in codex-shim logs, not inferred.
- [x] Manifest tests assert: image ref, port 8888, PVC mount at `/home/hindsight/.pg0`, both secret refs, `replicas: 1`, `Recreate`, ClusterIP, and that no Ingress object exists.
- [x] `specs/015_hindsight_backend.md`'s deployment checklist item is closed and its §4 table shows `8888`.

## Proposal question round

Interactive mode, but this executor had no direct question channel to the user. These do not block `sdd-spec`/`sdd-design`; each carries a proposed default to adopt unless corrected.

1. **Resource sizing starting point.** No precedent exists for this profile. Proposed: `requests: 1 CPU / 2Gi`, `limits: 4 CPU / 6Gi` (embedded Postgres ~512Mi, bge-small ~150Mi, reranker ~300Mi, plus JVM/Python and query headroom). Is a 6Gi ceiling acceptable on your node, or should this start tighter and be raised on the first OOMKill?
2. **PVC size.** Proposed `10Gi` — Postgres data plus cached model weights, versus `memory-router-journal`'s `1Gi` which holds only a transient queue. Confirm, since `local-path` PVCs are not trivially expandable in-place.
3. **`:latest` versus a pinned digest.** Proposed: `:latest` + `imagePullPolicy: Always` day one, pin after validation. The tradeoff is unattended breakage risk versus not yet knowing which version is good. Prefer pinning a specific tag now if you already have a version you trust.
4. **Auth blast radius on rollout order.** Enabling `HINDSIGHT_API_TENANT_API_KEY` means memory-router must be restarted with the matching token or every store fails 401. Proposed: land both Deployment changes in the same apply, and treat a 401 as loud failure rather than adding a permissive fallback. Confirm you do not want a grace period with auth off.
5. **Embedding source and language (resolved during proposal, superseding the original bundled-`bge-small` plan).** Originally proposed as `bge-small-en-v1.5` (English-only, 384-dim), then reconsidered against wiring the external `local-embeddings` service (multilingual e5-large, 1024-dim) per the project's standing policy that new/changed backends use `local-embeddings`. Live investigation of Hindsight's config surface found a third option that dominates both: Hindsight's own `onnx` provider supports `intfloat/multilingual-e5-small` (multilingual, 384-dim) **with native `query:`/`passage:` prefix handling inside Hindsight itself** — a correctness property `local-embeddings` cannot offer through its OpenAI-compatible endpoint (no signal to distinguish a query call from a passage call, so both would embed unprefixed, a symmetric-but-degraded ranking quality). **Resolved: bundled `onnx` + `intfloat/multilingual-e5-small`**, day one. This is a deliberate, confirmed exception to the local-embeddings policy for this specific backend, not an oversight.
