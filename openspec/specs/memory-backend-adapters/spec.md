# Memory Backend Adapters Specification

## Purpose

Define the backend adapter contract and Phase 1's single Engram adapter, including degraded-backend behavior.

## Requirements

### Requirement: Adapter Contract

Each backend adapter MUST declare its capabilities (e.g. supports store, supports search, supports health-check) and MUST implement a store interface, a search interface, and a health interface. The router MUST select backends per request only from adapters that declare the required capability.

#### Scenario: Router selects only capable adapters

- GIVEN a search request targets a namespace backed by two adapters, one of which does not declare search capability
- WHEN the router dispatches the search
- THEN only the adapter declaring search capability is queried

### Requirement: Phase 1 Engram Adapter

The system MUST ship exactly one adapter implementation in Phase 1: an Engram adapter using Engram's existing supported access path (MCP-stdio-equivalent), not an assumed HTTP-MCP transport.

#### Scenario: Engram adapter handles store and search

- GIVEN a request is routed to the Engram adapter
- WHEN the adapter performs store or search
- THEN it communicates with Engram via its existing supported access path and returns results/status through the adapter contract

### Requirement: Degraded Backend — Store Queues Instead of Dropping

When the target backend for a `store` request is unavailable, the router MUST queue/buffer the write and respond with an explicit "pending" status. The router MUST NOT respond with a committed-success status, MUST NOT respond with a generic failure status, and MUST NOT drop the write.

#### Scenario: Store queued when backend is down

- GIVEN the Engram backend is unavailable
- WHEN a client issues `POST /memory/store` with a validly declared, permitted namespace
- THEN the router queues the write and responds with an explicit "pending" status, not `200` committed and not a `5xx` failure

### Requirement: Degraded Backend — Search Returns Partial Results

When one or more backends required for a `search` are unavailable, the router MUST return available results from healthy backends plus an explicit per-backend "unavailable" marker. The router MUST NOT fail the entire search solely because one backend is down.

#### Scenario: Partial search results with unavailable marker

- GIVEN a search spans a namespace whose backend is unavailable while another in-scope namespace's backend is healthy
- WHEN the router processes the search
- THEN it returns results from the healthy backend along with an explicit marker identifying the unavailable backend, and the request does not fail outright
