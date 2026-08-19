# Private MCP Client Access Specification

## Purpose

Control private, repository-scoped client access to shared MCP services.

## Requirements

### Requirement: Authenticated Private Access

Clients MUST use private TLS and either mTLS or an identity proxy. Onboarding MUST bind access to an authenticated repository scope and MUST NOT use shared application tokens.

#### Scenario: Onboard an authorized client

- GIVEN a trusted client identity and repository authorization
- WHEN it connects through private TLS
- THEN it receives access only to its authorized repository

#### Scenario: Reject unauthenticated access

- GIVEN a client lacks valid mTLS or proxy identity
- WHEN it requests a shared MCP service
- THEN the service denies access

### Requirement: Isolation Validation Gate

Before claiming network isolation or enabling rollout, the system MUST evidence CNI-specific `hostNetwork` and NetworkPolicy behavior, including Hermes connectivity. Services MUST run with least privilege, non-root/read-only hardening, and no default Kubernetes API credentials or RBAC.

#### Scenario: Validate the CNI gate

- GIVEN the target CNI and host-network client path
- WHEN isolation and Hermes connectivity are tested
- THEN rollout proceeds only when expected controls are evidenced

#### Scenario: Fail the CNI gate

- GIVEN validation cannot prove the expected controls
- WHEN rollout is evaluated
- THEN isolation is not claimed and onboarding is blocked
