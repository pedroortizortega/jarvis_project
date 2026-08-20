# Delta for Memory Access Control

## ADDED Requirements

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
