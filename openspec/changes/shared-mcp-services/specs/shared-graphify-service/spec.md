# Shared Graphify Service Specification

## Purpose

Provide production private, repository-scoped Graphify revisions without changing Brave MCP.

## Requirements

### Requirement: MCPS Namespace Ownership

The system MUST place new shared MCP resources in the `mcps` namespace and MUST preserve existing Brave transport, probes, and behavior.

#### Scenario: Add a shared Graphify resource

- GIVEN the existing Brave MCP service
- WHEN a Graphify resource is introduced
- THEN it is owned by `mcps`
- AND Brave behavior is unchanged

#### Scenario: Remove shared Graphify resources

- GIVEN Graphify access is rolled back
- WHEN its new resources are removed
- THEN Brave remains available with its prior behavior

### Requirement: Approved Repository Revisions

Each repository CI MUST sanitize, validate, version, and atomically publish an isolated Graphify revision. Serving MUST be read-only, MUST exclude secrets and private data, and MUST use the last approved revision when publication fails.

#### Scenario: Publish an approved revision

- GIVEN CI produces a sanitized valid repository graph
- WHEN publication completes
- THEN clients read only that versioned repository revision

#### Scenario: Reject an unsafe or failed revision

- GIVEN sanitization, validation, or publication fails
- WHEN clients request that repository
- THEN the last approved revision remains served
