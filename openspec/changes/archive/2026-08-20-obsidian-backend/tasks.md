# Tasks: Knowledge-Vault Backend Adapter (Search on `/global`)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | 900-1200 (two packages, two test suites, systemd, k8s, docs, spec) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 (host) → PR 2 (router) → PR 3 (wiring/docs/spec) |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: pending
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Host: `search.py` D-01 score, `serve.py`, systemd unit | PR 1 | `python -m unittest discover -s tests -v` (hermes-native/knowledge-vault) | In-process HTTP handler tests, temp-vault fixtures | Delete `serve.py`, `test_serve.py`, unit file; revert `search.py` field |
| 2 | Router: `contracts.py` Protocol, `backends/knowledge_vault.py`, entry point | PR 2 | `python -m unittest discover -s tests` (hermes-native/memory-router) | Stubbed-transport adapter tests | Remove entry-point line, `backends/knowledge_vault.py`, its test; revert `contracts.py` addition |
| 3 | Coexistence test, k8s manifest, docs, `specs/018_*`, final verification | PR 3 | Both suites full run + coexistence test | Registry+Dispatcher injection test, no live deploy | Revert manifest/docs/spec; no runtime effect |

## Phase 1: Host — `search.py` score field (D-01, blocks serve.py)

- [x] 1.1 RED: `test_search.py` — assert `VaultHit` has `score` field, matching hit's `score == RetrievalHit.score` (not `0.0`)
- [x] 1.2 GREEN: add `score: float = 0.0` to `VaultHit`; populate from `hit.score` in `search_vault` (`knowledge_vault/search.py`)
- [x] 1.3 REFACTOR: confirm `main()` output unaffected; existing `test_search.py` cases pass unmodified

## Phase 2: Host — `serve.py` HTTP surface

- [x] 2.1 RED: unauthenticated request → 401, zero vault reads (stub `search_vault` fails test if called); wrong/empty bearer token same
- [x] 2.2 RED: non-`POST /search`/`GET /healthz` routes/methods → 404/405; no `do_PUT`/`do_DELETE` on handler (`dir()` assertion)
- [x] 2.3 GREEN: create `serve.py` — `ThreadingHTTPServer`, route table, `hmac.compare_digest` bearer check via `$CREDENTIALS_DIRECTORY`
- [x] 2.4 RED: `200` response includes non-zero `score` from a known-matching temp-vault note
- [x] 2.5 RED: slow-rebuild stub → `503 index_rebuild_timeout` within deadline, not sleep duration; concurrent requests trigger only one rebuild (call-count assertion)
- [x] 2.6 GREEN: `ThreadPoolExecutor` + single-flight `threading.Lock` + `future.result(timeout=KNOWLEDGE_VAULT_SEARCH_TIMEOUT_SECONDS)` (D-02/D-03)
- [x] 2.7 RED: empty vault and below-`MIN_RELEVANCE` query → `200 {"hits": []}`, not `503`
- [x] 2.8 RED: vault/index mtimes unchanged after a successful non-rebuild search
- [x] 2.9 REFACTOR: extract config resolution (`_env_default`-style) for host env vars; confirm no secret ever appears in a response body

## Phase 3: Host — systemd unit

- [x] 3.1 Create `systemd/knowledge-vault-search.service`: `Type=simple`, `LoadCredential=search-token:...`, `ReadOnlyPaths=` vault+index, no `ReadWritePaths=`, `After=sys-subsystem-net-devices-cni0.device`, hardening baseline matching existing units
- [x] 3.2 Add `knowledge-vault-search-serve` console script to `pyproject.toml`

## Phase 4: Router — `contracts.py` Protocol

- [x] 4.1 RED: `isinstance(x, SearchOnlyBackend)` mechanics test on a minimal stub; `MemoryBackend` byte-diff test (hash/string compare against origin/main) fails before change
- [x] 4.2 GREEN: add `SearchOnlyBackend` Protocol (`capabilities`/`health`/`search`) to `contracts.py`; verify `MemoryBackend` untouched

## Phase 5: Router — `backends/knowledge_vault.py`

- [x] 5.1 RED: `capabilities()` exact equality (`verbs=={"search"}`, `namespaces==("/global",)`, `hierarchical_search is False`, `name=="knowledge-vault"`); `hasattr(store)`/`hasattr(reflect)` false; zero-arg construction succeeds
- [x] 5.2 RED: `SearchRequest(namespace="/user/master")` → `BackendUnavailableError`, zero HTTP calls (transport fails test if invoked) — repeat for `/projects/x`, `/agents/x`
- [x] 5.3 GREEN: implement `KnowledgeVaultBackend` cloning `honcho.py` shape (`_env_default`, `_default_transport`, `_HttpJsonClient`, `ENDPOINTS`), namespace guard raising `BackendUnavailableError` (D-05), env config per design's Config Surface table
- [x] 5.4 RED: round trip — `score` reaches `SearchHit.score` unequal to `0.0`; `content == f"{note} — {title}\n{excerpt}"` (D-04); `namespace`/`backend` correct
- [x] 5.5 RED: connection error/non-2xx/503/malformed JSON → `BackendUnavailableError`
- [x] 5.6 RED: token substring absent from every raised reason; `Authorization` header present only in `bearer` mode
- [x] 5.7 RED: hostile `query` only in JSON body, never URL/headers; `limit` adapter-chosen int; timeout always set
- [x] 5.8 GREEN: `search()` implementation satisfying 5.4-5.7
- [x] 5.9 REFACTOR: dedupe against `honcho.py`/`cognee.py` patterns; docstring noting deliberate `SearchOnlyBackend`, not `MemoryBackend`

## Phase 6: Router — entry point

- [x] 6.1 Add `knowledge-vault = "memory_router.backends.knowledge_vault:KnowledgeVaultBackend"` to `pyproject.toml`

## Phase 7: Cross-cutting — coexistence (headline test)

- [x] 7.1 RED/GREEN: `Registry([FakeEngram(), KnowledgeVaultBackend(transport=stub)])` on `/global` → `Dispatcher.search` returns 2 hits, `{backend}` set == `{"engram","knowledge-vault"}`
- [x] 7.2 Re-run existing Engram `/global` search tests unmodified as regression check
- [x] 7.3 Assert `app.py`/`registry.py`/`permissions.py` byte-unmodified vs `origin/main`

## Phase 8: Deployment artifact

- [x] 8.1 Create `kubernetes/mcps/knowledge-vault-search-endpoints.yaml` — selector-less `Service` + `EndpointSlice` → `10.42.0.1:8088`; validate only, not applied

## Phase 9: Docs

- [x] 9.1 Update `docs/services/knowledge-vault.md`: "5 systemd units" → 6, add config rows, safety-model note for the read-only search unit

## Phase 10: Numbered spec companion

- [x] 10.1 Create `specs/018_knowledge_vault_backend.md` summarizing capability, config, wire format, threat matrix

## Phase 11: Final verification

- [x] 11.1 Full host suite green (`python -m unittest discover -s tests -v` in `hermes-native/knowledge-vault`) — 113 tests
- [x] 11.2 Full router suite green (`python -m unittest discover -s tests` in `hermes-native/memory-router`) — 250 tests
- [x] 11.3 Confirm coexistence test (7.1) and all D-01–D-08 assertions pass
- [x] 11.4 Confirm no secret substring in any reason/response across both suites
