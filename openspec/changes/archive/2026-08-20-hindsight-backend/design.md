# Design: Hindsight Backend Adapter

## Technical Approach

`HindsightBackend` mirrors `EngramBackend`'s class shape one-for-one (`capabilities/health/store/search`, keyword-only constructor with env defaults, injectable transport seam for tests). Only the transport differs: `_HttpJsonClient` (stdlib `urllib.request`) replaces `_StdioRpcClient`. Namespace becomes `bank_id`; `store`→`retain`, `search`→`recall`. Registration is one `pyproject.toml` entry-point line. **Zero edits to router core** (`app.py`, `registry.py`, `contracts.py`, `permissions.py`, `namespaces.py`, `identity.py`, `journal.py`) — this is the acceptance bar, and the design was checked against it: `Registry._load_entry_points` calls `backend_class()` with no args, so the adapter MUST be default-constructible; `app.py::_fallback_chain` re-queries the registry per candidate namespace, so cross-backend fallback already works with no dispatcher change.

## Namespace Ownership (the deferred decision)

Hindsight owns **`("/projects/*",)`** exclusively. `engram.py`'s tuple narrows to `("/global", "/user/master", "/agents/*")`.

| Point | Rationale |
|---|---|
| Why `/projects/*` | Only root that is naturally many-instance and bounded — maps 1:1 onto Hindsight's memory-bank model (one bank per project). `/global` and `/user/master` are singletons; splitting them off would give Hindsight one bank and prove nothing. |
| Why narrow Engram | `namespaces.py` admits exactly four shapes, and Engram's globs cover all four. Non-overlap is therefore **only** achievable by narrowing Engram. `engram.py` is an adapter, not router core, so this respects the acceptance bar. No test asserts Engram's real tuple. |
| Migration cost | None. Router is undeployed (Phase 1 design open question: deployment blocked), so no `ns:/projects/*` data exists in Engram. |
| Seam payoff | Search on `/projects/foo` now goes Hindsight → (no hits) → `/agents/{id}` Engram → `/global` Engram, exercising cross-backend fallback with zero router change. |

## Architecture Decisions

| Decision | Options / tradeoff | Choice and rationale |
|---|---|---|
| HTTP client | `httpx`/`requests` (ergonomic, **new dependency**) vs `http.client` (verbose) vs stdlib `urllib.request` | `urllib.request` — package has zero runtime deps and the proposal states "no new heavy dependency preferred". One `_HttpJsonClient` class isolates the wire format. |
| Namespace → bank | Hash (collision-free, opaque/undebuggable) vs raw namespace (illegal chars) vs sanitized flattening | Flatten: strip leading `/`, `/`→`-`, lowercase, optional `HINDSIGHT_BANK_PREFIX`. `/projects/lector-ine` → `projects-lector-ine`. Human-debuggable; `namespaces.py::_NAME_RE` forbids `/` in the name segment, so flattening is injective over the owned root. |
| Auth | Hardcode bearer (breaks local) vs infer from token presence only vs explicit mode + derived default | `HINDSIGHT_AUTH_MODE` ∈ `{none, bearer}`, defaulting to `bearer` iff `HINDSIGHT_TOKEN` is non-empty. Satisfies "no hardcoded mode" while staying zero-arg-constructible. |
| Bank lifecycle | Pre-provision (needs ops) vs lazy auto-create | Lazy: a `retain` answering 404 triggers one create-then-retry, then fails as unavailable. |
| Verbs | Include `reflect` | `frozenset({"store","search"})` only; `reflect` is out of scope and asserted absent in tests. |

## Config Surface

| Env var | Default |
|---|---|
| `HINDSIGHT_BASE_URL` | `http://hindsight.mcps.svc.cluster.local:8080` |
| `HINDSIGHT_AUTH_MODE` | `bearer` if token set, else `none` |
| `HINDSIGHT_TOKEN` | `""` |
| `HINDSIGHT_BANK_PREFIX` | `""` |
| `HINDSIGHT_TIMEOUT_SECONDS` | `10` |

## Interfaces

```python
ENDPOINTS = {  # single revisable wire-format surface
    "retain": "/v1/banks/{bank_id}/retain",   # POST {content, metadata} -> {id}
    "recall": "/v1/banks/{bank_id}/recall",   # POST {query, limit}      -> {results:[{content,score}]}
    "create": "/v1/banks",                    # POST {bank_id}
    "health": "/health",                      # GET
}

class HindsightBackend:
    def __init__(self, *, transport=None, base_url=None, auth_mode=None,
                 token=None, bank_prefix=None, timeout=None): ...
```

`transport(method, url, headers, body) -> (status, bytes)` is the test seam, exactly as `spawn` is for Engram.

## Data Flow

    store  -> _bank_id(ns) -> POST retain -> 2xx -> StoreResult("committed","hindsight",id)
                                          -> 404 -> POST create -> retry retain once
    search -> _bank_id(ns) -> POST recall -> hits(namespace=req.namespace, backend="hindsight")
    any transport/status/decode failure -> BackendUnavailableError("hindsight", reason)
    health -> GET /health -> 2xx: OK | else: DOWN(reason)   # never raises

`BackendUnavailableError` is caught by the existing dispatcher: store → journal + `pending`; search → skip + `unavailable` marker. No new handling.

## File Changes

| File | Action | Description |
|---|---|---|
| `.../backends/hindsight.py` | Create | Adapter + `_HttpJsonClient`. |
| `.../backends/engram.py` | Modify | One line: drop `/projects/*` from declared namespaces. |
| `hermes-native/memory-router/pyproject.toml` | Modify | One entry-point line. |
| `tests/test_memory_router_hindsight_adapter.py` | Create | Stubbed-transport RED tests. |
| `specs/015_hindsight_backend.md` | Create | Numbered spec companion. |

## Testing Strategy

| Layer | What | How |
|---|---|---|
| Unit | Protocol `isinstance`; `verbs == {"store","search"}` and `"reflect" not in verbs`; zero-arg construction; bank mapping; retain/recall round-trip; lazy create-on-404; auth header present/absent per mode | Stub transport, `python -m unittest discover -s tests` |
| Unit (security) | URL derives only from configured base + sanitized bank id; headers are a fixed key set; caller `content`/`metadata` never reach URL, headers, or query — analogous to Engram's fixed-argv/env tests | Capture `(url, headers)` in stub |
| Unit (degradation) | Connection error, non-2xx, malformed JSON each raise `BackendUnavailableError("hindsight", ...)` and nothing else | Raising/garbage stub |
| Integration | Live Hindsight | **Not performed** — no instance exists; explicit follow-up. |

## Threat Matrix

| Boundary | Applicability | Design response | Planned RED tests |
|---|---|---|---|
| Namespace routing selection | Applicable | Bank id derived only from a `namespaces.py`-validated namespace via a pure sanitizing function; result re-validated against `^[a-z0-9][a-z0-9_-]*$`, fail closed | Traversal/wildcard namespace never yields a path segment |
| Outbound request construction (replaces subprocess row) | Applicable | Base URL is router-owned config only; caller data travels solely in the JSON body; header set is fixed; no redirects followed; timeout always set | Malicious content/metadata absent from URL and headers |
| Secret handling | Applicable | Token from env only, never logged, never echoed into `BackendUnavailableError.reason` | Token absent from raised reason strings |
| Docs paths / Git / commit / push / PR | N/A — no file classification, VCS, or PR automation | — | None |

## Migration / Rollout

No data migration. Adding the entry point activates Hindsight for `/projects/*`; rollback = delete the adapter, its test, the entry-point line, and restore `/projects/*` to `engram.py`.

## Open Questions

- [ ] Exact Hindsight endpoint paths and payload keys are **unverified** against a live instance or authoritative docs; `ENDPOINTS` is the single revisable surface. Confirm before any deployment.
- [ ] Does Hindsight auto-create banks on `retain` (making the 404 branch dead code), or return a different status for an unknown bank?
- [ ] Bank naming: does Hindsight impose a length limit or charset stricter than `[a-z0-9_-]`? Long project names may need truncation with a hash suffix.
- [ ] Recall result schema — is a score returned, and is it normalized comparably to Engram's for future cross-backend ranking?
- [ ] Live validation of the adapter remains an explicit follow-up; unit-level proof only.
