# Design: Graphiti Backend Adapter (Reflect on `/global` and `/agents/*`)

## Technical Approach

One new file, three permission-row edits, one entry-point line. `backends/graphiti.py` clones `backends/cognee.py` verbatim — `_env_default`, `_default_transport`, `_HttpJsonClient`, a single `ENDPOINTS` dict, an `_ID_RE`-revalidated namespace→identifier mapper — and swaps two things: `_dataset_id()` becomes `_group_id()` (two namespace kinds, not one), and the wire surface points at Graphiti's `search_facts` instead of `/recall`. `GraphitiBackend` implements `ReflectiveBackend` structurally (Protocol convention, no inheritance) and declares `namespaces=("/global", "/agents/*")`, disjoint from Honcho's `("/user/master",)` and Cognee's `("/projects/*",)`.

`contracts.py`, `app.py`, and `registry.py` take **zero** diff — verified below.

## Verified Findings (read from current code)

- **F-1 — `app.py` already maps `empty`.** `app.py:245-246` contains `elif result.status == "empty" and status not in ("ready", "pending"): status = "empty"`, landed by cognee-backend (its D-06). Graphiti's `empty` is observable with no dispatcher change. Precedence today: `ready` > `pending` > `empty` > `degraded` > `no_backend`.
- **F-2 — nested `/agents/a/b` is unreachable.** `namespaces.py:3` `_NAME_RE = ^[a-zA-Z0-9][a-zA-Z0-9_.-]*$` has no `/` in its class; `validate_namespace` (`namespaces.py:40-46`) matches the whole post-prefix remainder, so `/agents/a/b` raises `NamespaceError` → `400 invalid_namespace`, before permissions, registry, or adapter. Closes proposal question 6 by code, not by comment. Corollary: `_NAME_RE` **does** admit uppercase and `.`, so `/agents/Jarvis.v2` is a legal namespace — drives D-03.
- **F-3 — `registry.backends_for` gates purely on `capabilities()` via `fnmatch`**, never `isinstance`; `/agents/foo` matches `/agents/*` with no code change. `/global` is an exact literal pattern, also fine.
- **F-4 — `permissions.py` `agents_other` for `jarvis` is `frozenset({"store","search"})`** today (`permissions.py:55`), and `coder`/`scientist` `agents_other` are empty frozensets. Deny-by-default flows through `.get(kind, frozenset())`.

## Architecture Decisions

| # | Decision | Options / tradeoff | Choice and rationale |
|---|---|---|---|
| D-01 | `/agents/*` → group scope (proposal Q2) | One group per agent vs one shared agents group | **Per-agent group, fail closed** (proposed default adopted). A shared group makes cross-agent isolation depend on a query-side filter being right on every call; one regression leaks agent B into agent A's reflect, laundered through an LLM-derived fact so it is unattributable. Per-agent groups make isolation structural. Cost: no cross-agent synthesis — deliberately traded, and `agents_other` reflect stays `jarvis`-only anyway. |
| D-02 | Group-id shape and injectivity | `{prefix}{name}` for both kinds vs distinct infixes | **`{prefix}global` for `/global`, `{prefix}agent-{name}` for `/agents/{name}`.** Correctness point, not cosmetics: a single flat scheme lets an agent literally named `global` map onto the shared global group. With the `agent-` infix, `p+"agent-"+name == p+"global"` is unsatisfiable, so the mapping is injective across both kinds by construction. |
| D-03 | Sanitizer: rewrite vs reject | Case-fold/substitute illegal chars vs accept only already-legal ids | **Reject, never rewrite** (matching cognee D-03). Rewriting is non-injective: `.lower()` collapses `/agents/Foo` and `/agents/foo` onto one group — exactly the leak D-01 exists to prevent. Since `_NAME_RE` admits uppercase and `.` (F-2), this is live, not theoretical. `_GROUP_RE = ^[a-z0-9][a-z0-9_-]*$`, applied to the **whole prefixed id**, so a malformed `GRAPHITI_GROUP_PREFIX` also fails closed. Cost: `/agents/Jarvis.v2` cannot reflect until the charset is verified wider or the agent is renamed — the safe failure direction. |
| D-04 | Failure mode of a rejected mapping | `ValueError` vs `BackendUnavailableError` | **`BackendUnavailableError("graphiti", …)`.** `Dispatcher.reflect` catches only that (`app.py:229`); a bare `ValueError` escapes as an unhandled 500 instead of `degraded`. Reason strings never echo the namespace or token. |
| D-05 | `search_facts` vs `search_nodes` (Q3) | Facts (edges, temporal) vs nodes (entity summaries) vs both | **Facts only in slice one** (proposed default adopted). A fact is a relationship statement — a derived conclusion. A node summary is a record, which is `search` semantics and would blur the verb boundary this whole matrix exists to keep sharp. `ENDPOINTS` makes adding nodes later a one-line surface change. |
| D-06 | Temporal validity (Q4) | Filter expired vs inline the interval in `content` | **Return only currently-valid facts** (proposed default adopted), filtered client-side: drop any fact whose `invalid_at` is non-null. Inlining intervals would smuggle a time dimension into free text that `Conclusion` cannot type, and the proposal explicitly defers a typed time field. **Sub-decision (correctness):** if filtering removes every fact, the result is `empty`, never `ready` with `conclusions == ()` — a `ready` with nothing is the fabricated-shape failure the success criteria forbid. |
| D-07 | Confidence (Q5) | Invent a number vs `0.0` | **`Conclusion(confidence=0.0)`** (proposed default adopted). Graphiti returns relevance rank, not calibrated confidence; any nonzero value is manufactured. `0.0` reads as "unscored", matching Cognee. |
| D-08 | Nested namespaces (Q6) | Map `/agents/a/b` to parent `a`'s group vs reject | **Reject** — mild deviation from the question's "reflect against the parent agent's group" phrasing, justified: F-2 proves such a string cannot reach the adapter, and mapping a child to a parent group would be a deliberately non-injective rewrite, contradicting D-03. `_group_id()` re-rejects embedded `/`, `..`, `*`, `?` as defense in depth. |
| D-09 | Multiple facts → conclusions | Concatenate into one `Conclusion` vs one per fact | **One `Conclusion` per surviving fact**, order preserved from the response. `ReflectResult.conclusions` is already a tuple; concatenation would destroy per-fact attribution for no gain. |
| D-10 | HTTP client | `httpx`/`requests` vs stdlib `urllib.request` | **`urllib.request`**, identical to `honcho.py`/`cognee.py`. Zero runtime dependencies; the injectable `transport(method, url, headers, body) -> (status, bytes)` seam is what tests substitute. |
| D-11 | Contract conformance | Extend `MemoryBackend` vs `ReflectiveBackend` only | **`ReflectiveBackend` only.** No `store`/`search` methods exist, so `isinstance(GraphitiBackend(), MemoryBackend)` is `False` and stays asserted false — it cannot be selected for a verb it does not serve even if a capabilities table regressed. |

## Interfaces / Contracts

```python
# backends/graphiti.py — single revisable wire surface (UNVERIFIED, see Open Questions)
ENDPOINTS = {
    "search_facts": "/search/facts",   # POST {query, group_ids:[id], max_facts} -> {facts: [...]}
    "health": "/healthz",
}
MAX_FACTS = 10

_GROUP_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

class GraphitiBackend:   # implements ReflectiveBackend, NOT MemoryBackend
    def __init__(self, *, transport=None, base_url=None, auth_mode=None,
                 token=None, group_prefix=None, timeout=None): ...
    def capabilities(self) -> Capabilities:
        return Capabilities(name="graphiti", verbs=frozenset({"reflect"}),
                            namespaces=("/global", "/agents/*"), hierarchical_search=False)
    def health(self) -> Health: ...          # never raises
    def reflect(self, req: ReflectRequest) -> ReflectResult: ...
```

Namespace → group mapping (D-01, D-02, D-03, D-04, D-08):

```python
def _group_id(self, namespace: str) -> str:
    if namespace == "/global":
        suffix = "global"
    elif namespace.startswith("/agents/"):
        agent = namespace[len("/agents/"):]
        if not agent or "/" in agent or ".." in agent or "*" in agent or "?" in agent:
            raise BackendUnavailableError("graphiti", "namespace does not yield a legal group id")
        suffix = f"agent-{agent}"          # infix keeps the mapping injective (D-02)
    else:
        raise BackendUnavailableError("graphiti", "namespace is not reflect-capable for graphiti")
    group = f"{self._group_prefix}{suffix}"   # NO case-folding, NO substitution (D-03)
    if not _GROUP_RE.match(group):
        raise BackendUnavailableError("graphiti", "namespace does not yield a legal group id")
    return group
```

Fact → conclusion (D-06, D-07, D-09):

```python
facts = self._decode(raw).get("facts") or []
live = [f for f in facts if not f.get("invalid_at")]
if not live:
    return ReflectResult(status="empty", backend="graphiti")
conclusions = tuple(
    Conclusion(namespace=req.namespace, backend="graphiti",
               content=str(f.get("fact") or "").strip(), confidence=0.0)
    for f in live if str(f.get("fact") or "").strip()
)
```

(An entry whose `fact` text is blank is dropped by the same rule; if that empties the tuple, return `empty`.)

## Config Surface

| Env var | Default | Notes |
|---|---|---|
| `GRAPHITI_BASE_URL` | `http://graphiti.mcps.svc.cluster.local:8000` | Router-owned; never caller-supplied. |
| `GRAPHITI_AUTH_MODE` | `bearer` if token set, else `none` | Same three-branch resolution as `cognee.py:96-103`. |
| `GRAPHITI_TOKEN` | `""` | Env only. Never logged, never in an error reason. |
| `GRAPHITI_GROUP_PREFIX` | `jarvis-` | Whole prefixed id revalidated against `_GROUP_RE`. |
| `GRAPHITI_TIMEOUT_SECONDS` | `10` | |
| `GRAPHITI_MAX_FACTS` | `10` | Bounds response size and LLM/graph cost per reflect. |

Explicit constructor arg > env var > fallback via `_env_default`, so zero-arg construction under `Registry._load_entry_points()` works.

## Data Flow

    POST /memory/reflect  {role, namespace: "/agents/scientist", query}
      -> _authenticate -> _validate_namespace     # F-2: "/agents/a/b" dies here, 400
      -> _authorize(verb="reflect")               # kind = agents_self | agents_other
      -> registry.backends_for(verb="reflect", namespace="/agents/scientist")
           fnmatch -> [GraphitiBackend]; Honcho and Cognee NOT selected
      -> 200 {"namespace", "status", "conclusions", "unavailable"}

    GraphitiBackend.reflect -> _group_id(ns) -> POST /search/facts
        {"query": req.query, "group_ids": [group], "max_facts": MAX_FACTS}
        2xx + >=1 currently-valid fact -> ReflectResult("ready", "graphiti", (Conclusion, ...))
        2xx + no facts / all expired    -> ReflectResult("empty", "graphiti")     # D-06
        non-2xx / transport error / malformed JSON -> BackendUnavailableError -> "degraded"
        illegal group mapping           -> BackendUnavailableError                # D-04
    health -> GET /healthz -> OK | DOWN(reason)   # never raises

Coexistence is by construction: the three reflect adapters' namespace patterns are pairwise disjoint, so `backends_for` returns exactly one for any validated namespace. No adapter observes another.

## Permissions

Exact `_ROLE_TABLE` diff (`permissions.py:43-56`) — three lines, matching the user-resolved grants:

```python
     "scientist": {
-        "global": frozenset({"store", "search"}),
+        "global": frozenset({"store", "search", "reflect"}),
-        "agents_self": frozenset({"store", "search"}),
+        "agents_self": frozenset({"store", "search", "reflect"}),
     "jarvis": {
-        "global": frozenset({"store", "search"}),
+        "global": frozenset({"store", "search", "reflect"}),
-        "agents_self": frozenset({"store", "search"}),
+        "agents_self": frozenset({"store", "search", "reflect"}),
-        "agents_other": frozenset({"store", "search"}),
+        "agents_other": frozenset({"store", "search", "reflect"}),
```

(Five row edits across two roles; `jarvis` needs both `global` and `agents_self` too, since only `scientist` and `jarvis` receive the grant.)

| Role | `global` | `agents_self` | `agents_other` |
|---|---|---|---|
| `coder` | `{"search"}` — **unchanged**, reflect denied | `{"store","search"}` — unchanged, reflect denied | empty, denied |
| `scientist` | `+reflect` | `+reflect` | empty, denied |
| `jarvis` | `+reflect` | `+reflect` | `+reflect` |

`coder`'s rows are not touched. `_namespace_kind` needs no change: `/global` and `/agents/…` already resolve (`permissions.py:21-29`).

## File Changes

| File | Action | Description |
|---|---|---|
| `.../memory_router/backends/graphiti.py` | Create | `GraphitiBackend` + `_HttpJsonClient` + `ENDPOINTS` + `_group_id`. |
| `.../memory_router/permissions.py` | Modify | Five rows across `scientist`/`jarvis` gain `reflect`. |
| `.../memory_router/contracts.py` | Unchanged | Zero diff (asserted in tests). |
| `.../memory_router/app.py` | Unchanged | Zero diff — `empty` mapping already present (F-1). |
| `.../memory_router/registry.py` | Unchanged | Zero diff (F-3). |
| `hermes-native/memory-router/pyproject.toml` | Modify | One entry-point line under `memory_router.backends`. |
| `tests/test_memory_router_graphiti_adapter.py` | Create | Stubbed-transport tests. |
| `tests/` permissions + dispatcher | Modify | `global`/`agents_*` reflect allow/deny; three-way coexistence. |
| `openspec/changes/graphiti-backend/specs/{memory-access-control,memory-backend-adapters}/` | Modify/Create | Delta specs. |
| `specs/019_graphiti_backend.md` | Create | Numbered spec companion. |

## Testing Strategy

No live Graphiti, no graph DB, no LLM key. Every test injects a stub `transport`, mirroring `tests/test_memory_router_cognee_adapter.py`.

| Layer | What to test | Approach |
|---|---|---|
| Protocol conformance | `isinstance(…, ReflectiveBackend)` true; `isinstance(…, MemoryBackend)` **false**; zero-arg construction succeeds | Direct assertions |
| Capabilities (exact) | `verbs == frozenset({"reflect"})` by equality; `"store"`/`"search"` asserted absent; `namespaces == ("/global", "/agents/*")` by equality; `hierarchical_search is False` | Direct assertions |
| Selection | Selected for `/global` and `/agents/foo`; **empty** for `/user/master` and `/projects/x` | Registry injection |
| Coexistence | `Registry([Honcho(), Cognee(), Graphiti()])`: each validated namespace selects exactly one; existing Honcho/Cognee tests re-run unmodified | Registry injection |
| Group mapping (D-02) | `/global` → `jarvis-global`; `/agents/scientist` → `jarvis-agent-scientist`; prefix override honored; `/agents/global` does **not** collide with `/global` | Stub captures POST body |
| Fail-closed (D-03/D-04/D-08) | `..`, `*`, `?`, embedded `/`, empty name, uppercase (`Foo`), dot (`a.b`), `/user/master`, malformed prefix each raise `BackendUnavailableError("graphiti", …)` and issue **no HTTP call** | Transport that fails the test if invoked |
| Round trip (D-09) | 2xx + N facts → `status == "ready"`, N conclusions in order, `confidence == 0.0`, namespace echoed, `backend == "graphiti"`; body carries exactly one `group_id` and `max_facts` | Stub transport |
| Temporal filter (D-06) | Mixed valid/`invalid_at` facts → only valid returned; **all** expired → `status == "empty"`, `conclusions == ()`, never `ready` and never `pending`; blank fact text dropped | Stub transport |
| Empty graph | 2xx with `{"facts": []}` / absent key → `empty`; no fabricated content anywhere in the payload | Stub transport |
| Degradation | `OSError`/`URLError`, non-2xx, malformed JSON → `BackendUnavailableError`; dispatcher reports `unavailable` + `status == "degraded"`, not a request failure | Raising/garbage stub |
| Secrets | `GRAPHITI_TOKEN` substring absent from every reason and payload; `Authorization` present in `bearer` mode, absent in `none` | Substring assertion |
| Outbound construction | Hostile `query` only in the JSON body — never in URL or headers; timeout always set | Stub inspects `url`/`headers` |
| Permissions | `scientist`+`jarvis` allowed reflect on `/global` and own agent ns; `coder` → `AuthorizationError` → `403`; `scientist` denied on another agent's ns; `jarvis` allowed there; `coder`'s existing verbs unaffected | Table-driven |
| Validation (F-2) | `validate_namespace("/agents/a/b")` raises `NamespaceError`; dispatcher returns `400 invalid_namespace` | Direct assertion |
| Matrix closure | Every fixed root now has ≥1 reflect-capable backend under the full registry | Registry injection |
| Integration | Live Graphiti `search_facts` | **Not performed** — no instance, no graph DB, no LLM key. Explicit follow-up. |

## Threat Matrix

| Boundary | Applicability | Design response | Planned RED tests |
|---|---|---|---|
| Cross-agent isolation | Applicable — headline risk | Per-agent group (D-01); injective by rejection, never rewriting (D-03); `agent-` infix prevents collision with `/global` (D-02) | Two namespaces never yield one group id; `/agents/global` ≠ `/global` group; case/dot variants fail closed |
| Namespace routing | Applicable | Nested namespaces dead at validation (F-2); adapter re-rejects `/`, `..`, wildcards | Traversal/wildcard yields no group and no HTTP call |
| Authorization | Applicable | Explicit rows for two roles only; `agents_other` reflect `jarvis`-only; deny-by-default elsewhere untouched | `coder` denied on `/global`; `scientist` denied on `agents_other` |
| Outbound request construction | Applicable | URL from router-owned config + sanitized group only; caller `query` in JSON body; fixed header set; timeout always set | Hostile query absent from URL and headers |
| Secret handling | Applicable | Token from env only; never logged; reasons carry no token and no caller data | Token substring absent from all reasons/responses |
| Derived-fact staleness | Applicable | Expired facts filtered (D-06); no interval smuggled into free text | All-expired → `empty`, not `ready` |
| LLM / graph cost | Applicable, deferred | Read-only path; no ingestion in this slice, so no write-side LLM cost; `MAX_FACTS` bounds response size | None — ops precondition before provisioning |
| Subprocess / shell / VCS / PR automation / executable classification | N/A — HTTP only, no shell, no VCS | — | None |

## Delivery Forecast

Estimated authored change: `graphiti.py` ~210, tests ~430, permissions ~5, `pyproject.toml` 1, delta specs ~90, `specs/019` ~330. **~1,050 changed lines**, of which roughly 420 are non-test code+spec prose and 430 are mechanical stub-transport tests.

`400-line budget risk: High` by raw count — but this is the same shape and roughly the same size as cognee-backend (adapter 202 + tests 397 + `specs/017` 340), which shipped as a single PR with an accepted size exception, and as the other three backend adapters. The change is purely additive, registration-only, and reverts by deleting three files plus five table rows.

**Recommendation: single PR with an explicit `size:exception`**, consistent with the four prior backends; reviewer load is genuinely lower than the line count implies because the adapter is a near-verbatim clone of a reviewed file and the tests are repetitive table-driven cases. Fallback split if the exception is declined, in dependency order: (1) adapter + entry point + adapter tests; (2) permissions rows + permission/dispatcher tests + delta specs + `specs/019`. `sdd-tasks` owns the binding guard lines.

## Migration / Rollout

No data migration, no stored state, no Graphiti-side cleanup (the adapter never writes). Adding the entry-point line activates reflect on `/global` and `/agents/*`; removing `graphiti.py`, its test, and the entry-point line returns `backends_for(verb="reflect", namespace="/global")` to empty and reflect to `no_backend` — the pre-change behavior, not a crash. Reverting the five `permissions.py` rows completes the rollback.

## Open Questions

- [ ] Graphiti's HTTP surface is **unverified**: path (`/search/facts` vs an MCP-style tool call), request keys (`group_ids` vs `group_id`, `max_facts` vs `limit`), and response shape (`{facts: [{fact, valid_at, invalid_at, uuid}]}` vs a bare list). `ENDPOINTS` + `_HttpJsonClient` + the fact-mapping block are the single revisable surface.
- [ ] Whether Graphiti server-side supports a "currently valid only" filter; if so, D-06's client-side filter becomes a request parameter and the client-side filter stays as belt-and-braces.
- [ ] Graphiti's legal `group_id` charset is unverified. `_GROUP_RE` is deliberately conservative; widening must stay injective (D-03).
- [ ] Whether groups are provisioned up front or created lazily, and what a search against a nonexistent group returns (404 → `degraded`, or 200-empty → `empty`).
- [ ] No ingestion path exists (`add_episode` is out of scope), so a real deployment plausibly returns `empty` in practice until one is built. That is the honest first-slice behavior, not a defect.
- [ ] `search_nodes` (D-05) remains unclaimed. If entity summaries are later wanted, decide whether they are reflect conclusions or a `search` concern before adding them.
- [ ] `honcho.py`'s `_peer_ref` still raises a bare `ValueError` the dispatcher does not catch — a latent 500. Graphiti avoids it (D-04); fixing Honcho stays a separate follow-up.
