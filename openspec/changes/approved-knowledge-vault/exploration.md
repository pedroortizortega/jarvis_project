## Exploration: approved-knowledge-vault

### Current State
Hermes is authoritative on the host through systemd; the Kubernetes Hermes deployment is intentionally scaled to zero to prevent a second gateway. The existing Python plugin is a narrowly scoped intent router: it classifies turns, applies a deterministic policy, delegates bounded work, and keeps a SQLite audit trail without prompt content. It has no note-proposal, approval, vault-writing, retrieval, or citation component. The repository has `unittest` coverage for the plugin, but no service or data-store foundation for a knowledge vault.

### Affected Areas
- `hermes-native/orchestration/` — current Hermes extension point and test conventions; a retrieval/proposal integration must preserve its local-only and least-privilege controls.
- `specs/004_hermes_native_clone_systemd.md` — defines the systemd primary and prohibits sharing mutable state or concurrent writers.
- `specs/009_hermes_intent_orchestration.md` — defines bounded worker packets, source/citation expectations, and metadata-only auditing.
- `kubernetes/` — future control-plane deployment location, but it must not become a second Hermes gateway or mount the canonical vault for writing.
- `openspec/changes/approved-knowledge-vault/` — new change location; `add-k3s-replicas` remains untouched.

### Approaches
1. **Dedicated approval API with a host-local writer** — Agents submit immutable proposals to a durable control-plane store; a human approves or rejects them; one systemd service validates and atomically publishes approved Markdown to the canonical vault. A separate read-only retrieval service indexes only published notes using lexical and vector search, fuses/reranks results, and returns note/fragment citations.
   - Pros: Enforces approval and a single writer; separates mutable workflow state from the vault; supports idempotency, auditability, and citations; respects the existing systemd-primary boundary.
   - Cons: Adds a database, API, indexing pipeline, and operations surface.
   - Effort: High

2. **Shared vault with agent-generated pending files** — Agents write proposals directly into a `pending/` vault folder and a human moves approved files into the canonical area; retrieval ignores pending files.
   - Pros: Minimal infrastructure and an immediately visible review queue in Obsidian.
   - Cons: Agents still write into the synced vault; approval, atomicity, provenance, retries, and multi-agent conflict handling become filesystem conventions rather than enforced controls.
   - Effort: Medium

### Recommendation
Use the dedicated approval API with a host-local single writer. Store proposals, approvals, source metadata, idempotency keys, and publication state outside the vault; treat the canonical Obsidian vault as a published read model. Keep mobile clients read/search-first. Index only approved published notes, combine lexical/BM25 with semantic retrieval, rerank, and require each Hermes answer to cite the note path plus stable fragment or heading. Validate retrieval quality against 20–50 real questions before relying on it.

### Risks
- The vault sync transport and any manual desktop edits can introduce conflicts; only the writer should publish automated changes, with atomic writes and revision checks.
- Embeddings and source excerpts may contain sensitive personal data; define local-model/storage, retention, access control, and backup policies before indexing.
- A control-plane outage must not cause agents to bypass approval or write directly to the vault; submissions should remain queued and publication fail closed.
- This is likely above the 400-line review budget; plan the future implementation as independently verifiable slices.

### Ready for Proposal
Yes — proceed with a proposal that fixes the trust boundaries, durable proposal lifecycle, approval UX, canonical-vault writer contract, retrieval/citation contract, and sync/backup assumptions. The sync provider and exact database/vector implementation remain explicit decisions for that proposal.
