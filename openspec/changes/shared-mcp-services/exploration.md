## Exploration: shared-mcp-services

### Current State
`kubernetes/mcps/` contains a Brave Deployment and ClusterIP Service, but neither resource declares `namespace: mcps` and no Namespace resource exists. Its `stdio` transport and `/metrics` probes are not a reusable HTTP-MCP pattern. The Kubernetes Hermes Deployment is deliberately scaled to zero because systemd is authoritative; when enabled it uses `hostNetwork: true`. Existing policies are ingress-only, namespace-local label selectors, so they do not establish safe access from host-network traffic.

Graphify is suitable for a shared service only as an authenticated HTTP MCP endpoint serving a prebuilt `graph.json`; graph construction and serving must remain separate. CodeGraph stores each checkout's `.codegraph/` SQLite/WAL state and has no verified official HTTP server/container, so it cannot be represented as an equivalent native shared service.

### Affected Areas
- `kubernetes/mcps/` — add an explicit `mcps` Namespace and future Graphify resources; do not alter the Brave manifest in this change's exploration.
- `kubernetes/hermes/hermes-agent-master.yaml` — consumer topology reference: host-network behavior requires CNI-specific validation before policy enforcement is claimed.
- `kubernetes/policy/netpol-llms.yaml` — existing NetworkPolicy style is a limited reference; cross-namespace and host-network rules need explicit design and cluster validation.
- `openspec/changes/shared-mcp-services/exploration.md` — hybrid exploration artifact.

### Approaches
1. **Graphify production service; CodeGraph stays per checkout** — Create `mcps`, store an immutable/versioned `graph.json` on a controlled PVC or object-backed sync volume, build it with a separate Job/CronJob, and serve it read-only from an authenticated Graphify HTTP Deployment behind internal TLS/Ingress or a Gateway. Each client receives the endpoint and secret through its own supported configuration. Keep CodeGraph local to each repository checkout.
   - Pros: Matches Graphify's documented model; separates untrusted source build from serving; does not invent CodeGraph support.
   - Cons: Requires secret rotation, TLS/access-path decisions, artifact refresh/rollback, and CNI validation for Hermes.
   - Effort: Medium

2. **Centralize both tools as symmetric HTTP MCP services** — Wrap CodeGraph's local state in a custom container/API and deploy it beside Graphify.
   - Pros: One apparent access model.
   - Cons: Experimental, unsafe for SQLite/WAL multi-writer semantics, lacks verified official CodeGraph HTTP/container support, and would falsely imply native multi-client compatibility.
   - Effort: High

### Recommendation
Adopt approach 1. Scope the deployable service to Graphify and name CodeGraph explicitly as a non-production experiment: local `.codegraph/` remains the supported path. The later proposal should require: an explicit Namespace; dedicated ServiceAccount with no unnecessary RBAC; non-root/read-only serving container; separate builder identity and writable storage; versioned graph artifact plus rollback; Secret-managed API key; TLS; readiness based on Graphify's documented health behavior; and a client onboarding matrix for Hermes, OpenCode, Claude Code, and Codex.

Network access must be a deployment gate, not an assumption: determine the installed CNI's hostNetwork/NetworkPolicy semantics and test Hermes-to-Graphify connectivity before claiming policy isolation. Until that passes, use a restricted authenticated endpoint and do not state that a NetworkPolicy protects host-network Hermes traffic.

### Risks
- `hostNetwork` traffic may bypass or be treated differently by the installed CNI's NetworkPolicy implementation.
- A shared graph can expose source-derived data; build inputs, retention, tenant/repository boundaries, TLS, and key rotation need explicit policy.
- Serving a graph while rebuilding or replacing it can yield inconsistent reads unless publication is atomic and the serving revision is pinned.
- CodeGraph centralization risks SQLite/WAL corruption or stale checkout-specific results and has no verified native HTTP contract.
- The existing Brave manifest's lack of namespace ownership confirms that adding `mcps` must be explicit; it must not be silently retrofitted in this change without approval.

### Ready for Proposal
Yes — propose a Graphify-only production service and a separately gated CodeGraph feasibility spike. The proposal must leave Kubernetes manifests unchanged, record the CNI/access-path validation as a blocker, and defer delivery-shape choice until tasks forecast the 400-line review budget.
