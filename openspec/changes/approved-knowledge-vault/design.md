# Design: Approved Knowledge Vault

## Technical Approach

Add a separate Python 3.11 knowledge-vault service suite beside the existing `hermes-native/orchestration` plugin. Hermes remains the systemd coordinator and submits immutable proposals through a host-local outbox to a K3s proposal API. K3s owns proposal, approval, lineage, and audit state, but has no canonical-vault mount and never runs a gateway. Host-local services project pending proposals into an Obsidian-visible review area, import human decisions, publish approved Markdown atomically, and build/query a local-only hybrid index. This implements all three delta specs.

## Architecture Decisions

| Decision | Options / tradeoff | Choice and rationale |
|---|---|---|
| Authority boundary | K3s writer is simple but violates host authority; host writer adds a pull step. | K3s is control plane only; one systemd publisher owns the canonical vault. This preserves the systemd-primary and `replicas: 0` boundary in `specs/004_hermes_native_clone_systemd.md`. |
| Review workflow | Web UI is centralized; filesystem review is less structured. | A host review projector writes pending proposal files outside the published vault; Obsidian exposes them. A local decision importer validates human frontmatter and records decisions in K3s. This keeps the first slice UI-free and auditable. |
| Delivery and publication | Direct submission loses work during outage; direct vault fallback bypasses approval. | A durable host-local outbox retries idempotent API delivery; no retry path writes the vault. Publisher pulls only approved records, validates content, then writes a same-filesystem temporary file and atomically replaces the target. |
| Retrieval lifecycle | Persisting historical embeddings costs space and risks staleness; rebuilds cost time. | Index only published notes locally. Persist the current manifest/index and rebuild from the vault; reject queries when its vault revision does not match the current manifest. |

## Data Flow

```text
Hermes systemd -> local outbox -> K3s proposal API -> proposal/audit store
                                      ^                  |
Obsidian reviewer <- review projector <- pending records  |
        |                                                  v
 decision frontmatter -> decision importer -> approved record
                                              |
                                     local single publisher
                                              v
                                  canonical vault -> local hybrid index
                                              |              |
                                  read-only Hermes/OpenCode  cited results
```

Proposal IDs and idempotency keys are immutable. Revisions receive new IDs with `predecessor_id`. The control plane detects duplicate/conflict candidates but does not auto-resolve them. The importer records a human decision and rationale; the publisher records every validation or write failure without changing existing notes. iCloud/Obsidian sync receives copies only.

## File Changes

| File | Action | Description |
|---|---|---|
| `hermes-native/knowledge-vault/pyproject.toml` | Create | Independent Python package and systemd entry points. |
| `hermes-native/knowledge-vault/src/knowledge_vault/{models,outbox,client,review,publisher,retrieval}.py` | Create | Immutable contracts, durable retry, authenticated control-plane client, review projection/import, single publisher, local hybrid retrieval. |
| `hermes-native/knowledge-vault/tests/test_{outbox,review,publisher,retrieval}.py` | Create | Strict RED-first `unittest` coverage. |
| `hermes-native/knowledge-vault/systemd/*.service` | Create | Coordinator-side services with distinct writable directories; publisher alone receives canonical-vault write access. |
| `kubernetes/knowledge-proposals/{deployment,service,configmap,networkpolicy}.yaml` | Create | Proposal API/control-plane resources; no Hermes gateway, hostPath, or canonical-vault volume. |
| `kubernetes/hermes/hermes-agent-master.yaml` | No change | Remains `replicas: 0`; do not alter it. |
| `specs/004_hermes_native_clone_systemd.md` | Modify | Document vault service fencing, filesystem permissions, and no-second-gateway invariant. |

## Interfaces / Contracts

```python
@dataclass(frozen=True)
class Proposal:
    id: str; idempotency_key: str; provenance: dict[str, str]
    markdown: str; predecessor_id: str | None

@dataclass(frozen=True)
class RetrievalHit:
    note_path: str; fragment_id: str; text: str; score: float
```

`POST /proposals` returns the existing proposal for a repeated key. `POST /proposals/{id}/decision` requires reviewer identity, decision, rationale, and version. `GET /approved` exposes only recorded approvals. Retrieval returns hits with `note_path` and a deterministic heading-plus-content-hash `fragment_id`, or an explicit unavailable result.

## Testing Strategy

| Layer | What to Test | Approach |
|---|---|---|
| Unit | Lifecycle, duplicate retries, revision lineage, decision validation | `unittest` fakes for store/client; run `python -m unittest discover -s tests`. |
| Unit | Outage queue, atomic publish, permissions, index consistency, citations | Temp directories and injected failures; assert no write or uncited result on failure. |
| Integration | API plus host services | Deferred: no integration harness currently exists; add after datastore/runtime selection. |

## Threat Matrix

| Boundary | Applicability | Design response | Planned RED tests |
|---|---|---|---|
| Documentation-like paths | N/A — Markdown is data, never executable. | No execution classification. | None. |
| Git repository selection | N/A — no Git automation. | None. | None. |
| Commit state | N/A — no commits. | None. | None. |
| Push state | N/A — no pushes. | None. | None. |
| PR commands | N/A — no PR commands. | None. | None. |

## Migration / Rollout

Create empty, permission-separated host state directories; deploy K3s control-plane resources without vault mounts; then enable outbox, review projection/import, publisher, and indexer sequentially. Keep the publisher disabled until a reviewed test proposal succeeds. Roll back by stopping host services; retain proposals/audit and published notes unchanged.

## Open Questions

- [ ] Select the control-plane datastore, embedding runtime, and sync/backup provider before implementation.
