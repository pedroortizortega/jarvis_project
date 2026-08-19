# Design: GPU Handoff Web Panel

> **Amendment 1 applied** — the cloud credential is a Codex OAuth session owned by a
> new `codex-shim` service, not a pay-per-use OpenAI API key. Everything about the GPU
> handoff (D1–D7, D9, D10, drain, undo stack, state ConfigMap) is unchanged.

## Amendment Log

### Amendment 3 — the Local Profile Picker gets a real in-cluster backend contract (D18)

**What changed:** one new decision **D18** (plus **D18a** on guarding), a new
`POST /api/profile` route in the Interfaces section, a `Profile switch` line in the Data
Flow, and three test rows. Nothing else.

**Why:** PR4 apply (Engram `sdd/gpu-handoff-web-panel/apply-progress`, obs #343, Learned
#1) correctly refused to improvise: the spec's **Local Profile Picker** requirement had
*no* backend contract anywhere in this design — no decision, no interface, no test row —
and `steps.switch_to()`'s `local` path is hardcoded to `FIXED_DEFAULT_PROFILE = "daily"`.
PR4 shipped the `<select>` **disabled** and flagged the gap instead of inventing a
mechanism. This amendment closes it.

**The crux — an in-cluster mechanism EXISTS, and it was already in the repo.** The PR4
finding assumed the daily/large mechanism was `hermes config set model.default`, which the
panel cannot reach (D1). Re-reading `kubernetes/llama-service/switch-model.sh` end-to-end
shows that call is only *half* the script, and it is the half the panel does not need:

- **Router-side activation (lines 52-60)** is a plain OpenAI-compatible HTTP request —
  `POST /v1/chat/completions {"model": "<preset>", "max_tokens": 1}` with the
  `llama-api-key` bearer. Under `--models-max 1 --models-autoload`
  (`deployment-router.yaml:56-59`) the router unloads the current preset and loads the
  requested one, and *the request blocks until the load completes* (the script's own
  comment says so). The script only wraps it in `kubectl exec` because it runs on the
  **host**; the panel is already in-cluster and can call the `llama-router` Service
  directly. Readiness is separately confirmable with
  `GET /health?model=<preset>&autoload=false`, the exact probe form already used by the
  router's `startupProbe` (`deployment-router.yaml:86`).
- **Client-side routing** — `hermes config set model.default "$MODEL"` (line 62) — is the
  host CLI's default-alias update. The panel's equivalent already exists and is already
  implemented: D1's `qwen3` alias rewrite in `litellm-config` + LiteLLM restart. The panel
  therefore never needs Hermes, `sudo`, or host access for this.

So the picker is buildable today from primitives this design already owns (D1 alias patch,
D3 `/slots` drain, the existing parameterized `preload_probe(alias)` hook, the StepRunner).
No new endpoint is invented and no new RBAC verb is required.

**Explicitly NOT claimed:** we did **not** confirm that llama.cpp `server-cuda-b10156`
exposes a dedicated admin load/unload endpoint (e.g. `POST /v1/models/{id}/load`). Nothing
in `router-config.yaml`, `deployment-router.yaml`, `README.md`, or `switch-model.sh`
references one, and its existence is **unknown, not disproven**. D18 deliberately does not
depend on one: it uses only the request-triggered autoload path that is proven in-repo.

**Explicitly unchanged by Amendment 3:** D1–D7, D9–D17, D15a, all of Amendment 1 and
Amendment 2, the whole `codex-shim` service and its translation layer, both RBAC tables,
the Threat Matrix, Migration/Rollout, and every open question (D-OQ1–D-OQ4).
`switch-model.sh` remains **byte-unchanged** (D2).

### Amendment 2 — D15 is a translation layer, not a passthrough proxy

**What changed:** D15 only. The shim's `/v1/chat/completions` is redesigned from a
transparent byte-passthrough reverse proxy into a **protocol translation layer**
(Chat Completions ⇄ Responses API), plus a new D15a recording where that translation
logic comes from. `proxy.py` gains a sibling `codex_translate.py`; the SSE-passthrough
test row becomes a translation-fidelity row.

**Why:** PR1 spike finding (Engram `sdd/gpu-handoff-web-panel/apply-progress`, obs #343).
D15 assumed `https://chatgpt.com/backend-api/codex` was "structurally identical, no
translation" to the vLLM/llama-router entries. That is provably false from repo code, no
live call needed: `kubernetes/docker/hermes-agent/agent/codex_runtime.py:875-896` and
`agent/auxiliary_client.py:1191-1295` show the endpoint is consumed via the OpenAI
**Responses API** — `client.responses.create(stream=True)`, path `/responses`, request
field `input` (not `messages`), streaming frames `response.output_item.done` /
`response.output_text.delta` / `response.completed` (not `chat.completion.chunk`). A
passthrough shim would hand LiteLLM's Chat Completions body straight to an endpoint that
does not speak it.

**Still open (neither resolved nor assumed):**
- **D-OQ1** — non-Codex-CLI client identity acceptance. The spike was **inconclusive**,
  not answered: the only available token was already expired (`exp` ≈ 2026-08-06 vs. run
  date 2026-08-16), so both probes returned `401 token_expired` from OpenAI's own layer
  before authorization was ever reached. Do not read that as accept *or* reject.
- **D-OQ4 (new)** — Cloudflare originator allow-list vs. in-cluster egress IP. See below.

**Explicitly unchanged by Amendment 2:** D1–D7, D9, D10 (GPU handoff, StepRunner/LIFO
undo, write-ahead state ConfigMap, drain via `--slots` — already implemented in PR1),
`switch-model.sh` byte-unchanged (D2), the LiteLLM `qwen3` alias-rewrite mechanism (D1),
D8′ session states, D11–D14, D16, D17 fail-closed behavior, and the RBAC split between
`model-panel` and `codex-shim` (D13).

## Technical Approach

A FastAPI service in `llms` drives the handoff through the Kubernetes API (official
`kubernetes` Python client, in-cluster config) — never `kubectl`. Its `handoff` package
generalizes `switch-model.sh`'s guarded shape (snapshot → stop consumers → mutate →
probe → restore-on-failure) into a `StepRunner` with an explicit LIFO undo stack. Local
mode means `llama-router` (replicas 1, holds the GPU, serves both profiles); Cloud mode
means zero GPU pods and LiteLLM's `qwen3` alias pointed at `codex-shim`.

A **second, separate** service — `codex-shim` — owns the Codex OAuth session: it holds
the token pair, refreshes it, and exposes a stable internal OpenAI-compatible endpoint.
It is deliberately not part of `model-panel`: it sits in LiteLLM's live request path
(it must stay up while the control-plane panel is down or being rolled), and its
credential custody must not inherit the panel's cluster-mutating RBAC.

## Architecture Decisions

| # | Decision | Alternatives rejected | Rationale |
|---|---|---|---|
| D1 | **Stable-alias indirection**: the panel only rewrites the `qwen3` entry in `litellm-config`; Hermes keeps `model.default: qwen3` untouched. | Patch Hermes config per switch. | Hermes runs two ways (in-cluster PVC seeded once by `seed-hermes-config`, and host `hermes-gateway.service` via the `hermes` CLI). The panel can reach neither reliably. Repointing one LiteLLM alias switches both consumers atomically. |
| D2 | **No shared code with `switch-model.sh`**; the script stays byte-unchanged, the routine lives in `kubernetes/model-panel/app/handoff/`. | Shared bash lib; script calls panel API. | The two callers have disjoint capabilities (host `sudo systemctl` + `hermes` CLI vs. in-cluster API-only). A shared substrate would be a false abstraction and would couple the CLI to panel/mTLS availability. Only the *sequence contract* is shared, documented in the README. |
| D3 | **Drain via `llama-router` `/slots`**: add `--slots` to the router args; poll until every slot is idle (120 s budget) before scaling to 0; busy at timeout ⇒ abort. | Hard cut; in-service counter. | The panel is not on the data path, so it cannot count requests. `/slots` is the only first-party in-flight signal, and 120 s matches `terminationGracePeriodSeconds`. |
| D4 | **Ensure KEDA paused, never unpause**: `vllm-big-model`/`vllm-small-model` ScaledObjects are annotated `paused-replicas=0` and stay paused in both modes. | Pause on Cloud, unpause on Local. | Unpausing while `llama-router` holds the GPU breaks the single-GPU invariant. |
| D5 | **GPU-free = zero non-terminal pods requesting `nvidia.com/gpu` in `llms`**. | `nvidia-smi`. | Host device inspection needs privileges the panel must not have; the pod check is the API-observable equivalent. |
| D6 | **State ConfigMap `model-panel-state`** written *before* each mutating step (write-ahead), reconciled against live cluster state on every read. | In-memory state. | Survives pod restart and makes partial switches detectable. The ConfigMap is a claim, never trusted alone. |
| D7 | **Duplicate the Engram exposure manifests** (`TLSOption` + `Ingress`) into `kubernetes/model-panel/`, plus an app bearer. | Kustomize base shared with `engram/`. | `TLSOption` is namespaced and its client-CA Secret must live beside it. Bearer is defense in depth since NetworkPolicy is disabled cluster-wide. |
| D9 | **Server-rendered Jinja2 page + ~40 lines of vanilla JS** polling `/api/status`. | React/Vite SPA. | One page, one action; a build toolchain is disproportionate. |
| D10 | **Panel Secrets mounted, not read via API** ⇒ the panel Role carries no `secrets` verb. | `get` on named Secrets. | Strictly less privilege for the same result. (The shim is the exception — see D13.) |

### Amended / new decisions (cloud auth)

| # | Decision | Alternatives rejected | Rationale |
|---|---|---|---|
| **D8′** | **Session-status indicator sourced from the shim** — the panel polls `GET /internal/session` on `codex-shim` and renders `not_configured / valid / expiring_soon / rate_limited / expired_needs_relogin / refresh_failed`. **Supersedes D8 (OpenAI Costs API spend indicator), which is deleted.** | OpenAI Costs API; LiteLLM spend DB. | The subscription is flat-rate: there is no per-request spend to show, so the Costs API is moot (and LiteLLM here has no Postgres, `allow_requests_on_db_unavailable: false`). The shim is the only component that knows the credential's real health, so it is the only honest source. No quota-remaining number is shown — no such endpoint is verified for this credential. |
| **D11** | **Vendor the *pure* refresh function**: copy `refresh_codex_oauth_pure` plus its constants (`CODEX_OAUTH_TOKEN_URL`, `CODEX_OAUTH_CLIENT_ID`, `CODEX_ACCESS_TOKEN_REFRESH_SKEW_SECONDS = 120`, `CODEX_RATE_LIMITED_CODE`) and its `AuthError` error-classification block into `kubernetes/codex-shim/app/codex_auth.py`, with a provenance header naming the source file and commit. | (a) `import hermes_cli.auth`; (b) extract a shared `hermes-codex-auth` package consumed by both. | (a) pulls an ~8 000-line module with Hermes-global state, file locks on `/opt/data/auth.json`, a credential pool, and CLI-recovery side effects into a service that must own exactly one credential. (b) there is no Python monorepo packaging in this repo and it would couple the shim image build to the `hermes-agent` image. The chosen seam is the upstream author's own: `refresh_codex_oauth_pure` is already explicitly state-free ("without mutating Hermes auth state"), ~130 lines, and takes/returns plain dicts — so vendoring is bounded and mechanical. Drift is accepted and made visible by the provenance header. |
| **D12** | **Secret `codex-shim-auth` is `auth.json`-shaped and read/written via the API**: `tokens.access_token`, `tokens.refresh_token`, `last_refresh`, plus shim-added `expires_at` (parsed from the access-token JWT `exp`, cached — never re-derived on the hot path). The shim patches it after every successful refresh. | Mounted volume; in-memory only; ConfigMap. | A token store must be read-back-and-written; kubelet-projected volumes are read-only and lag by up to the sync period, and an in-memory-only token is lost on every restart, forcing a needless refresh (which rotates the refresh token — see D16). |
| **D13** | **Own least-privilege ServiceAccount `codex-shim`** with a Role granting only `""/secrets: get, patch, update` on `resourceNames: [codex-shim-auth]`. No `deployments`, no `deployments/scale`, no `configmaps`, no `keda.sh/scaledobjects`, no `list`/`watch` on secrets. | Reuse the panel's ServiceAccount. | Reuse would hand the credential holder the ability to scale workloads and rewrite `litellm-config` — the panel is LAN-exposed and cluster-mutating; the shim is request-path and credential-bearing. Splitting them means a compromise of either one does not yield the other's capability. `list`/`watch` on secrets is omitted deliberately: `get` by name cannot enumerate the namespace. |
| **D14** | **Proactive + reactive refresh, single-flight, bounded**: a background task refreshes at `expires_at − 120 s` (matching Hermes's own skew); a request that receives **401 from upstream** triggers **exactly one** refresh and **one** retry, never a loop. Both paths go through one `asyncio.Lock` so concurrent requests cannot double-refresh. A **429 from the token endpoint** maps to `rate_limited` (credentials still valid — retry later), explicitly *not* to a re-login prompt. | Proactive-only (cron/CronJob); reactive-only; refresh per request. | Proactive-only breaks whenever the token is invalidated early; reactive-only makes the first request after any idle gap pay a refresh and possibly fail. Single-flight is required because refresh rotates the token (D16) — two in-flight refreshes would burn each other. The 429 distinction is copied from Hermes's own hard-won classification (its comment cites a real misleading-prompt bug). |
| **D15** (amended) | **Authenticating *translation* layer, not a passthrough proxy.** The shim exposes `/v1/chat/completions` and `/v1/models`, validates the static internal bearer (`CODEX_SHIM_INTERNAL_KEY`), swaps it for the live access token, then **translates**: Chat Completions request → Responses request (`messages` → `input` + `instructions`, `tools` → Responses tool schema), calls `client.responses.create(...)` against `https://chatgpt.com/backend-api/codex`, and translates the Responses result back to Chat Completions shape — `chat.completion.chunk` SSE frames when `stream: true`, a plain `chat.completion` object otherwise. LiteLLM's `cloud` entry is unchanged in form: `model: openai/<codex-model>`, `api_base: http://codex-shim.llms.svc.cluster.local:8080/v1`, `api_key: os.environ/CODEX_SHIM_KEY`. | (a) The **superseded** byte-passthrough proxy. (b) LiteLLM pointed straight at the Codex endpoint. (c) A LiteLLM custom provider/`api_mode: responses` handler instead of a shim. | (a) is simply wrong on the wire: `codex_runtime.py:875-896` and `auxiliary_client.py:1191-1295` prove the endpoint speaks the **Responses API** (`/responses`, `input`, `response.output_item.done`), so LiteLLM's `messages` body would never be accepted and its `chat.completion.chunk` parser would never match the reply. (b) hard-fails on expiry with no refresher and no place to express session status. (c) would put drifting vendor-shape handling inside a config file with no unit tests and no refresh hook. The translation lives in the shim precisely because the shim already owns the credential, the retry-once-on-401, and the session state — the same place that must react when the shape drifts. |
| **D15a** (new) | **Reuse the repo's existing translation logic where it is already a clean function; hand-roll only the streaming re-emission.** Vendor `_chat_messages_to_responses_input` and `_responses_tools` from `kubernetes/docker/hermes-agent/agent/codex_responses_adapter.py` into `kubernetes/codex-shim/app/codex_translate.py` with a provenance header (same pattern as D11), and mirror `_CodexCompletionsAdapter`'s response assembly (`auxiliary_client.py:1233-1295`) for the non-streaming path. **Hand-roll** the streaming direction: Responses SSE events → `chat.completion.chunk` frames + terminal `data: [DONE]`. | (a) Hand-roll everything. (b) `import agent.codex_responses_adapter`. (c) Reuse `_CodexCompletionsAdapter` as-is for streaming too. | (a) discards ~290 lines of already-debugged edge handling — notably `role: "tool"` messages, which the Responses API **rejects** outright (`Invalid value: 'tool'`) and which the existing converter re-encodes as `function_call` / `function_call_output` items with valid `call_id`s. LiteLLM will forward exactly that history. (b) drags in `agent.prompt_builder` and the hermes-agent image, same objection as D11(a). (c) is not reusable: that adapter *consumes* the stream internally and returns one assembled object — it has no chunk-out path, and LiteLLM must be able to stream. So the seam is: vendored request-side + non-streaming response assembly, hand-rolled chunk emitter. |
| **D16** | **The shim gets its *own* `codex login`, not a copy of Hermes's `/opt/data/auth.json`.** | Copy Hermes's existing credential file into the Secret. | Concrete mechanic, not policy: `refresh_codex_oauth_pure` **rotates** the refresh token (`next_refresh` replaces the stored one), and the upstream returns `refresh_token_reused` to whichever client refreshes second. Two clients sharing one refresh token therefore break *each other* — the shim would silently log Hermes out and vice versa. The user's accepted deviation from `specs/003` (one account serving several tools) survives intact; what is ruled out is one *token pair* serving several tools. |
| **D17** | **Fail-closed at the panel, before any mutation**: `POST /api/switch {"target":"cloud"}` first calls the shim's `/internal/session`. It proceeds only on `valid` or `expiring_soon`; on anything else (including shim unreachable/timeout) it returns `409` with the status and reason and performs **zero** cluster mutations. | Attempt the switch and roll back on failure. | Rolling back a GPU handoff costs minutes of pod churn. The precondition is one cheap in-cluster GET; there is no reason to pay the rollback. This also preserves the "block, don't half-switch" rule already applied to the GPU-free timeout. |

### New decision (Amendment 3 — Local Profile Picker)

| # | Decision | Alternatives rejected | Rationale |
|---|---|---|---|
| **D18** | **The profile switch is the router's own request-triggered autoload plus the D1 alias rewrite — nothing else.** `POST /api/profile {"profile":"daily"\|"large"}` runs a guarded sequence: drain `/slots` (D3) → **preload** the target preset by issuing `POST /v1/chat/completions {"model": <preset>, "max_tokens": 1, "chat_template_kwargs": {"enable_thinking": false}}` to `http://llama-router.llms.svc.cluster.local:8080/v1` with the `LLAMA_API_KEY` bearer (blocks until loaded; `--models-max 1 --models-autoload` evicts the previous preset) → confirm with `GET /health?model=<preset>&autoload=false` → patch the `qwen3` alias's `litellm_params.model` to `openai/<preset>` (D1) → restart LiteLLM → state `profile=<target>`. Profile→preset map is copied verbatim from `switch-model.sh`: `daily → qwen3.5-9b`, `large → qwen3.6-27b-q3`. | (a) Call `hermes config set model.default` from the panel. (b) Invent a router admin `load`/`unload` endpoint. (c) Scale a second deployment (`llama-server-q3`) instead of swapping presets. (d) Just patch the alias and let the first real user request trigger the autoload. | (a) is exactly what D1 already ruled out — Hermes runs on the host behind `sudo systemctl` and the `hermes` CLI, unreachable from an in-cluster pod; and it is unnecessary, because the panel already owns the routing decision through the `qwen3` alias. (b) would be fabricated: no such endpoint appears in `router-config.yaml`, `deployment-router.yaml`, `README.md`, or `switch-model.sh`, and we did not verify one exists in `server-cuda-b10156`. (c) violates the single-GPU invariant (D5) and throws away the whole point of `--models-max 1` — the router already holds the GPU and swaps in place, so no pod churn is needed. (d) would make one unlucky user's request block for the full model-load time (tens of seconds to minutes) and very likely time out at LiteLLM; preloading *before* repointing the alias is precisely the ordering `switch-model.sh` uses, and the reason it stops LiteLLM first. |
| **D18a** | **A profile switch is a full guarded StepRunner sequence under the same lock as `/api/switch` — not a direct call.** It reuses `app.state.switch_lock` + the single-worker executor, writes the state ConfigMap ahead of each mutating step (D6), and unwinds LIFO on failure (D6/abort). Preconditions, all fail-closed with **zero** mutations: `409 transition_in_progress` if `phase == "transitioning"`; `409 not_local` if `mode != "local"` (the picker is meaningless in Cloud mode — the router is at 0 replicas); `400` on an unknown profile; `200 {"unchanged": true}` if the requested profile is already active. The alias-patch step's undo restores the previous `litellm_params` bytes; the preload step's undo is a **best-effort** re-preload of the previous preset whose failure is logged but never escalated (routing correctness is already restored by the alias undo — only the next request's latency suffers, since the router will autoload on demand). | A simple direct HTTP call from the request handler, with no lock, no state write, and no undo. | A profile switch drains live traffic, evicts a loaded model, rewrites `litellm-config`, and restarts LiteLLM — it is multi-minute and cluster-mutating, structurally identical to `/api/switch`. Running it unguarded would let it interleave with a Cloud handoff and produce exactly the "alternating eviction" failure the README already warns about (`README.md:60-63`), or leave a half-patched alias behind. The guard is free: every primitive (`StepRunner`, `StateStore`, drain, alias patch, LiteLLM restart) already exists and is already tested from PR3. |

## Data Flow

    Browser ──mTLS(Traefik)──> Ingress ──> model-panel Service
       │  bearer                              │
       │                                      ├─ K8s API (scale, patch CM, annotate SO, list pods)
       │                                      ├─ llama-router /slots + /v1/chat/completions (preload)
       │                                      └─ codex-shim /internal/session   (status + precondition)
       └──────── /api/status (poll 2s) ───────┘

    Hermes / clients ──> LiteLLM ──(alias qwen3)──┬─ llama-router          [Local]
                                                  └─ codex-shim /v1/...   [Cloud]
                                                       │ static internal key in, access_token out
                                                       │ ChatCompletions in ⇄ Responses out (D15)
                                                       ├─ Secret codex-shim-auth  (get/patch, D12/D13)
                                                       ├─ auth.openai.com/oauth/token   (refresh)
                                                       └─ chatgpt.com/backend-api/codex/responses (inference)

    Translation (D15), per request:
      LiteLLM POST /v1/chat/completions {model, messages[], tools?, stream}
        → split role=system  ──────────────────> instructions
          remaining messages ──vendored converter──> input[]   (role=tool becomes
                                                     function_call_output items)
          tools[] ───────────────────────────────> Responses tool schema
        → responses.create(model, instructions, input, tools, store=False, stream)
        ← response.output_text.delta   → chat.completion.chunk {delta.content}
          response.output_item.done(function_call) → chunk {delta.tool_calls[]}
          response.completed → final chunk {finish_reason} + usage, then data: [DONE]
          non-stream: assemble one {object: "chat.completion", choices[0].message}

    model-panel-state CM ──claim──┐
                                  ├──> reconciled status (mode | transitioning | degraded)
    live deployments/pods/SO ─────┘

**Switch → Cloud**: check shim session (fail closed, D17) → state=transitioning → ensure
KEDA paused → drain `/slots` → scale `llama-router`, `vllm`, `vllm-big-model`,
`vllm-small-model`, `llama-server{,-q3,-q6}` to 0 → wait pod delete (5 m) → confirm GPU
free → patch `litellm-config` (`qwen3` → shim base_url) → restart LiteLLM +
`/health/readiness` → state=cloud.

**Switch → Local**: always the **fixed** `daily` profile → patch alias to `qwen3.5-9b` →
scale `llama-router` to 1 → rollout ready → preload probe (`max_tokens:1`) → restart
LiteLLM → state=local.

**Profile switch (Local → Local, D18)**: reject unless `mode == local` and no transition is
in progress → state=transitioning(`target_profile`) → drain `/slots` → preload target preset
via a blocking `max_tokens:1` completion → confirm `/health?model=<preset>&autoload=false` →
patch alias to `openai/<preset>` → restart LiteLLM + `/health/readiness` →
state=local(`profile=<target>`). No pod is scaled and no GPU pod churns — the router swaps
the loaded model in place.

**Abort**: any failed step unwinds the undo stack (restore replicas, restore prior
ConfigMap bytes), sets `state=degraded` with the failing phase and message, and never
force-scales. `last_known_good` is preserved for the UI's retry/repair action.

**Refresh (shim, independent of any switch)**: timer at `expires_at − 120 s`, or a 401
from upstream → acquire lock → `refresh_codex_oauth_pure(access, refresh)` → patch Secret
→ update cached `expires_at` → retry the original request once. Terminal error
(`invalid_grant`, `refresh_token_reused`, 401/403 from the token endpoint) ⇒ session
state becomes `expired_needs_relogin`, the shim keeps serving 503 to LiteLLM, and the
panel refuses further switches to Cloud.

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `kubernetes/model-panel/app/main.py` | Create | FastAPI app, bearer auth, routes |
| `kubernetes/model-panel/app/handoff/{runner,steps,state,gpu,drain}.py` | Create | Guarded sequence, undo stack, state CM, GPU/drain probes |
| `kubernetes/model-panel/app/handoff/steps.py` | Modify (Amendment 3) | Add `PROFILE_MODEL_ALIASES` + a `switch_profile(profile, ctx)` StepRunner sequence (drain → preload → confirm → alias patch → LiteLLM restart), reusing the existing step builders. `FIXED_DEFAULT_PROFILE` stays as-is for the Cloud→Local path (D18) |
| `kubernetes/model-panel/app/main.py` | Modify (Amendment 3) | Add `POST /api/profile` under the same `switch_lock`/executor, with the four fail-closed preconditions; wire a real `preload_probe`/`router_health` client against `LLAMA_ROUTER_BASE_URL` (D18a) |
| `kubernetes/model-panel/app/static/panel.js` | Modify (Amendment 3) | Enable the profile `<select>` (PR4 shipped it disabled); post to `/api/profile`, disable while transitioning or when `mode != local` |
| `kubernetes/model-panel/app/clients/codex_shim.py` | Create | **Replaces `clients/openai.py`** — session-status client + fail-closed precondition |
| `kubernetes/model-panel/app/{templates/index.html,static/panel.js}` | Create | Single page; session-status badge replaces the spend widget |
| `kubernetes/model-panel/{Dockerfile,requirements.txt}` | Create | Image build |
| `kubernetes/model-panel/{deployment,service,ingress,tlsoption,rbac,state-configmap,kustomization}.yaml` | Create | Manifests (`replicas: 1`, non-root, RO rootfs) |
| `kubernetes/codex-shim/app/codex_auth.py` | Create | Vendored `refresh_codex_oauth_pure` + constants + error classification (D11) |
| `kubernetes/codex-shim/app/store.py` | Create | Secret read/patch, JWT `exp` parse, cached `expires_at` (D12) |
| `kubernetes/codex-shim/app/session.py` | Create | Single-flight proactive/reactive refresh, state machine (D14) |
| `kubernetes/codex-shim/app/proxy.py` | Create | `/v1/*` endpoints: internal-bearer check, credential swap, Responses call, refresh-and-retry-once wiring (D14/D15) |
| `kubernetes/codex-shim/app/codex_translate.py` | Create | **New (Amendment 2)** — vendored `_chat_messages_to_responses_input` + `_responses_tools` (provenance header), non-streaming response assembly, and the hand-rolled Responses-event → `chat.completion.chunk` emitter (D15/D15a) |
| `kubernetes/codex-shim/app/main.py` | Create | FastAPI app, `/internal/session`, `/healthz` |
| `kubernetes/codex-shim/{Dockerfile,requirements.txt}` | Create | Image build |
| `kubernetes/codex-shim/{deployment,service,rbac,kustomization}.yaml` | Create | Own SA/Role/RoleBinding (D13); no Ingress — cluster-internal only |
| `kubernetes/proxy/litellm-config.yaml` | Modify | Add `cloud` entry → shim base_url + `CODEX_SHIM_KEY` (no `OPENAI_API_KEY`) |
| `kubernetes/llama-service/deployment-router.yaml` | Modify | Add `--slots` for drain visibility |
| `kubernetes/llama-service/README.md` | Modify | Point runbook at panel; keep manual fallback; document `codex login` provisioning |
| `specs/011_gpu_handoff_web_panel.md` | Create | Numbered spec |
| `kubernetes/llama-service/switch-model.sh` | **Unchanged** | Non-goal per D2 |
| `kubernetes/docker/hermes-agent/hermes_cli/auth.py` | **Unchanged** | Source of the vendored refresh logic; never modified in place |
| `kubernetes/docker/hermes-agent/agent/codex_responses_adapter.py` | **Unchanged** | Source of the vendored request-side translation (D15a); read-only reference |

Secrets created out-of-band, never committed: `codex-shim-auth` (token pair, D12),
`codex-shim-key` (static internal bearer), `model-panel-auth` (`bearer`).
The removed `openai-cloud-auth` Secret (`api-key`, `admin-key`) is **no longer needed**.

## Interfaces / Contracts

```python
class Step:                       # unchanged
    name: str
    def apply(self, ctx: Ctx) -> None: ...
    def undo(self, ctx: Ctx) -> None: ...   # no-op if apply never ran

class HandoffError(Exception):
    phase: str; message: str; recoverable: bool

SessionState = Literal[
    "not_configured",          # Secret absent or shape-invalid
    "valid",
    "expiring_soon",           # within the 120 s skew; refresh in flight
    "rate_limited",            # token endpoint 429 — creds valid, retry later
    "expired_needs_relogin",   # terminal: invalid_grant / refresh_token_reused / 401-403
    "refresh_failed",          # transient/unknown refresh error; will retry
]
```

```
# codex-shim (cluster-internal only, no Ingress)
GET  /internal/session -> {state, expires_at, last_refresh, last_error_code,
                           reason} # never any token material
POST /v1/chat/completions          # internal bearer in, access_token out, ChatCompletions⇄Responses (D15)
GET  /v1/models                    # static list from CODEX_CLOUD_MODEL; upstream has no
                                   #   Chat-Completions /models to proxy
GET  /healthz                      # liveness only; does NOT assert session validity

# model-panel (unchanged except `session` replacing `spend`)
GET  /api/status -> {mode, profile, transitioning, phase, error, gpu_pods,
                     session: {state, expires_at, stale} | null,
                     last_known_good, drift: bool}
POST /api/switch {"target": "cloud"|"local"} -> 202 {transition_id}
                                              | 409 {session_state, reason}  # D17
POST /api/profile {"profile": "daily"|"large"} -> 202 {transition_id}        # D18
                                              | 200 {"unchanged": true}      # already active
                                              | 400 {"error":"invalid_profile"}
                                              | 409 {"error":"not_local"}     # mode != local
                                              | 409 {"error":"transition_in_progress"}
POST /api/repair -> 202
```

```python
# D18 — the whole profile↔preset mapping, copied verbatim from switch-model.sh
PROFILE_MODEL_ALIASES = {"daily": "qwen3.5-9b", "large": "qwen3.6-27b-q3"}

# llama-router calls the panel makes (in-cluster, LLAMA_API_KEY bearer):
#   POST {LLAMA_ROUTER_BASE_URL}/chat/completions
#        {"model": <preset>, "messages": [{"role":"user","content":"OK"}],
#         "max_tokens": 1, "chat_template_kwargs": {"enable_thinking": false}}
#        -> 200 once the preset is loaded (blocking; --models-autoload)
#   GET  {router}/health?model=<preset>&autoload=false  -> 200 iff loaded
```

RBAC — **two separate Roles**:

`model-panel` (namespace `llms`) — unchanged:

| Group | Resource | Verbs | resourceNames |
|---|---|---|---|
| `apps` | `deployments` | get, list, watch | — (list/watch cannot be name-scoped) |
| `apps` | `deployments`, `deployments/scale` | patch, update | the 8 named deployments |
| `""` | `pods` | get, list, watch | — |
| `""` | `configmaps` | get, list, watch | — |
| `""` | `configmaps` | patch, update | `litellm-config`, `model-panel-state` |
| `keda.sh` | `scaledobjects` | get, list, watch, patch | `vllm-big-model`, `vllm-small-model` |

`codex-shim` (namespace `llms`) — new, minimal:

| Group | Resource | Verbs | resourceNames |
|---|---|---|---|
| `""` | `secrets` | get, patch, update | `codex-shim-auth` |

Gotcha recorded: Kubernetes forbids `resourceNames` with `list`/`watch`, hence the split
read/mutate rules in the panel Role — and why the shim Role has no `list`/`watch` at all.

## Testing Strategy

| Layer | What to Test | Approach |
|-------|-------------|----------|
| Unit | Undo stack unwinds in LIFO order; partial failure leaves no forced scale | `StepRunner` with fake steps |
| Unit | Drain timeout aborts; busy slots never proceed to scale | Fake `/slots` responses |
| Unit | `litellm-config` YAML patch preserves `litellm_callbacks.py` and unrelated entries | Round-trip on the real ConfigMap fixture |
| Unit (shim) | Refresh maps each upstream shape to the right `SessionState`: 429⇒`rate_limited`, `invalid_grant`/`refresh_token_reused`/401/403⇒`expired_needs_relogin`, 5xx⇒`refresh_failed` | Stubbed token endpoint; table-driven, mirrors the vendored classification |
| Unit (shim) | **Rotated refresh token is persisted** — the Secret patch carries the *new* `refresh_token` when upstream returns one | Fake K8s API; assert patch body |
| Unit (shim) | Single-flight: N concurrent 401s trigger exactly one refresh and one retry each, never a retry loop | `asyncio.gather` + call counter |
| Unit (shim) | No token material appears in `/internal/session`, logs, or error bodies | Assert response/log bytes against both token values |
| Unit (panel) | **Fail-closed**: every non-`valid`/`expiring_soon` session state, plus shim timeout, yields 409 and **zero** K8s mutations | Stubbed shim client; assert the fake API server received no calls |
| Integration | GPU-not-free within window ⇒ abort + `state=degraded` + replicas restored | `kubernetes` client against a fake API server |
| Integration | Status reconciles ConfigMap claim vs. live drift | Same harness |
| Unit (shim) | **Request translation (D15)**: `messages` → `input` + `instructions`; a history containing `role: "tool"` + assistant `tool_calls` produces `function_call_output` items with matching `call_id` and **no** `role: "tool"` item survives | Table-driven against the vendored converter; assert the rejected shape never appears |
| Unit (shim) | **Streaming translation (D15)**: a recorded Responses event sequence yields well-formed `chat.completion.chunk` frames (`object`, `id`, `created`, `choices[0].delta`), a terminal `finish_reason`, and a final `data: [DONE]` | Fake event iterator; parse the emitted SSE back as a client would |
| Unit (shim) | **Non-streaming translation (D15)**: same sequence collapses into one `chat.completion` object with `choices[0].message.content`, `tool_calls`, and `usage` mapped from `input_tokens`/`output_tokens` | Same fixture, `stream: false` |
| Unit (shim) | Upstream `type: "error"` frame and `response.failed` map to an OpenAI-shaped error body, not a truncated 200 | Fake event iterator |
| Integration (shim) | LiteLLM-shaped request in ⇒ LiteLLM-parseable stream out (chunks are not buffered into one blob) | Fake upstream emitting chunked Responses SSE |
| Unit (panel) | **D18 profile sequence**: `daily→large` preloads `qwen3.6-27b-q3` **before** the alias is patched, and the patched alias carries `openai/qwen3.6-27b-q3` | Fake preload/probe callables + fake K8s API; assert call ordering |
| Unit (panel) | **D18a preconditions, zero mutations**: `mode=cloud` ⇒ 409 `not_local`; `phase=transitioning` ⇒ 409 `transition_in_progress`; unknown profile ⇒ 400; already-active profile ⇒ 200 `unchanged` | Assert the fake K8s API and the fake router client received **no** calls |
| Unit (panel) | **D18a undo**: a failing LiteLLM restart restores the prior `litellm-config` bytes, and a failing best-effort re-preload does not escalate the abort | `StepRunner` with an injected failing step |
| Unit (panel) | Drain timeout aborts a profile switch too — a busy router is never evicted mid-request | Reuse the D3 fake `/slots` harness |
| E2E (manual) | Toggle both directions on the cluster; assert 0 GPU pods on Cloud; force one refresh and confirm cloud traffic survives it | README runbook checklist |
| Regression | `switch-model.sh daily\|large` still passes | Script untouched; run once post-merge |

## Threat Matrix

Not applicable — neither service introduces a shell, subprocess, VCS/PR automation, or
executable-file classification boundary; all cluster mutation goes through the typed
Kubernetes client, and no user input reaches a command line.

| Boundary | Applicability |
|---|---|
| Documentation-like paths | N/A — no file classification or execution |
| Git repository selection | N/A — no VCS access |
| Commit state | N/A — no VCS access |
| Push state | N/A — no VCS access |
| PR commands | N/A — no PR automation |

Two routing boundaries *are* present and are covered above: the LiteLLM alias rewrite
(ConfigMap round-trip + fail-closed tests) and the shim's credential-swapping **translating**
proxy (internal-bearer validation, no-token-leak assertions, single-flight refresh, plus the
Amendment-2 translation-fidelity tests — a body-rewriting boundary must be tested as one).

## Migration / Rollout

Additive and ordered: (1) `codex login` a dedicated session for the shim (D16) and create
`codex-shim-auth` + `codex-shim-key` — this is now the step that *produces* the fresh
token D-OQ1/D-OQ4 need, so both are settled here rather than by a separate spike;
(2) deploy `codex-shim` and verify `/internal/session` reports `valid` **and** a direct
in-cluster `/v1/chat/completions` (both `stream: true` and `stream: false`) returns a
well-formed Chat Completions payload — this single call resolves D-OQ1 and D-OQ4 from the
real egress path, and must pass **before** touching `litellm-config`; (3) deploy
`model-panel`; (4) verify one Cloud→Local round trip with the README runbook open as
fallback. If step (2) returns `403` + `cf-mitigated`, D-OQ4 is answered negatively and the
cloud path is re-scoped before any LiteLLM change — nothing is half-switched.

Rollback: delete `kubernetes/model-panel/` and `kubernetes/codex-shim/` and their
Secrets, revert `litellm-config.yaml` and `deployment-router.yaml`. Hermes's own Codex
integration is untouched and keeps working if the shim is removed.

## Open Questions

- [ ] **D-OQ1 — STILL UNRESOLVED (the spike did not answer it):** does
      `https://chatgpt.com/backend-api/codex` accept a **non-Codex-CLI HTTP client
      identity**? The PR1 spike (obs #343) was **inconclusive, not negative and not
      positive**: the only Codex OAuth token on the host was already expired, so both
      probes (bare identity and `codex_cli_rs`-shaped identity) got `401 token_expired`
      from OpenAI's own layer — authorization was never reached. Notably **neither** probe
      carried a `cf-mitigated` header, so from *that* network the WAF did not block either
      shape; that is a data point about the network, not an answer about identity.
      **Do not record this as resolved in either direction.** The body half of the original
      question *is* now answered and is no longer open: the endpoint speaks the Responses
      API (see Amendment 2 / D15). Re-run the probe when a fresh token exists — see D-OQ4
      for the safe timing.
- [ ] **D-OQ4 (new, Amendment 2) — Cloudflare originator allow-list vs. in-cluster egress:**
      `kubernetes/docker/hermes-agent/agent/auxiliary_client.py:778-814`
      (`_codex_cloudflare_headers`) documents that Cloudflare in front of this endpoint
      whitelists a small originator set (`codex_cli_rs`, `codex_vscode`, `codex_sdk_ts`,
      anything starting with `Codex`) and serves `403` + `cf-mitigated: challenge` to
      **non-residential-IP** callers that do not advertise one. The shim egresses from the
      home k3s cluster, which is **not verified** to be the same egress path as the spike
      session. This is **untested — assume neither pass nor fail.** Mitigation is cheap and
      already precedented (send the same `originator` / `User-Agent` / `ChatGPT-Account-ID`
      headers Hermes sends), but whether that is *sufficient* from the cluster's IP is
      unknown. **Validation timing constraint:** it needs a fresh, non-expired token, and
      per D16 the refresh token is single-use and rotates server-side — so probe
      **immediately after a normal Hermes-driven refresh**, reading the resulting access
      token, never by forcing a refresh ourselves. Forcing one would clobber the user's
      live Codex session. This question does **not** block writing the D15 translation
      code; it blocks declaring the cloud path working end-to-end.
- [ ] **D-OQ2:** exact Codex model id for the `cloud` entry (parameterized as
      `CODEX_CLOUD_MODEL`; a default must be picked at apply time).
- [ ] **D-OQ3:** confirm the user can perform a second, dedicated `codex login` for the
      shim (D16). If the account permits only one live session, the shim and Hermes will
      evict each other and the cloud path must be re-scoped.
