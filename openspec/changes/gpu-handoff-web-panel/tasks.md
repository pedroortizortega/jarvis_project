# Tasks: GPU Handoff Web Panel (Amendment 3 — Local Profile Picker gets a real backend, D18/D18a)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~2150–2650 (was ~1850–2250; +~300–400 for a new `llama_router.py` client, `steps.py`/`main.py`/`panel.js` hunks, and D18/D18a RED tests, per Amendment 3) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 → PR 2 → PR 3 → PR 4 → PR 5 → PR 6 |
| Delivery strategy | auto-chain |
| Chain strategy | feature-branch-chain |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: feature-branch-chain
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | D-OQ1 spike script (throwaway, not merged/kept minimal) + `--slots` on router | PR 1 | `python spike_codex_probe.py` | Manual run against live `chatgpt.com/backend-api/codex` with a real Codex session | Delete spike script; revert `deployment-router.yaml` `--slots` arg |
| 2 | `codex-shim` app: auth, store, session, **translate (vendored request-side converter + hand-rolled streaming emitter + non-streaming assembly)**, proxy, main + RBAC/manifests | PR 2 | `pytest kubernetes/codex-shim/tests -k "auth or store or session or translate"` | `kind`/fake K8s API for Secret get/patch; stubbed token endpoint; fake Responses-API event iterator for translation tests | Delete `kubernetes/codex-shim/`; delete `codex-shim-auth`/`codex-shim-key` Secrets |
| 3 | `model-panel` handoff core: StepRunner, undo stack, GPU/drain probes, state CM | PR 3 | `pytest kubernetes/model-panel/tests -k "runner or gpu or drain or state"` | Fake K8s API server (integration harness) | Delete `kubernetes/model-panel/app/handoff/`; no cluster mutation yet |
| 4 | `model-panel` app wiring: main.py routes, codex_shim client, UI, manifests | PR 4 | `pytest kubernetes/model-panel/tests -k "api or client"` | `kind` cluster or fake API server for `/api/switch` fail-closed test | Delete `kubernetes/model-panel/`; revert none (additive) |
| 5 | **(Amendment 3, new)** `model-panel` Local Profile Picker backend (D18/D18a): guarded `POST /api/profile` sequence — drain → preload → confirm → alias patch → LiteLLM restart, reusing PR3's StepRunner/undo primitives; new `llama_router.py` client; UI picker enabled | PR 5 | `pytest kubernetes/model-panel/tests -k profile` | Fake K8s API server + fake router HTTP client (preload/confirm) integration harness | Delete `kubernetes/model-panel/app/clients/llama_router.py`; revert `steps.py`/`main.py`/`panel.js` hunks; picker reverts to disabled (PR4 state) |
| 6 | LiteLLM `cloud` entry + README + regression pass | PR 6 | `bash kubernetes/llama-service/switch-model.sh daily` (regression) | Live cluster manual E2E both directions from phone browser | Revert `litellm-config.yaml` hunk; README revert |

## Phase 0: Spike (gates everything else — D-OQ1)

- [x] 0.1 Write throwaway `spike_codex_probe.py` (outside `kubernetes/`, not part of any service) issuing one authenticated request to `https://chatgpt.com/backend-api/codex` with a non-Codex-CLI `User-Agent`/client identity, using an existing Hermes-refreshed access token.
- [x] 0.2 Run it manually; record accept/reject, any required headers (`originator`, session field), and whether body shape matches plain OpenAI Chat Completions. Record result in `sdd/gpu-handoff-web-panel/design` as an amendment note if D15's proxy shape must change.
      **Result: INCONCLUSIVE on client identity + BLOCKING finding on body shape.** The
      only available access token (`~/.hermes/auth.json` credential pool) is expired
      (JWT `exp` ~2026-08-06, 10 days before this run); both a bare non-Codex identity
      request and a `codex_cli_rs`-shaped request to `POST /backend-api/codex/responses`
      got the same `401 token_expired` from OpenAI's own auth layer — neither was
      blocked by Cloudflare (`cf-mitigated` header absent both times), so the
      identity-rejection question could not be forced to a clean accept/reject from
      this network. Refreshing the token to get a valid one was **not attempted**: the
      refresh_token is single-use/rotating (D16) and using it — even without persisting
      the result — would invalidate Hermes's own live stored refresh_token and break
      the user's working Codex session; that is explicitly out of scope for a spike.
      Separately, and **not requiring live traffic to establish**: existing vendored
      logic in `kubernetes/docker/hermes-agent/agent/codex_runtime.py` (lines ~875-896)
      shows `chatgpt.com/backend-api/codex` is consumed via `client.responses.create()`
      / `responses.stream()` — the OpenAI **Responses API** (`input`, `/responses`,
      `response.output_item.done` events) — **not** plain Chat Completions. D15's
      "structurally identical, no translation" proxy assumption is therefore false
      regardless of the identity outcome.
- [x] 0.3 Gate check: **rejected outright is not what happened, but the body-shape finding above is blocking on its own** — D15 needs a Responses-API↔Chat-Completions translation layer before Phase 1+ (`codex-shim`'s `/v1/chat/completions` proxy, task 3.2) can be built as designed. Stopped here; design rework is out of scope for this apply run. Also unresolved: whether Cloudflare's documented originator allow-list (`kubernetes/docker/hermes-agent/agent/auxiliary_client.py:778-814`, `_codex_cloudflare_headers`) blocks the shim's actual in-cluster egress IP — untested, since the identity probe never reached an authorization decision.
- [x] 0.4 Delete/do not commit the spike script itself (throwaway, per design "throwaway script") — deleted from the scratchpad after the run; never touched the repo or git.

## Phase 1: codex-shim — Foundation (Requirement: Codex OAuth Session Ownership)

- [x] 1.1 Create `kubernetes/codex-shim/app/codex_auth.py`: vendor `refresh_codex_oauth_pure` + constants (`CODEX_OAUTH_TOKEN_URL`, `CODEX_OAUTH_CLIENT_ID`, `CODEX_ACCESS_TOKEN_REFRESH_SKEW_SECONDS=120`, `CODEX_RATE_LIMITED_CODE`) + `AuthError` classification, with provenance header citing source file/commit (D11). **Note**: `kubernetes/docker/hermes-agent/` is git-ignored in this repo (no commit hash available) — provenance header cites the source file path + sha256 instead. Added one testability-only `transport` kwarg to `refresh_codex_oauth_pure` (documented deviation).
- [x] 1.2 Create `kubernetes/codex-shim/app/store.py`: Secret `codex-shim-auth` read/patch (`tokens.access_token`, `tokens.refresh_token`, `last_refresh`, cached `expires_at` from JWT `exp`) (D12).
- [x] 1.3 Create `kubernetes/codex-shim/rbac.yaml`: ServiceAccount `codex-shim`, Role (`secrets: get,patch,update` on `resourceNames: [codex-shim-auth]` only, no `list`/`watch`), RoleBinding (D13).
- [ ] 1.4 D-OQ3 validation task: during/after provisioning, attempt a second dedicated `codex login` for the shim (D16) and confirm Hermes's existing session is unaffected (not evicted). **Cannot be performed by an automated agent** — requires an interactive OAuth login. Mechanism provided instead: `kubernetes/codex-shim/scripts/bootstrap_login.md` (manual runbook) — flagged as a manual step the user must run once before Phase 4 task 4.4's live validation.

## Phase 2: codex-shim — RED tests (refresh classification, single-flight, no-leak)

- [x] 2.1 RED: `test_refresh_maps_429_to_rate_limited` — stubbed token endpoint returns 429 → `SessionState.rate_limited`.
- [x] 2.2 RED: `test_refresh_maps_terminal_errors_to_expired_needs_relogin` — `invalid_grant`/`refresh_token_reused`/401/403 → `expired_needs_relogin`.
- [x] 2.3 RED: `test_refresh_maps_5xx_to_refresh_failed`.
- [x] 2.4 RED: `test_rotated_refresh_token_persisted` — fake K8s API, assert Secret patch body carries new `refresh_token` when upstream rotates it.
- [x] 2.5 RED: `test_single_flight_refresh` — N concurrent 401s via `asyncio.gather` trigger exactly one refresh call and one retry each.
- [x] 2.6 RED: `test_no_token_material_leaks` — assert `/internal/session` response, logs, and error bodies never contain either token value.
- [x] 2.7 RED (D15/D15a — translation, request side): `test_request_translation_messages_to_input_and_instructions` — table-driven against the vendored converter: `role: system` → `instructions`, remaining `messages` → `input[]`, `tools[]` → Responses tool schema.
- [x] 2.8 RED (D15/D15a — translation, request side): `test_request_translation_tool_role_becomes_function_call_output` — a history containing `role: "tool"` + assistant `tool_calls` produces `function_call_output` items with matching `call_id`; assert no `role: "tool"` item ever reaches the Responses payload (the shape the upstream API rejects outright).
- [x] 2.9 RED (D15/D15a — translation, streaming): `test_streaming_translation_chunk_shape` — a recorded Responses event sequence (`response.output_item.done`, `response.output_text.delta`, terminal `response.completed`) fed into the hand-rolled emitter yields well-formed `chat.completion.chunk` frames (`object`, `id`, `created`, `choices[0].delta`), a terminal `finish_reason`, and a final `data: [DONE]`.
- [x] 2.10 RED (D15/D15a — translation, non-streaming): `test_non_streaming_translation_single_object` — same fixture with `stream: false` collapses into one `chat.completion` object with `choices[0].message.content`, `tool_calls`, and `usage` mapped from `input_tokens`/`output_tokens`.
- [x] 2.11 RED (D15 — translation, error path): `test_upstream_error_maps_to_openai_error_shape` — a fake `type: "error"` event and a `response.failed`/`response.incomplete` terminal event each map to an OpenAI-shaped error body, not a truncated `200`.

## Phase 3: codex-shim — GREEN implementation

- [x] 3.1 Create `kubernetes/codex-shim/app/session.py`: proactive timer (`expires_at - 120s`) + reactive 401 handler, both behind one `asyncio.Lock` (D14) — make 2.1–2.5 pass. **Deviation**: single-flight coalescing uses a monotonic `_refresh_generation` counter rather than access-token-string comparison — two refreshes issued within the same wall-clock second can mint byte-identical JWTs (same `exp` second), which would otherwise defeat a string-equality check.
- [x] 3.2a Create `kubernetes/codex-shim/app/codex_translate.py` (request side): vendor `_chat_messages_to_responses_input` + `_responses_tools` from `kubernetes/docker/hermes-agent/agent/codex_responses_adapter.py`, with a provenance header naming the source file/line range and commit (same pattern as D11's `codex_auth.py` vendoring) (D15a) — make 2.7–2.8 pass.
- [x] 3.2b Extend `codex_translate.py` (non-streaming path): mirror `_CodexCompletionsAdapter`'s response assembly (`kubernetes/docker/hermes-agent/agent/auxiliary_client.py:1233-1295`) to collapse a completed Responses result into one `chat.completion` object — make 2.10 pass.
- [x] 3.2c Extend `codex_translate.py` (streaming path, hand-rolled — no reusable streaming code exists in the repo per D15a): emit `chat.completion.chunk` frames from `response.output_item.done`/`response.output_text.delta`, close on `response.completed`/`response.incomplete`/`response.failed` with correct `finish_reason` + `data: [DONE]`, and map error/failed terminal events to an OpenAI-shaped error body — make 2.9 and 2.11 pass.
- [x] 3.2 Create `kubernetes/codex-shim/app/proxy.py`: `/v1/chat/completions` + `/v1/models`, static internal bearer in / access token out, calls `codex_translate.py` for request translation, upstream POST against `https://chatgpt.com/backend-api/codex/responses`, and response translation back to Chat Completions shape (streaming and non-streaming) — **re-scoped from Amendment 1's byte-passthrough proxy per D15/D15a; wires 3.2a–3.2c together, no independent test of its own beyond wiring.** Uses a plain `httpx.AsyncClient` POST rather than the `openai` SDK's `client.responses.create(...)` (no `openai` SDK dependency needed for a translating reverse-proxy that only needs one POST + SSE parse).
- [x] 3.3 Create `kubernetes/codex-shim/app/main.py`: FastAPI app wiring `/internal/session`, `/healthz`, proxy routes — make 2.6 pass (response/log redaction).
- [x] 3.4 REFACTOR: `session.py`'s `_classify_error` is already the single shared error→`SessionState` mapping helper (no duplication introduced in `main.py`, which only reads `session.status()`); no further extraction needed.

## Phase 4: codex-shim — Integration tests + manifests

- [x] 4.1 Integration (re-scoped from SSE-passthrough to translation-fidelity per Amendment 2): fake upstream emits chunked Responses SSE → assert the shim's output is LiteLLM-parseable `chat.completion.chunk` frames, not buffered into one blob.
- [x] 4.2 Create `kubernetes/codex-shim/{Dockerfile,requirements.txt}`.
- [x] 4.3 Create `kubernetes/codex-shim/{deployment,service,kustomization}.yaml` — no Ingress (cluster-internal only), replicas 1.
- [x] 4.4 **Validation checkpoint, D-OQ4 (Cloudflare originator allow-list) — RESOLVED POSITIVE**, validated 2026-08-20 via `kubectl port-forward` + direct in-cluster calls (not a forced refresh — the cached token was already `valid`, unrelated network incident fixed first, see `sdd/gpu-handoff-web-panel/d-oq4-live-validation` for full trace): `GET /internal/session` → `200 {"state":"valid","expires_at":1787886494.0,...}`. `POST /v1/chat/completions` `stream:false` → `200`, well-formed `chat.completion` (`{"id":"chatcmpl-592ece56e1424724b96cdee1",...,"choices":[{"message":{"content":"pong"},"finish_reason":"stop"}],"usage":{...}}`), 2.02s. `stream:true` → `200`, well-formed SSE `chat.completion.chunk` frames terminated by `[DONE]`, 1.82s. No `403`/`cf-mitigated` on either call — the real egress path through Cloudflare's originator allow-list is clean. Gate cleared for PR2/PR5 "fully verified" status.
- [x] 4.5 **Opportunistic re-attempt, D-OQ1 (client identity) — RESOLVED POSITIVE, deviation from literal task text noted**: the Phase 0 spike script was deleted per its own rollback plan (0.1's PR1 boundary), so it could not be literally re-run. Used stronger evidence instead — the actual `codex-shim` `/v1/chat/completions` → `CODEX_RESPONSES_URL` (`https://chatgpt.com/backend-api/codex/responses`, same origin/path family the Phase 0 spike targeted) production path, exercised live in 4.4's validation. Both the streaming and non-streaming calls reached an authorization decision and got accepted (real `200` + real model output), not rejected — Phase 0's blocker ("the identity probe never reached an authorization decision") no longer applies now that the translation layer (D15) is built. Client identity is accepted by the live endpoint. Cloud path is fully verified.

## Phase 5: model-panel — Foundation (handoff core, no wiring yet)

- [x] 5.1 Create `kubernetes/model-panel/app/handoff/state.py`: state ConfigMap `model-panel-state` write-ahead read/write + reconcile-against-live logic (D6).
- [x] 5.2 Create `kubernetes/model-panel/app/handoff/gpu.py`: GPU-free check = zero non-terminal pods requesting `nvidia.com/gpu` in `llms` (D5).
- [x] 5.3 Create `kubernetes/model-panel/app/handoff/drain.py`: poll `llama-router` `/slots` until idle (120s budget), abort on busy-at-timeout (D3).
- [x] 5.4 Modify `kubernetes/llama-service/deployment-router.yaml`: add `--slots` arg to `llama-router` for drain visibility. **Pulled forward into PR1** per this apply run's explicit PR boundary (spike + router `--slots`); rest of Phase 5 untouched.

## Phase 6: model-panel — RED tests (StepRunner, drain, GPU-free-timeout)

- [x] 6.1 RED: `test_undo_stack_unwinds_lifo_on_partial_failure` — fake steps, assert LIFO undo order and no forced scale.
- [x] 6.2 RED: `test_drain_timeout_aborts_before_scale` — fake busy `/slots` responses never proceed to scale-to-0.
- [x] 6.3 RED: `test_litellm_config_patch_preserves_unrelated_entries` — round-trip patch on real ConfigMap fixture, assert `litellm_callbacks.py` and other entries untouched.
- [x] 6.4 RED: `test_gpu_not_free_within_window_aborts_and_restores` — fake API server, pod stuck terminating → `state=degraded`, replicas restored, no LiteLLM/Hermes update (spec: GPU Confirmation Timeout Blocks Switch).
- [x] 6.5 RED: `test_fail_closed_on_non_valid_session` — stubbed shim client returns non-`valid`/`expiring_soon`, assert 409-equivalent (`SwitchBlocked`) and zero calls reached the fake K8s API server (D17).

## Phase 7: model-panel — GREEN implementation

- [x] 7.1 Create `kubernetes/model-panel/app/handoff/runner.py`: `Step`/`HandoffError`, `StepRunner` with LIFO undo — make 6.1 pass.
- [x] 7.2 Create `kubernetes/model-panel/app/handoff/steps.py`: ordered switch-to-Cloud (drain → pause KEDA → scale to 0 → wait pod delete → confirm GPU free → patch litellm-config → restart LiteLLM) and switch-to-Local (scale fixed default profile → wait ready → preload probe → patch alias → restart LiteLLM) sequences — make 6.2–6.4 pass. **Deviation**: `litellm_params_for(target)` is injected via `HandoffContext` rather than hardcoded cloud/local param constants in this module — D-OQ2 (exact `CODEX_CLOUD_MODEL`) is still open and the exact local baseline `litellm_params` were not part of this PR's scope (Phase 9 wires the real `cloud` LiteLLM entry). The alias-rewrite *mechanism* (D1) is fully implemented and tested against the real `litellm-config.yaml` fixture; only the concrete param values are deferred.
- [x] 7.3 Create `kubernetes/model-panel/app/clients/codex_shim.py`: session-status client + fail-closed precondition call before any mutation — make 6.5 pass.
- [x] 7.4 REFACTOR: confirmed `steps.py` reuses `runner.py`'s `Step`/`FunctionStep` contract cleanly (every step is a `FunctionStep`), no duplicated undo logic — undo closures live one per step function, `switch_to()` only orchestrates state-CM writes and precondition/StepRunner wiring.

## Phase 8: model-panel — Wiring, UI, RBAC, exposure

- [x] 8.1 Create `kubernetes/model-panel/app/main.py`: FastAPI app, bearer auth, `GET /api/status`, `POST /api/switch`, `POST /api/repair`.
- [x] 8.2 Create `kubernetes/model-panel/app/{templates/index.html,static/panel.js}`: single page, mode/profile view, toggle, session-status badge (~40 lines JS, `/api/status` poll every 2s) (D9). **Design gap found and documented (not silently improvised)**: the spec's "Local Profile Picker" (switch daily↔large while staying Local) has no backend contract anywhere in design.md — no interface, no file, no test row — and `steps.switch_to()`'s `target=="local"` path is hardcoded to `FIXED_DEFAULT_PROFILE`, with no parameter to select a different local profile. `switch-model.sh`'s own daily/large mechanism also calls `hermes config set model.default`, which D1 explicitly says the panel "can reach neither reliably" — so the existing CLI mechanism cannot simply be ported into the panel as-is either. The UI ships the picker visible/hidden correctly per the "unavailable in Cloud mode" scenario, showing the current profile, but the select is intentionally left `disabled` with an explanatory note; no backend action is wired. Flagging for a design amendment before this can be completed.
      **Superseded by Amendment 3 (D18/D18a) — see Phase 9.** The design gap this task flagged is now closed: `switch-model.sh`'s router-side activation is a plain in-cluster HTTP call the panel can make directly, no Hermes/host access needed. Phase 9 wires the real `POST /api/profile` backend and enables the previously-disabled `<select>`.
- [x] 8.3 Create `kubernetes/model-panel/{Dockerfile,requirements.txt}`.
- [x] 8.4 Create `kubernetes/model-panel/rbac.yaml`: ServiceAccount `model-panel`, Role per design table (deployments get/list/watch cluster-wide + patch/update on 8 named deployments; pods/configmaps get/list/watch; configmaps patch/update on `litellm-config`/`model-panel-state`; KEDA scaledobjects get/list/watch/patch on the two named SOs). No `secrets` verb (D10).
- [x] 8.5 Create `kubernetes/model-panel/{deployment,service,state-configmap,kustomization}.yaml`: replicas 1, non-root, RO rootfs.
- [x] 8.6 Create `kubernetes/model-panel/{ingress,tlsoption}.yaml`: duplicate Engram's Traefik mTLS pattern (D7); app bearer via mounted Secret `model-panel-auth`.

## Phase 9: model-panel — Local Profile Picker backend (D18/D18a, Amendment 3, PR5)

RED tests (new file `kubernetes/model-panel/tests/test_profile.py`):

- [x] 9.1 RED: `test_profile_switch_preloads_before_alias_patch` — `daily→large` preloads `qwen3.6-27b-q3` via the router client **before** the `qwen3` alias is patched; patched alias carries `openai/qwen3.6-27b-q3` (D18).
- [x] 9.2 RED: `test_profile_preconditions_zero_mutations` — table-driven: `mode=cloud` → 409 `not_local`; `phase=transitioning` → 409 `transition_in_progress`; unknown profile → 400 `invalid_profile`; already-active profile → 200 `{"unchanged": true}`; assert the fake K8s API and fake router client receive zero calls in all four cases (D18a).
- [x] 9.3 RED: `test_profile_switch_undo_restores_config_and_tolerates_preload_undo_failure` — a failing LiteLLM restart step restores prior `litellm-config` bytes; a failing best-effort re-preload undo is logged, not escalated (D18a).
- [x] 9.4 RED: `test_profile_switch_drain_timeout_aborts` — reuse the D3 fake `/slots` harness; busy-at-timeout aborts before preload/alias patch, zero mutation.

GREEN implementation:

- [x] 9.5 Create `kubernetes/model-panel/app/clients/llama_router.py`: `preload(preset)` (blocking `POST /v1/chat/completions {"model": preset, "max_tokens": 1, "chat_template_kwargs": {"enable_thinking": false}}` against `LLAMA_ROUTER_BASE_URL` with the `LLAMA_API_KEY` bearer) + `confirm_loaded(preset)` (`GET /health?model=<preset>&autoload=false`); own timeout, default `router_ready_timeout=300` per design's D18/D18a note.
- [x] 9.6 Modify `kubernetes/model-panel/app/handoff/steps.py`: add `PROFILE_MODEL_ALIASES = {"daily": "qwen3.5-9b", "large": "qwen3.6-27b-q3"}` and `switch_profile(profile, ctx)` StepRunner sequence — drain `/slots` (reuse D3 step) → preload target preset (9.5) → confirm loaded (9.5) → patch `qwen3` alias (reuse D1 patch step) → restart LiteLLM — make 9.1, 9.3, 9.4 pass.
- [x] 9.7 Modify `kubernetes/model-panel/app/main.py`: add `POST /api/profile {"profile": "daily"|"large"}` under the existing `switch_lock`/executor, with the four fail-closed preconditions — make 9.2 pass; reuses the `StateStore` write-ahead pattern from `/api/switch`.
- [x] 9.8 Modify `kubernetes/model-panel/app/static/panel.js`: enable the previously-disabled profile `<select>` (supersedes Phase 8 task 8.2's deviation); POST to `/api/profile`; disable while transitioning or when `mode != local`.
- [x] 9.9 REFACTOR: confirm `switch_profile()` reuses the same drain/alias-patch/LiteLLM-restart step builders as `switch_to()` (no duplicated undo logic), and `POST /api/profile` shares the state-CM write-ahead + LIFO-undo wiring with `POST /api/switch` rather than reimplementing it.

## Phase 10: LiteLLM + Hermes routing

- [x] 10.1 Modify `kubernetes/proxy/litellm-config.yaml`: add `cloud` entry (`model: openai/<CODEX_CLOUD_MODEL>`, `api_base: http://codex-shim.llms.svc.cluster.local:8080/v1`, `api_key: os.environ/CODEX_SHIM_KEY`), no `OPENAI_API_KEY`. Pick `CODEX_CLOUD_MODEL` default per D-OQ2. **D-OQ2 resolved by user: `gpt-5.6-sol`** — committed literally as `model: openai/gpt-5.6-sol` (not an env placeholder in the ConfigMap itself; matches the value already used as the Python-level fallback default in `codex-shim/app/proxy.py` and `model-panel/app/main.py`, and as the literal `CODEX_CLOUD_MODEL` env value in `codex-shim/deployment.yaml`). Also added the `CODEX_SHIM_KEY` env var to the `litellm` Deployment, sourced from the existing `codex-shim-key` Secret's `internal-key` key (same Secret/key the shim itself reads via `CODEX_SHIM_INTERNAL_KEY`), so the static internal bearer values on both sides come from one Secret. No `OPENAI_API_KEY` added.
- [x] 10.2 Confirm `model.default: qwen3` in Hermes config stays untouched (D1 — panel only rewrites the `qwen3` LiteLLM alias target, never Hermes config directly). **Confirmed, no change made**: `kubernetes/hermes/config/config.yaml` line 2 still reads `default: qwen3`. This is correct and intentional per D1 — the new `cloud` `model_list` entry added in 10.1 is a separate, always-addressable-by-name LiteLLM alias (satisfies the `cloud-model-routing` spec's literal "LiteLLM's `cloud` model_list entry" requirement / "Cloud entry routes through the shim" scenario), but it is **not** the mechanism `switch_to("cloud")` uses to route Hermes traffic — that mechanism (D1) rewrites the `qwen3` alias's `litellm_params` in place, so Hermes's `model.default: qwen3` never needs to change to reach the shim. Both are correct simultaneously: `cloud` gives any caller a stable, mode-independent name for the shim; the `qwen3`-alias rewrite is what makes Hermes's *existing* traffic follow the mode without any Hermes-side config change (D1's stated reason: "the panel can reach neither [Hermes config surface] reliably").

## Phase 11: Integration tests + regression

- [x] 11.1 Integration: GPU-not-free-within-window abort restores replicas end-to-end against fake K8s API server (already RED in 6.4 — confirm passes with full wiring). **Confirmed (PR7)**: `test_steps.py::test_gpu_not_free_within_window_aborts_and_restores` passes as part of the full 54-test `model-panel` suite and in an isolated `-k` re-run; goes through the real `StepRunner`/`steps.py` wiring against the fake K8s API server fixture, not an isolated unit stub.
- [x] 11.2 Integration: `/api/status` reconciles ConfigMap claim vs. live drift (mismatched replica count / SO pause state). **Confirmed (PR7)**: `test_state.py::test_reconcile_against_live_detects_drift`, `test_reconcile_against_live_consistent_when_matching`, and `test_api.py::test_status_surfaces_partial_degraded_state` all pass, exercising the real `/api/status` → `state.py` reconcile path against the fake K8s API server fixture.
- [ ] 11.3 Regression: run `kubernetes/llama-service/switch-model.sh daily` and `switch-model.sh large` once post-merge — confirm byte-unchanged file, unaffected CLI behavior. **No task in this plan touches `switch-model.sh`.** **Partially confirmed (PR7), live-run portion deferred:** byte-unchanged confirmed without a live cluster — `switch-model.sh` is untracked in this repo's git history (no prior commit to diff against) and no task or PR in this build's file-changed lists (PR1–PR7 apply-progress, Engram `sdd/gpu-handoff-web-panel/apply-progress`) ever names it as modified; current sha256 recorded as `fd7b1e3604dd394dafe6baa5649a00781807fdbcaf0215efe7d3e498618ffeea` for future diffing. Actually invoking `switch-model.sh daily`/`large` against a running cluster to confirm unaffected CLI *behavior* needs a real k3s cluster with the router deployed — **not resolvable by an automated apply run**, left unchecked.
- [ ] 11.4 Manual E2E: from a phone browser on the LAN, switch Local→Cloud (assert 0 GPU pods, Codex traffic served), then Cloud→Local (assert fixed default profile up); force one shim token refresh mid-Cloud and confirm traffic survives it. Also exercise Phase 9's `daily↔large` picker end-to-end while Local. **Cannot be completed by an automated agent** — needs a real cluster, a phone browser on the LAN, and a live, valid Codex OAuth session (blocked on the same fresh-token/live-cluster gate as 1.4/4.4/4.5).

## Phase 12: Documentation / Cleanup

- [x] 12.1 Modify `kubernetes/llama-service/README.md`: point runbook at the panel, keep manual fallback steps, document out-of-band `codex login` provisioning for the shim (D16). Done (PR7): new "Ruta recomendada: panel web (`model-panel`)" section added right after the intro, pointing at the panel as the primary Local↔Cloud path and at `kubernetes/codex-shim/scripts/bootstrap_login.md` for the shim's out-of-band `codex login`; the existing manual runbook ("Secuencia resumida" / "Volver a vLLM") and the router daily/large section are explicitly kept, unedited, as fallback.
- [x] 12.2 Create `specs/012_gpu_handoff_web_panel.md`: numbered spec per repo convention. **Renumbered from the originally-planned `011` to `012` (PR7)** — `011` was already taken by `011_engram_cloud_centralized.md` (added to `specs/` independently since this tasks.md was written). Written as a pointer/summary cross-referencing the OpenSpec artifacts rather than duplicating their full content, per the proposal's Affected Areas table.
- [x] 12.3 Confirm removed Secret `openai-cloud-auth` (`api-key`, `admin-key`) is not referenced anywhere post-migration. **Confirmed (PR7)**: `grep -rn "openai-cloud-auth"` across the whole repo (excluding `.git/`) finds only the historical note in `design.md` line 231 ("no longer needed") and this task line itself — no manifest, script, or code references it. No stray `*cloud-auth*` files found either.
