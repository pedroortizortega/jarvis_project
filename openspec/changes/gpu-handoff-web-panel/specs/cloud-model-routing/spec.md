# cloud-model-routing Specification

## Purpose

Routes inference to a cloud model in Cloud mode, backed by the user's existing
Codex/ChatGPT OAuth subscription session rather than a pay-per-use API key. A
dedicated codex-shim service owns the OAuth session and exposes a stable
internal OpenAI-compatible endpoint; LiteLLM's `cloud` entry is unchanged in
shape, only the credential and backing service behind it change.

## Requirements

### Requirement: Codex OAuth Session Ownership
A dedicated codex-shim service MUST be the sole owner of the Codex OAuth token
pair (access + refresh token), storing it only in a Kubernetes Secret. The
shim MUST refresh the token pair proactively before expiry and reactively on
a 401 from the upstream, reusing the logic pattern proven in
`hermes_cli/auth.py`'s `resolve_codex_runtime_credentials` against
`https://chatgpt.com/backend-api/codex`.

#### Scenario: Proactive refresh before expiry
- GIVEN the stored access token is nearing its expiry window
- WHEN the shim's refresh check runs
- THEN the shim refreshes the token pair and rewrites the Secret before the token expires

#### Scenario: Reactive refresh on 401
- GIVEN the shim forwards a request using the current access token
- WHEN the upstream responds 401
- THEN the shim refreshes the token pair once and retries the request before surfacing failure

### Requirement: Internal OpenAI-Compatible Endpoint
The shim MUST expose a stable internal OpenAI-compatible endpoint. LiteLLM's
`cloud` model_list entry MUST point at this internal endpoint using a static
internal key, structurally unchanged from today's vLLM/llama-router entries.

#### Scenario: Cloud entry routes through the shim
- GIVEN mode = Cloud and the shim reports a valid session
- WHEN a request is routed via LiteLLM's `cloud` entry
- THEN the request reaches the shim's internal endpoint and is proxied upstream using the current Codex access token

### Requirement: Session-Status Reporting
The shim MUST report Codex session status as one of exactly: "not configured",
"valid", "expiring soon", "expired / needs re-login", "refresh failed". OAuth
login itself MUST NOT be performed by the shim or panel; login remains an
out-of-band interactive `codex login`-style flow.

#### Scenario: Status reflects real session state
- GIVEN the panel queries session status
- WHEN the shim evaluates the stored token pair
- THEN it returns exactly one of the five defined states, accurately reflecting the current session

#### Scenario: Login is never automated
- GIVEN the session is "expired / needs re-login" or "not configured"
- WHEN the user is on the panel
- THEN the system MUST NOT attempt to perform OAuth login on the user's behalf

### Requirement: Fail-Closed on Non-Valid Session
If session status is anything other than "valid" or "expiring soon", the
system MUST fail closed: it MUST NOT attempt the switch to Cloud and MUST
keep serving Local.

#### Scenario: Expired session blocks switch
- GIVEN session status = "expired / needs re-login"
- WHEN the user triggers switch to Cloud
- THEN the switch is blocked before any GPU scale-down step, the panel reports the reason, and mode remains Local

#### Scenario: Refresh failure blocks switch
- GIVEN session status = "refresh failed"
- WHEN the user triggers switch to Cloud
- THEN the switch is blocked before any GPU scale-down step, the panel reports the reason, and mode remains Local

### Requirement: Secret-Only Credential Handling
The Codex OAuth token pair MUST be stored only as a Kubernetes Secret and
MUST NOT be rendered, echoed, or logged by the panel or codex-shim.

#### Scenario: Tokens never shown in UI
- GIVEN a user views the panel, including error states
- WHEN any request or response is inspected
- THEN neither the access token nor the refresh token MUST appear in any UI element or client-visible payload

#### Scenario: Tokens never logged
- GIVEN the codex-shim processes a refresh or a proxied request
- WHEN logs are inspected
- THEN neither the access token nor the refresh token MUST appear in log output

### Requirement: Hermes Default Routing Update
Switching to Cloud MUST update Hermes `model.default` to the `cloud` model
entry; switching to Local MUST update it back to the fixed default local
profile.

#### Scenario: Hermes follows mode
- GIVEN mode transitions from Local to Cloud
- WHEN the switch completes
- THEN Hermes `model.default` reflects the `cloud` model entry

### Requirement: Accepted Deviation From Spec 003
The system MAY share one Codex OAuth session across the CLI (Hermes) and the
codex-shim, as an explicitly accepted deviation from
`specs/003_codex_profiles_and_opencode.md`'s one-session-per-tool convention.
This deviation MUST be traceable to this requirement and MUST be revisited if
a second independent Codex login becomes preferable.

#### Scenario: Shared session is intentional, not accidental
- GIVEN the codex-shim and Hermes both hold access to the same Codex OAuth
  session
- WHEN this design is reviewed against spec 003
- THEN the deviation is traced to this requirement as user-accepted, not treated as a spec-003 violation
