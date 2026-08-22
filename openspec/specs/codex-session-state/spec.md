# Codex Session State Specification

## Purpose

Define the complete `/internal/session` state taxonomy exposed by `codex-shim`'s `SessionManager`, including the connectivity-failure classification, the sanitized-reason contract, and the guarantee that no token material ever leaves the process.

## Requirements

### Requirement: Complete State Enumeration

`SessionManager` MUST classify every observable condition of the Codex session into exactly one of: `not_configured`, `valid`, `expiring_soon`, `rate_limited`, `expired_needs_relogin`, `refresh_failed`, `backend_unreachable`. No unclassified exception MUST propagate out of `_load_cached()` or `ensure_fresh()` as an unhandled error.

#### Scenario: SecretNotFound yields not_configured

- GIVEN `TokenStore.read()` raises `SecretNotFound` because the Secret does not exist yet
- WHEN `_load_cached()` handles the read
- THEN `state` is `not_configured`
- AND this behavior is unchanged from before this change

#### Scenario: Unknown store exception does not reach main.py unhandled

- GIVEN `TokenStore.read()` raises an exception outside the previously-handled set (`SecretNotFound`, `AuthError`)
- WHEN `_load_cached()` or `ensure_fresh()` processes the read
- THEN the exception is classified into a defined `SessionState`, never re-raised past the classifier

### Requirement: Backend-Unreachable Classification

The system MUST classify `kubernetes`/`urllib3` connectivity failures — including `ApiException` (non-definitive HTTP failures from the API server), `MaxRetryError`, and connect timeouts — raised by the live `CoreV1Api.read_namespaced_secret` call as `state == "backend_unreachable"`. `SecretNotFound` MUST NOT be reclassified as `backend_unreachable`, since it is a definitive answer (the secret does not exist), not a connectivity failure.

#### Scenario: ApiException classified as backend_unreachable

- GIVEN `CoreV1Api.read_namespaced_secret` raises a mocked `ApiException` (e.g. 503 from the API server)
- WHEN `/internal/session` is requested
- THEN the response has HTTP 200 with `state == "backend_unreachable"`

#### Scenario: MaxRetryError / connect timeout classified as backend_unreachable

- GIVEN `CoreV1Api.read_namespaced_secret` raises a mocked `MaxRetryError` or a connect-timeout exception
- WHEN `/internal/session` is requested
- THEN the response has HTTP 200 with `state == "backend_unreachable"`

#### Scenario: SecretNotFound is not reclassified

- GIVEN `CoreV1Api.read_namespaced_secret` raises `SecretNotFound`
- WHEN `/internal/session` is requested
- THEN `state` remains `not_configured`, not `backend_unreachable`

### Requirement: Sanitized Reason Contract

Every `SessionState` that carries a `reason` MUST populate it with a short, sanitized, human-readable string. The `reason` MUST NOT contain a raw traceback, MUST NOT contain token material (access tokens, refresh tokens, or any Secret data value), and MUST NOT contain raw Kubernetes API response bodies.

#### Scenario: backend_unreachable reason is sanitized

- GIVEN a connectivity exception is classified as `backend_unreachable`
- WHEN `reason` is inspected
- THEN it contains a short stable description (e.g. a connectivity/timeout summary) and no traceback text, no stack frame paths, and no exception `args` that could embed response bodies

#### Scenario: No Secret contents ever appear in reason

- GIVEN any classified exception
- WHEN `reason` is constructed
- THEN it is built only from a fixed set of sanitized templates/codes, never from interpolating the raw Secret payload or raw token strings

### Requirement: No-Token-Material Guarantee

`/internal/session`'s response body, across all seven states, MUST NOT include access tokens, refresh tokens, or any other Secret value, regardless of which code path produced the response.

#### Scenario: Response body audited across all states

- GIVEN each of the seven `SessionState` values is produced in turn (via mocked store behavior)
- WHEN the `/internal/session` JSON response is inspected
- THEN no field contains token material or raw Secret content in any of the seven cases

### Requirement: D17 Switch-to-Cloud Gating Unaffected

`ALLOWED_SESSION_STATES` (`{"valid", "expiring_soon"}`) MUST continue to exclude `backend_unreachable` and every other non-listed state, and `assert_switch_to_cloud_allowed` MUST fail closed for `backend_unreachable` with zero cluster mutations.

#### Scenario: Switch-to-Cloud rejected while backend_unreachable

- GIVEN the current session `state` is `backend_unreachable`
- WHEN `assert_switch_to_cloud_allowed` is called
- THEN it rejects the switch and performs zero cluster mutations

### Requirement: Response Shape Stability

The `/internal/session` response schema (field names and types) MUST remain unchanged by the addition of `backend_unreachable`; only the set of valid `state` string values grows.

#### Scenario: Existing consumers see no schema change

- GIVEN a client that already parses `/internal/session` for the prior six states
- WHEN the client receives a `backend_unreachable` response
- THEN every existing field it depends on (state, reason, expires_at, etc.) is present with its prior type, and the new state is simply an additional recognized string value
