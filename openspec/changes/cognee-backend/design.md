# Design: Cognee Backend Adapter (Reflect on `/projects/*`)

## Technical Approach

One new file plus two table rows. `backends/cognee.py` copies `backends/honcho.py`'s shape verbatim — `_env_default`, `_default_transport`, `_HttpJsonClient`, a single `ENDPOINTS` dict, an `_ID_RE`-revalidated namespace→identifier mapper — and swaps two things: `_peer_ref()` becomes `_dataset_id()` (project name → Cognee dataset), and the wire surface points at `/recall` with `search_type=GRAPH_COMPLETION` instead of Dialectic `/chat`. `CogneeBackend` implements `ReflectiveBackend` structurally (no inheritance, matching the codebase's Protocol convention) and declares `namespaces=("/projects/*",)`, disjoint from Honcho's `("/user/master",)`.

`contracts.py` and `registry.py` need **zero** diff: `ReflectRequest`/`ReflectResult`/`Conclusion`/`ReflectiveBackend` already exist and fit, and `Registry.backends_for` gates purely on `capabilities()` via `fnmatch`, never `isinstance`, so `/projects/foo` matches `/projects/*` with no code change. `permissions.py` gains `reflect` on two `projects` rows. `app.py` is docstring-only **plus one contested three-line mapping** — see D-06, the one place where this design does not agree with the proposal's "docstring only" claim.

## Verified Findings (read from the code, not assumed)

### F-1 — Nested namespaces `/projects/a/b` are unreachable. Definitively.

`namespaces.py:3` is `_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")`. The character class is `[a-zA-Z0-9_.-]` — it does **not** contain `/`. `validate_namespace` (`namespaces.py:40-46`) strips the `/projects/` prefix and matches the entire remainder against `_NAME_RE` with `.match()` against a pattern anchored at both ends by `^…$`. So `/projects/a/b` yields `name == "a/b"`, `_NAME_RE.match("a/b")` returns `None`, and the call raises `NamespaceError`, which `Dispatcher._validate_namespace` converts to `400 invalid_namespace`.

**Conclusion:** proposal open question 4 is closed. Nested project namespaces are already rejected at the validation layer, before permissions, before the registry, before any adapter. The `fnmatch` `/projects/*` pattern *would* match `/projects/a/b`, but no such string can ever reach `Registry.backends_for` through the dispatcher. Cognee needs no special nesting handling; `_dataset_id()` still fails closed on an embedded `/` as defense in depth, for the same reason `_peer_ref` does.

Corollary: `_NAME_RE` **does** admit uppercase letters and `.` (`/projects/Jarvis.v2` is a legal namespace). That drives D-03.

### F-2 — `Dispatcher.reflect()` silently discards `ReflectResult(status="empty")`.

`app.py:222-245`: `status` initializes to `"no_backend"` and is only reassigned on `result.status == "ready"` or `result.status == "pending"`. An adapter returning `"empty"` matches neither branch, adds nothing to `unavailable`, and the response reports `{"status": "no_backend"}` — which is a lie: a backend *was* selected, *was* reached, and *did* answer. Honcho never exercises this because it only ever emits `ready`/`pending`. Cognee, per the approved decision, emits `empty`. See D-06.

## Architecture Decisions

| # | Decision | Options / tradeoff | Choice and rationale |
|---|---|---|---|
| D-01 | Namespace → Cognee scope | One dataset per project vs one shared dataset filtered by node set | **One dataset per project, fail closed.** Pre-decided in the proposal round; confirmed here. A shared graph makes isolation depend on a query-side filter being correct on every call — a single filter regression leaks project B into a reflect on project A, and `GRAPH_COMPLETION` launders the leak through an LLM so it is unattributable and unauditable. Dataset separation makes isolation structural: the wrong dataset is simply not reachable by that request. Cost: no cross-project synthesis (a real capability loss, deliberately traded for isolation) and a per-project provisioning step once a live instance exists. |
| D-02 | Empty-graph result status | `pending` (Honcho's value) vs `empty` vs `ready` with a placeholder conclusion | **`empty`.** Honcho's Dialectic is asynchronous, so `202`/empty-body genuinely means "ask again later" — `pending` is the honest word. Cognee's `/recall` is **synchronous**: the call completed, the graph was consulted, and it had nothing. Re-using `pending` would tell callers to retry a query whose answer will not change without an ingestion pass (which this slice does not build). `ready` with a synthesized placeholder is fabrication and is forbidden by the proposal's success criteria. `"empty"` is already in the `ReflectResult.status` docstring union (`contracts.py:97`), so this is a documented value, not an invention. |
| D-03 | Sanitizer: rewrite vs reject | Lower/substitute illegal chars (Honcho's `.lower()` approach) vs accept only already-legal names | **Reject, never rewrite.** Rewriting is not injective: `.lower()` collapses `/projects/Foo` and `/projects/foo` onto one dataset, and mapping `.`→`_` collapses `/projects/a.b` and `/projects/a_b`. Both are exactly the cross-project leakage D-01 exists to prevent, arriving through the back door. Since `_NAME_RE` admits uppercase and `.` (F-1 corollary), this is a live case, not theoretical. So `_dataset_id()` validates and passes through; a project name that is not already a legal dataset id fails closed. Cost: `/projects/Jarvis.v2` cannot reflect until either Cognee's charset is verified to be wider or the project is renamed. That is the safe failure direction. |
| D-04 | Failure mode of a rejected mapping | `ValueError` (Honcho's `_peer_ref` behavior) vs `BackendUnavailableError` | **`BackendUnavailableError("cognee", …)`.** `Dispatcher.reflect` catches only `BackendUnavailableError` (`app.py:226`); a bare `ValueError` escapes the handler entirely and surfaces as an unhandled 500, not as a degraded backend. Honcho's `_peer_ref` raising `ValueError` is a latent 500 of the same kind — noted as a follow-up, deliberately not fixed here to keep this change additive. |
| D-05 | Confidence value | Invent a number vs omit vs `0.0` | **`Conclusion(confidence=0.0)`**, the dataclass default. `GRAPH_COMPLETION` returns synthesized prose with no score; any nonzero number would be manufactured. `0.0` reads as "unscored". If a future response shape carries a real score, `float(result.get("confidence", 0.0))` (Honcho's line) picks it up with no contract change. |
| D-06 | Making `empty` observable | Accept F-2 and let `empty` surface as `no_backend` vs add three lines to `Dispatcher.reflect` vs return `pending` from the adapter | **Add the mapping to `app.py`** (see Interfaces). This contradicts the proposal's "`app.py` docstring only / zero functional diff" claim, which F-2 shows is not achievable together with the approved `empty` decision — the two are in direct conflict. Returning `pending` instead would silently reverse D-02. Accepting `no_backend` would make the router report "nothing claims this namespace" when a backend answered, breaking the success criterion "never a generic failure". **This is a scope delta requiring orchestrator/user confirmation before `sdd-tasks`.** If it is rejected, D-02 must be re-opened, not quietly worked around. |
| D-07 | HTTP client | `httpx`/`requests` vs stdlib `urllib.request` | **`urllib.request`**, identical to `honcho.py` and `hindsight.py`. The package keeps zero runtime dependencies; the `transport(method, url, headers, body) -> (status, bytes)` seam is what tests substitute, so no client library buys anything. |
| D-08 | Contract conformance | Extend `MemoryBackend` vs implement `ReflectiveBackend` only | **`ReflectiveBackend` only.** No `store`/`search` methods exist on the class, so `isinstance(CogneeBackend(), MemoryBackend)` is `False` and stays asserted false — the class cannot accidentally be selected for a verb it does not serve, independent of the capabilities table. |

## Interfaces / Contracts

```python
# backends/cognee.py — single revisable wire surface (UNVERIFIED, see Open Questions)
ENDPOINTS = {
    "recall": "/recall",     # POST {query, search_type, datasets:[id]} -> {result|answer: str}
    "health": "/healthz",
}
SEARCH_TYPE = "GRAPH_COMPLETION"   # graph-synthesized answer, not CHUNKS retrieval

_DATASET_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

class CogneeBackend:   # implements ReflectiveBackend, NOT MemoryBackend
    def __init__(self, *, transport=None, base_url=None, auth_mode=None,
                 token=None, dataset_prefix=None, timeout=None): ...
    def capabilities(self) -> Capabilities:
        return Capabilities(name="cognee", verbs=frozenset({"reflect"}),
                            namespaces=("/projects/*",), hierarchical_search=False)
    def health(self) -> Health: ...          # never raises
    def reflect(self, req: ReflectRequest) -> ReflectResult: ...
```

Namespace → dataset mapping (D-01, D-03, D-04):

```python
def _dataset_id(self, namespace: str) -> str:
    # Fail closed. namespaces.py already rejects traversal, wildcards and
    # embedded "/" (design F-1); this is defense in depth, mirroring
    # honcho.py's _peer_ref and hindsight.py's _bank_id.
    prefix = "/projects/"
    if not namespace.startswith(prefix):
        raise BackendUnavailableError("cognee", "namespace is not a project namespace")
    project = namespace[len(prefix):]
    if not project or "/" in project or ".." in project or "*" in project or "?" in project:
        raise BackendUnavailableError("cognee", "namespace does not yield a legal dataset id")
    dataset = f"{self._dataset_prefix}{project}"      # NO case-folding, NO substitution (D-03)
    if not _DATASET_RE.match(dataset):
        raise BackendUnavailableError("cognee", "namespace does not yield a legal dataset id")
    return dataset
```

The rejection message never echoes the namespace or the token — error reasons stay caller-controlled-data-free.

Contested `app.py` addition (D-06), the complete functional diff:

```python
            elif result.status == "pending" and status != "ready":
                status = "pending"
+           elif result.status == "empty" and status not in ("ready", "pending"):
+               status = "empty"
```

Precedence: `ready` > `pending` > `empty` > `degraded` > `no_backend`. Honcho's existing behavior is bit-for-bit unchanged because it never emits `empty`.

Corrected `Dispatcher.reflect()` docstring (replaces `app.py:199-204`):

```
"""Read-oriented derived-conclusion query over a single validated
namespace. Runs the exact same identity -> namespace -> permission
pipeline as store/search/context (never bypassed), authorizing the
"reflect" verb. Mirrors `context()` — one namespace, no
`_fallback_chain`: a derived conclusion is scoped to the namespace it
was derived from and is never inherited from a parent. Which
namespaces are reflect-capable is a registry/capabilities question,
not a dispatcher one; the dispatcher is namespace-agnostic here.
"""
```

The old wording ("single `/user/master` namespace… since `/user/master` has no parent") hard-codes a fact that stops being true the moment `/projects/*` also reflects, and it justified the no-fallback rule with the wrong reason. The replacement states the real invariant: conclusions are not inherited, whatever the namespace.

## Config Surface

| Env var | Default | Notes |
|---|---|---|
| `COGNEE_BASE_URL` | `http://cognee.mcps.svc.cluster.local:8000` | Router-owned; never caller-supplied. |
| `COGNEE_AUTH_MODE` | `bearer` if token set, else `none` | Same three-branch resolution as `honcho.py:95-102`. |
| `COGNEE_TOKEN` | `""` | Env only. Never logged, never in an error reason. |
| `COGNEE_DATASET_PREFIX` | `jarvis-` | Prefixed then whole-string revalidated against `_DATASET_RE`, so a malformed prefix fails closed too. |
| `COGNEE_TIMEOUT_SECONDS` | `10` | |

Explicit constructor arg > env var > fallback, via `_env_default` — identical to `honcho.py`, so zero-arg construction under `Registry._load_entry_points()` works.

## Data Flow

    POST /memory/reflect  {role, namespace: "/projects/hermes", query}
      -> _authenticate -> _validate_namespace   # F-1: "/projects/a/b" dies here, 400
      -> _authorize(verb="reflect")             # scientist|jarvis allow, coder 403
      -> registry.backends_for(verb="reflect", namespace="/projects/hermes")
           fnmatch("/projects/hermes", "/projects/*") -> [CogneeBackend]
           fnmatch("/projects/hermes", "/user/master") -> Honcho NOT selected
      -> 200 {"namespace", "status", "conclusions", "unavailable"}

    CogneeBackend.reflect -> _dataset_id(ns) -> POST /recall
        {"query": req.query, "search_type": "GRAPH_COMPLETION", "datasets": [dataset]}
        2xx + non-empty answer -> ReflectResult("ready", "cognee", (Conclusion(confidence=0.0),))
        2xx + empty/absent answer -> ReflectResult("empty", "cognee")     # sync, NOT "pending" (D-02)
        non-2xx / transport error / malformed JSON -> BackendUnavailableError("cognee", reason)
        illegal dataset mapping -> BackendUnavailableError("cognee", reason)   # D-04
    health -> GET /healthz -> OK | DOWN(reason)   # never raises

Coexistence is by construction: the namespace patterns are disjoint, so `backends_for` returns exactly one of the two adapters for any validated namespace. Neither adapter observes the other.

## Permissions

Exact `_ROLE_TABLE` diff (`permissions.py:35-57`) — two lines:

```python
     "scientist": {
-        "projects": frozenset({"search"}),
+        "projects": frozenset({"search", "reflect"}),
     "jarvis": {
-        "projects": frozenset({"store", "search"}),
+        "projects": frozenset({"store", "search", "reflect"}),
```

| Role | `projects` verbs after change |
|---|---|
| `coder` | `{"store", "search"}` — **unchanged**, reflect denied |
| `scientist` | `{"search", "reflect"}` |
| `jarvis` | `{"store", "search", "reflect"}` |

Confirmed against the current file: `coder`'s `projects` row is `frozenset({"store", "search"})` (`permissions.py:39`) and is not touched. No `reflect` row is added to `global`, `agents_self`, `agents_other`, or `other`; `authorize()` resolves an absent verb through `.get(kind, frozenset())`, so those stay denied by default with no new code — asserted, not assumed. `_namespace_kind` needs no change: `/projects/…` already maps to `"projects"` (`permissions.py:25-26`).

## File Changes

| File | Action | Description |
|---|---|---|
| `.../memory_router/backends/cognee.py` | Create | `CogneeBackend` + `_HttpJsonClient` + `ENDPOINTS` + `_dataset_id`. |
| `.../memory_router/permissions.py` | Modify | Two `projects` rows gain `reflect`. |
| `.../memory_router/app.py` | Modify | `Dispatcher.reflect()` docstring; **plus** the three-line `empty` mapping pending D-06 confirmation. |
| `.../memory_router/contracts.py` | Unchanged | Zero diff. |
| `.../memory_router/registry.py` | Unchanged | Zero diff. |
| `hermes-native/memory-router/pyproject.toml` | Modify | One entry-point line under `memory_router.backends`. |
| `tests/test_memory_router_cognee_adapter.py` | Create | Stubbed-transport tests. |
| `tests/` permissions + dispatcher | Modify | Projects-reflect allow/deny; coexistence assertions. |
| `openspec/specs/{memory-access-control,memory-backend-adapters}/` | Modify | Delta specs. |
| `specs/017_cognee_backend.md` | Create | Numbered spec companion. |

## Testing Strategy

No live Cognee instance. Every test injects a stub `transport` callable, mirroring `tests/test_memory_router_honcho_adapter.py`.

| Layer | What to test | Approach |
|---|---|---|
| Protocol conformance | `isinstance(CogneeBackend(), ReflectiveBackend)` true; `isinstance(…, MemoryBackend)` **false**; zero-arg construction succeeds | Direct assertions |
| Capabilities (exact) | `verbs == frozenset({"reflect"})` by equality, not membership; `"store"`/`"search"` asserted absent; `namespaces == ("/projects/*",)` by equality; `hierarchical_search is False` | Direct assertions |
| Namespace selection | `Registry([Cognee()]).backends_for(verb="reflect", namespace=…)` selects for `/projects/foo`, and is **empty** for `/user/master`, `/global`, `/agents/x` | Registry injection |
| Coexistence (disjoint) | `Registry([Honcho(), Cognee()])`: `/user/master` → **only** Honcho; `/projects/foo` → **only** Cognee. Existing Honcho tests re-run unmodified | Registry injection |
| Dataset mapping | `/projects/hermes` → `jarvis-hermes`; prefix override honored | Stub transport captures the POST body |
| Dataset fail-closed (D-03/D-04) | `..`, `*`, `?`, embedded `/`, leading `-`, uppercase (`Foo`), dot (`a.b`), empty name each raise `BackendUnavailableError("cognee", …)` and issue **no HTTP call**; malformed `COGNEE_DATASET_PREFIX` also fails closed | Transport that fails the test if invoked |
| Round trip | 2xx + answer → `status == "ready"`, one `Conclusion`, `confidence == 0.0`, `namespace` echoed, `backend == "cognee"`; request body carries `search_type == "GRAPH_COMPLETION"` and exactly the one dataset | Stub transport |
| Empty (D-02) | 2xx with empty/absent/whitespace answer → `status == "empty"`, `conclusions == ()`; asserted **not** `"pending"` and **not** `"ready"`; no fabricated content anywhere in the payload | Stub transport |
| Degradation | Connection error (`OSError`/`URLError`), non-2xx, malformed JSON each raise `BackendUnavailableError("cognee", …)`; dispatcher reports `unavailable` + `status == "degraded"`, not a request failure | Raising/garbage stub |
| Secrets | `COGNEE_TOKEN` substring absent from every raised `BackendUnavailableError.reason` and every dispatcher payload; `Authorization` header present in `bearer` mode, absent in `none` mode | Token-substring assertion |
| Outbound construction | A hostile `query` appears only in the JSON body — never in the URL, never in a header; timeout always set | Stub transport inspects `url`/`headers` |
| Permissions | `scientist` and `jarvis` allowed `reflect` on `/projects/x`; `coder` raises `AuthorizationError` → `403 authorization_denied`; `reflect` still denied on `/global`, `/agents/*` for all three roles; `coder`'s `projects` `{"store","search"}` still allowed | Table-driven |
| Validation (F-1) | `validate_namespace("/projects/a/b")` raises `NamespaceError`; dispatcher returns `400 invalid_namespace` — the nesting question is closed by test, not by comment | Direct assertion |
| Dispatcher (D-06) | Reflect on `/projects/x` with an `empty`-returning backend reports `status == "empty"`, **not** `"no_backend"`; `ready` still wins over `empty`; Honcho's `pending` path unchanged | Registry injection |
| Integration | Live Cognee `/recall` | **Not performed** — no instance, no LLM key. Explicit follow-up. |

## Threat Matrix

| Boundary | Applicability | Design response | Planned RED tests |
|---|---|---|---|
| Cross-project isolation | Applicable — the headline risk | One dataset per project (D-01); mapping is injective by rejection, never rewriting (D-03); dataset revalidated after prefixing | Two distinct namespaces never yield one dataset id; case/dot variants fail closed rather than collide |
| Namespace routing | Applicable | Nested namespaces already dead at validation (F-1); adapter re-rejects `/`, `..`, wildcards regardless | Traversal/wildcard yields no dataset and no HTTP call |
| Authorization | Applicable | Explicit rows on `projects` for two roles only; deny-by-default elsewhere untouched | `coder` denied reflect; reflect denied on other kinds |
| Outbound request construction | Applicable | URL from router-owned config + sanitized dataset only; caller `query` in the JSON body; fixed header set; timeout always set | Hostile query absent from URL and headers |
| Secret handling | Applicable | Token from env only; never logged; error reasons carry no token and no caller data | Token substring absent from all reasons/responses |
| LLM cost / abuse | Applicable, deferred | `GRAPH_COMPLETION` bills an LLM call per reflect; no rate limit in this slice, no live instance to bill | None — flagged as an ops precondition before provisioning |
| Subprocess / VCS / PR automation / executable classification | N/A — HTTP only, no shell, no VCS | — | None |

## Migration / Rollout

No data migration, no stored state, no Cognee-side cleanup. Adding the entry-point line activates reflect on `/projects/*`; removing `cognee.py`, its test, and the entry-point line returns `backends_for(verb="reflect", namespace="/projects/x")` to empty and reflect to `no_backend` — the pre-change behavior, not a crash. Reverting the two `permissions.py` rows and the `app.py` commit completes the rollback. Both steps are pure code reverts on a feature branch.

## Open Questions

- [ ] **D-06 needs an explicit answer before `sdd-tasks`.** The proposal states `app.py` has zero functional diff; F-2 shows that is incompatible with the approved `empty` status, which would surface as `no_backend`. Either the three-line dispatcher mapping is in scope, or D-02 reopens.
- [ ] Cognee `/recall` wire format — path, request keys (`search_type`, `datasets` vs `dataset_ids`/`dataset_name`), response key (`result` vs `answer` vs a list), and whether `datasets` takes names or UUIDs — is **unverified** against a live instance or authoritative docs. `ENDPOINTS` + `_HttpJsonClient` + `_dataset_id` are the single revisable surface.
- [ ] Cognee's legal dataset-identifier charset is unverified. `_DATASET_RE` is deliberately conservative (`^[a-z0-9][a-z0-9_-]*$`); if Cognee accepts `.` and uppercase, widening the regex un-blocks `/projects/Jarvis.v2` without touching anything else. Widening must stay injective.
- [ ] Does reflect ever need Cognee's ingestion path (`/remember`, add + cognify)? Deferred per the proposal, same posture as Honcho's deferred ingestion. Until then a real deployment plausibly returns `empty` in practice — which is why `empty` must be observable (D-06).
- [ ] Whether datasets are provisioned per project up front or lazily on first reflect, and what a reflect against a nonexistent dataset returns (404 → degraded, or 200-empty → `empty`). Unverifiable without an instance; the adapter treats non-2xx as degraded by default.
- [ ] `honcho.py`'s `_peer_ref` raises a bare `ValueError` that `Dispatcher.reflect` does not catch — a latent unhandled 500. Cognee avoids it (D-04); fixing Honcho is a separate follow-up, deliberately out of this change.
- [ ] Confidence normalization if reflect conclusions are ever merged with search hits — Cognee contributes an unscored `0.0` (D-05), which a naive ranker would sort last.
