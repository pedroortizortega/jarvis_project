# Design: Classified Session Degradation + Debounced Telegram Alerts

## Technical Approach

Two independently revertible halves, joined only by the existing `/internal/session` JSON contract.

**Piece 1 (codex-shim)** adds one exception type (`StoreUnreachable` in `store.py`), one `SessionState` literal (`backend_unreachable`), and three `except` blocks. `TokenStore.read()`/`write()` already wrap the live `read_namespaced_secret` call in a `try` that special-cases `status == 404` → `SecretNotFound` and re-raises everything else bare (`store.py:92-99`); that bare re-raise is the entire defect. It becomes a classified, sanitized `StoreUnreachable`, which `SessionManager` maps to the new state exactly the way it already maps `SecretNotFound` to `not_configured`.

**Piece 2 (model-panel)** adds `app/alerts/` — a pure signer, a pure transition state machine, and a daemon-thread ticker started from a FastAPI lifespan. The ticker polls `/internal/session` through the *existing* `CodexShimClient` on its own schedule and POSTs a V2-signed alert to Hermes. It is entirely off the request path: `/api/status` takes **zero** diff.

## Verified Findings (read from current code)

- **F-1 — D17 is already fail-closed, no edit needed.** `clients/codex_shim.py:17` `ALLOWED_SESSION_STATES = {"valid", "expiring_soon"}`, and `assert_switch_to_cloud_allowed` (`:62`) raises `SwitchBlocked` on `state not in ALLOWED_SESSION_STATES`. It is an allow-list, so `backend_unreachable` is rejected by construction. Confirmed against source, not assumed. Only a regression test is added.
- **F-2 — `panel.js` also needs zero edit.** `sessionStateClass` (`panel.js:91-95`) returns `"bad"` for anything not `valid`/`expiring_soon`/`rate_limited`, and the toggle gate (`panel.js:72`) uses the same two-state allow-list. The new state renders red and blocks the button with no JS change.
- **F-3 — the browser is the only poller today.** `panel.js` drives `GET /api/status` on a `setInterval`; nothing in `model-panel/app/` runs periodically. `app.state.executor = ThreadPoolExecutor(max_workers=1)` (`main.py:211`) exists solely for multi-minute switch/realign jobs and is serialized against `app.state.switch_lock`. This is why the proposal's "reuse the existing poll path, no new poller" instruction cannot stand (resolved question 1) **and** why the ticker must not reuse that executor.
- **F-4 — the debounce precedent is wall-clock, in-memory, on `app.state`.** `main.py:220-221` + `maybe_self_heal_alias_drift` (`:313-329`): `app.state.last_alias_heal_attempt = 0.0`, `ALIAS_HEAL_MIN_INTERVAL_SECONDS = 30.0`, guarded by `if now - app.state.last_alias_heal_attempt < INTERVAL: return` using `time.time()`. The alerter follows this shape exactly rather than inventing one.
- **F-5 — single replica, `Recreate`.** `model-panel/deployment.yaml:10-12` `replicas: 1`, `strategy.type: Recreate`. Two instances never run concurrently, so in-memory alert state cannot double-fire across replicas.
- **F-6 — Hermes's V2 contract (`gateway/platforms/webhook.py:1136-1161`).** `X-Webhook-Signature-V2` = lowercase hex `hmac.new(secret, b"<timestamp>.<raw_body>", sha256).hexdigest()`; `X-Webhook-Timestamp` (unix seconds) is **required** — its absence is a hard reject, never a V1 fallback; `abs(now - ts) > 300` rejects. Comparison is `_hmac_str_equal` → `hmac.compare_digest` on UTF-8 bytes (`:158-169`). A `deliver_only` route renders a template over the posted JSON (`:1305-1331`), so the payload must be flat, named fields.
- **F-7 — the proxy path has the same unclassified-500 gap.** `proxy.py:39-51` `_session_error_body` already exists and its three call sites catch only `(AuthError, SecretNotFound)` (`:162, :293, :387`). A `StoreUnreachable` would escape as the same opaque 500 this change exists to remove.

## Architecture Decisions

| # | Decision | Options / tradeoff | Choice and rationale |
|---|---|---|---|
| D-01 | Where connectivity failure is classified | In `SessionManager` (sniff exception types) vs in `TokenStore` (own exception) | **New `StoreUnreachable(RuntimeError)` raised by `store.py`.** The store is the only module that knows the Kubernetes client exists; `session.py` currently imports nothing from `kubernetes`/`urllib3` and must keep it that way, otherwise every session test grows an optional-dependency import. Mirrors the existing `SecretNotFound` seam exactly. |
| D-02 | How the exception family is recognised | Import `ApiException`/`MaxRetryError` and `isinstance`-check vs classify by position | **By position: inside `read()`'s existing `try`, anything that is not a 404 is a failed API call.** That `try` wraps *only* `read_namespaced_secret` — decoding happens after it — so there is no non-transport exception to misclassify. This needs zero imports (the `kubernetes` import is deliberately lazy, `store.py:80`), covers `ApiException`, `MaxRetryError`, `socket.timeout`, `OSError`, and any future client exception without a maintained type list, and fails in the safe direction: an unknown error becomes a non-switchable state, never an unclassified 500. |
| D-03 | `SecretNotFound` conflation | Fold 404 into `backend_unreachable` vs keep it separate | **Keep `not_configured` byte-for-byte unchanged.** A 404 is a definitive answer from a reachable API server ("you have not bootstrapped"), not a connectivity failure; the operator action differs (`bootstrap_login.md` vs "check the cluster"). The existing `not_configured` test must pass **unmodified** — that is the regression guard. |
| D-04 | Reason sanitisation (2.6) | Truncate `str(exc)` vs template from a code | **Template from a derived code only; the exception's text is never read.** `code = f"k8s_api_{status}"` when `getattr(exc, "status", None)` is an int, else `"k8s_transport"`; `reason = f"kubernetes API secret read failed ({code})"`. Truncation is not a security control — a 200-char prefix of an `ApiException` still carries response bodies and headers. Templating makes leakage structurally impossible, so the "no traceback, no token material" criterion is provable by construction, not by a substring test. A 403 therefore reads as `k8s_api_403` — honest, and still non-switchable. |
| D-05 | Handler-body change in `main.py` | Leave the handler alone vs add one `except` | **Add `except StoreUnreachable: pass` alongside the existing `SecretNotFound`/`AuthError` passes (`main.py:48-51`).** The response *shape* is untouched (still the same five explicitly-listed keys); without this line `ensure_fresh()`'s re-raise still produces the 500 the change exists to kill. |
| D-06 | `proxy.py` | Out of scope vs widen the existing tuples | **Widen — `(AuthError, SecretNotFound, StoreUnreachable)` at three call sites plus one `_session_error_body` branch → `503 {"state": "backend_unreachable"}`.** ~6 authored lines. Leaving it means the new exception type is classified for the status poll and opaque for real traffic, which is the same defect wearing a different hat. Flagged as a deliberate, bounded extension of the proposal's file list. |
| D-07 | Caching interaction | Invalidate `_cached` on failure vs leave it | **Leave it.** `_load_cached` returns the cached record without touching the store (`session.py:88`), so once a token is cached a transient API-server outage is correctly invisible to the status poll and only surfaces on a refresh (`write`) attempt. Dropping the cache would *manufacture* an outage on the hot path out of a monitoring concern. Documented, not silent. |
| D-08 | Alert trigger source | Hook `/api/status` (proposal's text) vs an independent server-side ticker | **Independent ticker** — the user's resolved question 1. The proposal's `/api/status` hook assumed a server-side poll; F-3 shows the poll is browser-driven, so an alerter hooked there is silent exactly when nobody is watching, which is the whole point of alerting. Consequence: `/api/status` takes zero diff and the "must not add latency to the 2s poll" criterion is satisfied structurally rather than by careful async. |
| D-09 | Ticker mechanism | `asyncio` task vs `@app.on_event("startup")` vs daemon `threading.Thread` from a lifespan | **`threading.Thread(daemon=True)` + `threading.Event`, started/stopped in a FastAPI `lifespan` context manager.** model-panel is thread-shaped throughout: sync `def` route handlers, `threading.Lock`, `ThreadPoolExecutor`, and a sync `httpx.Client` inside `CodexShimClient`. An asyncio task would need an async duplicate of that client. `on_event` is deprecated. `Event.wait(interval)` gives an interruptible sleep so pod shutdown is prompt within `terminationGracePeriodSeconds: 30`. Test-friendly side effect: `TestClient(app)` without a `with` block never runs lifespan, so all 20 existing test modules keep passing with no ticker spawned. |
| D-10 | Executor reuse | `app.state.executor` vs a dedicated thread | **Dedicated thread.** The shared executor is `max_workers=1` and holds `switch_lock` for multi-minute switches (F-3); a ticker queued behind a switch would go blind for minutes — precisely when a session failure is most likely. |
| D-11 | Poll interval | 2s (match the browser) vs 5s vs 30s | **`SESSION_ALERT_POLL_INTERVAL_SECONDS = 5.0`.** 2s adds pointless load; 30s would make a 10s threshold unobservable. 5s bounds worst-case alert latency at ~15s against a 10s threshold, and is safe against the token endpoint because `MIN_PROACTIVE_REFRESH_RETRY_INTERVAL_SECONDS = 30` (`session.py:56`) already caps proactive refresh attempts independently of caller frequency. |
| D-12 | Threshold semantics | N consecutive observations vs wall-clock duration | **Wall-clock: `SESSION_ALERT_SUSTAIN_SECONDS = 10.0`, measured from a monotonic `degraded_since` stamp** (question 5's proposed default). A count of N is silently redefined by any interval change or a slow tick; a duration is not. This is the requested independence from the ticker's own scheduling: `now - degraded_since >= 10.0`, evaluated with `time.monotonic()` so a wall-clock/NTP step cannot fire or suppress an alert. |
| D-13 | Alert-worthy set | Include `rate_limited` vs the proposed default | **`{expired_needs_relogin, refresh_failed, backend_unreachable, unreachable}`** (question 2's default adopted). `rate_limited` self-resolves and would be the dominant noise source; `not_configured` is a pre-bootstrap steady state, not a regression. Deliberately a *separate* constant from `ALLOWED_SESSION_STATES` — alerting is a superset-complement of switchability by coincidence today, and coupling them would make a future switchability tweak silently retune the alerts. |
| D-14 | Re-alert policy | Periodic re-alert vs one-shot | **One-shot per transition** (question 4's default). `alerted_state` is set on fire and only cleared on the transition back to `valid`, which emits exactly one recovery notice and re-arms. A degraded→degraded *change* (e.g. `refresh_failed` → `expired_needs_relogin`) is a new transition and fires once more: the operator action genuinely changed. |
| D-15 | Debounce state persistence | ConfigMap/`StateStore` vs in-memory `app.state` | **Purely in-memory on `app.state.session_alerter`**, per F-4/F-5. A single replica with `Recreate` cannot double-fire, and persisting alert bookkeeping into `model-panel-state` would put monitoring metadata into the handoff state machine's ConfigMap, coupling two unrelated lifecycles. Accepted cost: a pod restart during a sustained outage re-arms and re-alerts once ~10s later. That is a *feature* — a restart is itself news, and the alternative silently swallows the all-clear. |
| D-16 | Delivery isolation | Background task per alert vs inline in the tick | **Inline in the ticker thread, wrapped in `try/except Exception` with explicit `httpx.Timeout(connect=2.0, read=3.0, write=3.0, pool=2.0)`.** The ticker is already off every request path (D-08), so a dead Hermes can at worst delay the *next tick* by ≤5s, never a user request. Spawning a thread per alert to save that is complexity with no observable benefit. The bounded timeout is what keeps "slow Hermes" from becoming "stalled ticker". |
| D-17 | Ticker crash containment | Let the thread die vs catch-and-continue | **The whole tick body is `try/except Exception: logger.exception(...)`, then `continue`.** A monitor that dies silently on one bad response is worse than no monitor. Only the stop `Event` ends the loop. |
| D-18 | Signature construction | V1 body-only vs V2 | **V2 only** (F-6): `X-Webhook-Signature-V2` + `X-Webhook-Timestamp`, signing `f"{ts}.".encode() + body` over the **exact** bytes sent. The body is serialized **once** into `bytes` and both signed and POSTed as `content=`, never re-serialized from a dict — a re-`json.dumps` with different key order or separators produces a valid-looking signature that Hermes rejects. Timestamp is `str(int(time.time()))` (wall clock, not monotonic — it must match Hermes's ±300s window). |
| D-19 | Secret exposure | One shared secret vs model-panel-only | **New `model-panel-webhook` Secret, mounted via `secretKeyRef` in `model-panel/deployment.yaml` only.** `codex-shim/deployment.yaml` diff is asserted zero by a manifest test, preserving its thin, no-outbound-alerting posture. Missing/empty secret ⇒ the ticker logs once at startup and **does not start** — fail-closed and silent, never an unsigned POST. |
| D-20 | Message content | Structured JSON vs prerendered text | **Flat JSON with named fields** (`event`, `state`, `previous_state`, `reason`, `expires_at`, `next_action`, `sustained_seconds`, `source`), because a `deliver_only` route renders a Hermes-side template over the payload (F-6). `next_action` is a one-line hint from a fixed `dict[state] -> str` map (question 3), so the Telegram text stays useful even with a trivial `{{ }}` template. `reason` is whatever the shim already sanitized (D-04); the synthetic `unreachable` reason is templated locally the same way, never `str(exc)`. |

## Interfaces / Contracts

```python
# codex-shim/app/store.py
class StoreUnreachable(RuntimeError):
    """Kubernetes API call for the Secret failed for a non-404 reason."""
    def __init__(self, code: str, reason: str) -> None:
        super().__init__(reason)
        self.code = code          # "k8s_api_<status>" | "k8s_transport"
        self.reason = reason      # templated; never derived from str(exc)

# read(), replacing the bare `raise` at store.py:99
except Exception as exc:
    status = getattr(exc, "status", None)
    if status == 404:
        raise SecretNotFound(...) from exc
    code = f"k8s_api_{status}" if isinstance(status, int) else "k8s_transport"
    raise StoreUnreachable(code, f"kubernetes API secret read failed ({code})") from exc

# codex-shim/app/session.py
SessionState = Literal[..., "refresh_failed", "backend_unreachable"]

def _load_cached(self) -> TokenRecord:
    if self._cached is None:
        try:
            self._cached = self._store.read()
        except SecretNotFound:
            self._state = "not_configured"; self._reason = "codex-shim-auth Secret not found"; raise
        except StoreUnreachable as exc:
            self._state = "backend_unreachable"
            self._last_error_code = exc.code
            self._reason = exc.reason
            raise
    return self._cached
```

`_do_refresh_locked` wraps `self._store.write(tokens)` in the same `except StoreUnreachable` block (a refresh that cannot persist is a backend failure, not an `AuthError`). `ensure_fresh` propagates it unchanged, exactly like `SecretNotFound`.

```python
# model-panel/app/alerts/signing.py — pure (F-6)
def sign_v2(secret: str, body: bytes, timestamp: str) -> str:
    return hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()

# model-panel/app/alerts/state.py — pure state machine, no I/O, no clock of its own
ALERT_WORTHY_STATES = frozenset({
    "expired_needs_relogin", "refresh_failed", "backend_unreachable", "unreachable",
})
SESSION_ALERT_SUSTAIN_SECONDS = 10.0
SESSION_ALERT_POLL_INTERVAL_SECONDS = 5.0

@dataclass
class AlertDecision:
    kind: Literal["none", "degraded", "recovery"]
    payload: Optional[Dict[str, Any]] = None

class SessionAlerter:
    degraded_since: Optional[float]   # time.monotonic()
    alerted_state: Optional[str]
    def observe(self, session: Dict[str, Any], now: float) -> AlertDecision: ...
```

`observe()` is the whole policy and is 100% unit-testable with an injected `now` — no sleeping tests. Transition table:

| Observed | `degraded_since` | `alerted_state` | Emits |
|---|---|---|---|
| non-alert-worthy, was `None` | stays `None` | `None` | `none` |
| alert-worthy, first sighting | set to `now` | `None` | `none` |
| same alert-worthy, `now - since < 10` | unchanged | `None` | `none` |
| same alert-worthy, `now - since >= 10` | unchanged | ← state | `degraded` (once) |
| same alert-worthy, already alerted | unchanged | unchanged | `none` (D-14) |
| different alert-worthy state | reset to `now` | cleared | `none`, then `degraded` after 10s |
| `valid` after having alerted | `None` | `None` | `recovery` |
| `valid` / `rate_limited` / `not_configured` without a prior alert | `None` | `None` | `none` |

## Data Flow

    lifespan startup ──► SessionAlertTicker.start()   [daemon thread, only if secret+url set]
       │
       └─ loop every 5s until stop Event (D-11, D-17):
            CodexShimClient.get_session_status()      # existing client, existing 5s timeout
              │  raises ──► {"state": "unreachable", "reason": "<templated>"}   (mirrors main.py:397)
              ▼
            SessionAlerter.observe(session, time.monotonic())   # pure (D-12/D-13/D-14)
              │
              ├─ "none"                      ──► next tick
              └─ "degraded" | "recovery"     ──► body = json.dumps(payload).encode()   # once (D-18)
                                                 ts   = str(int(time.time()))
                                                 POST {HERMES_WEBHOOK_URL}
                                                   X-Webhook-Signature-V2: sign_v2(secret, body, ts)
                                                   X-Webhook-Timestamp:    ts
                                                 except Exception -> logger.warning, continue (D-16)

    lifespan shutdown ──► stop.set(); thread.join(timeout=…)      # interruptible wait

    GET /api/status ──► unchanged. Zero diff. Never touches the alerter.

## File Changes

| File | Action | Description |
|---|---|---|
| `kubernetes/codex-shim/app/store.py` | Modify | `StoreUnreachable`; classify non-404 in `read()`/`write()` (D-01/D-02/D-04). |
| `kubernetes/codex-shim/app/session.py` | Modify | `backend_unreachable` literal; `except StoreUnreachable` in `_load_cached` + `_do_refresh_locked`. |
| `kubernetes/codex-shim/app/main.py` | Modify | One `except StoreUnreachable: pass`; response shape unchanged (D-05). |
| `kubernetes/codex-shim/app/proxy.py` | Modify | Three tuple widenings + one `_session_error_body` branch → 503 (D-06). |
| `kubernetes/codex-shim/deployment.yaml` | **Unchanged** | Zero diff, asserted by test (D-19). |
| `kubernetes/codex-shim/tests/test_session_backend_unreachable.py` | Create | Mocked `ApiException`/`MaxRetryError`/timeout; sanitisation; 404 still `not_configured`. |
| `kubernetes/model-panel/app/alerts/__init__.py` | Create | Public surface: `SessionAlerter`, `SessionAlertTicker`, `sign_v2`. |
| `kubernetes/model-panel/app/alerts/signing.py` | Create | `sign_v2` (pure). |
| `kubernetes/model-panel/app/alerts/state.py` | Create | `SessionAlerter.observe`, thresholds, `next_action` map. |
| `kubernetes/model-panel/app/alerts/ticker.py` | Create | Daemon thread, stop `Event`, webhook POST, fail-closed startup (D-09/D-16/D-17/D-19). |
| `kubernetes/model-panel/app/main.py` | Modify | `lifespan=` on `FastAPI(...)`; construct alerter/ticker; `app.state.session_alerter`. `/api/status` untouched. |
| `kubernetes/model-panel/deployment.yaml` | Modify | `HERMES_WEBHOOK_URL` env + `MODEL_PANEL_WEBHOOK_SECRET` `secretKeyRef` (`model-panel-webhook`/`secret`). |
| `kubernetes/model-panel/app/static/panel.js` | **Unchanged** | Zero diff (F-2). |
| `kubernetes/model-panel/tests/test_session_alerter.py` | Create | Transition table, injected `now`, no sleeps. |
| `kubernetes/model-panel/tests/test_alert_webhook.py` | Create | Signature vector, exact-bytes binding, transport failure, fail-closed secret. |
| `kubernetes/model-panel/tests/test_switch_blocked_backend_unreachable.py` | Create | D17 regression (F-1). |
| `openspec/specs/codex-session-state/`, `openspec/specs/session-degradation-alerting/` | Create | Delta specs. |
| `specs/020_codex_shim_session_alerts.md` | Create | Numbered spec companion. |
| `docs/` runbook | Create | Hermes route (`deliver_only: true`, `deliver: telegram`) + shared-secret provisioning. |

## Testing Strategy

No live cluster, no live Hermes, no real socket. Every test injects a stub (`k8s_core_v1` into `TokenStore`, `http_client` into `CodexShimClient`, a transport callable into the ticker) and an explicit clock.

| Layer | What to test | Approach |
|---|---|---|
| Unit — store | 404 → `SecretNotFound`; `ApiException(status=500)` → `StoreUnreachable("k8s_api_500")`; `MaxRetryError`/`socket.timeout`/`OSError` → `"k8s_transport"`; `write()` classifies identically | Stub `core_v1` raising each type |
| Unit — sanitisation (2.6) | A crafted exception whose `str()` contains `"sk-secret"`, a traceback, and Secret bytes: none of those substrings appears in `reason`, `last_error_code`, or the `/internal/session` body | Substring assertions |
| Unit — session | `backend_unreachable` set on read and on refresh-write failure; `not_configured` test file passes **unmodified**; `_cached` not dropped (D-07) | `SessionManager` with stub store |
| Integration — shim | `/internal/session` returns **200** with `state == "backend_unreachable"`, not 500; body keys unchanged | `TestClient` |
| Integration — proxy (D-06) | `StoreUnreachable` on the chat path → `503 {"state": "backend_unreachable"}`, not 500 | `TestClient` |
| Unit — D17 (F-1) | `assert_switch_to_cloud_allowed` raises `SwitchBlocked(session_state="backend_unreachable")` with **zero** cluster calls | Stub client + a k8s stub that fails the test if invoked |
| Unit — alerter | Full transition table above; 9.9s emits nothing, 10.0s emits exactly one; 100 further ticks emit zero; recovery emits one and re-arms; `rate_limited`/`not_configured` never alert; degraded→different-degraded re-arms | Injected monotonic `now` |
| Unit — signing | Known-vector `sign_v2` equals an independently computed `hmac.new(secret, ts.encode()+b"."+body, sha256).hexdigest()`; signature covers the **exact** posted bytes; `X-Webhook-Timestamp` present and within ±300s | Recomputation + captured request |
| Unit — delivery | Transport raising `httpx.ConnectError`/timeout is logged and does **not** propagate; ticker survives and ticks again; a hung transport is bounded by the configured timeout | Raising/slow stub transport |
| Unit — fail-closed (D-19) | Empty/absent secret or URL ⇒ ticker never starts and **zero** POSTs are attempted | Transport that fails the test if invoked |
| Unit — lifecycle | Lifespan start spawns the thread; shutdown sets the Event and joins; `TestClient(app)` without `with` spawns nothing (existing suites unaffected) | `with TestClient(app)` |
| Manifest | `codex-shim/deployment.yaml` contains no webhook secret reference; `model-panel/deployment.yaml` does; `replicas == 1` still holds (D-15 depends on it) | YAML assertions, following `test_rbac_manifest.py` |
| E2E | Real Hermes → Telegram delivery | **Not performed** — the Hermes route is outside this repo's git tracking (proposal Out of Scope). Manual runbook verification. |

## Threat Matrix

| Boundary | Applicability | Design response | Planned RED tests |
|---|---|---|---|
| Secret handling / token material (2.6) | Applicable — headline | Reason templated from a code, exception text never read (D-04); `/internal/session`'s explicit five-key allow-list unchanged | Crafted exception carrying token-like text: absent from every surface |
| Signing-secret blast radius | Applicable | Secret in model-panel only; codex-shim manifest zero-diff (D-19) | Manifest assertions both ways |
| Outbound request construction | Applicable — new egress from model-panel | URL from operator-owned env only, never caller-supplied; fixed header set; explicit timeouts; body is generated fields only, never request-derived | Timeout always set; no caller input reaches URL/headers |
| Replay / forgery | Applicable | V2 with mandatory timestamp; signature over exact bytes; fail-closed on missing secret (D-18/D-19) | Signature vector; re-serialization mismatch caught |
| Fail-closed authorization (D17) | Applicable | New state excluded by the existing allow-list (F-1) | `SwitchBlocked` with zero mutations |
| Availability of the monitor | Applicable | Ticker catches everything and continues (D-17); dedicated thread, never behind the switch executor (D-10) | One bad tick does not end the loop |
| Alert storm / self-DoS | Applicable | Wall-clock sustain + one-shot per transition (D-12/D-14); 5s interval below the 30s proactive-refresh cap | Long outage emits exactly one alert |
| Shell / subprocess / VCS or PR automation / executable-file classification | N/A — HTTP and the Kubernetes client only, no shell, no VCS | — | None |

## Migration / Rollout

No data migration, no schema, no persisted alert state (D-15). Piece 1 is additive: rolling out the shim turns a 500 into a classified 200; rolling it back restores the 500. Piece 2 is inert until both `HERMES_WEBHOOK_URL` and the `model-panel-webhook` Secret exist — deploying the code without them starts no ticker and changes no behavior, so the manifest/Secret step is the real feature flag. Hermes-side route provisioning is a manual runbook precondition for *delivery* only, never for merge or rollout. Rollback of Piece 2: delete the two env entries (ticker stops starting), then revert the module and the lifespan line.

## Delivery Forecast

| Slice | Authored lines (est.) |
|---|---|
| **PR 1 — codex-shim** (`store.py` ~25, `session.py` ~20, `main.py` ~3, `proxy.py` ~6, tests ~180, delta spec `codex-session-state` ~90) | **~325** |
| **PR 2 — model-panel** (`alerts/` ~230, `main.py` wiring ~30, `deployment.yaml` ~15, tests ~290, delta spec ~110, `specs/020` ~330, runbook ~60) | **~1,065** |

`Decision needed before apply: Yes`
`Chained PRs recommended: Yes`
`400-line budget risk: High`

**Recommendation: two chained PRs on the natural boundary the proposal's own Rollback Plan already draws** — Piece 1 then Piece 2 — matching how honcho-backend, obsidian-backend, and graphiti-backend split. PR 1 lands under budget and is autonomously valuable: it closes the live D-OQ4 defect (a 500 becomes a diagnosis) and ships the D17 regression test, with or without alerting. PR 2 depends on PR 1 only for the `backend_unreachable` literal it alerts on, and needs an explicit `size:exception` (its bulk is `specs/020` prose plus repetitive table-driven tests, consistent with the accepted exceptions on the four backend adapters). If that exception is declined, split PR 2 as **2a** (`alerts/signing.py` + `alerts/state.py` + their unit tests — pure, no wiring, no manifest) then **2b** (`ticker.py` + lifespan wiring + `deployment.yaml` + runbook + specs). In a Feature Branch Chain, PR 1 targets the tracker branch and PR 2 targets PR 1's branch. `sdd-tasks` owns the binding guard lines.

## Open Questions

- [ ] Hermes's Telegram template for this route is unwritten; the payload field names (D-20) are the contract the runbook must document. If the route is configured with a bare `{{ body }}`-style passthrough, the operator sees raw JSON — usable, but the runbook should ship a concrete template.
- [ ] `next_action` hint text per state is proposed, not reviewed (e.g. `expired_needs_relogin` → "re-run bootstrap_login.md"). Wording is a one-line dict edit; wrong wording is the difference between an actionable alert and a mystery.
- [ ] Dead-man's switch remains out of scope (proposal): if the cluster network is severed, the alert POST also fails and the outage is silent. `backend_unreachable` makes the *symptom* diagnosable in the panel but does not make the alert deliverable. External heartbeat is a follow-up.
- [ ] D-04 maps 403 to `backend_unreachable`. If RBAC drift becomes a real recurring failure mode, a distinct `not_authorized` state may be worth splitting out later; the `last_error_code` (`k8s_api_403`) already distinguishes it in the payload without a new state.
- [ ] Whether the recovery notice should also fire when the ticker observes `valid` for the first time after a pod restart mid-outage (D-15 currently says no — no prior `alerted_state` in memory, so no bookend). Accepted as the quieter default.
