# Approved Vault Publication Specification

## Purpose

Publish approved knowledge to one canonical local vault while preserving its authority boundary.

## Requirements

### Requirement: Approved single-writer publication

The system MUST allow only one host-local publisher to write the canonical vault. It MUST publish only validated proposals with recorded approval, atomically, and MUST preserve existing published content on failure.

#### Scenario: Publish an approved proposal

- GIVEN a validated proposal with recorded approval
- WHEN the local publisher processes it
- THEN its Markdown is atomically available in the canonical vault

#### Scenario: Reject unapproved publication

- GIVEN a pending, rejected, or invalid proposal
- WHEN publication is attempted
- THEN the publisher performs no vault write and records the failure

### Requirement: Read-only consumers and copies

The system MUST provide Hermes and OpenCode read-only vault access. Mobile iCloud/Obsidian copies MUST be read/search-first and MUST NOT become publication authority.

#### Scenario: Consumer access

- GIVEN Hermes, OpenCode, or a mobile copy reads a note
- WHEN it accesses the published vault content
- THEN it cannot publish or approve a proposal
