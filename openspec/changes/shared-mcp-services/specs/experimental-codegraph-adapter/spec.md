# Experimental CodeGraph Adapter Specification

## Purpose

Offer an explicitly experimental private CodeGraph feasibility adapter without representing it as native HTTP support.

## Requirements

### Requirement: Immutable Snapshot Isolation

The adapter MUST serve read-only, immutable snapshots scoped to one repository. It MUST NOT share `.codegraph` SQLite or WAL state, accept mutations, or expose data across repositories.

#### Scenario: Query an isolated snapshot

- GIVEN an approved immutable snapshot for a repository
- WHEN an authorized client queries it
- THEN the adapter returns only read-only data from that snapshot

#### Scenario: Attempt a mutation or cross-repository query

- GIVEN a client requests a write or another repository's data
- WHEN the adapter evaluates the request
- THEN it denies the request without altering any snapshot

### Requirement: Experimental Boundary

The service MUST identify the adapter as experimental and MUST NOT claim official CodeGraph HTTP or container support. Unsupported operation MUST preserve the local CodeGraph workflow.

#### Scenario: Encounter an unsupported operation

- GIVEN a request outside supported snapshot access
- WHEN the adapter cannot fulfill it
- THEN it reports the operation as unsupported
- AND local CodeGraph remains unaffected
