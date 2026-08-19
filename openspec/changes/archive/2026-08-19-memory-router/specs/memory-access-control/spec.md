# Memory Access Control Specification

## Purpose

Define the Phase 1 role model, client-identity-to-role mapping, and per-role namespace+verb authorization enforced by the router.

## Requirements

### Requirement: Fixed Phase 1 Role Set

The system MUST support exactly three roles in Phase 1: `coder`, `scientist`, `jarvis`. No other role MUST be recognized.

#### Scenario: Unknown role is rejected

- GIVEN a request declares a role that is not `coder`, `scientist`, or `jarvis`
- WHEN the router evaluates the request
- THEN the router rejects it with an invalid-role error

### Requirement: Router-Side Client-Identity-to-Role Mapping

The mapping from an authenticated client identity (e.g. `pedro-claude-code`, `codex`, `opencode`, `hermes-gateway`) to its permitted role(s) MUST be configured server-side in the router. A caller MUST NOT self-assert or self-declare its permitted roles; the caller only declares which role it is acting as for the current request, and the router validates that role against its configured permitted set for that identity.

#### Scenario: Client asserts a role outside its permitted set

- GIVEN client identity `codex` is configured to permit only the `coder` role
- WHEN `codex` sends a request declaring role `jarvis`
- THEN the router rejects the request because `jarvis` is not among `codex`'s permitted roles, regardless of what the caller asserts

#### Scenario: Client acting within its permitted role succeeds

- GIVEN client identity `codex` is configured to permit the `coder` role
- WHEN `codex` sends a request declaring role `coder` for a namespace+verb permitted to `coder`
- THEN the router authorizes the request

### Requirement: Per-Role Namespace and Verb Authorization, Deny by Default

The router MUST authorize each request against a per-role namespace+verb rule set and MUST deny any request that does not match an explicit allow rule. Example Phase 1 rule set: `coder` may read `project/*` and write `coding/*` but is denied `admin/*`; `scientist` may read and write `scientific/*`; `jarvis` may read and write `*`.

#### Scenario: Role denied a namespace/verb combination

- GIVEN role `coder` has no allow rule for `admin/*`
- WHEN a request declaring role `coder` targets namespace `admin/settings` for any verb
- THEN the router denies the request with `403`, since deny-by-default applies to any namespace/verb not explicitly allowed

#### Scenario: Role permitted its declared scope

- GIVEN role `scientist` has an allow rule for read and write on `scientific/*`
- WHEN a request declaring role `scientist` targets namespace `scientific/experiment-1` for a store or search verb
- THEN the router authorizes the request
