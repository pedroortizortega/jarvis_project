# FreeCAD MCP Service Specification

## Purpose

Expose a private FreeCAD MCP service in the `mcps` namespace that generates the 3D CAD model stage of the KiCad design pipeline.

## Requirements

### Requirement: Private MCP Access

The FreeCAD MCP service MUST run in the `mcps` namespace and MUST require private-TLS, mTLS or identity-proxy authentication. It MUST NOT be reachable from the public Internet.

#### Scenario: Authenticate a client

- GIVEN an authenticated, identity-verified client
- WHEN it calls a FreeCAD MCP tool
- THEN the service serves the tool over private TLS

#### Scenario: Reject an unauthenticated client

- GIVEN a client without valid credentials
- WHEN it calls a FreeCAD MCP tool
- THEN the service denies the call

### Requirement: 3D Model Generation

The FreeCAD MCP service MUST generate a 3D model from a routed PCB layout.

#### Scenario: Generate a 3D model

- GIVEN a routed PCB layout
- WHEN 3D model generation is requested
- THEN the service returns a 3D CAD model of the board

#### Scenario: Reject an unroutable layout

- GIVEN a layout without a valid board geometry
- WHEN 3D model generation is requested
- THEN the service reports the layout as unusable for 3D generation

### Requirement: Versioned, Pinned Server

The service MUST pin a specific FreeCAD MCP server build and MUST run headless without a display server.

#### Scenario: Run headless

- GIVEN a deployed service
- WHEN it generates a model
- THEN it operates without a display server
