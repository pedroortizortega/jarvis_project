# Session Degradation Alerting Specification

## Purpose

Define model-panel's server-side detection of a sustained, alert-worthy Codex session degradation, and its debounced, HMAC-signed outbound notification to Hermes's webhook, independent of any browser tab.

## Requirements

### Requirement: Server-Side Ticker

model-panel MUST run a server-side ticker that periodically resolves session state via the existing `/api/status` → `/internal/session` read path, independent of any browser tab polling. Alert evaluation MUST NOT depend on a browser tab being open.

#### Scenario: Alert fires with no browser tab open

- GIVEN no browser tab is polling `/api/status`
- AND the session transitions into a sustained alert-worthy state
- WHEN the server-side ticker observes the transition
- THEN an alert is still emitted

### Requirement: Alert-Worthy State Set

The alerter MUST treat exactly these states as alert-worthy: `expired_needs_relogin`, `refresh_failed`, `backend_unreachable`, and the panel's synthetic `unreachable`. It MUST NOT alert on `rate_limited` (self-resolving) or `not_configured` (a steady pre-bootstrap state, not a regression).

#### Scenario: Alert-worthy state triggers evaluation

- GIVEN the resolved session state is one of `expired_needs_relogin`, `refresh_failed`, `backend_unreachable`, or `unreachable`
- WHEN the ticker observes it
- THEN the state is eligible for debounced alerting

#### Scenario: Excluded states never alert

- GIVEN the resolved session state is `rate_limited` or `not_configured`, sustained for any duration
- WHEN the ticker observes it
- THEN no alert is emitted for that state

### Requirement: Transition-Triggered Debounce (Sustained ≥10s)

The alerter MUST only evaluate alerting on a transition into an alert-worthy state, not on every observation. It MUST require the alert-worthy state to be sustained continuously for at least 10 seconds before emitting an alert. If the state reverts to a non-alert-worthy value before 10 seconds elapse, no alert MUST be emitted for that transition.

#### Scenario: Sustained degradation past threshold emits exactly one alert

- GIVEN the session transitions into an alert-worthy state
- AND it remains in that state continuously for at least 10 seconds
- WHEN the ticker's next observation confirms the sustained duration
- THEN exactly one alert is emitted for that transition

#### Scenario: Transient blip under threshold emits nothing

- GIVEN the session transitions into an alert-worthy state
- AND it reverts to a non-alert-worthy state before 10 seconds have elapsed
- WHEN the ticker observes the reversion
- THEN zero alerts are emitted for that transition

#### Scenario: Continued degradation after the initial alert emits no duplicate

- GIVEN an alert has already been emitted for the current sustained degraded transition
- WHEN further ticker observations continue to find the same alert-worthy state
- THEN no additional alert is emitted while that transition remains unresolved

### Requirement: One-Shot-Per-Transition Policy

The alerter MUST emit at most one alert per degradation transition, regardless of how long the degraded state persists. Re-alerting on the same unresolved transition MUST NOT occur.

#### Scenario: Long-running outage emits only the original alert

- GIVEN the session has been in an alert-worthy state, already alerted once, for an extended period
- WHEN many further ticker observations occur while the state remains unresolved
- THEN no repeat alert is emitted at any point

### Requirement: Recovery Notice on Reverse Transition

On a transition from an alerted degraded state back to `valid`, the alerter MUST emit exactly one recovery notice, and MUST re-arm itself so a future degradation can alert again.

#### Scenario: Recovery after an alerted outage

- GIVEN the alerter previously emitted a degradation alert for the current transition
- WHEN the session state transitions back to `valid`
- THEN exactly one recovery notice is emitted
- AND the alerter is re-armed to detect a subsequent degradation transition

#### Scenario: No recovery notice without a prior alert

- GIVEN the session was in a non-alert-worthy state (e.g. `rate_limited`, never alerted) and transitions to `valid`
- WHEN the ticker observes the transition
- THEN no recovery notice is emitted, since no alert was previously sent for that transition

### Requirement: HMAC-Signed Webhook Delivery

Every outbound alert or recovery notice MUST be delivered as a `POST` to Hermes's `POST /webhooks/{route}` endpoint, signed with `HMAC-SHA256` over the exact request body, carried in an `X-Webhook-Signature-V2` header. The signing secret MUST be sourced from a Kubernetes Secret mounted only in model-panel, never in codex-shim.

#### Scenario: Outbound POST carries a valid signature

- GIVEN an alert or recovery notice is ready to send
- WHEN the POST is constructed
- THEN it includes `X-Webhook-Signature-V2` computed as HMAC-SHA256 over the exact serialized body, using the mounted signing secret

#### Scenario: codex-shim never holds the signing secret

- GIVEN the codex-shim deployment manifest
- WHEN it is inspected for secret mounts
- THEN it has zero diff from before this change, and the signing secret does not appear in it

### Requirement: Alert Message Content

An alert payload MUST include the degraded `state`, the sanitized `reason`, `expires_at` (if known), and a one-line next-action hint appropriate to the state (e.g. a re-login pointer for `expired_needs_relogin`).

#### Scenario: Alert payload includes required fields

- GIVEN a debounced alert is emitted for `expired_needs_relogin`
- WHEN the payload is inspected
- THEN it includes `state`, a sanitized `reason`, `expires_at` (or an explicit absence marker if unknown), and a one-line next-action hint

### Requirement: Delivery-Failure Semantics

A webhook transport failure (timeout, connection error, non-2xx response) MUST be logged locally and MUST NOT raise an unhandled exception, MUST NOT fail `/api/status`, and MUST NOT add latency to the 2-second poll cycle.

#### Scenario: Webhook down does not affect status endpoint

- GIVEN the webhook transport raises a connection error or times out
- WHEN an alert delivery is attempted
- THEN the failure is logged, `/api/status` still returns its normal response, and no added latency is observed on that poll

#### Scenario: Non-2xx webhook response is treated as delivery failure

- GIVEN Hermes's webhook responds with a non-2xx status
- WHEN the alerter processes the response
- THEN it logs the failure and does not raise, matching the same best-effort semantics as a transport-level failure
