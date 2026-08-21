# Design: Local Embeddings Service

## Technical Approach

A fourth `kubernetes/` service directory that is byte-for-byte conventional with `codex-shim`/`model-panel` where it can be (`python:3.12-slim`, uid 10001, uvicorn on 8080, `create_app()` + `@asynccontextmanager lifespan`, `HOME=/tmp` + emptyDir, `/healthz` probes, ClusterIP, `kustomization.yaml` listing `rbac.yaml, deployment.yaml, service.yaml`) and deliberately divergent only where CPU inference forces it (resources, startup probe, zero-RBAC SA, baked model layer).

Three modules, one seam:

- `app/embeddings.py` — **pure**. No `fastapi`, no `fastembed`, no I/O. Validation, batching rules, response/usage assembly, error taxonomy. Depends on an injected `Embedder` protocol.
- `app/model.py` — the ONNX adapter. Lazy-imports `fastembed` *inside* the loader so the pure module and the HTTP module both import cleanly on a dev machine with no `fastembed` installed.
- `app/main.py` — FastAPI: lifespan model load, route wiring, threadpool offload, error-shape rendering.

This mirrors the proposal's Approach line ("pure request→vector translation separate from the HTTP layer, embedder injectable") and matches the `alerts/state.py` (pure) vs `alerts/ticker.py` (I/O) split already proven in `model-panel`.

## Verified Findings (read from current repo)

- **F-1 — the enforced test command does not reach `kubernetes/`.** `openspec/config.yaml` sets `test_command: python -m unittest discover -s tests`. `-s tests` roots discovery at the repo-root `tests/` package only; `kubernetes/codex-shim/tests/` and `kubernetes/model-panel/tests/` are outside that tree and are **never executed** by the strict-TDD command today.
- **F-2 — and they could not be, as written.** Every sibling service test is a module-level plain function (`def test_deployments_scale_subresource_grants_get():`, `test_webhook_manifest.py:33`) with `@pytest.fixture` parameters (`model-panel/tests/conftest.py:142`). `unittest` collects only `TestCase` subclasses, so pointing discovery at those directories would collect **zero** tests while still importing `pytest`. Their `pytest.ini` (`testpaths = tests`, `asyncio_mode = auto`) is the only thing that runs them.
- **F-3 — the root suite is `unittest.TestCase` and stdlib-pure.** `tests/test_memory_router_registry.py:1-11` imports only `sys`/`unittest`/`pathlib` plus the module under test, reached via `sys.path.insert`. A grep for `import yaml|fastapi|httpx|pytest` across `tests/` returns **no matches**. Third-party imports in the enforced suite would be a new precedent, not an existing one.
- **F-4 — `model-panel` already uses the app-factory + lifespan shape** (`main.py:218-227`), so the model-load-in-lifespan pattern is established, not invented here.
- **F-5 — sibling resources are uniform** (`100m`/`500m` CPU, `128Mi`/`256Mi` memory) and are sized for a proxy that holds no model. Copying them would OOMKill this pod on the first request.

## Architecture Decisions

| # | Decision | Options / tradeoff | Choice and rationale |
|---|---|---|---|
| D-01 | Model load timing | Per-request vs module import vs lifespan | **Lifespan.** Import-time load makes `app/main.py` unimportable without `fastembed` and its ~500MB of weights, which would poison every unit test. Per-request is a non-starter (seconds per call). Lifespan also gives the readiness contract for free: `app.state.embedder` is `None` until the load returns. Injectable — `create_app(embedder=fake)` skips the loader entirely, which is how the HTTP tests run. |
| D-02 | Where the baked model lives | `/app/models` vs `$HOME` vs `/opt/models/fastembed` | **`/opt/models/fastembed`, written as root during build, then `USER 10001`.** Owned by root, mode 0755: readable by the runtime uid, writable by nobody. It sits outside `/app` so a `COPY app ./app` layer rebuild does not invalidate the ~500MB model layer. `FASTEMBED_CACHE_PATH` and an explicit `cache_dir=` both point at it. |
| D-03 | Proving zero runtime egress | Trust the cache vs enforce it | **Enforce: `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, `HF_HOME=/tmp`, `XDG_CACHE_HOME=/tmp` in `deployment.yaml`.** If the cache path ever misses, the pod must fail loudly at startup rather than silently reach the internet — which, with no NetworkPolicy (proposal decision), it otherwise could. `HOME`/`XDG_CACHE_HOME` on the `/tmp` emptyDir catches any stray HF write against `readOnlyRootFilesystem: true`. |
| D-04 | RBAC | Copy the sibling SA+Role vs SA only | **Own ServiceAccount `local-embeddings`, no Role, no RoleBinding, and `automountServiceAccountToken: false`.** Siblings need the API (Secrets, deployment scale); this service needs nothing. `rbac.yaml` still exists so `kustomization.yaml` stays conventional and the intent is recorded rather than inferred from an absence. Deliberate divergence from the sibling template. |
| D-05 | Inference concurrency | Bare `def` handler vs `async def` + threadpool vs threadpool + gate | **`async def` + `run_in_threadpool` + an `asyncio.Semaphore(1)` around it.** The threadpool keeps the event loop free (proposal risk row). The semaphore exists because ONNX is CPU-bound: N concurrent 256-item batches on a 2-core limit multiply memory and thrash rather than parallelize. Serialized inference makes latency predictable and bounds in-flight memory to one batch. Callers queue; nobody is throttled with an error. |
| D-06 | Batch ceiling enforcement | Truncate vs queue vs reject | **Reject >256 with 400 `batch_size_exceeded`** (settled input 5). Truncation silently drops data; queueing an unbounded batch is the memory risk D-05 exists to close. |
| D-07 | `dimensions` param | Ignore vs pad/truncate vs reject on mismatch | **Echo-safe reject: absent or `== 384` proceeds; any other value is 400 `dimension_mismatch`.** Padding/truncation is the hard invariant the proposal forbids (settled input 4) — a padded 1536-vector is a permanently poisoned index that no test downstream can detect. |
| D-08 | `encoding_format` | Implement base64 vs reject | **`float` (or absent) proceeds; `base64` is 400 `unsupported_encoding_format`.** fastembed yields `float32` arrays; base64 would mean hand-rolling OpenAI's little-endian float32 packing, which is real work with no consumer asking for it. Explicit 400 beats a silently-wrong body. |
| D-09 | `model` field | Strict allow-list vs accept-and-echo | **Accept any string, always serve the pinned model, echo the request's string back** (settled input 3). Consumers hardcoded to `text-embedding-3-small` work unpatched. `GET /v1/models` still advertises the real id, so a curious client can discover the truth. |
| D-10 | e5 query/passage prefixes | Auto-prefix vs verbatim vs opt-in param | **Opt-in `input_type` request field, default verbatim.** `intfloat/multilingual-e5-*` was trained with `query: ` / `passage: ` prefixes, but `/v1/embeddings` has no query-vs-document channel by default, and the same endpoint serves both ingest and search for every consumer. Auto-prefixing by inferring intent would be an invisible, asymmetric semantic mutation — rejected. Instead: an **optional** request field `input_type: "query" \| "passage"`. Omitted (the case for every stock OpenAI-SDK client, which doesn't know this field exists) → embed verbatim, unchanged from before — the service stays a true drop-in replacement. Present → prepend the matching `query: `/`passage: ` string before embedding. This is additive on the wire (unknown-field-tolerant clients are unaffected) and gives memory-router's own adapters (which we control) a path to correct asymmetric retrieval quality without forcing every other consumer to opt in. Decided now, in this change, rather than deferred — small addition (one field + prefix concat + a handful of scenarios), stays inside the PR1 line budget. |
| D-11 | `usage` token counts | Omit vs approximate vs tokenizer | **Adapter-supplied `count_tokens()` on the `Embedder` protocol: exact tokenizer counts when the fastembed tokenizer is reachable, else a documented `ceil(len(text)/4)` fallback.** The success criterion requires `usage` populated; a guarded `getattr` chain into fastembed internals must not be able to 500 a valid request. The fake embedder in tests returns deterministic counts, so the pure assembly logic is exactly testable either way. |
| D-12 | Error body shape | FastAPI's default `{"detail": ...}` (422) vs OpenAI's envelope | **OpenAI's `{"error": {message, type, param, code}}` with HTTP 400, including a `RequestValidationError` handler so malformed bodies do not leak FastAPI's 422 shape.** The whole point of the service is that an OpenAI SDK client works unmodified; SDK error parsing is part of that contract. |
| D-13 | Startup vs liveness race | Generous liveness vs `startupProbe` | **`startupProbe` on `/healthz`, `periodSeconds: 5`, `failureThreshold: 30` (150s budget), with liveness kept at the sibling's tight settings.** Loading ~500MB of ONNX on a cold CPU node takes tens of seconds; without a startup probe the sibling liveness settings (`15s × 4`) would CrashLoop the pod forever before it ever became ready. |
| D-14 | Resources (diverges from F-5) | Sibling values vs sized-for-inference | **requests `500m` / `768Mi`, limits `2` / `1536Mi`; `OMP_NUM_THREADS=2` + `ORT_*` intra-op 2.** `multilingual-e5-small` is ~118M params (a 250k-token vocab dominates), ≈470MB fp32 ONNX, resident in RAM for the pod's whole life — 768Mi request covers weights + interpreter + tokenizer with headroom; 1536Mi limit absorbs ORT's arena on a 256-item batch. CPU request 500m keeps it schedulable next to the GPU workloads; the 2-core limit plus a matching thread cap stops ORT from spawning one thread per host core and being CFS-throttled into worse latency than single-threaded. |
| D-15 | Test runner (F-1/F-2/F-3) | pytest-only (sibling convention) vs unittest-only vs both | **Tests are written as `unittest.TestCase` classes in `kubernetes/local-embeddings/tests/` — collectible by *both* runners — plus a root-level bridge module `tests/test_local_embeddings.py` that `sys.path.insert`s the service dir and imports the pure-core and manifest cases into the enforced suite.** See below. |

### D-15 expanded — resolving the `pytest.ini` vs `unittest discover` conflict

The conflict is real and the sibling services are on the losing side of it: per F-1/F-2 their tests are invisible to the strict-TDD command. Copying that convention would mean this service's RED tests are never run by the gate that `strict_tdd: true` implies.

| What | Where | Runner |
|---|---|---|
| Pure core (`test_embeddings_core.py`) — validation, batch cap, ordering, usage, error taxonomy | `kubernetes/local-embeddings/tests/` as `TestCase`, **bridged** into root `tests/` | unittest **and** pytest |
| Manifest (`test_local_embeddings_manifest.py`) — GPU/security/service invariants | same, **bridged** | unittest **and** pytest |
| HTTP layer (`test_api.py`) — `TestClient`, lifespan, threadpool, handler wiring | `kubernetes/local-embeddings/tests/` only | pytest only |

The bridge is one ~12-line module: `sys.path.insert(...)` then `from test_embeddings_core import *` / `from test_local_embeddings_manifest import *`, which is exactly the `sys.path.insert` reach-across already used at `tests/test_memory_router_registry.py:5`.

Consequences accepted explicitly:
- The bridged manifest tests import `yaml`, breaking F-3's stdlib-pure root suite. Justified: the manifest invariants (no GPU, `readOnlyRootFilesystem`, `drop: [ALL]`, ClusterIP, no Ingress) are the highest-value regressions in this change and belong in the enforced gate. PyYAML is already a de-facto dev dependency of `model-panel`'s tests; it is declared in `kubernetes/local-embeddings/requirements-dev.txt`.
- **Rejected: a `try/except ImportError → SkipTest` guard on the bridge.** A silently skipped security assertion is worse than a loud missing dependency.
- HTTP tests are *not* bridged: pulling `fastapi` into the root suite is a heavier precedent, and D-01's injectable embedder plus the pure-core split means the HTTP module holds only wiring, with all decision logic already under the enforced runner.
- Retrofitting the sibling services onto this pattern is **out of scope** and left as a follow-up; this change does not touch their files.

## Data Flow

    POST /v1/embeddings
      │
      ├─ FastAPI/pydantic parse ──(malformed)──► RequestValidationError handler ──► 400 OpenAI envelope (D-12)
      ▼
    embeddings.validate_request(body)                      # PURE — no I/O, no fastembed
      │   input: str -> [str] | list[str] passthrough
      │   len > 256                 ──► EmbeddingError("batch_size_exceeded")     (D-06)
      │   encoding_format=="base64" ──► EmbeddingError("unsupported_encoding_format")
      │   dimensions not in (None,384) ──► EmbeddingError("dimension_mismatch")   (D-07)
      ▼  EmbeddingRequest(texts=[...], echo_model="text-embedding-3-small")
    async with app.state.inference_gate:                   # Semaphore(1)         (D-05)
        vectors = await run_in_threadpool(embedder.embed, texts)
      ▼
    embeddings.build_response(req, vectors, token_counts)  # PURE
      │   assert every len(v) == 384  ──► 500 (invariant breach, never truncate)  (D-07)
      ▼
    {"object":"list","data":[{"object":"embedding","index":i,"embedding":[...]}],
     "model": echo_model, "usage":{"prompt_tokens":n,"total_tokens":n}}

    lifespan startup ──► model.load_embedder()  [/opt/models/fastembed, offline] ──► app.state.embedder
    GET /healthz     ──► 200 {"status":"ok"} iff app.state.embedder is not None, else 503   (D-01/D-13)
    GET /v1/models   ──► the pinned id, static, no model touch

## File Changes

| File | Action | Description |
|---|---|---|
| `kubernetes/local-embeddings/app/__init__.py` | Create | Package marker. |
| `kubernetes/local-embeddings/app/embeddings.py` | Create | Pure core: `Embedder` protocol, `EmbeddingError`, `validate_request`, `build_response`, `MODEL_ID`, `DIMENSION = 384`, `MAX_BATCH = 256`. No third-party imports. |
| `kubernetes/local-embeddings/app/model.py` | Create | `load_embedder()` — lazy `import fastembed`, `TextEmbedding(MODEL_ID, cache_dir=...)`, `count_tokens` with guarded tokenizer access (D-11). |
| `kubernetes/local-embeddings/app/main.py` | Create | `create_app(embedder=None)`, lifespan, three routes, semaphore, error handlers. |
| `kubernetes/local-embeddings/requirements.txt` | Create | `fastapi`, `uvicorn[standard]`, `fastembed` — all pinned, matching the sibling pin style. |
| `kubernetes/local-embeddings/requirements-dev.txt` | Create | `pytest`, `pyyaml`, `httpx` (TestClient). |
| `kubernetes/local-embeddings/Dockerfile` | Create | Sibling template + a `RUN python -c "...TextEmbedding(...)"` bake step into `/opt/models/fastembed` before `USER 10001` (D-02). |
| `kubernetes/local-embeddings/deployment.yaml` | Create | Sibling security template; **no `nvidia.com/gpu`**; D-03 env; D-13 startupProbe; D-14 resources; `automountServiceAccountToken: false`. |
| `kubernetes/local-embeddings/service.yaml` | Create | ClusterIP `local-embeddings`, port 8080 → `http`, commented no-Ingress rationale like `codex-shim/service.yaml`. |
| `kubernetes/local-embeddings/rbac.yaml` | Create | ServiceAccount only, with the D-04 comment explaining the absent Role. |
| `kubernetes/local-embeddings/kustomization.yaml` | Create | `rbac.yaml, deployment.yaml, service.yaml`. |
| `kubernetes/local-embeddings/pytest.ini` | Create | `testpaths = tests` (sibling parity for local runs). |
| `kubernetes/local-embeddings/tests/{__init__,test_embeddings_core,test_local_embeddings_manifest,test_api}.py` | Create | Per D-15. |
| `tests/test_local_embeddings.py` | Create | Root bridge into the enforced suite (D-15). |
| `openspec/specs/local-embeddings/` | Create | Capability delta. |
| `specs/021_local_embeddings_service.md` | Create | Numbered spec companion. |
| `kubernetes/policy/netpol-llms.yaml` | **Unchanged** | Explicit no-op (proposal decision). |

## Interfaces / Contracts

```python
# app/embeddings.py — pure
MODEL_ID  = "intfloat/multilingual-e5-small"
DIMENSION = 384
MAX_BATCH = 256

class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...
    def count_tokens(self, texts: list[str]) -> list[int]: ...

class EmbeddingError(Exception):
    def __init__(self, code: str, message: str, param: str | None = None,
                 status: int = 400, type_: str = "invalid_request_error") -> None: ...
```

```jsonc
// POST /v1/embeddings — request
{"input": "hola" | ["hola", "mundo"], "model": "text-embedding-3-small",
 "encoding_format": "float",   // optional; "base64" -> 400
 "dimensions": 384}            // optional; anything else -> 400
// 200
{"object": "list", "model": "text-embedding-3-small",
 "data": [{"object": "embedding", "index": 0, "embedding": [/* 384 floats */]}],
 "usage": {"prompt_tokens": 3, "total_tokens": 3}}
// 4xx — every client error, one shape
{"error": {"message": "batch size 300 exceeds the maximum of 256",
           "type": "invalid_request_error", "param": "input",
           "code": "batch_size_exceeded"}}
```

| Condition | Status | `code` | `param` |
|---|---|---|---|
| `len(input) > 256` | 400 | `batch_size_exceeded` | `input` |
| `encoding_format == "base64"` (or other non-`float`) | 400 | `unsupported_encoding_format` | `encoding_format` |
| `dimensions` present and `!= 384` | 400 | `dimension_mismatch` | `dimensions` |
| `input` missing / not str-or-list-of-str / empty list / empty string | 400 | `invalid_input` | `input` |
| Model not yet loaded | 503 | `model_not_ready` | — |

`GET /healthz` → `200 {"status":"ok","model":MODEL_ID,"dimension":384}` once loaded, else `503`.
`GET /v1/models` → `{"object":"list","data":[{"id":MODEL_ID,"object":"model","owned_by":"local"}]}`.

## Testing Strategy

Injected fake embedder throughout (`embed` returns `[float(i)] * 384`, `count_tokens` returns `len(t.split())`) — deterministic, no ONNX, no network, no model download in CI.

| Layer | What to test | Approach |
|---|---|---|
| Unit — core (enforced) | str→1 vector; list→N vectors with `index` 0..N-1 **in input order**; 256 passes / 257 rejects; `base64` rejects; `dimensions=1536` rejects and `384`/absent pass; empty string & empty list reject; `model` echoed verbatim including unknown names; `usage` non-zero; a fake returning a 1536-length vector raises instead of truncating (D-07 invariant) | `unittest.TestCase`, pure calls |
| Unit — manifest (enforced) | `nvidia.com/gpu` absent from requests **and** limits; `readOnlyRootFilesystem: true`; `runAsNonRoot: true`; `runAsUser: 10001`; `capabilities.drop == ["ALL"]`; `seccompProfile.type == RuntimeDefault`; `allowPrivilegeEscalation: false`; `/tmp` emptyDir mounted; `HOME=/tmp`; offline env vars present; startupProbe present; Service `type: ClusterIP`; **no `kind: Ingress` anywhere in the directory**; `kustomization.yaml` lists all three | `yaml.safe_load_all`, following `model-panel/tests/test_rbac_manifest.py` |
| Integration — HTTP (pytest only) | Round trip through `TestClient` with the fake; 400 bodies carry the OpenAI envelope not FastAPI's `detail`; malformed JSON → same envelope; `/healthz` 503 before load and 200 after; `/v1/models`; `Authorization: Bearer sk-anything` accepted and ignored; concurrent requests both succeed under the semaphore | `create_app(embedder=fake)` |
| E2E | Real ONNX inference, real image, real pod | **Not automated.** Manual: build, `kubectl apply -k`, curl from an in-namespace pod, confirm 384 floats and no egress. Downloading ~500MB in CI is out of scope. |

## Threat Matrix

| Boundary | Applicability | Design response | Planned RED test |
|---|---|---|---|
| Untrusted request input | Applicable | Input is only ever tokenized text; no eval, no path, no template, no format string. Size bounded by `MAX_BATCH` before any allocation. | Oversized batch rejected *before* the embedder is called (fake fails the test if invoked) |
| Outbound network / egress | Applicable | Model baked at build; `HF_HUB_OFFLINE=1`/`TRANSFORMERS_OFFLINE=1` at runtime (D-03); no NetworkPolicy by explicit proposal decision — offline env is the enforcement | Manifest asserts the offline vars; load path asserts the local `cache_dir` |
| Filesystem write under `readOnlyRootFilesystem` | Applicable | `HOME`/`XDG_CACHE_HOME`/`HF_HOME` → `/tmp` emptyDir; model dir root-owned, unwritable by uid 10001 | Manifest assertions |
| Privilege / GPU contention | Applicable | No GPU request (must never compete with spec 012's handoff); non-root, drop ALL, no privilege escalation, no SA token mounted (D-04) | Manifest assertions |
| Data-integrity (wrong-dimension vectors) | Applicable — the one-way door | Hard invariant, never pad/truncate; assertion in `build_response` | Fake emitting a wrong-length vector must raise |
| Resource exhaustion / DoS | Applicable | Batch cap + `Semaphore(1)` + memory limit (D-05/D-06/D-14) | Batch-cap unit test; concurrency test |
| Auth | N/A by design | Cluster-internal, no Ingress; `Authorization` accepted and ignored for SDK compatibility | Header present → still 200 |
| Shell / subprocess / VCS or PR automation / executable-file classification / routing | N/A | HTTP and in-process ONNX only; no shell, no subprocess, no VCS | None |

## Migration / Rollout

No migration — the service is stateless and nothing consumes it yet. Rollout is `kubectl apply -k kubernetes/local-embeddings/`; rollback is `kubectl delete -k` plus a commit revert, per the proposal. The genuine one-way door is not the code but the **first consumer that persists a vector**: from that moment `intfloat/multilingual-e5-small` @ 384 is a re-embed migration, not a config edit. That is why the model id and dimension are pinned in the spec, asserted in the core tests, and echoed by `/healthz` and `/v1/models` so a drifted deployment is observable from outside.

## Delivery Forecast

| Slice | Authored lines (est.) |
|---|---|
| **PR 1 — service code** (`embeddings.py` ~130, `model.py` ~60, `main.py` ~140, requirements ~10, `Dockerfile` ~20, core + HTTP tests ~280) | **~640** |
| **PR 2 — manifests, specs, enforcement** (4 manifests ~150, manifest test ~120, root bridge ~15, `openspec/specs/local-embeddings/` ~130, `specs/021` ~330) | **~745** |

`Decision needed before apply: Yes`
`Chained PRs recommended: Yes`
`400-line budget risk: High`

Recommendation: two chained PRs on the code/manifest seam (PR 2 targets PR 1's branch), matching how the archived backend changes split. PR 1 is autonomously verifiable — the pure core and the HTTP layer are fully testable with the fake, with no cluster. PR 2 is where the security invariants are asserted and the service becomes deployable. Both slices still exceed 400 authored lines and will each need an explicit `size:exception`, consistent with the accepted exceptions on the four memory-backend changes; if declined, PR 1 splits at the pure/HTTP seam (`embeddings.py` + core tests, then `model.py` + `main.py` + HTTP tests) and PR 2 splits at manifests-and-tests vs specs. `sdd-tasks` owns the binding guard lines.

## Open Questions

- [x] **D-10 e5 prefixes.** Resolved: opt-in `input_type` request field (default verbatim), built in this change — see the Decisions table. Residual note: any vector already stored via verbatim (no `input_type`) calls stays verbatim; a caller that starts passing `input_type` later is not retroactively re-embedding prior vectors — that's the same "mixing" caveat, now scoped to whoever opts in, not a whole-fleet migration question.
- [ ] **D-11 token counts.** The exact-tokenizer path depends on fastembed internals that are not a stable public API; the `ceil(len/4)` fallback makes `usage` approximate. Fine for the current consumers, wrong if anything ever bills on it.
- [ ] Image size: the baked model layer is ~500MB on top of `python:3.12-slim` + ONNX Runtime. Whether the k3s node's image store has headroom for that alongside the existing images is unverified from this repo and should be checked before the first build.
- [ ] `Semaphore(1)` (D-05) makes throughput exactly one batch at a time. If bulk ingest turns out to need more, the tuning knob is the semaphore plus the CPU limit together — never one without the other.
- [ ] D-15 leaves `codex-shim` and `model-panel` tests outside the enforced suite. Retrofitting them is deliberately out of scope here, but it is a standing gap in the strict-TDD guarantee that someone should own.
