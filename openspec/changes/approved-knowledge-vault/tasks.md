# Tasks: Approved Knowledge Vault

## Review Workload Forecast

| Field | Value |
|---|---|
| Estimated changed lines | 900–1,200 total; each PR ≤400 |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 → PR 2 → PR 3 |
| Delivery strategy | ask-on-risk |
| Chain strategy | feature-branch-chain |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: feature-branch-chain
400-line budget risk: High

Tracker: draft/no-merge PR from `feat/approved-knowledge-vault` to `main`.

### Work Unit 1 — Proposal Control (PR 1, ≤360 lines)
Start: `feat/approved-knowledge-vault-01-control` from tracker. End: strictly tested local contracts, outbox, and Obsidian decision flow. Depends on: tracker only. Verify: `python -m unittest discover -s tests -p 'test_outbox.py'` and `-p 'test_review.py'`; runtime N/A—datastore undecided. Rollback: revert package models/outbox/review/tests. Follow-up: publisher/API manifests in PR 2. Out-of-scope: vault writes, retrieval, runtime selection.

```text
main → tracker (no-merge) ← 📍 PR 1
                           └── PR 2
```

### Work Unit 2 — Publication Fence (PR 2, ≤360 lines)
Start: `feat/approved-knowledge-vault-02-publication` from PR 1. End: atomically fenced local publisher and control-plane-only manifests. Depends on: PR 1. Verify: `python -m unittest discover -s tests -p 'test_publisher.py'`; runtime N/A—publisher stays disabled pending reviewed proposal. Rollback: revert publisher, systemd units, manifests, documentation. Follow-up: retrieval in PR 3. Out-of-scope: changing `kubernetes/hermes/hermes-agent-master.yaml`, direct agent publishing, vault mounts.

```text
main → tracker (no-merge) ← PR 1 ← 📍 PR 2
                                      └── PR 3
```

### Work Unit 3 — Cited Retrieval (PR 3, ≤360 lines)
Start: `feat/approved-knowledge-vault-03-retrieval` from PR 2. End: local published-only hybrid retrieval with safe citations. Depends on: PR 2. Verify: `python -m unittest discover -s tests -p 'test_retrieval.py'` and full suite; runtime N/A—embedding runtime undecided. Rollback: revert retrieval/index/tests. Follow-up: integration harness after runtime selection. Out-of-scope: historical embeddings, cloud retrieval, mobile authority.

```text
main → tracker (no-merge) ← PR 1 ← PR 2 ← 📍 PR 3
```

Retarget/rebase any polluted child diff to its immediate parent before review.

## Phase 1: PR 1 — Proposal Control

- [x] 1.1 RED: Create `hermes-native/knowledge-vault/tests/test_outbox.py` for immutable IDs, retry idempotency, outage queue/no vault write, and predecessor lineage.
- [x] 1.2 GREEN/REFACTOR: Create `pyproject.toml` and `src/knowledge_vault/{models,outbox,client}.py`; agents submit only through the durable queue.
- [x] 1.3 RED then GREEN/REFACTOR: Create `tests/test_review.py` and `src/knowledge_vault/review.py` for Obsidian-visible pending files and versioned, rationaled human decisions.

## Phase 2: PR 2 — Publication Fence

- [x] 2.1 RED: Create `tests/test_publisher.py` for approved atomic publish, unapproved no-write, write-failure preservation, and single-writer fencing.
- [x] 2.2 GREEN/REFACTOR: Create `src/knowledge_vault/publisher.py` and `systemd/*.service`; only the local publisher can write the canonical vault.
- [x] 2.3 Create `kubernetes/knowledge-proposals/{deployment,service,configmap,networkpolicy}.yaml` without vault mount, hostPath, or second Hermes gateway; update `specs/004_hermes_native_clone_systemd.md` without altering `add-k3s-replicas`.

## Phase 3: PR 3 — Local Cited Retrieval

- [ ] 3.1 RED: Create `tests/test_retrieval.py` for published-only results, deterministic path/fragment citations, and unavailable output for absent/inconsistent indexes.
- [ ] 3.2 GREEN/REFACTOR: Create `src/knowledge_vault/retrieval.py` with local lexical/semantic fusion and reconstructible current index only.
- [ ] 3.3 Run `python -m unittest discover -s tests`; record deferred integration coverage pending datastore and embedding runtime.
