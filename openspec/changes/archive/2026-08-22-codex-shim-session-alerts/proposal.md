# Proposal: Classified Session Degradation + Debounced Telegram Alerts

## Intent

`codex-shim`'s `SessionManager` classifies only `SecretNotFound` and `AuthError`. Anything else raised by `TokenStore.read()`'s live `CoreV1Api.read_namespaced_secret` call — `ApiException`, `MaxRetryError`, connect timeouts — escapes unclassified and `/internal/session` returns a generic FastAPI 500. Found live during D-OQ4 validation of `gpu-handoff-web-panel` (PR #36) when the operator's path to the in-cluster kube-apiserver briefly dropped.

Two gaps: (1) a real failure mode has no state, so the panel shows a 500 instead of a diagnosis; (2) nothing tells the operator the Codex session degraded — it is discovered only by looking at the panel.

## Scope

### In Scope
- New `SessionState` value (`backend_unreachable`) in `kubernetes/codex-shim/app/session.py`, alongside `not_configured|valid|expiring_soon|rate_limited|expired_needs_relogin|refresh_failed`.
- `_load_cached()` / `ensure_fresh()` wrap the Secret read and map connectivity exceptions to that state with a **sanitized** `reason` — never a raw traceback, never token material (2.6).
- `main.py`'s `/internal/session` response shape is unchanged; the exception simply never reaches it unclassified.
- `kubernetes/model-panel/`: debounced degradation alert. On a **transition** into sustained-degraded (N consecutive observations, default 3 ≈ 6s), POST an HMAC-SHA256-signed payload (`X-Webhook-Signature-V2`) to Hermes's existing `POST /webhooks/{route}`. Recovery-to-`valid` notice on the reverse transition.
- Reuse the existing `/api/status` → `/internal/session` read path; no new poller.
- Signing secret as a new k8s Secret mounted in **model-panel only**, never in codex-shim.
- Unit tests both sides with mocked kubernetes-client exceptions and a stubbed webhook transport; no live cluster.

### Out of Scope
- Hermes's own `config.yaml` route (`deliver_only: true`, `deliver: telegram`) and its secret — `hermes-agent` is outside this repo's git tracking. Documented as a manual runbook step, not code.
- Dead-man's switch. If the cluster network is severed the alert POST may also fail. Accepted for v1: the realistic cases (rate limit, expired session, refresh failure) do not sever model-panel→Hermes. An external heartbeat (e.g. Uptime Kuma) is a future follow-up.
- Changing `codex-shim`'s thin posture: no new probes, no outbound alerting, `readOnlyRootFilesystem: true` preserved.
- Changing `ALLOWED_SESSION_STATES` / D17 switch-to-Cloud gating semantics.

## Capabilities

### New Capabilities
- `codex-session-state`: the full `/internal/session` state taxonomy, including connectivity classification, sanitized reasons, and the no-token-material guarantee.
- `session-degradation-alerting`: transition-triggered, debounced, signed outbound alerting from model-panel, with recovery notice and delivery-failure semantics.

### Modified Capabilities
- None. No existing `openspec/specs/` capability covers codex-shim or model-panel (all five are memory-router).

## Approach

**codex-shim.** Mirror the existing `_classify_error(AuthError)` precedent with a store-read classifier: catch the kubernetes/urllib3 connectivity exception family, set `_state = "backend_unreachable"`, `_last_error_code` to a stable code, `_reason` to a short sanitized string. `SecretNotFound` (a *definitive* answer, not a connectivity failure) keeps its current `not_configured` path unchanged. The state is non-switchable by construction: `ALLOWED_SESSION_STATES = {"valid", "expiring_soon"}` already excludes it, so D17 stays fail-closed with zero edit.

**model-panel.** A small `app/alerts/` module holding transition state (consecutive-degraded counter, last-alerted state) on `app.state`, following the existing `last_alias_heal_attempt` debounce precedent. `/api/status` calls it after it has resolved `session`; the alert POST is fire-and-forget on the existing background executor so a slow or dead Hermes never adds latency to a 2s poll. Signing and payload assembly are pure functions, injectable transport — the same seam as `CodexShimClient`.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `kubernetes/codex-shim/app/session.py` | Modified | New state + store-read classifier. |
| `kubernetes/codex-shim/app/main.py` | Unchanged | Response shape untouched. |
| `kubernetes/codex-shim/tests/` | New | Mocked `ApiException`/`MaxRetryError` classification tests. |
| `kubernetes/model-panel/app/alerts/` | New | Debounce state machine, HMAC signer, webhook client. |
| `kubernetes/model-panel/app/main.py` | Modified | Hook the alerter into `/api/status`. |
| `kubernetes/model-panel/deployment.yaml` | Modified | Mount webhook secret, alert env vars. |
| `kubernetes/model-panel/tests/` | New | Transition/debounce/signature/failure tests. |
| `openspec/specs/{codex-session-state,session-degradation-alerting}/` | New | Full specs. |
| `specs/0NN_codex_shim_session_alerts.md` | New | Numbered spec companion. |
| `docs/` runbook | New | Hermes route + secret provisioning steps. |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Alerts only fire while a browser tab polls `/api/status` (poll is browser-driven, `panel.js setInterval`) | **High** | Open question 1 — must be settled before spec; a server-side ticker may be required. |
| Alert storm on a flapping session | Med | Alert only on transitions, never per-poll; N-consecutive debounce; minimum re-alert interval. |
| Raw exception text leaks into the panel/alert | Med | Sanitized reason at classification time; `/api/status`'s existing `str(exc)` fallback audited in the same change. |
| Signing secret mounted too broadly | Low | model-panel only; codex-shim manifest asserted unchanged. |
| Hermes webhook down → alert lost silently | Med | Log the delivery failure locally; accepted best-effort per v1 decision. |
| Scope creep into codex-shim doing its own alerting | Med | Asserted: no outbound HTTP added to codex-shim; deployment.yaml diff is zero. |
| New state breaks a consumer expecting the old six | Low | Additive; `ALLOWED_SESSION_STATES` already fails closed on unknown states. |

## Rollback Plan

Two independent reverts. (1) **codex-shim**: drop the state literal and the store-read `except` block — behavior returns to the pre-change unhandled-exception 500. No persisted state, no schema, no migration. (2) **model-panel**: remove the `app/alerts/` import and its call site in `/api/status` (one line) — polling, switching, and repair are untouched; then delete the Secret and the env vars from `deployment.yaml`. The Hermes-side route is inert once nothing posts to it and can be removed at leisure. Reverting either half does not require reverting the other.

## Dependencies

- Hermes webhook route configured externally with `deliver_only: true` + `deliver: telegram` — manual, not merge-blocking but alert-delivery-blocking.
- Shared signing secret provisioned in both Hermes's config and the k8s Secret.
- No new Python dependency: `hmac`/`hashlib` are stdlib; `httpx` is already in model-panel.

## Success Criteria

- [ ] A mocked `ApiException` from `TokenStore.read()` yields `state == "backend_unreachable"` from `/internal/session` with HTTP 200, not a 500.
- [ ] A mocked `MaxRetryError`/connect timeout yields the same state.
- [ ] `SecretNotFound` still yields `not_configured` — unchanged test passes unmodified.
- [ ] The `reason` contains no traceback, no token material, and no Secret contents.
- [ ] `assert_switch_to_cloud_allowed` rejects `backend_unreachable` with zero cluster mutations.
- [ ] N-1 consecutive degraded observations emit **zero** alerts; the Nth emits exactly one.
- [ ] Remaining degraded for many further polls emits no additional alert.
- [ ] Transition back to `valid` emits exactly one recovery alert and re-arms the alerter.
- [ ] The POST carries a valid `X-Webhook-Signature-V2` HMAC-SHA256 over the exact body.
- [ ] A webhook transport failure is logged and does not raise, does not fail `/api/status`, and does not add latency to the 2s poll.
- [ ] `kubernetes/codex-shim/deployment.yaml` has zero diff; the signing secret appears only in model-panel.

## Proposal question round

Question 1 is **resolved** by explicit user decision (AskUserQuestion): option (b) — a small server-side ticker in model-panel, independent of any browser tab. This supersedes the exploration's "no new poller" instruction, which had assumed the existing poll was server-side; it is not. Questions 2-5 are **not blocking** — each carries a proposed default `sdd-spec`/`sdd-design` should adopt unless a concrete correctness reason emerges to deviate (same standing as this repo's other recent changes, e.g. graphiti-backend's open-question round):

2. **Which states are alert-worthy?** Proposed: `expired_needs_relogin` (needs you to act), `refresh_failed`, `backend_unreachable`, and the panel's synthetic `unreachable`. Excluded: `rate_limited` (self-resolving, would be noisy) and `not_configured` (a steady state before bootstrap, not a regression).
3. **Urgency and message content.** Proposed: Telegram message contains state, sanitized reason, `expires_at`, and a one-line next-action hint (e.g. "re-run bootstrap_login.md" for `expired_needs_relogin`). A recovery notice on transition back to `valid` is wanted (confirms the outage ended without requiring you to check the panel).
4. **Re-alert policy for a long outage.** Proposed: **one-shot per transition**, no repeat — avoids nagging; the recovery notice (question 3) is the natural "all clear" bookend.
5. **Debounce threshold.** Now that question 1 is a server-side ticker (with its own interval, to be fixed by design), express the rule as **sustained for ≥10 seconds** rather than a poll-count N, so the threshold survives whatever ticker interval design picks.
