# Tasks: Local Embeddings Service

## Review Workload Forecast

The design's own forecast (2 slices, ~640 / ~745 authored lines) exceeded the
400-line budget on both. This breakdown re-slices along the design's three
natural seams (pure core, HTTP layer, manifests) so every chained PR fits.

| Field | Value |
|-------|-------|
| Estimated changed lines | PR1 ~365 / PR2 ~280 / PR3 ~290 (specs/021 + openspec spec already exist from prior phases; only checklist edits, ~10 lines, land in PR3) |
| 400-line budget risk | Low (per slice) / was High (unsliced) |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 (pure core + adapter) -> PR 2 (HTTP layer) -> PR 3 (manifests + enforcement + spec checklist) |
| Delivery strategy | chained PRs (explicit user decision, resolves review-budget risk) |
| Chain strategy | **Sequential against updated main** — PR1 merges to main first, PR2 branches fresh off that updated main, then PR3 branches off PR2's merged main. Same pattern already used for the 5 memory-router backend PRs this session; each PR is reviewable fully independently, none depends on another staying open. |

Decision needed before apply: Resolved (2026-08-21)
Chained PRs recommended: Yes
Chain strategy: sequential-against-main
400-line budget risk: Low

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Pure core (`embeddings.py`) + lazy fastembed adapter (`model.py`), fully unit-testable with a fake embedder, no server, no fastembed install required | PR 1 | `cd kubernetes/local-embeddings && python -m pytest tests/test_embeddings_core.py -v` | N/A — pure module, injected fake embedder, no cluster/model needed | delete `app/{__init__.py,embeddings.py,model.py}` and `tests/test_embeddings_core.py`; nothing else references them yet |
| 2 | FastAPI HTTP layer (`main.py`): lifespan load, 3 routes, semaphore, error envelope | PR 2 | `cd kubernetes/local-embeddings && python -m pytest tests/test_api.py -v` | `create_app(embedder=fake)` + `TestClient` in-process; no live cluster/ONNX needed | delete `app/main.py`, `tests/test_api.py`, `pytest.ini`; PR 1's pure core stays importable and tested standalone |
| 3 | Manifests (Dockerfile/deployment/service/rbac/kustomization), manifest tests, root-suite bridge, spec checklist closeout | PR 3 | `python -m unittest tests.test_local_embeddings -v` and `cd kubernetes/local-embeddings && python -m pytest tests/test_local_embeddings_manifest.py -v` | `kubectl apply -k kubernetes/local-embeddings/ --dry-run=client` (manifest validation only); real deploy + curl is manual E2E per design, out of automated scope | `kubectl delete -k kubernetes/local-embeddings/` (if applied) + revert commit; PR 1/PR 2 app code stays functional standalone |

## Phase 1: Pure Core (`embeddings.py`) — PR 1

- [x] 1.1 RED: create `kubernetes/local-embeddings/tests/test_embeddings_core.py` (`unittest.TestCase`, per D-15) with `validate_request`/`build_response` cases: single string -> 1 vector; list of 5 -> 5 entries with `index` 0..4 **in input order**.
- [x] 1.2 RED: add batch-ceiling cases — 256 inputs pass, 257 rejected with `batch_size_exceeded` / `param: "input"` (256-batch-ceiling 4xx behavior).
- [x] 1.3 RED: add `encoding_format` case — `"base64"` rejected with `unsupported_encoding_format`; `"float"`/absent proceed.
- [x] 1.4 RED: add `dimensions` cases — `1536` rejected with `dimension_mismatch`; `384`/absent proceed (never-pad-truncate invariant, D-07).
- [x] 1.5 RED: add invariant case — a fake embedder returning a non-384-length vector makes `build_response` raise, never truncate or pad.
- [x] 1.6 RED: add model-name case — request `model: "text-embedding-3-small"` succeeds and is echoed verbatim in the response `model` field while inference still targets the pinned model (accept-any-model-name-echo-back behavior, D-09).
- [x] 1.7 RED: add `input_type` cases (D-10, opt-in query/passage prefixing, decided 2026-08-21 — supersedes the original verbatim-only design): (a) omitted `input_type` — text passed to `Embedder.embed` is byte-identical to the request input, no prefix; (b) `input_type: "query"` — text passed is `"query: " + input`; (c) `input_type: "passage"` — text passed is `"passage: " + input`; (d) `input_type: "doc"` (or any other unknown value) — rejected with a 4xx `invalid_input_type` error, never silently falls back to verbatim or guesses an intent.
- [x] 1.8 RED: add `usage` case — a fake with deterministic `count_tokens` produces non-zero `prompt_tokens`/`total_tokens`; empty-input case rejected with `invalid_input`.
- [x] 1.9 GREEN: create `kubernetes/local-embeddings/app/embeddings.py` — `MODEL_ID`, `DIMENSION = 384`, `MAX_BATCH = 256`, `INPUT_TYPE_PREFIXES = {"query": "query: ", "passage": "passage: "}`, `Embedder` Protocol, `EmbeddingError`, `validate_request` (parses/validates `input_type`, applies the matching prefix or none), `build_response`, per design.md Interfaces/Data Flow (D-10 updated). No third-party imports.
- [x] 1.10 GREEN: run Phase 1 tests, confirm green.
- [x] 1.11 REFACTOR: confirm `embeddings.py` imports cleanly with zero third-party dependencies (`python -c "import app.embeddings"` in a clean venv).

## Phase 2: Lazy Fastembed Adapter (`model.py`) — PR 1

- [x] 2.1 RED: add adapter tests to `test_embeddings_core.py` (or a sibling `TestCase`) asserting `model.py` imports without `fastembed` installed (lazy import only inside `load_embedder`).
- [x] 2.2 GREEN: create `kubernetes/local-embeddings/app/model.py` — `load_embedder()` lazily importing `fastembed`, constructing `TextEmbedding(MODEL_ID, cache_dir="/opt/models/fastembed")`, `count_tokens` with a guarded tokenizer-access fallback to `ceil(len(text)/4)` (D-11).
- [x] 2.3 GREEN: create `kubernetes/local-embeddings/requirements.txt` (`fastapi`, `uvicorn[standard]`, `fastembed`, pinned) and `requirements-dev.txt` (`pytest`, `pyyaml`, `httpx`).
- [x] 2.4 GREEN: run Phase 2 tests, confirm green; run full Phase 1+2 suite.

## Phase 3: HTTP Layer (`main.py`) — PR 2

- [x] 3.1 RED: create `kubernetes/local-embeddings/tests/test_api.py` (pytest only, `TestClient`) — round-trip `POST /v1/embeddings` via `create_app(embedder=fake)`; 4xx bodies use the OpenAI `{"error": {...}}` envelope, never FastAPI's default `detail` shape (D-12).
- [x] 3.2 RED: add malformed-JSON case asserting the same OpenAI envelope via the `RequestValidationError` handler.
- [x] 3.3 RED: add `/healthz` cases — 503 before `app.state.embedder` is set, 200 once loaded (D-01/D-13).
- [x] 3.4 RED: add `GET /v1/models` case asserting the pinned model id is returned without touching the embedder.
- [x] 3.5 RED: add `Authorization: Bearer sk-dummy` case — request succeeds identically to no header (accept-and-ignore).
- [x] 3.6 RED: add concurrency case — two concurrent requests both succeed and are serialized through `asyncio.Semaphore(1)` (D-05), asserting no more than one in-flight call to the fake embedder at a time.
- [x] 3.7 GREEN: create `kubernetes/local-embeddings/app/main.py` — `create_app(embedder=None)`, `@asynccontextmanager` lifespan calling `model.load_embedder()` when no embedder is injected, 3 routes, `run_in_threadpool` + semaphore around inference, `RequestValidationError`/`EmbeddingError` handlers rendering the OpenAI envelope.
- [x] 3.8 GREEN: create `kubernetes/local-embeddings/pytest.ini` (`testpaths = tests`, `asyncio_mode = auto`), matching `codex-shim`/`model-panel` (per spec 021 section 5 decision — this directory does not use `unittest discover`).
- [x] 3.9 GREEN: run Phase 3 tests, confirm green.
- [x] 3.10 REFACTOR: confirm `main.py` holds only wiring — no validation/response-assembly logic duplicated from `embeddings.py`.

## Phase 4: Manifests — PR 3

- [x] 4.1 RED: create `kubernetes/local-embeddings/tests/test_local_embeddings_manifest.py` (`unittest.TestCase`, `yaml.safe_load_all`, per D-15/model-panel pattern) asserting: no `nvidia.com/gpu` in requests or limits; `readOnlyRootFilesystem: true`; `runAsNonRoot: true`; `runAsUser: 10001`; `capabilities.drop == ["ALL"]`; `seccompProfile.type == RuntimeDefault`; `allowPrivilegeEscalation: false`.
- [x] 4.2 RED: add resource-sizing assertions — `requests` `500m`/`768Mi`, `limits` `2`/`1536Mi`, and `OMP_NUM_THREADS=2` present in container env (D-14 resource sizing + thread-count callout).
- [x] 4.3 RED: add offline/cache assertions — `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, `HOME=/tmp`, `XDG_CACHE_HOME=/tmp`, `/tmp` emptyDir mounted (D-03, baked-model-at-build-time / zero-runtime-egress).
- [x] 4.4 RED: add `startupProbe` presence assertion (D-13) and Service assertions — `type: ClusterIP`, no `kind: Ingress` anywhere in the directory, `automountServiceAccountToken: false` (D-04).
- [x] 4.5 RED: add `kustomization.yaml` assertion — lists exactly `rbac.yaml, deployment.yaml, service.yaml`.
- [x] 4.6 GREEN: create `kubernetes/local-embeddings/Dockerfile` — sibling template (`python:3.12-slim`, uid 10001, uvicorn) plus a build-time `RUN python -c "...TextEmbedding(...)"` bake step into `/opt/models/fastembed` before `USER 10001` (D-02).
- [x] 4.7 GREEN: create `kubernetes/local-embeddings/deployment.yaml` — no GPU request, D-03 env vars, D-13 `startupProbe`, D-14 resources + `OMP_NUM_THREADS=2`, `readOnlyRootFilesystem: true` preserved.
- [x] 4.8 GREEN: create `kubernetes/local-embeddings/service.yaml` — ClusterIP `local-embeddings`, port 8080 -> `http`, commented no-Ingress rationale.
- [x] 4.9 GREEN: create `kubernetes/local-embeddings/rbac.yaml` — ServiceAccount only, comment explaining the absent Role/RoleBinding (D-04).
- [x] 4.10 GREEN: create `kubernetes/local-embeddings/kustomization.yaml` listing the three manifests.
- [x] 4.11 GREEN: run Phase 4 tests, confirm green.

## Phase 5: Root-Suite Enforcement Bridge — PR 3

- [x] 5.1 RED: create `tests/test_local_embeddings.py` at repo root — expect import failure before the bridge exists.
- [x] 5.2 GREEN: implement the bridge — `sys.path.insert(...)` to `kubernetes/local-embeddings/tests/`, then `from test_embeddings_core import *` and `from test_local_embeddings_manifest import *` (per D-15; mirrors `tests/test_memory_router_registry.py:5`'s reach-across). Do **not** bridge `test_api.py` — HTTP/TestClient tests stay pytest-only.
- [x] 5.3 GREEN: run `python -m unittest tests.test_local_embeddings -v` from repo root, confirm the bridged core + manifest cases execute and pass under `unittest`.
- [x] 5.4 GREEN: run `cd kubernetes/local-embeddings && python -m pytest -v`, confirm all tests (core, manifest, HTTP) pass under `pytest` too.
- [x] 5.5 REFACTOR: confirm `test_api.py` is absent from both the bridge file and any `unittest discover` collection path.

## Phase 6: Spec Checklist Closeout — PR 3

- [x] 6.1 Update `specs/021_local_embeddings_service.md` section 8 checklist — mark "Tareas (sdd-tasks)" done, add the ordered PR1->PR2->PR3 task-group summary (mirroring this file's Suggested Work Units); since PR 3 is landing now, "Implementación (sdd-apply)" is also marked complete with the full-chain summary.
- [x] 6.2 Confirm `openspec/specs/local-embeddings/spec.md` requirements match what was actually implemented (no drift); no edits needed — every requirement/scenario maps directly to the Phase 4-6 manifests and tests.
- [x] 6.3 Run the full local suite one more time (`python -m unittest tests.test_local_embeddings -v` + `cd kubernetes/local-embeddings && python -m pytest -v`) as the PR 3 closing gate — 38/38 and 47/47 green.
