# Design: Shared MCP Services

## Technical Approach

Create new, isolated resources in `mcps` without changing `kubernetes/mcps/brave-search-mcp-deployment.yaml`. Per-repository CI produces a sanitized Graphify artifact, validates it, and atomically promotes an immutable approved revision. A production Graphify service and an explicitly experimental CodeGraph snapshot adapter serve only pinned, read-only repository revisions over private TLS, authenticated by mTLS or an identity proxy. This implements all three delta specs and blocks onboarding until CNI evidence exists.

## Architecture Decisions

| Decision | Options / tradeoff | Choice and rationale |
|---|---|---|
| Revision publication | Mutable shared graph risks partial reads and cross-repository leakage; immutable artifacts need retention | CI writes `repos/<repo-id>/<version>/`, verifies sanitation/validation, then atomically switches the approved pointer. Readers resolve only that pointer; failed publication leaves the prior approved revision intact. |
| Service separation | One service simplifies deployment but mixes production and experimental support | Separate Graphify and CodeGraph workloads with distinct artifact mounts and identities. Graphify is production; CodeGraph is labelled experimental and supports only the custom snapshot contract. |
| CodeGraph storage | Sharing `.codegraph` SQLite/WAL conflicts with isolation and immutability | CI exports a repository-scoped immutable snapshot; the adapter has no write route, no SQLite/WAL mount, and no claim of native CodeGraph HTTP/container support. |
| Client identity | Shared tokens are simple but non-attributable | Terminate private TLS at an mTLS gateway or identity proxy; map authenticated identity to an allow-listed repository scope. No shared application token. |
| Kubernetes privileges | Default token/RBAC eases discovery but expands blast radius | Set `automountServiceAccountToken: false`, omit RBAC resources, run non-root with read-only root filesystem and dropped capabilities. |

## Data Flow

```text
repository CI checkout
  -> sanitize + validate + version immutable artifact
  -> atomic approved pointer (per repo)
  -> read-only Graphify / experimental snapshot adapter
  -> private TLS gateway (mTLS or identity proxy + repo authorization)
  -> scoped client
```

Publication failure, unsafe input, unknown repository, unauthorized identity, write request, or unsupported CodeGraph operation fails closed. The previous approved Graphify revision remains readable; local CodeGraph remains unchanged.

## File Changes

| File | Action | Description |
|---|---|---|
| `kubernetes/mcps/namespace.yaml` | Create | Declare `mcps`; every new resource explicitly sets `metadata.namespace: mcps`. |
| `kubernetes/mcps/graphify-*.yaml` | Create | Future hardened Graphify service, artifact reader, private TLS/auth integration; no Brave edits. |
| `kubernetes/mcps/codegraph-adapter-*.yaml` | Create | Future experimental immutable-snapshot adapter and explicit experimental metadata. |
| `kubernetes/policy/mcps-networkpolicy.yaml` | Create | Default-deny and explicit ingress/egress policy, contingent on CNI evidence. |
| `tests/test_shared_mcp_contracts.py` | Create | RED contract tests, placed under root `tests/` for the configured command. |
| `kubernetes/mcps/brave-search-mcp-deployment.yaml` | No change | Preserve its stdio transport and `/metrics` probes exactly. |

## Interfaces / Contracts

```yaml
approved-revision:
  repo_id: allow-listed stable identifier
  version: immutable CI version
  artifact_digest: verified content digest
  status: approved
  read_only: true
```

The gateway receives an authenticated principal and requested `repo_id`; it forwards only when the authorization mapping contains that pair. Adapter methods are query-only and return `unsupported` for operations outside the snapshot contract.

## Testing Strategy

| Layer | What to Test | Approach |
|---|---|---|
| Unit | Sanitization, validation, version/pointer atomicity, previous-revision fallback, scope authorization, denied mutations | RED tests first; run `python -m unittest discover -s tests`. |
| Integration | Private TLS identity path, Graphify revision mount, adapter snapshot isolation | Ephemeral cluster/CI environment; no rollout. |
| E2E | CNI-specific NetworkPolicy and `hostNetwork` Hermes connectivity | Evidence runbook against target CNI; rollout blocked on passing evidence. |

## Threat Matrix

| Boundary | Applicability | Design response | Planned RED tests |
|---|---|---|---|
| Documentation-like paths | N/A — CI treats repository files as data and executes none | No execution boundary | None |
| Git repository selection | Applicable — CI derives artifacts from repositories | Accept only CI-provided allow-listed repository ID and immutable checkout; reject relative/absolute overrides, fail closed, retain approved revision | Allowed ID; relative path; absolute path rejection |
| Commit state | N/A — CI reads a supplied checkout and creates no commits | No index/worktree mutation | None |
| Push state | N/A — publication is artifact storage, not Git push | No destination/ref resolution | None |
| PR commands | N/A — no PR automation | No command composition | None |

## Migration / Rollout

No data migration required. First establish private certificates/identity mapping and CNI evidence for NetworkPolicy plus Hermes `hostNetwork`. Deploy without onboarding, verify isolation and fallback, then onboard one repository at a time. Roll back by disabling onboarding and restoring its previous approved pointer; remove only new resources.

## Open Questions

- [ ] Which target CNI and identity-provider/mTLS issuer will provide the required rollout evidence?
- [ ] Where will approved immutable artifacts and retention metadata be stored?

## Unresolved Deployment Inputs

- [ ] Target CNI and Hermes `hostNetwork` behavior: validate the CNI's
  NetworkPolicy enforcement and the Hermes path before claiming isolation or
  enabling onboarding. No CNI or policy substitute is selected here.
- [ ] Identity issuer or proxy: select the private-TLS mTLS issuer or identity
  proxy that binds principals to repository scopes. No shared token or
  substitute identity provider is selected here.
- [ ] Immutable artifact store and retention: select the store, approved-pointer
  durability mechanism, and retention policy for sanitized revisions. No
  storage backend or retention substitute is selected here.

## PR 1 Contract Guards

- CI accepts only allow-listed repository IDs and rejects relative and absolute path overrides before publication.
- Publication requires a sanitized, validated immutable artifact digest, atomically promotes only the approved pointer, and the last approved revision remains served on publication failure.
- Access requires private TLS with repository-scoped authenticated identity; missing or unauthorized identity is denied and shared application tokens are forbidden.
- Future serving workloads set `automountServiceAccountToken: false`, run with a non-root, read-only root filesystem, and have all Linux capabilities dropped.
- The experimental adapter uses an immutable snapshot per repository: snapshot mutation and cross-repository access are denied, and requests are unsupported outside query operations.
- The adapter does not mount `.codegraph` SQLite or WAL state and does not replace the local CodeGraph workflow.
- Onboarding remains blocked without CNI NetworkPolicy evidence and Hermes `hostNetwork` connectivity evidence; until then, isolation is not claimed.
