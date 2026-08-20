# Proposal: Hindsight Backend Adapter

## Intent

Phase 1 shipped Memory Router with exactly one adapter (Engram) and claimed "adding a second backend requires only a new adapter plus registration — no router change". That claim is untested: with one adapter the plugin seam is unproven, and the adapter contract is silently overfit to Engram's MCP-over-stdio subprocess transport. Hindsight (`ghcr.io/vectorize-io/hindsight`, MIT, arXiv 2512.12818) is the natural second backend — it is structurally different (HTTP transport, native memory-bank namespacing), so it falsifies or confirms the seam. Until a non-stdio adapter exists, every future backend (Graphiti, Honcho, Cognee, Obsidian) carries unquantified re-architecture risk.

## Resolved Decisions

- **Namespace overlap / write fan-out**: Hindsight declares a narrower `capabilities().namespaces` set than Engram (does not blanket-match every namespace Engram already serves). This avoids double-writing and duplicate search results by default while the adapter is unvalidated against a live instance. Which specific namespace glob(s) Hindsight owns day one is an `sdd-design` decision, not a re-open of this question — the constraint is "no overlap with Engram's existing namespaces," not "no namespaces at all."

### In Scope
- `HindsightBackend` adapter in `hermes-native/memory-router/src/memory_router/backends/hindsight.py`, implementing the existing `MemoryBackend` Protocol: `capabilities()`, `health()`, `store()`, `search()`.
- HTTP client transport (Hindsight MCP-over-HTTP / plain HTTP API), replacing Engram's stdio-subprocess pattern.
- `capabilities().verbs = frozenset({"store", "search"})` — `reflect` explicitly excluded and asserted excluded in tests, mirroring the Engram adapter test.
- Namespace mapping: Memory Router namespace -> Hindsight memory bank (`bank_id`), analogous to Engram's `ns:` topic_key prefix. Declared namespace set does NOT overlap Engram's — avoids double-write/double-search fan-out by default (see Resolved Decisions).
- Config-driven auth supporting both local no-auth and Hindsight Cloud bearer token, defaults via env, no hardcoded mode.
- Entry-point registration only: `hindsight = "memory_router.backends.hindsight:HindsightBackend"` under the existing `memory_router.backends` group.
- Unit tests with a stubbed HTTP transport (no live Hindsight instance required).

### Out of Scope
- Wiring `/memory/reflect` end-to-end (contracts, per-role permission rows, dispatcher). Deferred: needs undecided product/security decisions and would break the no-router-change contract.
- Kubernetes manifests / real Hindsight deployment — no live instance exists to deploy or integration-test against.
- Choosing the production auth mode for the cluster; the adapter supports both, the decision is deferred.
- Cross-backend merge, ranking, dedup, or write fan-out.

## Capabilities

### New Capabilities
None.

### Modified Capabilities
- `memory-backend-adapters`: adds a second adapter requirement — a Hindsight adapter over HTTP with bank-per-namespace mapping and `reflect` excluded from declared verbs — and promotes "Phase 1 ships exactly one adapter" to a multi-adapter statement.

## Approach

Mirror `engram.py`'s class shape exactly; swap only the transport layer. A small internal HTTP client raises `BackendUnavailableError("hindsight", ...)` on connection/status/decode failure, so the existing dispatcher's degraded-backend handling (pending store, partial search) applies unchanged. Namespace becomes `bank_id`; `store` maps to Hindsight `retain`, `search` to `recall`. Registration through the entry-point group means `Registry` and every router core file stay untouched.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `hermes-native/memory-router/src/memory_router/backends/hindsight.py` | New | HTTP adapter. |
| `hermes-native/memory-router/pyproject.toml` | Modified | One entry-point line. |
| `tests/test_memory_router_hindsight_adapter.py` | New | Stubbed-transport unit tests. |
| `openspec/specs/memory-backend-adapters/` | Modified | Delta spec. |
| `specs/015_hindsight_backend.md` | New | Numbered spec companion. |
| Router core (`app.py`, `registry.py`, `contracts.py`, `permissions.py`, `namespaces.py`, `identity.py`, `journal.py`) | Unchanged | Contract of this change: zero edits. |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Hindsight HTTP API shape unverified against a live instance | High | Isolate wire format in one client class; tests use stubbed transport; treat schema as revisable. |
| `MemoryBackend` Protocol proves Engram-shaped and needs changes | Med | If a core edit becomes unavoidable, stop and re-propose — no-router-change is the acceptance criterion, not a preference. |
| Bank-per-namespace conflicts with Hindsight bank lifecycle (creation, limits) | Med | Adapter creates/uses bank lazily; document unsupported namespace patterns in `capabilities().namespaces`. |
| Adapter cannot be integration-tested | High | Accept unit-level proof only; flag live validation as an explicit follow-up. |
| Scope creep into `reflect` | Med | `reflect` excluded from declared verbs and asserted absent in tests. |

## Rollback Plan

Delete `backends/hindsight.py`, its test file, and the single `pyproject.toml` entry-point line, then reinstall the package. Because registration is entry-point based and no core file is touched, removal restores the exact Phase 1 behavior. No data migration, no stored state, nothing to restore.

## Dependencies

- Phase 1 Memory Router merged on `main` (PR #14) — satisfied.
- An HTTP client library available to the router package (stdlib `urllib` or an existing dependency; no new heavy dependency preferred).
- Hindsight API documentation for `retain` / `recall` request and response shapes.

## Success Criteria

- [ ] `HindsightBackend` satisfies the `MemoryBackend` Protocol (`isinstance` check against `runtime_checkable` Protocol passes).
- [ ] `capabilities().verbs == {"store", "search"}` and a test asserts `"reflect" not in verbs`.
- [ ] `Registry` discovers two backends via entry points with zero changes to `registry.py`.
- [ ] `store` / `search` round-trip against a stubbed HTTP transport, including namespace -> `bank_id` mapping.
- [ ] Transport failure raises `BackendUnavailableError` and yields pending-store / partial-search behavior via the existing dispatcher.
- [ ] `git diff` shows no modification to any router core file.
- [ ] Auth mode is selectable by config for both local no-auth and bearer token.
