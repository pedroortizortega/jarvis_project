# Delta for Memory Access Control

## ADDED Requirements

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
