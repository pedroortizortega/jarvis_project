# KiCad MCP Service Specification

## Purpose

Provide the PCB design stage of the KiCad design pipeline through a KiCad MCP server that runs as a stdio subprocess of Hermes and drives a local KiCad instance over its IPC API.

## Requirements

### Requirement: Local Stdio MCP Access

The KiCad MCP server MUST run as a stdio subprocess of Hermes (launched via `uvx`) and MUST communicate over stdin/stdout. It MUST NOT be deployed to the cluster and MUST NOT be reachable from the public Internet.

#### Scenario: Hermes launches the server

- GIVEN a Hermes session with the `kicad` MCP entry enabled
- WHEN Hermes loads the MCP server
- THEN Hermes launches the KiCad MCP server as a stdio subprocess and speaks MCP over stdin/stdout

#### Scenario: No cluster exposure

- GIVEN the KiCad MCP server
- WHEN the cluster is inspected
- THEN no deployment, service, or endpoint exists for it in the `mcps` namespace

### Requirement: Design Tool Coverage

The KiCad MCP server MUST expose schematic creation, footprint placement, routing, DRC/ERC evaluation, and export tools.

#### Scenario: Build a schematic

- GIVEN a validated component/net extraction
- WHEN the server is asked to create a schematic
- THEN it produces a KiCad schematic

#### Scenario: Route and check a board

- GIVEN a placed PCB
- WHEN routing and DRC/ERC are requested
- THEN the server returns routing results and DRC/ERC status

### Requirement: Local KiCad Instance

The KiCad MCP server MUST run against a local KiCad instance that is running and MUST NOT run headless.

#### Scenario: Connect to the local instance

- GIVEN a running KiCad instance
- WHEN the MCP server is invoked
- THEN it connects to the instance over its IPC API

#### Scenario: Instance provides the endpoint

- GIVEN a running KiCad instance
- WHEN the MCP server is invoked
- THEN the instance's IPC API exposes the tools the server calls
