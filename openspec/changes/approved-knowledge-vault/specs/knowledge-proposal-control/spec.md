# Knowledge Proposal Control Specification

## Purpose

Govern immutable knowledge proposals from submission through human decision without granting vault-write authority.

## Requirements

### Requirement: Durable proposal lifecycle

The system MUST record each submission with identity, provenance, and idempotency key; it MUST retain proposals and audit events indefinitely.

#### Scenario: Accept unique submission

- GIVEN a valid proposal and unused idempotency key
- WHEN an agent submits it
- THEN the system records an immutable pending proposal

#### Scenario: Retry submission

- GIVEN an existing submission with the same idempotency key
- WHEN the agent retries it
- THEN the system returns the existing proposal without duplication

### Requirement: Human decisions and revision lineage

The system MUST expose pending proposals for Obsidian-visible human review. Duplicate or contradictory proposals MUST require an explicit human decision. A rejected revision MUST create a new proposal identity linked to its predecessor.

#### Scenario: Resolve conflicting proposals

- GIVEN pending proposals that conflict
- WHEN a reviewer records a decision
- THEN the decision and rationale are retained in the audit trail

#### Scenario: Resubmit rejected content

- GIVEN a rejected proposal
- WHEN revised content is submitted
- THEN it is pending under a new identity linked to the rejected proposal

### Requirement: Outage-safe submission

The system MUST durably queue submissions during control-plane unavailability and MUST NOT approve or publish them until recorded human approval is available.

#### Scenario: Control-plane outage

- GIVEN the control plane is unavailable
- WHEN an agent submits a valid proposal
- THEN the proposal is retained locally for later delivery and no vault write occurs
