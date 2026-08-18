# 012 — GPU Handoff Web Panel (Local ↔ Cloud Model Switch)

## Summary

Single RTX 4070 Ti SUPER GPU handoff between local inference (vLLM/llama.cpp
via `llama-router`) and a cloud model, driven from a LAN web panel instead of
the manual ~15-step `kubectl`/bash runbook in
`kubernetes/llama-service/README.md`. Cloud capacity is served by the user's
existing Codex/ChatGPT subscription session (no metered API key) through a
dedicated OAuth-owning shim service, never by a manual runbook edit.

This spec is a pointer, not a duplicate. The authoritative, versioned
artifacts are the OpenSpec change `gpu-handoff-web-panel`:

- `openspec/changes/gpu-handoff-web-panel/proposal.md` — intent, scope,
  resolved product decisions, amendment log, success criteria.
- `openspec/changes/gpu-handoff-web-panel/design.md` — architecture,
  decision table (D1–D18a), Testing section, open questions
  (D-OQ1/D-OQ2/D-OQ3/D-OQ4).
- `openspec/changes/gpu-handoff-web-panel/specs/` — full delta specs for the
  three new capabilities: `model-switch-panel`, `gpu-handoff-orchestration`,
  `cloud-model-routing`.
- `openspec/changes/gpu-handoff-web-panel/tasks.md` — phased task list and
  work-unit/PR split with evidence notes.

Mirrored copies of the spec, design, and tasks are also kept in Engram under
topic keys `sdd/gpu-handoff-web-panel/{spec,design,tasks,apply-progress}`.

## What shipped

- `kubernetes/model-panel/` (new service): FastAPI app, single-page UI
  (mode/profile view + toggle + Local profile picker), guarded
  `StepRunner`/LIFO-undo GPU handoff sequences (drain → pause KEDA → scale
  to 0 → confirm GPU free → patch LiteLLM `qwen3` alias → restart LiteLLM,
  and the reverse), `POST /api/profile` for the daily↔large picker while
  Local, RBAC scoped to named deployments/ConfigMaps/ScaledObjects, Traefik
  mTLS + app bearer exposure (Engram precedent).
- `kubernetes/codex-shim/` (new service): owns the Codex OAuth token pair in
  a dedicated Secret, proactive + reactive (401) refresh reusing Hermes's
  vendored refresh logic, a Responses-API↔Chat-Completions translation layer
  (request, streaming, non-streaming, error-shape), and `/internal/session`
  status reporting (`valid` / `expiring_soon` / `expired_needs_relogin` /
  `rate_limited`).
- `kubernetes/proxy/litellm-config.yaml`: new `cloud` model_list entry
  (`openai/gpt-5.6-sol` against the shim's internal endpoint,
  `CODEX_SHIM_KEY` bearer) — a stable, mode-independent name for the shim,
  distinct from the `qwen3` alias-rewrite mechanism that actually carries
  Hermes's mode-following traffic.
- `kubernetes/hermes/config/config.yaml`: intentionally **unchanged**
  (`model.default: qwen3` stays; the panel rewrites the alias's
  `litellm_params`, never Hermes config directly).
- `kubernetes/llama-service/switch-model.sh`: **unchanged** throughout the
  whole change — confirmed byte-identical, never touched by any task or PR
  in this build (see Verification below).
- `kubernetes/llama-service/README.md`: runbook now points at the panel as
  the primary path, manual steps kept as fallback (this PR).

## Key decisions (see design.md for full rationale)

- Cloud credential: Codex OAuth session via the shim, not a metered OpenAI
  Platform API key (Amendment 1) — accepted deviation from
  `specs/003_codex_profiles_and_opencode.md`'s per-tool-login convention.
- Codex's `chatgpt.com/backend-api/codex` speaks the OpenAI **Responses
  API**, not plain Chat Completions — the shim contains a real translation
  layer (D15/D15a), discovered by the Phase 0 spike and not assumed.
- Return to Local always restores the fixed default profile, not the
  previously active one.
- GPU-not-free-within-window is a hard abort (warn + block), never a forced
  switch.
- Local Profile Picker (daily↔large while Local) has a real backend
  contract (Amendment 3, D18/D18a): drain → preload via `llama_router.py` →
  confirm loaded → patch alias → restart LiteLLM, reusing the same
  StepRunner/undo primitives as the Local↔Cloud switch.

## Verification (this change, cumulative across PR1–PR7)

- Full test suite: `kubernetes/model-panel/tests` — 54 passed;
  `kubernetes/codex-shim/tests` — 17 passed. 71/71 passing, no cross-service
  regressions (services are independent Python processes; the only shared
  contract — the shim's OpenAI-compatible response shape — is covered by
  `codex-shim`'s translation tests and consumed as an HTTP client fixture on
  the `model-panel` side, never imported directly).
- Regression: `kubernetes/llama-service/switch-model.sh` confirmed
  byte-unchanged (sha256 recorded in apply-progress) and absent from every
  PR's file-changed list. Live `switch-model.sh daily`/`large` execution
  against a running cluster is deferred — no cluster access in this
  environment.
- Not verifiable without a live cluster / fresh Codex OAuth token: end-to-end
  phone-browser Local↔Cloud switch, mid-session token refresh survival,
  Cloudflare originator allow-list (D-OQ4), and non-Codex-CLI client
  identity acceptance (D-OQ1, opportunistic re-attempt). These remain open
  per `tasks.md` Phase 1 (1.4), Phase 4 (4.4, 4.5), and Phase 11 (11.4).

## Rollback

Additive only: delete `kubernetes/model-panel/` and `kubernetes/codex-shim/`
manifests and the OAuth Secret, revert the `litellm-config.yaml` `cloud`
entry and `CODEX_SHIM_KEY` env var, revert the README hunk. The manual
runbook in `kubernetes/llama-service/README.md` remains the fallback at all
times. Hermes's own Codex integration is untouched.

## Live cluster verification (post-merge, same day)

Deployed for real against the live cluster (single-node k3s on `trantor`) and
exercised end-to-end via the actual Telegram-facing Hermes gateway, not just
`curl`/unit tests. Found and fixed with TDD, each confirmed live after the
fix:

- Build: `PyYAML==6.0.2` pin conflicted with `kubernetes==36.0.3` — bumped.
- `codex-shim`'s `/internal/session` never transitioned to `"valid"` on a
  passive poll (only via a real proxy call) — now calls `ensure_fresh()`.
- D-OQ2 resolved: the real model id is `gpt-5.6-sol` (confirmed against the
  live account), not the placeholder `gpt-5.1-codex`.
- Real Responses API requires `stream: true` always, `store: false` always,
  and rejects `max_output_tokens`/`temperature` outright — none of this is
  documented anywhere, all confirmed live.
- Terminal `response.completed` event's own `output` is genuinely empty —
  real content only arrives via earlier `response.output_item.done` events;
  had to be spliced back in for both `/v1/chat/completions` and the new
  `/v1/responses` endpoint (see below).
- RBAC: `deployments/scale` needs its own `get` grant, distinct from plain
  `deployments`.
- `read_namespaced_deployment_scale().spec.replicas` is `None` (not `0`) for
  a deployment already scaled to zero (`omitempty` on the wire).
- A deployment listed in `GPU_DEPLOYMENTS` but never applied to this cluster
  (`llama-server-q6`) must 404-tolerate, not abort the whole switch.
- `restart_litellm` had no real production implementation — `None` always,
  silent no-op. LiteLLM never picked up the alias patch. Now patches the
  Deployment's pod-template annotation.
- LiteLLM's own `allowed_fails: 1` / `cooldown_time: 60s` defaults turn any
  one-off blip into a full outage for a single-deployment alias — raised to
  `allowed_fails: 3`, `cooldown_time: 30`.
- **Design gap, now closed**: a routine `kubectl apply -f
  litellm-config.yaml` silently reverts the live-patched `qwen3` alias to
  the file's checked-in baseline, invisible to the router-replicas/GPU-pods
  drift checks alone. Added `classify_qwen3_alias_target()` +
  `reconcile_against_live(..., qwen3_alias_target=...)` +
  `realign_litellm_alias()`, self-healing on the next `/api/status` poll
  (debounced 30s) — verified live, self-healed before the next manual check.
- **New capability, `/v1/responses`**: LiteLLM bridges Chat Completions calls
  carrying both `reasoning_effort` and `tools` for gpt-5.4+ models straight
  to `{api_base}/responses`, bypassing `/v1/chat/completions` entirely
  (`responses_api_bridge_check` in litellm's own source) — and Hermes sends
  that combination on every agentic/tool-calling turn, not as an edge case.
  The shim only had `/v1/chat/completions`; added `/v1/responses` as a
  near-direct proxy (no translation needed — LiteLLM already speaks the same
  shape as the real upstream) with the same `stream`/`store`/param-stripping
  and output-splicing fixes applied.
- Host-side finding, not a code bug: the live `hermes-gateway.service`'s
  `model.default` was `qwen3.5-9b` (a concrete local preset), not the
  `qwen3` alias the panel repoints — the switch was invisible to Hermes.
  Fixed with `hermes config set model.default qwen3` + gateway restart.

All fixes shipped with new regression tests (RED confirmed before GREEN);
73/73 `model-panel`, 35/35 `codex-shim` after this pass.

### Known issue — NOT fixed, needs follow-up

**Jarvis voice stopped working, in both Local and Cloud mode**, noticed by
the user after this session's live testing. Not yet investigated — could be
unrelated to this change (e.g. `hermes-gateway.service` restarts performed
during diagnosis, or the `model.default` change) or a genuine regression.
Start from `specs/010_jarvis_voice_piper.md` and check `hermes-gateway`
logs/config for the voice/TTS path; confirm whether it was working before
today's `hermes config set model.default qwen3` and gateway restarts.
