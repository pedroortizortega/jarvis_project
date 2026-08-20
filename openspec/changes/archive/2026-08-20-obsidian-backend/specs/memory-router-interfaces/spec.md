# Memory Router Interfaces Specification

## Purpose

Define the MCP and REST surfaces Memory Router exposes for Phase 1, their request/response contracts, and error semantics.

## Requirements

### Requirement: Search-Only Backend Contract Is Separate From MemoryBackend

The system MUST expose a narrow, capability-gated `SearchOnlyBackend` Protocol (`capabilities()`, `health()`, `search()`) for backends that support `search` but not `store`, mirroring the existing precedent set by `ReflectiveBackend` for `reflect`-only backends. `SearchOnlyBackend` MUST NOT require or declare a `store()` method.

The `MemoryBackend` Protocol MUST NOT gain a default or no-op behavior to accommodate search-only adapters. The dispatcher MUST reach a backend's `search()` only through registry selection gated on `capabilities().verbs` containing `"search"`, never by structurally assuming every registered backend implements `store()`.

Existing adapter conformance MUST remain unmodified and passing: `isinstance(EngramBackend(), MemoryBackend)` and `isinstance(HindsightBackend(), MemoryBackend)` MUST still hold true.

#### Scenario: SearchOnlyBackend is distinct from MemoryBackend

- GIVEN `SearchOnlyBackend` and `MemoryBackend` are inspected
- WHEN their method sets are compared
- THEN `SearchOnlyBackend` has no `store()` method, and it is a separate Protocol that a search-only adapter (e.g. `KnowledgeVaultBackend`) satisfies

#### Scenario: A search-only adapter is not a MemoryBackend

- GIVEN an adapter implements only `capabilities()`, `health()`, and `search()`
- WHEN `isinstance(adapter, SearchOnlyBackend)` and `isinstance(adapter, MemoryBackend)` are evaluated
- THEN the first is `True` and the second is `False`, because `MemoryBackend` requires `store()`

#### Scenario: Existing MemoryBackend conformance is unaffected

- GIVEN `EngramBackend` and `HindsightBackend` as they exist before this change
- WHEN `isinstance(EngramBackend(), MemoryBackend)` and `isinstance(HindsightBackend(), MemoryBackend)` are evaluated after this change
- THEN both still return `True`, with their existing conformance tests unmodified

#### Scenario: Dispatcher gates search dispatch on declared capability

- GIVEN a registered backend does not declare `"search"` in `capabilities().verbs`
- WHEN `Registry.backends_for(verb="search", namespace=...)` is evaluated
- THEN that backend is never returned and the dispatcher never calls `search()` on it

### Requirement: Dual MCP and REST Surface

The system MUST expose an MCP server and a REST API offering equivalent capabilities: `POST /memory/store`, `POST /memory/search`, `POST /memory/reflect`, `GET /agents/context`, `GET /projects/context`.

#### Scenario: REST store request accepted

- GIVEN an authenticated client sends `POST /memory/store` with `namespace`, `role`, and content
- WHEN the request passes authorization and namespace validation
- THEN the router accepts the request and returns a commit or pending status per backend availability

#### Scenario: MCP and REST parity

- GIVEN the same store or search operation is issued via MCP and via REST with equivalent parameters
- WHEN both requests are authorized identically
- THEN both surfaces produce equivalent routing decisions and equivalent response semantics

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

### Requirement: Explicit Error Semantics

The system MUST distinguish authorization failures, invalid-namespace errors, invalid-role errors, and degraded-backend conditions with distinct, explicit response codes/payloads. It MUST NOT collapse these into a generic failure.

#### Scenario: Authorization failure returns 403

- GIVEN a client's declared role is not permitted to act on the declared namespace/verb
- WHEN the request is evaluated
- THEN the router returns `403` with a reason identifying the denied role/namespace/verb combination

#### Scenario: Invalid role returns explicit rejection

- GIVEN a client declares a role not present in its permitted role set
- WHEN the request is evaluated
- THEN the router rejects the request with an explicit "role not permitted for this client identity" error, distinct from a namespace authorization failure

### Requirement: Router Is the Default Entry Point Once Healthy

Once Memory Router is deployed and reports healthy, it MUST be the default path for all new and normal memory operations by onboarded clients. Direct Engram MCP-stdio access MUST remain reachable as a rollback path but MUST NOT be the documented steady-state integration path once the router is active.

#### Scenario: Router used as default path

- GIVEN Memory Router is deployed and its health check passes
- WHEN an onboarded client integrates with or resumes normal memory operations
- THEN the client's traffic is routed through Memory Router, not directly to Engram

#### Scenario: Direct backend access remains available for rollback

- GIVEN Memory Router becomes unavailable or is intentionally rolled back
- WHEN a client falls back to direct Engram MCP-stdio access
- THEN the fallback path functions without requiring Memory Router or its removal
