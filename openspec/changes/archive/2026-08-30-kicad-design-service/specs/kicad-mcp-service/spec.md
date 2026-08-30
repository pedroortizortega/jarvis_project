# KiCad MCP Service Specification

## Purpose

Expose a private KiCad MCP service in the `mcps` namespace that provides schematic, footprint, routing, DRC/ERC, and export tools for the design pipeline.

## Requirements

### Requirement: Private MCP Access

The KiCad MCP service MUST run in the `mcps` namespace and MUST require private-TLS, mTLS or identity-proxy authentication. It MUST NOT be reachable from the public Internet.

#### Scenario: Authenticate a client

- GIVEN an authenticated, identity-verified client
- WHEN it calls a KiCad MCP tool
- THEN the service serves the tool over private TLS

#### Scenario: Reject an unauthenticated client

- GIVEN a client without valid mTLS or identity-proxy credentials
- WHEN it calls a KiCad MCP tool
- THEN the service denies the call

### Requirement: Design Tool Coverage

The KiCad MCP service MUST expose schematic creation, footprint placement, routing, DRC/ERC evaluation, and export tools.

#### Scenario: Build a schematic

- GIVEN a validated component/net extraction
- WHEN the service is asked to create a schematic
- THEN it produces a KiCad schematic

#### Scenario: Route and check a board

- GIVEN a placed PCB
- WHEN routing and DRC/ERC are requested
- THEN the service returns routing results and DRC/ERC status

### Requirement: Versioned, Pinned Server

The service MUST pin a specific KiCad MCP server build and MUST record its license boundary.

#### Scenario: Identify the pinned build

- GIVEN a deployed service
- WHEN an operator inspects the deployment
- THEN the pinned server build and license boundary are recorded
