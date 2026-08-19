# Proposal: GPU Handoff Web Panel (Local ↔ Cloud Model Switch)

## Intent

Freeing the single RTX 4070 Ti SUPER for gaming today requires a ~15-step manual
kubectl/bash runbook (`kubernetes/llama-service/README.md`): pause KEDA, scale
vLLM/llama.cpp to 0, wait for pod deletion, republish LiteLLM config, restart the
Hermes gateway. That is error-prone, undocumented for non-terminal use, and
unusable from a phone. No web panel exists anywhere (Hermes is CLI/TUI only).

We want one user-friendly LAN page: a toggle that moves inference to a cloud model
backed by the user's existing Codex/ChatGPT subscription session and actually
releases the GPU, and moves it back when done.

## Scope

### In Scope
- Minimal internal web service (single page): current-state view + Local/Cloud toggle + local profile picker (daily / large).
- Backend performs the **full GPU handoff**: pause KEDA ScaledObjects → scale vLLM/llama.cpp deployments to 0 → wait for pod deletion → confirm GPU free; reverse on return to Local.
- LiteLLM routing update + Hermes `model.default` update as part of each switch.
- New **Codex auth shim service** in `llms` that owns the Codex OAuth session
  (access/refresh token pair), refreshes it proactively and on 401 by reusing
  Hermes's proven logic in `kubernetes/docker/hermes-agent/hermes_cli/auth.py`
  (`resolve_codex_runtime_credentials`, base_url `https://chatgpt.com/backend-api/codex`),
  and exposes a stable internal OpenAI-compatible endpoint.
- `cloud` entry in `kubernetes/proxy/litellm-config.yaml` pointing at that internal
  shim endpoint (structurally identical to today's vLLM / llama-router entries),
  resolving the tier left pending in `specs/002`.
- OAuth credential Secret in `llms` holding the token pair (auth.json-shaped, rewritten by the shim on refresh).
- Scoped ServiceAccount + Role (namespace `llms`, named deployments only, `deployments/scale`, ConfigMap patch, KEDA ScaledObject patch).
- Exposure via the Engram precedent: Traefik LAN Ingress + mTLS + app bearer. No LoadBalancer, no NodePort, no public path.
- Guarded sequencing with rollback on failure, generalized from `switch-model.sh`.

### Out of Scope
- Any change to the existing router daily↔large behaviour of `switch-model.sh` (must keep working unchanged).
- Multi-user accounts, RBAC UI, audit dashboards, metrics/graphs.
- Anthropic/OpenRouter providers, pay-per-use OpenAI Platform API keys, automatic cost budgeting, LiteLLM `failure_callback` auto-fallback (spec 002 A2 stays deferred).
- Performing the OAuth login itself from the panel: login stays an interactive, user-driven `codex login`-style flow performed out-of-band.
- Any quota-remaining / usage-count number (no such endpoint is verified for this credential).
- Replacing Hermes CLI model switching; touching `hermes-native/orchestration/`.
- Waking/scheduling the GPU automatically (no game detection, no Wake-on-LAN).

## Capabilities

### New Capabilities
- `model-switch-panel`: user-friendly web UI showing current mode and switching local↔cloud safely.
- `gpu-handoff-orchestration`: ordered, guarded, rollback-capable GPU release/reclaim sequence.
- `cloud-model-routing`: Codex OAuth session ownership in a shim service (token-pair Secret, proactive/401 refresh reusing Hermes's logic), the internal OpenAI-compatible endpoint it exposes, LiteLLM/Hermes default routing to it, and session-status reporting (valid / expiring soon / expired → needs re-login).

### Modified Capabilities
- None (no `openspec/specs/` main specs exist yet).

## Approach

New small Python service deployed in `llms`, talking to the Kubernetes API with a
least-privilege ServiceAccount instead of shelling out to kubectl. Its switch
routine generalizes the proven guarded sequence in `switch-model.sh` (stop
consumers → mutate → health-probe → restore, with a `trap`-style rollback) and
extends it with the README's vLLM↔llama.cpp handoff steps. The UI is a single
state-driven page: one visible mode, one action, explicit progress and result.

Cloud capacity comes from the user's existing Codex/ChatGPT subscription rather
than a metered API key. A dedicated shim service holds the OAuth token pair and
refreshes it (porting Hermes's `hermes_cli/auth.py` logic against
`https://chatgpt.com/backend-api/codex`), so LiteLLM keeps its existing shape:
an `openai/`-protocol entry pointing at a stable internal base_url with a static
internal key — the same pattern as vLLM and llama-router today.

### Constraints
- One GPU: vLLM and llama.cpp MUST never be scaled up simultaneously.
- Repo invariant: GPU deployments stay committed at `replicas: 0`.
- Switching is stateful and order-dependent; partial states must be detectable and recoverable.
- NetworkPolicy enforcement is disabled cluster-wide — network isolation is not a security boundary; mTLS + bearer carry the auth.
- The panel must remain safe when the Codex session is missing, expired, or unrefreshable (fail closed, stay local); "not configured" and "needs re-login" are distinct states.
- OAuth login is inherently interactive: the panel reports session status, it never performs login itself.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `kubernetes/model-panel/` (new) | New | Deployment, Service, Ingress, TLSOption, ServiceAccount/Role/RoleBinding |
| `kubernetes/codex-shim/` (new) | New | Separate Deployment/Service owning the Codex OAuth session + internal OpenAI-compatible endpoint. Kept out of `model-panel` on purpose: it sits in LiteLLM's **request path** (must stay up independently of the control-plane panel) and its credential custody must not inherit the panel's cluster-mutating RBAC. |
| `kubernetes/docker/hermes-agent/hermes_cli/auth.py` | Reused | Source of the proven refresh logic (`resolve_codex_runtime_credentials`); ported/extracted, not modified in place |
| `kubernetes/proxy/litellm-config.yaml` | Modified | Add `cloud` model entry pointing at the internal codex-shim base_url |
| `kubernetes/llama-service/switch-model.sh` | Modified | Extract/generalize guarded sequence; keep current CLI behaviour |
| `kubernetes/llama-service/README.md` | Modified | Point runbook at the panel, keep manual fallback |
| `kubernetes/hermes/config/config.yaml` | Modified | `model.default` becomes panel-managed |
| `specs/` | New | Numbered spec for this change |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Naive toggle leaves two GPU consumers scheduled → preemption/OOM | Med | Enforce ordering; block scale-up until prior pods confirmed deleted |
| Partial/stuck switch leaves system unusable | Med | Rollback guard + panel shows and can retry/repair partial state |
| Codex OAuth token leak | Med | Token pair Secret-only, never rendered in UI or logs; mTLS-gated panel; shim is the sole holder |
| Sharing one Codex OAuth session across clients deviates from `specs/003_codex_profiles_and_opencode.md` ("each tool logs in via its own official mechanism") | High (certain) | **Accepted deviation, explicitly approved by the user.** Documented here and to be restated in the spec; revisit if a second independent Codex login becomes preferable |
| OpenAI ToS / reliability of proxying a ChatGPT-subscription Codex credential as server-to-server traffic is unverified (endpoint may enforce client identity; account may be rate-limited or flagged) | Med | **Accepted as an open, non-blocking risk by the user.** Fail-closed to Local on cloud failure; keep the manual runbook; treat endpoint compatibility as a spike during design |
| Refresh fails (refresh_token revoked, logged out elsewhere, subscription lapsed) → cloud silently unavailable | Med | Shim surfaces a distinct "needs re-login" state; panel blocks the switch to Cloud instead of half-switching |
| Panel becomes a LAN-reachable GPU/cost control with weak auth | Med | Engram mTLS + bearer pattern, no LoadBalancer |
| Existing daily/large router toggle regresses | Low | Refactor behind existing script contract; keep CLI path tested |

## Rollback Plan

Panel and shim are additive: delete `kubernetes/model-panel/` and
`kubernetes/codex-shim/` manifests and the credential Secret, revert
`litellm-config.yaml` and `switch-model.sh` to prior commits. The manual README
runbook remains the fallback for GPU handoff at all times. Hermes's own Codex
integration is untouched and keeps working if the shim is removed.

## Dependencies

- A valid Codex/ChatGPT OAuth session, obtained out-of-band via the interactive login flow and provisioned as a token-pair Secret.
- Hermes's `hermes_cli/auth.py` refresh logic available to port/extract.
- KEDA ScaledObjects present and pausable in `llms`.
- Traefik + mTLS CA material already used by `kubernetes/engram/`.

## Success Criteria

- [ ] From a phone browser on the LAN, one action switches to Cloud and the GPU shows 0 inference pods.
- [ ] One action switches back to Local and inference works again without manual kubectl.
- [ ] Panel always displays the true current mode, including partial/failed states.
- [ ] vLLM and llama.cpp are never simultaneously scaled up.
- [ ] `switch-model.sh` daily↔large behaviour is unchanged.
- [ ] Cloud mode serves requests through the Codex session without manual token handling, including across at least one token refresh.
- [ ] The panel shows an accurate Codex session status and refuses the switch to Cloud when the session is expired/unrefreshable.

## Resolved Product Decisions

The proposal question round below was answered by the user; these decisions are
binding for `sdd-spec` and `sdd-design`:

1. **In-flight request on switch to Cloud**: drain, not hard-cut. The panel
   waits for the active request on the local model to finish before scaling
   vLLM/llama.cpp to 0.
2. **Session-status indicator** (amended, supersedes the earlier "spend
   indicator"): included in the first slice. The subscription is flat-rate, so
   there is no per-request spend to show. That UI slot instead shows the
   Codex/ChatGPT **session status**: valid / expiring soon / expired — needs
   re-login. No quota-remaining number is shown (no such endpoint is verified).
3. **Return to Local**: fixed default profile, not previous-profile
   restoration. Switching back to Local always brings up the same default
   profile (e.g. daily), regardless of which profile (daily/large) was active
   before the switch to Cloud.
4. **GPU fails to free in time**: warn and block. If the local pod is stuck
   terminating and the GPU isn't confirmed free within the expected window,
   the panel surfaces a clear error, does **not** complete the switch to
   Cloud, and leaves the system in the last known-consistent state (no forced
   switch).
5. **Cloud auth mechanism** (amendment 1): Codex OAuth session via a shim
   service, not a pay-per-use OpenAI Platform API key.

## Amendment Log

**Amendment 1 — cloud auth: pay-per-use API key → Codex OAuth session via shim**

- **What changed**: the cloud credential is no longer a metered OpenAI Platform
  API key. A shim/sidecar service owns the user's existing Codex OAuth session,
  reusing Hermes's proven refresh logic
  (`kubernetes/docker/hermes-agent/hermes_cli/auth.py`,
  `resolve_codex_runtime_credentials`, base_url
  `https://chatgpt.com/backend-api/codex`), and exposes a stable internal
  OpenAI-compatible endpoint that LiteLLM calls like any other internal model.
  The spend indicator became a session-status indicator. New affected area
  `kubernetes/codex-shim/`; new Secret shape (token pair, not a static key).
- **Why**: user request after `sdd/gpu-handoff-web-panel/explore-codex-auth`
  surfaced the pay-per-use vs. existing-subscription tradeoff. The user prefers
  reusing the subscription already paid for over adding metered spend.
- **Risks explicitly accepted by the user**: (a) sharing one Codex OAuth session
  across clients deviates from the convention in
  `specs/003_codex_profiles_and_opencode.md`; (b) OpenAI ToS/reliability for
  server-to-server proxying of a ChatGPT-subscription credential is unverified
  and non-blocking; (c) OAuth login stays interactive — the panel reports
  session status and never logs in on the user's behalf.
- **Unchanged**: GPU handoff orchestration, drain-on-switch semantics,
  fixed-default-profile on return to Local, and block-on-timeout.
