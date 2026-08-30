# Proposal: KiCad Design Service

## Intent

Provide a private, Kubernetes-hosted KiCad design service in the `mcps` namespace that converts the user's electronic design sketches (images) into KiCad plans following best design practices — from circuits/schematics through to 3D PCB models — exposed as a hardened MCP service following the `shared-mcp-services` conventions.

## Scope

### In Scope
- Own a new KiCad design service in the `mcps` namespace; ingest electronic design sketches/images.
- Run a pipeline: image → component/net extraction → KiCad schematic → PCB layout with footprints/routing/DRC/ERC → 3D model.
- Expose the pipeline as a hardened MCP service following the shared-mcp-services conventions (private TLS, mTLS or identity-proxy auth, least privilege, non-root/read-only hardening, no default Kubernetes API credentials/RBAC, CNI-validated isolation).
- Span two MCP servers: a KiCad MCP (schematic + PCB) as primary and a FreeCAD MCP (3D CAD) as the 3D-model stage.
- Record the leading MCP decision(s) for the KiCad and FreeCAD servers.

### Out of Scope
- Public Internet exposure.
- Changing Brave MCP behavior.
- Changing `shared-mcp-services` artifacts.
- Implementing `kubernetes/` manifests in this phase (that is the apply phase).

## Capabilities

### New Capabilities
- `kicad-design-pipeline`: End-to-end conversion of electronic design sketches/images into KiCad schematics, PCB layouts, and 3D PCB models.
- `kicad-mcp-service`: Private KiCad MCP service exposing schematic, footprint, routing, DRC/ERC, and export tools in the `mcps` namespace.
- `freecad-mcp-service`: Private FreeCAD MCP service exposing 3D CAD model generation for the PCB stage.
- `sketch-ingestion`: Image ingestion with component/net extraction feeding the KiCad pipeline.

### Modified Capabilities
None — no existing OpenSpec capability specifications exist for this namespace beyond `shared-mcp-services`.

## Approach

Use the leading MCP candidates as the service backends: `mixelpixx/KiCAD-MCP-Server` (169 tools: schematics, footprints, routing, DRC/ERC, export, FreeRouting autorouter, JLCPCB; MIT) as the primary KiCad MCP, and `freecad-robust-mcp` (150+ tools, XML-RPC/socket/embedded, Docker) as the FreeCAD MCP for the 3D-model stage. The service ingests design sketches, runs the image → component/net extraction → KiCad schematic → PCB layout → 3D model pipeline, and exposes the result as a hardened MCP service. Gate rollout on CNI and Hermes validation.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `kubernetes/mcps/` | Modified | New `mcps`-namespaced KiCad design service; Brave unchanged. |
| `kubernetes/policy/` | Modified | CNI-validated isolation policy for the new service. |
| `openspec/changes/kicad-design-service/` | Created | SDD artifacts for this change. |
| `openspec/changes/shared-mcp-services/` | Reference | Conventions anchor; not modified. |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| CNI bypasses assumed isolation | High | Block rollout until cluster tests pass. |
| KiCad MCP licensing (successor "Konnect" is AGPL-3.0) | Med | Pin the MIT-licensed `mixelpixx/KiCAD-MCP-Server`; document the licensing boundary. |
| FreeCAD MCP runtime state / Docker dependency | Med | Use `freecad-robust-mcp` embedded mode; validate headless operation. |
| Sketch extraction fidelity | High | Gate on component/net extraction quality before claiming PCB output. |

## Rollback Plan

Disable onboarding, route traffic away, and remove only the new `mcps` resources for the KiCad design service. Leave Brave, `shared-mcp-services`, and local CodeGraph unchanged.

## Dependencies

- Private TLS/certificates and mTLS or identity-proxy management.
- CNI host-network/NetworkPolicy and Hermes connectivity validation.
- KiCad MCP server (`mixelpixx/KiCAD-MCP-Server`) and FreeCAD MCP server (`freecad-robust-mcp`) availability.
- Strict TDD command: `python -m unittest discover -s tests`.

## Success Criteria

- [ ] KiCad design service permits only mTLS- or identity-proxy-authenticated, private-TLS access in the `mcps` namespace.
- [ ] Pipeline converts a design sketch image into a KiCad schematic, PCB layout (footprints/routing/DRC/ERC), and 3D model.
- [ ] KiCad MCP (`mixelpixx/KiCAD-MCP-Server`) and FreeCAD MCP (`freecad-robust-mcp`) are pinned and versioned.
- [ ] CNI behavior is evidenced before any isolation claim; Brave behavior is unchanged.
