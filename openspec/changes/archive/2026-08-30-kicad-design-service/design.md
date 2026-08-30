# Design: KiCad Design Service

## Technical Approach

Add two new, isolated MCP workloads in `mcps` — a primary KiCad MCP service and a FreeCAD MCP service — without touching `kubernetes/mcps/brave-search-mcp-deployment.yaml` or the shared-mcp-services artifacts. A sketch image is ingested, components and nets are extracted with confidence, and the pipeline commits a KiCad plan only when DRC/ERC pass: schematic, routed PCB layout, and a 3D PCB model. Both services run private-TLS, mTLS or identity-proxy authenticated, least-privilege, non-root with read-only root filesystem and dropped capabilities. Onboarding is blocked until CNI NetworkPolicy evidence and Hermes `hostNetwork` connectivity evidence exist.

## Architecture Decisions

| Decision | Options / tradeoff | Choice and rationale |
|---|---|---|
| Primary KiCad MCP server | `mixelpixx/KiCAD-MCP-Server` (169 tools: schematics, footprints, routing, DRC/ERC, export, FreeRouting autorouter, JLCPCB; MIT) vs `lamaalrajih/kicad-mcp` (uv, KiCad >= 9.0, lightweight) | Pin `mixelpixx/KiCAD-MCP-Server` for tool coverage and MIT license; the AGPL-3.0 successor "Konnect" is explicitly out of scope. |
| 3D CAD MCP server | `freecad-robust-mcp` (150+ tools, XML-RPC/socket/embedded, Docker) vs `mcpfreecad` (PyPI, stdio/HTTP + API key) vs `freecad-mcp` (headless + PNG) vs `FreeCAD-MCP-Server` (theosib, runtime state) | Pin `freecad-robust-mcp` headless for the 3D-model stage; it is the only candidate that runs without a display server and exposes the full CAD tool surface. |
| Pipeline commit gate | Auto-commit vs gate on DRC/ERC | Gate: a plan is committed only when DRC/ERC pass; violations are reported and the plan is not marked clean. |
| Sketch confidence | Auto-accept vs confidence threshold | Confidence-gated: extraction records confidence; low-confidence extractions do not auto-commit and are flagged for review. |
| Service separation | One combined workload vs two pinned workloads | Two workloads with distinct pinned builds and identities: KiCad is the design authority; FreeCAD is the 3D-model stage only. |
| Kubernetes privileges | Default token/RBAC eases discovery but expands blast radius | Set `automountServiceAccountToken: false`, omit RBAC resources, run non-root with read-only root filesystem and dropped capabilities. |
| Ingestion boundary | Inline vs stage | A discrete ingestion stage validates the sketch and emits components/nets with confidence; it hands off to the pipeline and does not commit the plan itself. |

## Data Flow

```text
sketch image
  -> ingestion (validate + extract components/nets + confidence)
  -> low confidence? flag, do not auto-commit
  -> KiCad MCP: schematic -> footprints -> routing -> DRC/ERC
  -> DRC/ERC fail? report, do not claim clean
  -> FreeCAD MCP: routed layout -> 3D PCB model
  -> private TLS gateway (mTLS or identity proxy + scope authorization)
  -> scoped client
```

Unreadable sketch, low-confidence extraction, DRC/ERC violation, unauthorized identity, or unroutable layout fails closed. No plan is committed unless every gate passes.

## File Changes

| File | Action | Description |
|---|---|---|
| `kubernetes/mcps/namespace.yaml` | Reuse | `mcps` is already declared by shared-mcp-services; no edit. |
| `kubernetes/mcps/kicad-mcp-deployment.yaml` | Create | Hardened KiCad MCP Deployment: pinned `mixelpixx/KiCAD-MCP-Server` build, non-root, read-only root, dropped capabilities, `automountServiceAccountToken: false`. |
| `kubernetes/mcps/kicad-mcp-service.yaml` | Create | ClusterIP-only service; no public exposure. |
| `kubernetes/mcps/freecad-mcp-deployment.yaml` | Create | Hardened FreeCAD MCP Deployment: pinned `freecad-robust-mcp` headless build, same hardening. |
| `kubernetes/mcps/freecad-mcp-service.yaml` | Create | ClusterIP-only service. |
| `kubernetes/mcps/sketch-ingest-config.yaml` | Create | ConfigMap for extraction confidence threshold and pipeline handoff. |
| `kubernetes/policy/mcps-networkpolicy.yaml` | Reuse | Default-deny plus explicit ingress/egress; contingent on CNI evidence. |
| `tests/test_kicad_design_contracts.py` | Create | RED contract tests under root `tests/` for the configured command. |
| `kubernetes/mcps/brave-search-mcp-deployment.yaml` | No change | Preserved exactly. |

## Interfaces / Contracts

```yaml
sketch-extraction:
  image_ref: allow-listed sketch identifier
  components: extracted component list
  nets: extracted net list
  confidence: float in [0, 1]
  auto_commit: false   # low confidence never auto-commits
kicad-plan:
  schematic: produced when DRC/ERC pass
  pcb_layout: routed layout with footprints
  drc_status: pass | violations
  erc_status: pass | violations
  clean_claim: only when DRC/ERC pass
freecad-model:
  source: routed pcb layout
  headless: true
  model: 3d cad model of the board
```

The gateway receives an authenticated principal and a requested scope; it forwards only when the authorization mapping contains that pair. Both MCP workloads are read-only to the cluster API and serve only their pinned tool surface.

## Testing Strategy

| Layer | What to Test | Approach |
|---|---|---|
| Unit | Ingestion confidence gating, DRC/ERC commit gate, extraction handoff, denied unauthorized calls | RED tests first; run `python -m unittest discover -s tests`. |
| Integration | Private TLS identity path, KiCad MCP revision mount, FreeCAD headless run | Ephemeral cluster/CI environment; no rollout. |
| E2E | CNI-specific NetworkPolicy and `hostNetwork` Hermes connectivity | Evidence runbook against target CNI; rollout blocked on passing evidence. |

## Threat Matrix

| Boundary | Applicability | Design response | Planned RED tests |
|---|---|---|---|
| Sketch image input | Applicable — ingestion executes on untrusted image bytes | Validate the sketch before extraction; reject empty or unreadable images; fail closed | Valid sketch; empty image; unreadable image |
| Component/net extraction | Applicable — extraction feeds the plan | Record confidence; low confidence never auto-commits | High-confidence extraction; low-confidence flag |
| DRC/ERC gate | Applicable — commit gate for the plan | A plan is committed only when DRC/ERC pass; violations are reported | Clean layout; DRC violation; ERC violation |
| MCP tool invocation | Applicable — both workloads expose tools | Require private TLS plus mTLS or identity-proxy identity; deny unauthorized calls | Authenticated call; unauthenticated call; cross-scope call |
| FreeCAD headless run | Applicable — 3D stage runs without a display | Run headless; reject an unroutable layout before 3D generation | Routed layout; unroutable layout |
| Git repository selection | N/A — no CI publication in this change | No repository selection boundary | None |
| Push state | N/A — no Git push | No destination/ref resolution | None |
| PR commands | N/A — no PR automation | No command composition | None |

## Migration / Rollout

No data migration required. First establish private certificates/identity mapping and CNI evidence for NetworkPolicy plus Hermes `hostNetwork`. Deploy without onboarding, verify isolation and the commit gate, then onboard one sketch at a time. Roll back by disabling onboarding and removing only the new `kicad-mcp-*` and `freecad-mcp-*` resources; leave Brave and shared-mcp-services unchanged.

## Open Questions

- [ ] Which target CNI and identity-provider/mTLS issuer will provide the required rollout evidence?
- [ ] Where will extracted sketches and confidence metadata be stored between runs?

## Unresolved Deployment Inputs

- [ ] Target CNI and Hermes `hostNetwork` behavior: validate the CNI's NetworkPolicy enforcement and the Hermes path before claiming isolation or enabling onboarding. No CNI or policy substitute is selected here.
- [ ] Identity issuer or proxy: select the private-TLS mTLS issuer or identity proxy that binds principals to pipeline scopes. No shared token or substitute identity provider is selected here.
- [ ] Sketch retention: select the store and retention policy for extracted sketches and confidence metadata. No storage backend or retention substitute is selected here.

## PR 1 Contract Guards

- Ingestion accepts only allow-listed sketch identifiers and rejects empty or unreadable images before extraction.
- Extraction records confidence; low-confidence extractions never auto-commit and are flagged for review.
- A KiCad plan is committed only when DRC/ERC pass; violations are reported and the plan is not marked clean.
- Both MCP workloads require private TLS with scope-authenticated identity; missing or unauthorized identity is denied and shared application tokens are forbidden.
- Both workloads set `automountServiceAccountToken: false`, run with a non-root, read-only root filesystem, and have all Linux capabilities dropped.
- The FreeCAD workload runs headless and rejects an unroutable layout before 3D generation.
- Onboarding remains blocked without CNI NetworkPolicy evidence and Hermes `hostNetwork` connectivity evidence; until then, isolation is not claimed.
