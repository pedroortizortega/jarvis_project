# Memory Router Interfaces Specification

## Purpose

Define the MCP and REST surfaces Memory Router exposes for Phase 1, their request/response contracts, and error semantics.

## Requirements

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

### Requirement: Reflect Endpoint Returns Not Implemented

`POST /memory/reflect` MUST return `501 Not Implemented` (or an MCP-equivalent explicit "not yet implemented" error) in Phase 1. The router MUST NOT attempt any summarization or write-back against Engram or any backend for this endpoint.

#### Scenario: Reflect call is rejected as unimplemented

- GIVEN an authenticated, authorized client calls `POST /memory/reflect`
- WHEN the request is processed
- THEN the router returns `501 Not Implemented` and performs no read, write, or summarization against any backend

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
