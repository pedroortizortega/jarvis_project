# Proposal: Shared MCP Services

## Intent

Provide private repository-intelligence MCP access without exposing sensitive source-derived data or inventing CodeGraph support.

## Scope

### In Scope
- Own the `mcps` namespace; preserve Brave MCP behavior unless explicitly changed later.
- Deploy Graphify: each repository CI builds and atomically publishes its own sanitized, versioned graph revision; serving is read-only.
- Deploy experimental CodeGraph through a read-only adapter over immutable per-repository snapshots; never share `.codegraph` SQLite/WAL or claim native HTTP.
- Require private TLS, mTLS or identity-proxy authentication, least privilege, non-root/read-only hardening, no default Kubernetes API credentials/RBAC, and client onboarding.
- Validate CNI-specific `hostNetwork`/NetworkPolicy behavior before claiming isolation.

### Out of Scope
- Public Internet exposure, shared mutable state, or an official CodeGraph HTTP/container claim.
- Changing Brave MCP transport, probes, or behavior.
- Implementing Kubernetes manifests in this proposal phase.

## Capabilities

### New Capabilities
- `shared-graphify-service`: Private Graphify service with CI-published, sanitized, immutable repository revisions.
- `experimental-codegraph-adapter`: Explicitly experimental read-only snapshot adapter for private CodeGraph access.
- `private-mcp-client-access`: Client onboarding through mTLS or an identity proxy, never shared application tokens.

### Modified Capabilities
None — no existing OpenSpec capability specifications exist.

## Approach

Use distinct builder and serving identities. Repository CI publishes isolated, validated revisions atomically; servers pin read-only revisions. Failed builds retain the last approved revision. Graphify is production; CodeGraph is a snapshot-backed feasibility adapter. Gate rollout on CNI and Hermes validation.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `kubernetes/mcps/` | Modified | Namespace and future private services; Brave unchanged. |
| `kubernetes/hermes/hermes-agent-master.yaml` | Reference | Validate host-network connectivity. |
| `kubernetes/policy/` | Modified | CNI-validated isolation policy. |
| `openspec/changes/shared-mcp-services/` | Modified | SDD artifacts. |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| CNI bypasses assumed isolation | High | Block rollout until cluster tests pass. |
| Sensitive-data or cross-repository leakage | Med | CI sanitization, isolated artifacts, network identity, TLS. |
| CodeGraph incompatibility | High | Keep experimental; retain local workflow. |

## Rollback Plan

Disable onboarding, route traffic away, and restore the prior Graphify revision. Remove only new `mcps` resources; leave Brave and local CodeGraph unchanged.

## Dependencies

- Private TLS/certificates and mTLS or identity-proxy management.
- CNI host-network/NetworkPolicy and Hermes connectivity validation.
- Strict TDD command: `python -m unittest discover -s tests`.

## Success Criteria

- [ ] Graphify permits only mTLS- or identity-proxy-authenticated, repository-scoped private-TLS access.
- [ ] CI publication is sanitized, versioned, atomic, rollback-tested; failed builds retain the last approved revision.
- [ ] CodeGraph is read-only, snapshot-isolated, and experimental.
- [ ] CNI behavior is evidenced before any isolation claim; Brave behavior is unchanged.
