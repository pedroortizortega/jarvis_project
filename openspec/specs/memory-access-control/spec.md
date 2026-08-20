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

### Requirement: Per-Role Reflect Authorization on `/user/master`

The router MUST authorize the `reflect` verb on the `user_master` namespace kind per role, per this explicit table:

| Role | `reflect` on `user_master` |
|------|------------------------------|
| `jarvis` | allow |
| `scientist` | allow |
| `coder` | deny |

Deny-by-default MUST continue to apply: `reflect` MUST remain denied for every role on every other namespace kind (`global`, `projects`, `agents_self`, `agents_other`), since no reflect-capable backend exists for those namespace kinds in this change.

#### Scenario: jarvis reflects on /user/master

- GIVEN client identity `hermes-gateway` declares role `jarvis`
- WHEN it requests `reflect` on namespace `/user/master`
- THEN the router authorizes the request

#### Scenario: scientist reflects on /user/master

- GIVEN client identity `opencode` declares role `scientist`
- WHEN it requests `reflect` on namespace `/user/master`
- THEN the router authorizes the request

#### Scenario: coder is denied reflect on /user/master

- GIVEN a client declares role `coder`
- WHEN it requests `reflect` on namespace `/user/master`
- THEN the router denies the request with `403`, since `coder` has no allow rule for `reflect` on `user_master`

#### Scenario: reflect denied on namespace kinds other than user_master

- GIVEN any role, including `jarvis`
- WHEN it requests `reflect` on a namespace of kind `global`, `projects`, `agents_self`, or `agents_other`
- THEN the router denies the request with `403`, since no allow rule exists for `reflect` outside `user_master`

### Requirement: Per-Role Reflect Authorization on `projects`

The router MUST authorize the `reflect` verb on the `projects` namespace kind per role, per this explicit table:

| Role | `reflect` on `projects` |
|------|--------------------------|
| `jarvis` | allow |
| `scientist` | allow |
| `coder` | deny |

This table is additive to the existing `projects` allow rules (`store`, `search` remain unchanged for all three roles). Deny-by-default MUST continue to apply to every other namespace kind not covered by an explicit reflect allow rule (`global`, `agents_self`, `agents_other`); those are unaffected by this change and remain governed by the existing `Per-Role Namespace and Verb Authorization, Deny by Default` requirement.

#### Scenario: jarvis reflects on /projects/x

- GIVEN client identity `hermes-gateway` declares role `jarvis`
- WHEN it requests `reflect` on namespace `/projects/x`
- THEN the router authorizes the request

#### Scenario: scientist reflects on /projects/x

- GIVEN client identity `opencode` declares role `scientist`
- WHEN it requests `reflect` on namespace `/projects/x`
- THEN the router authorizes the request

#### Scenario: coder is denied reflect on /projects/x

- GIVEN a client declares role `coder`
- WHEN it requests `reflect` on namespace `/projects/x`
- THEN the router denies the request with `403`, since `coder` has no allow rule for `reflect` on `projects`

#### Scenario: store and search on projects remain unaffected

- GIVEN role `coder` retains its existing allow rule for `store` and `search` on `projects`
- WHEN a request declaring role `coder` targets a `/projects/*` namespace for `store` or `search`
- THEN the router authorizes the request exactly as before this change

### Requirement: Per-Role Reflect Authorization on `global` and `agents_self`

The router MUST authorize the `reflect` verb on the `global` and `agents_self` namespace kinds per role, per this explicit table (identical grant shape for both kinds):

| Role | `reflect` on `global` | `reflect` on `agents_self` |
|------|------------------------|------------------------------|
| `jarvis` | allow | allow |
| `scientist` | allow | allow |
| `coder` | deny | deny |

This table is additive to existing allow rules for these namespace kinds. Deny-by-default MUST continue to apply to every namespace kind not covered by an explicit reflect allow rule.

#### Scenario: jarvis reflects on /global

- GIVEN client identity `hermes-gateway` declares role `jarvis`
- WHEN it requests `reflect` on namespace `/global`
- THEN the router authorizes the request

#### Scenario: scientist reflects on their own agent namespace

- GIVEN client identity `opencode` declares role `scientist`
- WHEN it requests `reflect` on namespace `/agents/scientist` (its own agent namespace)
- THEN the router authorizes the request

#### Scenario: coder is denied reflect on /global and agents_self

- GIVEN a client declares role `coder`
- WHEN it requests `reflect` on namespace `/global` or on its own agent namespace under `/agents/*`
- THEN the router denies the request with `403`, since `coder` has no allow rule for `reflect` on `global` or `agents_self`

### Requirement: Per-Role Reflect Authorization on `agents_other`

The router MUST authorize the `reflect` verb on the `agents_other` namespace kind (one agent reflecting on another agent's namespace) only for `jarvis`, per this explicit table:

| Role | `reflect` on `agents_other` |
|------|-------------------------------|
| `jarvis` | allow |
| `scientist` | deny |
| `coder` | deny |

A namespace under `/agents/*` that does not resolve to a nested path (e.g. `/agents/a/b`) resolves to the parent agent's namespace kind for authorization purposes.

#### Scenario: jarvis reflects on another agent's namespace

- GIVEN client identity `hermes-gateway` declares role `jarvis`
- WHEN it requests `reflect` on `/agents/other-agent`, a namespace that is not its own
- THEN the router authorizes the request

#### Scenario: scientist is denied reflect on another agent's namespace

- GIVEN client identity `opencode` declares role `scientist`
- WHEN it requests `reflect` on `/agents/other-agent`, a namespace that is not its own
- THEN the router denies the request with `403`, since `scientist` has no allow rule for `reflect` on `agents_other`

#### Scenario: coder is denied reflect on another agent's namespace

- GIVEN a client declares role `coder`
- WHEN it requests `reflect` on any `/agents/*` namespace, including its own
- THEN the router denies the request with `403`
