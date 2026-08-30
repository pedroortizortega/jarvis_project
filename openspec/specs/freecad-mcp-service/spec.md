# FreeCAD MCP Service Specification

## Purpose

Provide the 3D CAD model stage of the KiCad design pipeline through a FreeCAD MCP server that runs as a stdio subprocess of Hermes and drives a local FreeCAD GUI instance via the FreeCADMCP addon (XML-RPC on `localhost:9875`).

## Requirements

### Requirement: Local Stdio MCP Access

The FreeCAD MCP server MUST run as a stdio subprocess of Hermes (launched via `uvx freecad-mcp`) and MUST communicate over stdin/stdout. It MUST NOT be deployed to the cluster and MUST NOT be reachable from the public Internet.

#### Scenario: Hermes launches the server

- GIVEN a Hermes session with the `freecad` MCP entry enabled
- WHEN Hermes loads the MCP server
- THEN Hermes launches `uvx freecad-mcp` as a stdio subprocess and speaks MCP over stdin/stdout

#### Scenario: No cluster exposure

- GIVEN the FreeCAD MCP server
- WHEN the cluster is inspected
- THEN no deployment, service, or endpoint exists for it in the `mcps` namespace

### Requirement: 3D Model Generation

The FreeCAD MCP server MUST generate a 3D model from a routed PCB layout.

#### Scenario: Generate a 3D model

- GIVEN a routed PCB layout
- WHEN 3D model generation is requested
- THEN the server returns a 3D CAD model of the board

#### Scenario: Reject an unroutable layout

- GIVEN a layout without a valid board geometry
- WHEN 3D model generation is requested
- THEN the server reports the layout as unusable for 3D generation

### Requirement: Local GUI with Addon

The FreeCAD MCP server MUST run against a local FreeCAD GUI instance with the FreeCADMCP addon installed and MUST NOT run headless.

#### Scenario: Connect to the local instance

- GIVEN a running FreeCAD GUI with the FreeCADMCP addon
- WHEN the MCP server is invoked
- THEN it connects to the instance's XML-RPC endpoint on `localhost:9875`

#### Scenario: Addon provides the endpoint

- GIVEN a FreeCAD GUI with the FreeCADMCP addon installed
- WHEN the instance starts
- THEN the addon starts its XML-RPC server and exposes the endpoint to the MCP server
