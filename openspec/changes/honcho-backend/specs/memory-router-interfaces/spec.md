# Delta for Memory Router Interfaces

## MODIFIED Requirements

### Requirement: Reflect Endpoint Is Routed Through the Standard Pipeline

`POST /memory/reflect` MUST run the same `identity -> namespace -> permission -> registry` pipeline as `store`/`search`/`context`. The router MUST NOT unconditionally raise `501 Not Implemented`, MUST NOT contain any reference to `"phase": "hindsight"` in its error payloads, and MUST NOT contain the comment or claim that reflect "lands with Hindsight" anywhere in `app.py`.

When authentication, namespace validation, or permission checks fail, the router MUST respond with the same distinct, explicit error codes used by `store`/`search`/`context` (`401`, `400`, `403`) — never `501` for those cases.

When the pipeline succeeds but no reflect-capable backend is registered for the requested namespace, the router MUST return an explicit empty or pending `ReflectResult` payload. It MUST NOT return a generic failure and MUST NOT fabricate a successful conclusion.

(Previously: unconditionally raised `501 Not Implemented` after authentication only, with a stale "lands with Hindsight" comment and a `"phase": "hindsight"` error hint.)

#### Scenario: Authorized reflect call is routed and dispatched

- GIVEN an authenticated client whose declared role is authorized for `reflect` on `/user/master`
- WHEN it calls `POST /memory/reflect` with `namespace="/user/master"`
- THEN the router resolves identity, validates the namespace, authorizes the `reflect` verb, selects reflect-capable backends via the registry, and returns a `ReflectResult` payload — never `501`

#### Scenario: Unauthorized role reflecting is denied, not "not implemented"

- GIVEN a client's declared role is not permitted `reflect` on `/user/master` (e.g. `coder`)
- WHEN it calls `POST /memory/reflect` targeting `/user/master`
- THEN the router returns `403 authorization_denied`, never `501`

#### Scenario: Reflect with no reflect-capable backend registered

- GIVEN the pipeline authorizes the request but `Registry.backends_for(verb="reflect", namespace=...)` returns no backend
- WHEN the router processes the reflect request
- THEN it returns an explicit empty or pending `ReflectResult`, never a generic failure and never a fabricated successful conclusion

#### Scenario: No stale Hindsight references remain

- GIVEN `app.py` and its error payload builder are inspected
- WHEN searching for `"phase": "hindsight"` or `"lands with Hindsight"`
- THEN neither string occurs anywhere in the file
