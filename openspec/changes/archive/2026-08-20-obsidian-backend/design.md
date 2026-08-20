# Design: Knowledge-Vault Backend Adapter (Search on `/global`)

## Technical Approach

Two thin pieces, one Protocol, no router surgery.

**Host side** — `knowledge_vault/serve.py`: a stdlib `ThreadingHTTPServer` (same server class `memory_router/app.py` already uses; the package keeps zero runtime dependencies) exposing exactly `POST /search` and `GET /healthz` over the existing `search_vault()`. Bearer token from a systemd credential, bound to a node-local address, read-only on vault and index.

**Router side** — `backends/knowledge_vault.py`: `backends/honcho.py`'s shape verbatim (`_env_default`, `_default_transport`, `_HttpJsonClient`, one `ENDPOINTS` dict, the `transport(method, url, headers, body) -> (status, bytes)` seam), with `reflect()` swapped for `search()` and the namespace→identifier mapper deleted rather than replaced (D-05). `contracts.py` gains one `SearchOnlyBackend` Protocol, mirroring `ReflectiveBackend`'s precedent exactly; `MemoryBackend` stays byte-identical.

## Verified Findings (read from the code, not assumed)

**F-1 — Coexistence on `/global` needs zero `app.py` diff.** `_fallback_chain` (`app.py:114-122`) returns `["/global"]` for `/global`, and the inner loop (`app.py:143-159`) iterates **every** backend `backends_for(verb="search", namespace=candidate)` returns and `extend`s their hits into one list. Engram and knowledge-vault therefore merge by construction. The `break` at `app.py:161` is per-*candidate*, not per-backend, so it never truncates the merge.

**F-2 — Selection is duck-typed.** `Registry.backends_for` (`registry.py:32-41`) reads only `capabilities()`; no `isinstance` anywhere. A non-`MemoryBackend` object selects correctly today. `_load_entry_points` calls `backend_class()` with zero args — the adapter must construct argument-free.

**F-3 — `SearchHit` has no metadata field** (`contracts.py:57-62`) and `Dispatcher.search` projects exactly `namespace/backend/content/score`. The note id can only reach a caller inside `content` unless the dataclass changes. Drives D-04.

**F-4 — The score exists and is discarded.** `Retriever.search` builds `RetrievalHit(..., score)` (`retrieval.py:130-133`), `search_vault` filters on `hit.score < MIN_RELEVANCE` and then never carries it into `VaultHit` (`search.py:49-65`). Nothing needs to be computed — only kept.

**F-5 — The stale-index rebuild is synchronous and uncancellable.** `search_vault` calls `build_index(vault, index_path)` inline (`search.py:43`), which re-reads and SHA-256s every note via `vault_revision` (`retrieval.py:47-51`) and re-fragments every file. There is no deadline hook inside the loop, so a "deadline check inside the rebuild" would require editing `build_index` itself. Drives D-02. `build_index` writes through `write_atomic`, so an abandoned rebuild cannot leave a torn index.

**F-6 — Node/pod networking, verified from `kubernetes/knowledge-proposals/networkpolicy.yaml:1-11`.** `trantor` node LAN `192.168.100.13` in `192.168.100.0/24`; flannel pod CIDR `10.42.0.0/24`; `cni0` host address `10.42.0.1`. The `mcps` namespace has no NetworkPolicy today (only `knowledge-proposals` does). Drives D-06.

## Architecture Decisions

| # | Decision | Options / tradeoff | Choice and rationale |
|---|---|---|---|
| D-01 | Score surfacing | Modify `search_vault()` to carry the score vs. call `Retriever.search()` directly from `serve.py` | **Add `score: float = 0.0` as a fourth field on `VaultHit` and populate it in `search_vault`.** Calling `Retriever` directly forces `serve.py` to re-implement the `MIN_RELEVANCE` filter, the per-note dedupe, the frontmatter title lookup, the excerpt truncation *and* the stale-index retry — five behaviours that would silently drift from the CLI path. The defaulted field is backward compatible: `search.py:main()` and the only consumer, `knowledge-vault/tests/test_search.py`, read attributes and never construct a `VaultHit`, so nothing breaks. Cost: a shared-module edit instead of a purely additive file. Accepted — it is four tokens of diff against five duplicated behaviours. |
| D-02 | Bounding the inline rebuild | Deadline check inside `build_index` vs. subprocess with timeout vs. worker thread with a deadline vs. never rebuild inline | **Run the whole `search_vault()` call in a `ThreadPoolExecutor` and `future.result(timeout=…)`, guarded by a single-flight `threading.Lock`.** F-5 rules out an in-loop deadline without editing `build_index`; a subprocess buys real cancellation but costs a fork and an interpreter start on every query for a rebuild that `write_atomic` already makes crash-safe. The thread is not killed on timeout — it finishes in the background and the *next* request is fast, so the service self-heals instead of thrashing. `ThreadingHTTPServer` would otherwise let N concurrent requests each start a full rebuild; the non-blocking single-flight lock makes at most one run at a time. |
| D-03 | What a timed-out rebuild returns | `200 {"hits": []}` vs. `503` | **`503` with a reason.** `200`-empty is indistinguishable from "the vault has nothing on this", which is a lie about a corpus that may hold the answer. `503` maps through the adapter to `BackendUnavailableError` → `unavailable[]` + degraded (`app.py:146-148`), which is exactly what "we could not look" means. Genuinely empty results (empty vault, no note over `MIN_RELEVANCE`) stay `200 {"hits": []}`. |
| D-04 | `SearchHit.content` shape | Extend `SearchHit` with a metadata field vs. embed the id in `content` | **Embed: `content = f"{note} — {title}\n{excerpt}"`.** F-3 says the note id has no other route to the caller, and `search.py`'s own docstring is explicit that the vault "answers with the note id an agent must write into a link". Extending `SearchHit` would change a dataclass every backend and both dispatcher projections share, breaking the proposal's "`contracts.py` gains one Protocol, everything else untouched" bar for a cosmetic gain. First line is the citable id, the rest is prose — deterministic and greppable. |
| D-05 | Namespace guard | Mirror Hindsight's `_bank_id` / Honcho's `_peer_ref` fail-closed mapper vs. no guard at all | **Keep a two-line `namespace == "/global"` guard raising `BackendUnavailableError("knowledge-vault", …)`, but no mapper.** There is nothing to map: one fixed namespace, one fixed corpus, no per-namespace identifier on the wire — so the entire `_ID_RE` re-validation apparatus is dead weight here and is deliberately not copied. The guard itself is redundant with `capabilities().namespaces` under `Registry` (F-2) but the adapter is also directly constructible in tests and by future callers; a search-only backend that would answer `/user/master` if handed it is a latent isolation bug. `BackendUnavailableError`, not `ValueError` — `Dispatcher.search` catches only the former (`app.py:146`), so a bare `ValueError` would escape as an unhandled 500. |
| D-06 | In-cluster → host reachability | `hostNetwork: true` on the router pod vs. node LAN IP vs. `cni0` gateway address vs. Engram Cloud mTLS proxy | **Bind the service to the `cni0` gateway `10.42.0.1:8088`, and give it a stable name via a selector-less `Service` + manually-managed `EndpointSlice` in `mcps` pointing at that address.** `hostNetwork` on the router would dissolve the pod's network boundary for one outbound call and collides with `readOnlyRootFilesystem`/PSA posture. The node LAN IP (`192.168.100.13`) works but publishes curated knowledge to every device on `192.168.100.0/24`. `10.42.0.1` is reachable *only* from pods on this node and never from the LAN — the tightest address that is actually reachable. `127.0.0.1` is not an option: pod traffic arrives via `cni0`, not loopback. The mTLS proxy is for off-host clients; this traffic never leaves `trantor` (see Open Questions for when that flips). The unit orders on `sys-subsystem-net-devices-cni0.device` so it does not race the interface. |
| D-07 | Router HTTP client | `httpx`/`requests` vs. stdlib `urllib.request` | **`urllib.request`**, identical to `honcho.py`, `cognee.py`, `hindsight.py`. Zero runtime dependencies; the `transport` seam is what tests substitute, so a client library buys nothing. Confirmed, not re-litigated. |
| D-08 | Contract conformance | Extend `MemoryBackend` with an optional `store` vs. a new `SearchOnlyBackend` Protocol | **`SearchOnlyBackend` only.** The class declares no `store`, so `isinstance(backend, MemoryBackend)` is `False` and stays asserted false — it cannot be selected for a verb it does not serve even if a capabilities table were mis-edited. `MemoryBackend` is not touched at all. |

## Interfaces / Contracts

```python
# contracts.py — additive; MemoryBackend byte-identical
@runtime_checkable
class SearchOnlyBackend(Protocol):
    """Narrow contract for read-only backends that serve `search` and
    nothing else. Deliberately NOT MemoryBackend (which mandates
    `store()`), same precedent as ReflectiveBackend: registry verb
    selection is the dispatch gate, not Protocol conformance."""

    def capabilities(self) -> Capabilities: ...
    def health(self) -> Health: ...
    def search(self, req: SearchRequest) -> SearchResult: ...
```

```python
# backends/knowledge_vault.py
ENDPOINTS = {"search": "/search", "health": "/healthz"}
NAMESPACE = "/global"

class KnowledgeVaultBackend:          # SearchOnlyBackend, NOT MemoryBackend
    def __init__(self, *, transport=None, base_url=None, auth_mode=None,
                 token=None, limit=None, timeout=None): ...
    def capabilities(self) -> Capabilities:
        return Capabilities(name="knowledge-vault", verbs=frozenset({"search"}),
                            namespaces=("/global",), hierarchical_search=False)
    def health(self) -> Health: ...   # GET /healthz, never raises
    def search(self, req: SearchRequest) -> SearchResult: ...
```

```python
# search.py — the complete score-surfacing diff (D-01)
 class VaultHit:
     note: str
     title: str
     excerpt: str
+    score: float = 0.0
...
-        hits.append(VaultHit(note.name, fields.get("title") or note.stem, excerpt_text))
+        hits.append(VaultHit(note.name, fields.get("title") or note.stem, excerpt_text, hit.score))
```

Wire format (host-owned, both sides implement it — the single revisable surface):

```
POST /search   Authorization: Bearer <token>
  -> {"query": str, "limit": int}
  <- 200 {"hits": [{"note": "0007-....md", "title": "…", "excerpt": "…", "score": 0.83}]}
  <- 401 {"error": "unauthenticated"}          missing/wrong token
  <- 400 {"error": "invalid_body"}             non-JSON, absent/blank query, non-int limit
  <- 503 {"error": "index_rebuild_timeout"}    D-03
GET /healthz  -> 200 {"status": "ok"}          unauthenticated liveness, touches no vault file
Any other path or method -> 404 / 405. There is no write verb to reach.
```

## Config Surface

| Side | Key | Default | Notes |
|---|---|---|---|
| Host | `KNOWLEDGE_VAULT_DIR` / `KNOWLEDGE_VAULT_INDEX` | existing unit values | Reused verbatim, no new names. |
| Host | `KNOWLEDGE_VAULT_SEARCH_HOST` / `_PORT` | `10.42.0.1` / `8088` | D-06. |
| Host | `KNOWLEDGE_VAULT_SEARCH_TIMEOUT_SECONDS` | `5` | D-02 deadline. |
| Host | `KNOWLEDGE_VAULT_SEARCH_LIMIT_MAX` | `20` | Caller `limit` clamped into `1..MAX`. |
| Host | token | `LoadCredential=search-token:/etc/knowledge-vault/search-token` | Read from `$CREDENTIALS_DIRECTORY`; never an env var, never in the repo. Compared with `hmac.compare_digest`. |
| Router | `KNOWLEDGE_VAULT_BASE_URL` | `http://knowledge-vault-search.mcps.svc.cluster.local:8088` | Router-owned, never caller-supplied. |
| Router | `KNOWLEDGE_VAULT_AUTH_MODE` / `KNOWLEDGE_VAULT_TOKEN` | `bearer` if token set else `none` / `""` | Same three-branch resolution as `honcho.py:95-102`. Distinct from the host's `_DIR`/`_INDEX` names, so no collision. |
| Router | `KNOWLEDGE_VAULT_LIMIT` | `5` | Matches `search_vault`'s own default. |
| Router | `KNOWLEDGE_VAULT_TIMEOUT_SECONDS` | `10` | Must exceed the host's 5s deadline so the host answers `503` rather than the client giving up first. |

Explicit constructor arg > env var > fallback via `_env_default`, so zero-arg construction under `_load_entry_points` works (F-2).

## Data Flow

    POST /memory/search {role, namespace: "/global", query}
      -> _authenticate -> _validate_namespace -> _authorize(verb="search")   # already granted, permissions.py untouched
      -> _fallback_chain("/global") == ["/global"]                           # F-1
      -> registry.backends_for(verb="search", namespace="/global")
             -> [EngramBackend, KnowledgeVaultBackend]                       # both, merged
      -> 200 {"hits": [...engram..., ...knowledge-vault...], "unavailable": []}

    KnowledgeVaultBackend.search
      -> guard namespace == "/global"                    # D-05, else BackendUnavailableError, no HTTP call
      -> POST /search {"query": req.query, "limit": N}
         2xx  -> SearchResult(hits=(SearchHit("/global", "knowledge-vault",
                    f"{note} — {title}\n{excerpt}", score), ...))            # D-04, D-01
         non-2xx / transport error / malformed JSON -> BackendUnavailableError("knowledge-vault", reason)

    serve.py POST /search
      -> bearer check (compare_digest) -> 401 on miss, before any vault read
      -> single-flight lock + executor.submit(search_vault, ...).result(timeout=5)  # D-02
         TimeoutError -> 503 index_rebuild_timeout                                  # D-03
         [] -> 200 {"hits": []}    hits -> 200 {"hits": [...]}

## File Changes

| File | Action | Description |
|---|---|---|
| `hermes-native/knowledge-vault/src/knowledge_vault/serve.py` | Create | Read-only HTTP search surface: `POST /search`, `GET /healthz`, bearer auth, bounded rebuild. |
| `hermes-native/knowledge-vault/src/knowledge_vault/search.py` | Modify | `VaultHit.score` field + one populated argument (D-01). |
| `hermes-native/knowledge-vault/pyproject.toml` | Modify | One `knowledge-vault-search-serve` console script. |
| `hermes-native/knowledge-vault/systemd/knowledge-vault-search.service` | Create | `Type=simple`, `ReadOnlyPaths=` vault+index, **no** `ReadWritePaths=`, `LoadCredential=`, `After=sys-subsystem-net-devices-cni0.device`. |
| `hermes-native/knowledge-vault/tests/test_serve.py` | Create | In-process host tests. |
| `.../memory_router/backends/knowledge_vault.py` | Create | `KnowledgeVaultBackend` + `_HttpJsonClient` + `ENDPOINTS`. |
| `.../memory_router/contracts.py` | Modify | Add `SearchOnlyBackend`; `MemoryBackend` byte-identical. |
| `.../memory_router/{app,registry,permissions}.py` | Unchanged | Zero diff — asserted by test, per F-1/F-2. |
| `hermes-native/memory-router/pyproject.toml` | Modify | One entry-point line under `memory_router.backends`. |
| `kubernetes/mcps/knowledge-vault-search-endpoints.yaml` | Create | Selector-less `Service` + `EndpointSlice` → `10.42.0.1:8088` (D-06). Not applied, same posture as the rest of `kubernetes/mcps`. |
| `tests/test_memory_router_knowledge_vault_adapter.py` | Create | Stubbed-transport router tests. |
| `docs/services/knowledge-vault.md` | Modify | "The 5 systemd units" → 6; config + safety-model rows. |
| `openspec/specs/{memory-router-interfaces,memory-backend-adapters}/` | Modify | Delta specs. |
| `specs/0NN_knowledge_vault_backend.md` | Create | Numbered spec companion. |

## Testing Strategy

No live service on either side. Host tests drive the handler in-process over a temp vault; router tests inject a stub `transport`, mirroring `tests/test_memory_router_cognee_adapter.py`.

| Layer | What to test | Approach |
|---|---|---|
| Host / auth | No `Authorization`, wrong token, and `Bearer` with an empty token each yield `401` **and** perform no vault read | Handler invoked with a `search_vault` stub that fails the test if called |
| Host / no write path | `POST /` , `POST /publish`, `PUT`, `DELETE`, `PATCH` on any path yield 404/405; the module exposes no write helper; the handler class defines no `do_PUT`/`do_DELETE` | Route table assertions + `dir()` assertion |
| Host / response shape | `200` body carries `note`, `title`, `excerpt`, **and a non-zero `score` equal to the underlying `RetrievalHit.score`** — asserted not `0.0` | Temp vault fixture with a known-matching note |
| Host / bounded rebuild | A `search_vault` stub that sleeps past the deadline yields `503 index_rebuild_timeout` within the deadline (not the sleep), and a second concurrent request does **not** start a second rebuild | Fake clock-free: stub sleeps, assert elapsed < sleep; call-count assertion for single-flight |
| Host / honest empty | Empty vault and a query below `MIN_RELEVANCE` both yield `200 {"hits": []}` — asserted **not** `503`, and no fabricated hit | Temp vault fixture |
| Host / read-only | Vault + index file mtimes unchanged across a successful search that does not rebuild | `stat` before/after |
| Router / protocol | `isinstance(b, SearchOnlyBackend)` true; `isinstance(b, MemoryBackend)` **false**; `store`/`reflect` absent via `hasattr`; zero-arg construction succeeds | Direct assertions |
| Router / capabilities (exact) | `verbs == frozenset({"search"})` by equality; `namespaces == ("/global",)` by equality; `hierarchical_search is False`; `name == "knowledge-vault"` | Direct assertions |
| Router / namespace selection | `Registry([KV()]).backends_for(verb="search", ns=…)` selects for `/global`, is **empty** for `/projects/x`, `/agents/x`, `/user/master`; `verb="store"`/`"reflect"` on `/global` empty | Registry injection |
| Router / coexistence (the headline) | `Dispatcher` built with `Registry([FakeEngram(), KnowledgeVaultBackend(transport=stub)])`, both returning one hit for `/global`; assert the response `hits` has length 2 and `{h["backend"] for h in hits} == {"engram", "knowledge-vault"}`. `FakeEngram` is a local `MemoryBackend`-shaped stub declaring `verbs={"store","search"}, namespaces=("/global",)`; the KV stub transport returns a canned `{"hits":[…]}`. Existing Engram `/global` search tests re-run **unmodified** as the regression check | Registry injection into a real `Dispatcher` |
| Router / round trip | `score` from the response reaches `SearchHit.score` — asserted equal to the wire value and **not** `0.0`; `content` contains the note id, the title, and the excerpt; `namespace == "/global"`; `backend == "knowledge-vault"` | Stub transport |
| Router / fail closed (D-05) | `SearchRequest(namespace="/user/master")` raises `BackendUnavailableError("knowledge-vault", …)` and issues **no** HTTP call | Transport that fails the test if invoked |
| Router / degradation | Connection error (`OSError`/`URLError`), non-2xx, `503`, malformed JSON each raise `BackendUnavailableError`; through the dispatcher they land in `unavailable[]` with hits still returned from Engram — never a request failure | Raising/garbage stub |
| Router / secrets | `KNOWLEDGE_VAULT_TOKEN` substring absent from every raised `reason` and every dispatcher payload; `Authorization` present in `bearer` mode, absent in `none` | Token-substring assertion |
| Router / outbound construction | A hostile `query` appears only in the JSON body — never in the URL, never in a header; `limit` is always an int the adapter chose, never caller-supplied; timeout always set | Stub transport inspects `url`/`headers` |
| Regression | `contracts.MemoryBackend` unchanged; `app.py`/`registry.py`/`permissions.py` unchanged | Existing suites re-run unmodified |
| Integration | Live pod → `10.42.0.1:8088` reachability | **Not performed** — memory-router is undeployed. Explicit follow-up. |

## Threat Matrix

| Boundary | Applicability | Design response | Planned RED tests |
|---|---|---|---|
| New network surface over curated knowledge | Applicable — the headline risk | Two routes only; bearer auth before any vault read; bound to `10.42.0.1`, unreachable from the LAN (D-06) | Unauthenticated `401` with zero vault reads; non-route methods 404/405 |
| Write path / vault mutation | Applicable | No write verb exists in code; unit has `ReadOnlyPaths=` for vault+index and **no** `ReadWritePaths=`; publisher remains sole writer | No `do_PUT`/`do_DELETE`; mtimes unchanged after a search |
| Namespace routing | Applicable | Single fixed `/global`; adapter re-guards fail-closed with no mapper to get wrong (D-05) | Non-`/global` request raises and issues no HTTP call |
| Authorization | Applicable, unchanged | `search` on `global` already granted to all three roles; `permissions.py` zero diff | Existing permission suite re-run unmodified |
| Outbound request construction | Applicable | URL from router-owned config only; caller `query` in the JSON body; fixed header set; `limit` router-chosen and host-clamped | Hostile query absent from URL and headers |
| Secret handling | Applicable | Host token via systemd credential (never a repo file, never an env var); router token via env; neither logged nor echoed in a reason | Token substring absent from all reasons/responses |
| Denial of service via rebuild | Applicable | Deadline-bounded rebuild + single-flight lock (D-02); `503`, not a hung request (D-03) | Slow-rebuild stub returns `503` inside the deadline; concurrent requests start one rebuild |
| Subprocess / VCS / PR automation / executable classification | N/A — HTTP and in-process calls only, no shell, no VCS | — | None |

## Migration / Rollout

No data migration, no stored state, no vault-side cleanup. Merging the code changes nothing at runtime: the entry-point line activates the adapter, and until `knowledge-vault-search.service` is enabled every `/global` search simply reports knowledge-vault in `unavailable[]` while Engram's hits still return — degraded, not broken. Rollback is three independent reverts (entry point + adapter file, the `SearchOnlyBackend` addition, `systemctl disable --now`), each safe alone.

## Open Questions

- **The 5s rebuild deadline is a starting value, not a measured one.** Nobody has timed `build_index` against the real `/opt/knowledge-vault/vault`, whose size is unknown here. If a cold rebuild exceeds 5s the first query after a publish always returns `503` and the second succeeds — acceptable, but the number should be tuned from one measurement on `trantor`, and a `knowledge-vault-index.timer` that rebuilds after each publish would remove the inline path entirely (a strictly better future state, out of scope here).
- **mTLS if the router ever moves off `trantor`.** D-06 is correct only while both sides share the node. If memory-router is ever scheduled elsewhere, `10.42.0.1` stops resolving to the right host and bearer-over-plaintext stops being adequate — that is the trigger to adopt the full Engram Cloud mTLS proxy pattern, not a gradual degradation. Worth an explicit comment in the manifest so the coupling is not discovered by outage.
- **`10.42.0.1` is flannel-specific.** Verified from `knowledge-proposals/networkpolicy.yaml`, but a CNI change would move it. The address is a single unit env var, so the blast radius is one line — noted so it is not treated as a constant of nature.
- **The `mcps` namespace has no NetworkPolicy today (F-6).** So no egress rule is needed for this to work now. If `mcps` ever gains a default-deny, an egress allow to `10.42.0.1/32:8088` becomes a hard prerequisite, and its absence will look like a knowledge-vault outage.
- **Cross-backend score comparability is explicitly out of scope.** Engram's score and this lexical/semantic blend are not on a common scale; a caller that naively sorts merged hits will interleave incomparable numbers. `SearchHit.backend` is the honest discriminator until someone owns normalization.
- **`limit` semantics under merge.** The adapter asks for 5 and Engram returns its own count; nobody caps the merged total. Fine at current scale, a ranking question later.
