# Delta for Memory Backend Adapters

## ADDED Requirements

### Requirement: Honcho Adapter

The system MUST ship a `HonchoBackend` adapter that reaches Honcho's Dialectic API over HTTP transport (not stdio-subprocess), config-driven auth via environment variables, mirroring the Hindsight adapter's shape.

`HonchoBackend.capabilities().verbs` MUST equal exactly `frozenset({"reflect"})`. `HonchoBackend.capabilities().namespaces` MUST equal exactly `("/user/master",)`. The adapter MUST NOT implement or declare `store` or `search`.

#### Scenario: Honcho adapter declares reflect-only verbs

- GIVEN `HonchoBackend().capabilities()` is inspected
- WHEN `verbs` is read
- THEN it equals `frozenset({"reflect"})` exactly, and `"store" not in verbs` and `"search" not in verbs` both hold

#### Scenario: Honcho adapter declares only /user/master

- GIVEN `HonchoBackend().capabilities()` is inspected
- WHEN `namespaces` is read
- THEN it equals `("/user/master",)` exactly, so `reflect` requests on `/projects/*` or `/agents/*` select no Honcho backend

#### Scenario: Honcho adapter reaches Honcho over HTTP

- GIVEN a `reflect` request is routed to the Honcho adapter
- WHEN the adapter performs the Dialectic query
- THEN it communicates with Honcho via an injectable HTTP transport, using bearer-token or no-auth per environment configuration, and returns a `ReflectResult`

#### Scenario: Honcho transport failure raises BackendUnavailableError

- GIVEN the Honcho HTTP endpoint is unreachable, returns a non-success status, or returns an undecodable response
- WHEN the adapter attempts `reflect`
- THEN it raises `BackendUnavailableError("honcho", reason)` and does not raise any other exception type

### Requirement: Reflect-Capable Backend Contract Is Separate From MemoryBackend

The system MUST expose a narrow, capability-gated contract (a distinct Protocol) for backends that support `reflect`, separate from the existing `MemoryBackend` Protocol (`capabilities()`, `health()`, `store()`, `search()`).

The `MemoryBackend` Protocol MUST NOT gain a default or no-op `reflect()` method. The dispatcher MUST reach a backend's `reflect()` only through registry selection gated on `capabilities().verbs` containing `"reflect"`, never by structurally assuming every `MemoryBackend` implements `reflect()`.

Existing adapter conformance MUST remain unmodified and passing: `isinstance(EngramBackend(), MemoryBackend)` and `isinstance(HindsightBackend(), MemoryBackend)` MUST still hold true, and neither adapter is required to implement `reflect()`.

#### Scenario: Reflect-capable Protocol is distinct from MemoryBackend

- GIVEN the reflect-capable contract and `MemoryBackend` are inspected
- WHEN their method sets are compared
- THEN `MemoryBackend` has no `reflect()` method, and the reflect-capable contract is a separate Protocol that `HonchoBackend` satisfies

#### Scenario: Engram and Hindsight conformance is unaffected

- GIVEN `EngramBackend` and `HindsightBackend` as they exist before this change
- WHEN `isinstance(EngramBackend(), MemoryBackend)` and `isinstance(HindsightBackend(), MemoryBackend)` are evaluated after this change
- THEN both still return `True`, with their existing conformance tests unmodified

#### Scenario: Dispatcher gates reflect dispatch on declared capability

- GIVEN a registered backend does not declare `"reflect"` in `capabilities().verbs`
- WHEN `Registry.backends_for(verb="reflect", namespace=...)` is evaluated
- THEN that backend is never returned and the dispatcher never calls `reflect()` on it
