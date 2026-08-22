# Proposal: Local Embeddings Service (self-hosted OpenAI-compatible `/v1/embeddings`)

**Correction (2026-08-22, post-merge):** the model pin discussed below
(`intfloat/multilingual-e5-small`) doesn't exist in `fastembed` — the
shipped model is `intfloat/multilingual-e5-large` (1024 dims, ~2.24GB).
See `specs/021_local_embeddings_service.md`'s correction note for the
full story; this proposal is kept as the historical record of the
original decision process, not updated line-by-line.

## Intent

The cluster has no embeddings endpoint. Every memory backend that needs vectors (Honcho `embedding.model_config`, Graphiti `embedder`, Cognee embedding config, and later Hindsight / knowledge-vault / Engram) must currently point at OpenAI — external egress, per-token cost, and a hard dependency on a key. `llama-router` is not a substitute: it requests `nvidia.com/gpu: "1"` and is unavailable whenever spec 012 hands the GPU to Cloud, and its Qwen chat models are not embedding-optimized.

Goal: a **durable, general-purpose, cluster-wide embeddings utility** — CPU-only, zero external calls, zero token cost, always up regardless of GPU handoff state. Unblocking live Honcho/Graphiti/Cognee validation is the motivating first use case, not the scope ceiling.

## Scope

### In Scope
- New service dir `kubernetes/local-embeddings/` (namespace `llms`), sibling to `codex-shim`/`model-panel`, following their exact conventions.
- FastAPI app exposing `POST /v1/embeddings` (OpenAI-compatible request/response, `input` as string or list, `model` echoed) plus `GET /healthz` and `GET /v1/models`.
- `fastembed` (ONNX Runtime, no torch/CUDA) with one pinned model + pinned dimension, baked into the image at build time so runtime egress is zero.
- Manifests: `Dockerfile` (python:3.12-slim, uid 10001, uvicorn), `deployment.yaml` (replicas 1, Recreate, runAsNonRoot / readOnlyRootFilesystem / drop ALL / RuntimeDefault, `HOME=/tmp` + emptyDir, probes on `/healthz`, **no GPU request**), `service.yaml` (ClusterIP, no Ingress), `rbac.yaml` (own least-privilege SA), `kustomization.yaml`.
- Consumption contract: `LOCAL_EMBEDDINGS_BASE_URL=http://local-embeddings.llms.svc.cluster.local:8080/v1`, mirroring `CODEX_SHIM_BASE_URL`.
- Unit tests: request/response contract, batching, error shapes, plus `test_local_embeddings_manifest.py` asserting parsed YAML (per the model-panel manifest-test pattern).
- `specs/021_local_embeddings_service.md`.

### Out of Scope
- Deploying Honcho / Graphiti / Cognee containers themselves (separate blocker).
- Wiring any consumer's config to this service; wiring lands with each consumer.
- Reranking, `/v1/completions`, chat, or any non-embedding endpoint.
- Authentication. Cluster-internal, no Ingress — an `Authorization` header is accepted and ignored for client compatibility.
- A per-service `NetworkPolicy` (see Decisions).
- Vector storage, indexing, or migration of existing vectors.
- GPU acceleration, autoscaling, multi-model serving.

## Capabilities

### New Capabilities
- `local-embeddings`: the OpenAI-compatible embeddings contract — endpoint shape, pinned model/dimension guarantee, batching, error semantics, and the no-external-egress / no-GPU invariants.

### Modified Capabilities
- None. No existing `openspec/specs/` capability covers embeddings.

## Approach

Thin FastAPI wrapper over a single long-lived `fastembed.TextEmbedding` instance created once at startup (lifespan), so per-request cost is inference only. The ONNX model is **downloaded during `docker build`** into an image path and loaded from there with the cache dir set to that path — this preserves `readOnlyRootFilesystem: true`, removes a runtime network dependency, and makes the image self-contained and reproducible. Pure request→vector translation is a separate module from the HTTP layer so it is unit-testable without a server; the embedder is injectable so tests use a deterministic fake.

## Decisions

| Decision | Resolution |
|---|---|
| NetworkPolicy | **None added.** `kubernetes/policy/netpol-llms.yaml` has no default-deny in `llms`; broad in-namespace consumption is the explicit design goal. ClusterIP + no Ingress is the boundary. Recorded deliberately, not omitted. |
| Model + dimension | Pinned in spec as a forward-compatibility contract (anything storing vectors binds to it). Default proposed below, pending confirmation. |
| Auth | None. Accept-and-ignore `Authorization` so OpenAI SDK clients work unmodified. |

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `kubernetes/local-embeddings/` | New | App, Dockerfile, manifests, tests. |
| `kubernetes/policy/netpol-llms.yaml` | Unchanged | Explicit no-op decision. |
| `specs/021_local_embeddings_service.md` | New | Numbered spec companion. |
| `openspec/specs/local-embeddings/` | New | Full capability spec. |
| Consumer manifests (Honcho/Graphiti/Cognee) | Deferred | Wired when those containers land. |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Dimension is a one-way door once vectors are persisted | **High impact** | Pin dimension in the spec; treat a change as a re-embed migration, not a config tweak. |
| An English-only model degrades Spanish/multilingual memories | Med | Open question 1 — settle model choice before spec. |
| Model download at build time makes the image large / build network-dependent | Med | Small ONNX model (tens–hundreds of MB); build-time only, runtime egress stays zero. |
| CPU latency on large batches blocks the event loop | Med | Run inference in a threadpool; cap batch size and return a clear 4xx above it. |
| A consumer sends OpenAI params we ignore (`encoding_format`, `dimensions`) | Med | Spec explicit behavior: honor `float`, reject `base64` and mismatched `dimensions` with a clear error rather than silently. |
| readOnlyRootFilesystem breaks ONNX/HF cache writes | Med | Baked-in read-only cache path + `HOME=/tmp` emptyDir, asserted by a manifest test. |

## Rollback Plan

Fully additive and isolated. `kubectl delete -k kubernetes/local-embeddings/` removes the Deployment/Service/SA; nothing else in the cluster references it yet, so no consumer breaks. Revert the commit to drop the directory and `specs/021`. No persisted state, no schema, no migration — the service is stateless. If a consumer has already stored vectors against it, rollback requires that consumer to re-embed; that coupling only exists after a consumer is wired, which is out of scope here.

## Dependencies

- New Python deps in the service only: `fastembed`, `fastapi`, `uvicorn`. No repo-wide dependency change.
- Build-time network access to fetch the ONNX model.
- Sufficient node CPU/memory headroom in `llms` (model resident in RAM).

## Success Criteria

- [ ] `POST /v1/embeddings` with a string input returns a valid OpenAI-shaped response with the pinned dimension.
- [ ] A list input returns one `data` entry per item, in order, with correct `index` values.
- [ ] The response `usage` and `model` fields are populated and the `object` fields match the OpenAI schema.
- [ ] An unsupported `encoding_format`/`dimensions` yields a clear 4xx, never a silently wrong vector.
- [ ] `GET /healthz` returns ready only after the model is loaded.
- [ ] Manifest tests assert: no `nvidia.com/gpu` request, `readOnlyRootFilesystem: true`, `runAsNonRoot`, `drop: [ALL]`, ClusterIP, and no Ingress object.
- [ ] The pod starts and serves with **no network egress** (model resolved from the image).
- [ ] An OpenAI-SDK-style client pointed at `LOCAL_EMBEDDINGS_BASE_URL` works unmodified, including sending a dummy API key.

## Proposal question round

Interactive mode, but no direct question channel was available to this executor. These shape the spec; each carries a proposed default that `sdd-spec` should adopt unless corrected.

1. **Model and dimension (the one-way door).** Proposed default: `BAAI/bge-small-en-v1.5`, 384 dims — smallest, fastest, fastembed's default. **Concern**: it is English-only, and your memories are likely Spanish/mixed. If multilingual retrieval quality matters, prefer `intfloat/multilingual-e5-small` (384) or `multilingual-e5-large` (1024, notably heavier CPU). Which matters more: RAM/latency footprint, or Spanish recall quality?
2. **Model-name compatibility.** Consumers may be hardcoded to `text-embedding-3-small`. Proposed: accept any `model` string and always serve the pinned model, echoing back the requested name, so no consumer needs patching. Alternative is strict rejection of unknown names (safer, but forces per-consumer config edits).
3. **Dimension mismatch with consumer defaults.** Honcho/Graphiti/Cognee often assume 1536. Proposed: each consumer declares the real dimension in its own config when wired (out of scope here); this proposal does **not** pad or truncate vectors to fake 1536. Confirm padding is unwanted.
4. **Batch ceiling and slow-path behavior.** Proposed: cap at 256 inputs per request, reject above it with 4xx rather than degrading; inference off the event loop. Confirm the cap is not too low for bulk-ingest use.
5. **Directory name.** Proposed `kubernetes/local-embeddings/` with Service name `local-embeddings` (DNS `local-embeddings.llms.svc.cluster.local`). Your brief wrote `local-embeddings-service...` in the example URL — confirm which name is canonical, since it becomes the DNS contract.

Note also: the exploration handoff stated `openspec/changes/` does not exist yet. That is incorrect — it exists with several active and archived changes, and `openspec/config.yaml` is present (`strict_tdd: true`, `test_command: python -m unittest discover -s tests`). No bootstrap was needed. Be aware the config's test command is `unittest`, while the exploration notes describe a `pytest.ini` convention for the `kubernetes/` services; `sdd-tasks` should reconcile which runner applies to this new service dir.
