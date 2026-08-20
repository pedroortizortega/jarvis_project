# Delta for Memory Access Control

## ADDED Requirements

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
