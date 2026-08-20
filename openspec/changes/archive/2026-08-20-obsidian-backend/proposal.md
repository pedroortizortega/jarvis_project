# Proposal: Knowledge-Vault Backend Adapter (Search on `/global`)

## Intent

`/global` is meant to be Jarvis' primary source for definitional, cross-cutting knowledge, but today only Engram answers `search` there — and Engram holds session memory, not curated reference material. Meanwhile the repo already runs a curated, human-approved corpus: `knowledge-vault`, five systemd units on `trantor` publishing to `/opt/knowledge-vault/vault`, with a working local hybrid lexical+semantic `search_vault()` (`knowledge_vault/search.py` → `VaultHit(note, title, excerpt)`). That corpus is unreachable from memory-router. This change adds the missing read path so approved knowledge answers `/global` searches alongside Engram, not instead of it.

Purely additive: `contracts.py` gains one Protocol, everything else in the router is untouched. `permissions.py` needs **no** change — `search` on `global` is already allowed for `coder`, `scientist`, and `jarvis`.

## Scope

### In Scope
- `KnowledgeVaultBackend` in `.../memory_router/backends/knowledge_vault.py` — HTTP adapter with an injectable `transport(method, url, headers, body)` seam and env-driven config, mirroring `backends/honcho.py`/`cognee.py`.
- `capabilities()` = name `knowledge-vault`, `verbs = frozenset({"search"})`, `namespaces = ("/global",)`, `hierarchical_search=False`. No `store`, no `reflect`.
- `SearchOnlyBackend` Protocol in `contracts.py` (`capabilities`/`health`/`search`), mirroring `ReflectiveBackend`'s precedent — `MemoryBackend` mandates `store()`, which this backend must not have.
- A new read-only HTTP wrapper service on `trantor` exposing `search_vault()` over a small authenticated surface (`POST /search`, `GET /healthz`), plus its systemd unit — code and unit file only.
- `VaultHit` → `SearchHit(namespace="/global", backend="knowledge-vault", content, score)` mapping.
- Empty vault, stale/unavailable index, and transport failure handled explicitly; never a fabricated hit.
- Entry-point line under `memory_router.backends`; unit tests on both sides with stubbed transport — no live service required.

### Out of Scope
- Obsidian's Local REST API plugin and any access to the user's personal raw vault. **Rejected**: exposes read/write when only search is needed, and depends on a laptop being online where `trantor` is always-on.
- `store` and `reflect` for this backend. Writes into the vault stay behind the existing propose/review/approve/publish pipeline.
- The knowledge-vault pipeline itself — already built; this change only adds a read path from its output.
- Actual deployment of the wrapper to `trantor` (separate operational step, same posture as memory-router's own undeployed status).
- Namespaces other than `/global`; changing Engram's `/global` behavior.

## Capabilities

### New Capabilities
- `knowledge-vault-search-bridge`: the read-only HTTP search surface over the published vault (request/response shape, auth, empty/stale-index semantics, no write path).

### Modified Capabilities
- `memory-router-interfaces`: adds the `SearchOnlyBackend` Protocol as a first-class contract alongside `MemoryBackend` and `ReflectiveBackend`.
- `memory-backend-adapters`: adds the knowledge-vault adapter requirement (HTTP, search-only, `/global`-only, non-hierarchical).

## Approach

Two thin pieces and no router surgery.

**Host side**: a sixth knowledge-vault unit, `knowledge-vault-search.service`, running a stdlib HTTP server that calls the existing `search_vault(query, KNOWLEDGE_VAULT_DIR, KNOWLEDGE_VAULT_INDEX, limit)` and returns JSON. It reuses the vault read-only; the publisher stays the only writer. Bearer token from a systemd credential; bound to a local interface only.

**Router side**: clone the Honcho adapter shape, swap the wire format and drop `reflect` for `search`. No namespace→identifier mapping is needed (single fixed namespace), so `_peer_ref`-style resolution collapses to a `namespace == "/global"` guard that fails closed.

**Reachability**: memory-router runs in-cluster on the same host as the vault, so the established Engram Cloud local-bridge pattern (`docs/engram-cloud/remote-access/`) applies in its simple local-reachable form; the full mTLS remote proxy is for off-host clients and should not be pulled in unless design proves otherwise.

`Dispatcher.search()` already iterates every backend selected for a verb+namespace and merges hits, so Engram and knowledge-vault coexist on `/global` with zero dispatcher, registry, or permissions changes. `Registry.backends_for()` is duck-typed on `capabilities()`, so a non-`MemoryBackend` adapter selects correctly today.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `.../memory_router/backends/knowledge_vault.py` | New | HTTP search-only adapter. |
| `.../memory_router/contracts.py` | Modified | Add `SearchOnlyBackend` Protocol. |
| `.../memory_router/app.py` | Unchanged | Search fan-out already generic. |
| `.../memory_router/registry.py` | Unchanged | Duck-typed capability selection. |
| `.../memory_router/permissions.py` | Unchanged | `search` on `global` already granted to all roles. |
| `hermes-native/memory-router/pyproject.toml` | Modified | One entry-point line. |
| `hermes-native/knowledge-vault/src/knowledge_vault/serve.py` | New | Read-only HTTP search surface. |
| `hermes-native/knowledge-vault/systemd/knowledge-vault-search.service` | New | Unit, read-only vault + index. |
| `docs/services/knowledge-vault.md` | Modified | Document the 6th unit and its config. |
| `tests/test_memory_router_knowledge_vault_adapter.py`, `knowledge-vault/tests/test_serve.py` | New | Stubbed-transport / in-process tests. |
| `openspec/specs/{memory-router-interfaces,memory-backend-adapters}/` | Modified | Delta specs. |
| `specs/0NN_knowledge_vault_backend.md` | New | Numbered spec companion. |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| New network surface over curated knowledge | Med | Search-only verbs, no write path, bearer auth, bind local-only, read-only systemd mount. |
| Introducing a 6th unit widens the vault's safety model | Med | Unit is read-only on vault+index; `ReadOnlyPaths=`, no `ReadWritePaths=`; publisher remains sole writer. |
| `search_vault()` rebuilds the index on staleness — latency spike inside a request | Med | Bound with a request timeout; consider serving `available=False` instead of rebuilding inline (design question 2). |
| Two backends on `/global` return duplicate/competing hits with incomparable scores | Med | `SearchHit.backend` distinguishes sources; scoring normalization is a design question, not silently invented. |
| Adapter name says "obsidian" but the target is knowledge-vault | Low | Resolved: module and backend name are `knowledge-vault`; only the SDD change name keeps its historical `obsidian-backend` label. |
| Scope creep into store/reflect | Med | `verbs == {"search"}` asserted; absence of `store`/`reflect` asserted. |
| `SearchOnlyBackend` duplicates `MemoryBackend`'s search half | Low | Precedent already set by `ReflectiveBackend`; registry selection, not Protocol conformance, is the dispatch gate. |

## Rollback Plan

Registration-only on the router side. (1) Remove the entry-point line, `backends/knowledge_vault.py`, and its test — `backends_for(verb="search", namespace="/global")` returns Engram alone, exactly the pre-change behavior. (2) Revert the `SearchOnlyBackend` addition to `contracts.py` (additive, no existing caller). (3) On the host, `systemctl disable --now knowledge-vault-search.service` and delete the unit; the vault, its index, and the publish pipeline are untouched. No data migration, no stored state, no writes to undo.

## Dependencies

- knowledge-vault deployed on `trantor` with `KNOWLEDGE_VAULT_DIR` / `KNOWLEDGE_VAULT_INDEX` — satisfied.
- memory-router `main` with the registry/dispatcher search fan-out — satisfied.
- A decision on how memory-router reaches the host service from in-cluster (see design question 1) — required for deployment, not for merging.

## Success Criteria

- [x] `KnowledgeVaultBackend.capabilities().verbs == frozenset({"search"})`; `"store"` and `"reflect"` asserted absent.
- [x] `capabilities().namespaces == ("/global",)`; search on `/projects/x`, `/agents/x`, `/user/master` does not select it.
- [x] `isinstance(backend, SearchOnlyBackend)` holds; `isinstance(backend, MemoryBackend)` is False.
- [x] A `/global` search selects **both** Engram and knowledge-vault and merges their hits; existing Engram `/global` tests pass unmodified.
- [x] `app.py`, `registry.py`, and `permissions.py` have zero functional diff.
- [x] Empty vault or unavailable index yields zero hits with no error and no fabricated content.
- [x] Transport failure raises `BackendUnavailableError` and surfaces in `unavailable[]` as degraded, never as request failure.
- [x] The HTTP surface exposes no write verb; a `POST` that would mutate the vault is not routable.
- [x] An unauthenticated request to the search service is rejected.
- [x] Each `SearchHit` carries `backend="knowledge-vault"` so a caller can tell curated knowledge from session memory.

## Proposal question round (open questions for `sdd-design`)

Scope was confirmed with the user across two rounds; these remain genuinely undecided and should not be settled silently.

1. **In-cluster → host reachability (blocking for design).** memory-router runs in the k8s cluster on `trantor`, the search service on the host. Does the pod reach it via host-gateway/node IP, a headless `Service` + `Endpoints`, or the fuller Engram Cloud mTLS proxy? Proposed default: **simplest local-reachable path, mTLS deferred** — the traffic never leaves the host.
2. **Stale-index behavior inside a request.** `search_vault()` rebuilds the index when it has fallen behind, which is unbounded work on a synchronous request path. Proposed: keep the rebuild but bound it by timeout; alternative is returning empty and letting a timer unit rebuild.
3. **Score semantics across backends.** `VaultHit` carries no score (`search_vault()` drops it after `MIN_RELEVANCE` filtering), while Engram hits do. Proposed: surface `RetrievalHit.score` through the HTTP layer rather than emitting `0.0`, and treat cross-backend normalization as out of scope.
4. **Hit content shape.** `SearchHit.content` from a 200-char excerpt loses the note id an agent needs to cite or link. Proposed: content = excerpt, with `note`/`title` preserved — but `SearchHit` has no metadata field today, so design must choose between embedding the id in `content` or extending the dataclass.
5. **Is `/global` the right namespace?** Strong default per prior research, but the vault is a Zettelkasten of the user's own approved notes, not neutral reference data. Confirm `/global` rather than a new root before spec freezes it.
