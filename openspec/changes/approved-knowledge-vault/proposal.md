# Proposal: Approved Knowledge Vault

## Intent

Provide a trustworthy knowledge pipeline: Hermes agents can propose Zettelkasten notes, but only a human-approved, single local writer can publish to the canonical vault. Users can safely consult synced copies and receive cited local knowledge retrieval.

## Scope

### In Scope
- A K3s-hosted proposal control plane for immutable submissions, approval state, provenance, idempotency, and durable outage queuing.
- Indefinite retention of proposal history and audit records; rejected revisions are new proposals linked to their predecessor.
- Obsidian-visible pending proposals for human review; duplicate or contradictory proposals require an explicit human decision.
- A host-local, single-writer publisher that atomically publishes approved Markdown to the canonical local vault.
- Read-only vault access for Hermes and OpenCode; read/search-first iCloud/Obsidian copies for mobile devices.
- Local-only hybrid lexical and semantic retrieval over published notes, returning note-path and stable-fragment citations; retain only a reconstructible current embeddings index.

### Out of Scope
- A central web approval UI or a self-hosted Obsidian receiver.
- K3s hosting or writing the canonical vault, agent direct publishing, or approval bypass during an outage.
- Selecting a specific database, vector store, embedding model, sync provider, or backup implementation.

## Capabilities

### New Capabilities
- `knowledge-proposal-control`: indefinitely retained proposal/audit lifecycle, predecessor-linked revisions, human decisions, conflict handling, and fail-closed queuing.
- `approved-vault-publication`: single-writer validation and atomic publication to the local canonical vault.
- `approved-knowledge-retrieval`: local hybrid retrieval of published notes with path and fragment citations.

### Modified Capabilities
None. No existing OpenSpec capabilities exist.

## Approach

Hermes under systemd remains coordinator; agents submit proposals to K3s. The control plane indefinitely retains proposal/audit history and records approval before a local publisher fetches, validates, and atomically writes notes. Published notes alone are indexed locally; the current index is reconstructible and sync distributes copies, never authority.

## Affected Areas

| Area | Impact | Description |
|---|---|---|
| `hermes-native/orchestration/` | Modified | Submit proposals and consume cited retrieval under least privilege. |
| `kubernetes/` | New | Proposal control-plane deployment; no vault mount or Hermes gateway. |
| Local canonical vault | New | Writer-owned published notes and Obsidian pending-review view. |
| `specs/004_hermes_native_clone_systemd.md` | Modified | Preserve systemd primary and single-writer boundary. |

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Sync or manual edit conflict | Med | Atomic writes, revision checks, and one automated writer. |
| Sensitive indexed content | Med | Local-only processing and explicit access/retention policy before launch. |
| K3s outage | Med | Durable local queue; fail closed without direct vault writes. |

## Rollback Plan

Disable proposal submission and publisher services; retain queued proposals and published vault content unchanged. Restore retrieval to unavailable/read-only mode; do not promote the K3s control plane to a writer.

## Dependencies

- Human review workflow in Obsidian; local durable queue; local embedding/index runtime; vault sync and backup decisions.

## Success Criteria

- [ ] No agent can publish without recorded human approval; conflicts require a human decision.
- [ ] Rejected revisions receive a new identity linked to the rejected proposal; proposal and audit history are retained indefinitely.
- [ ] K3s outages retain submissions locally and never bypass approval.
- [ ] Approved notes publish through one writer and retrieval returns local citations from a reconstructible current index.
