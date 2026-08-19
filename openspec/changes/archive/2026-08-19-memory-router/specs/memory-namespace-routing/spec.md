# Memory Namespace Routing Specification

## Purpose

Define the namespace model, the explicit-declaration requirement, and hierarchical search fallback behavior.

## Requirements

### Requirement: Fixed Namespace Model

The system MUST support exactly these namespace roots: `/global`, `/user/master`, `/projects/{name}`, `/agents/{name}`.

#### Scenario: Namespace outside the model is rejected

- GIVEN a request declares a namespace that does not match any supported root
- WHEN the router validates the namespace
- THEN the request is rejected with an invalid-namespace error

### Requirement: Explicit Namespace Declaration

The caller MUST explicitly declare the target namespace on every `store` and `search` request. The router MUST NOT infer namespace from caller identity or from request content.

#### Scenario: Missing namespace is rejected

- GIVEN a `store` or `search` request omits the `namespace` field
- WHEN the router processes the request
- THEN the router rejects the request rather than inferring a namespace

#### Scenario: Declared namespace outside permitted roles is a 403

- GIVEN a caller declares a namespace not among the namespaces permitted for its authorized role
- WHEN the router evaluates the request
- THEN the router returns a `403` authorization failure and does not silently substitute a permitted namespace

### Requirement: Search Uses Hierarchical Fallback

`POST /memory/search` MUST apply hierarchical fallback across `/projects/{name}` -> `/agents/{name}` -> `/global` when the declared namespace yields insufficient or no results, subject to the caller's permitted namespaces. `POST /memory/store` MUST NOT apply any fallback — it MUST write only to the exact declared namespace.

#### Scenario: Search falls back from project to global

- GIVEN a search declares `namespace: /projects/lector-ine` and the project namespace has no matching results
- WHEN the caller is also permitted to read `/agents/*` and `/global`
- THEN the router additionally searches the agent namespace, then `/global`, and returns combined results indicating the namespace each result came from

#### Scenario: Store never falls back

- GIVEN a store request declares `namespace: /projects/lector-ine`
- WHEN the target namespace backend write succeeds or fails
- THEN the router never redirects the write to `/agents/*` or `/global`
