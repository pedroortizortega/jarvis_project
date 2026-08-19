# Tasks: Shared MCP Services

## Review Workload Forecast

| Field | Value |
|---|---|
| Estimated changed lines | 650–900 total; 220–330 per child PR |
| 400-line budget risk | High (mitigated by chain) |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 → PR 2 → PR 3 |
| Delivery strategy | ask-on-risk |
| Chain strategy | feature-branch-chain |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: feature-branch-chain
400-line budget risk: High

## Draft/No-Merge Feature Branch Chain

Plan only: create no branches or PRs in this phase. Tracker `feat/shared-mcp-services` starts at `main` and opens a draft/no-merge PR to `main`. PR 1 branch `feat/shared-mcp-services-01-contracts` targets the tracker; PR 2 branch `feat/shared-mcp-services-02-graphify` targets PR 1’s branch; PR 3 branch `feat/shared-mcp-services-03-adapter` targets PR 2’s branch. Retarget/rebase any polluted child diff.

### PR 1 — Contracts and Inputs (≤260 lines)

Start: tracker branch. End: executable contract suite and documented unresolved inputs. Depends on: none. Verify: `python -m unittest discover -s tests`; CI publication/failure simulation. Rollback: remove `tests/test_shared_mcp_contracts.py` and decision record. Follow-up: Graphify serving. Out of scope: manifests, onboarding, CNI claim.

```text
main → tracker
       └── 📍 PR 1 contracts
            └── PR 2 Graphify
```

### PR 2 — Production Graphify (≤330 lines)

Start: PR 1 branch. End: private, hardened, read-only Graphify resource and approved-revision path. Depends on: artifact store and identity issuer/proxy resolved. Verify: strict unittest command; ephemeral-cluster approved-read, denied-identity, and fallback scenario. Rollback: Graphify resources and per-repo CI only. Follow-up: adapter/policy evidence. Out of scope: CodeGraph and rollout.

```text
tracker → PR 1
           └── 📍 PR 2 Graphify
                └── PR 3 adapter
```

### PR 3 — Experimental Adapter and Gate (≤300 lines)

Start: PR 2 branch. End: query-only experimental adapter and evidence-gated policy/onboarding. Depends on: PR 2 plus target-CNI `hostNetwork` evidence. Verify: strict unittest command; target-CNI Hermes, TLS identity, and isolation run. Rollback: adapter, policy, and onboarding only. Follow-up: tracker integration. Out of scope: Brave changes and local CodeGraph replacement.

```text
PR 1 → PR 2
        └── 📍 PR 3 adapter
             └── tracker merge
```

## Phase 1: RED Contracts and Dependencies (PR 1)

- [x] 1.1 Record unresolved target CNI/`hostNetwork`, identity issuer/proxy, and immutable artifact store/retention in `openspec/changes/shared-mcp-services/design.md`; do not select substitutes.
- [x] 1.2 RED: create expected-failure guards in `tests/test_shared_mcp_contracts.py` for `mcps` ownership and unchanged `kubernetes/mcps/brave-search-mcp-deployment.yaml`.
- [x] 1.3 RED: guard allow-listed CI repository IDs; reject relative/absolute overrides; require sanitized atomic immutable digest and last-approved fallback.
- [x] 1.4 RED: guard private-TLS repository identity, denied missing/unauthorized identity, no shared token, disabled token automount, non-root/read-only filesystem, and dropped capabilities.
- [x] 1.5 RED: guard snapshot mutation/cross-repository denial, `unsupported` outside queries, and no `.codegraph` SQLite/WAL mount.
- [x] 1.6 RED: guard onboarding/isolation claims to fail closed without CNI NetworkPolicy and Hermes `hostNetwork` evidence.

## Phase 2: Graphify GREEN/REFACTOR (PR 2)

- [ ] 2.1 After artifact-store approval, add each owning repository CI definition for sanitization, validation, version/digest, and atomic approved-pointer promotion.
- [ ] 2.2 GREEN: create `kubernetes/mcps/namespace.yaml` and hardened `kubernetes/mcps/graphify-*.yaml`; promote Graphify guards using read-only approved artifacts and resolved private-TLS identity scope.
- [ ] 2.3 REFACTOR: centralize revision/pointer and authorization fields across CI, manifests, and `tests/test_shared_mcp_contracts.py`.

## Phase 3: Adapter and Rollout Gate (PR 3)

- [ ] 3.1 GREEN: create experimental query-only `kubernetes/mcps/codegraph-adapter-*.yaml`; promote adapter guards with immutable repository snapshots only.
- [ ] 3.2 After CNI proof, create default-deny `kubernetes/policy/mcps-networkpolicy.yaml` with only evidenced traffic; omit RBAC and Kubernetes API credentials.
- [ ] 3.3 Run strict unittest, ephemeral-cluster contracts, and target-CNI Hermes evidence; block onboarding on any failure and verify rollback leaves Brave unchanged.
