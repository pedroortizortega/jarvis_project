# Tasks: Classified Session Degradation + Debounced Telegram Alerts

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | PR 1 ~325, PR 2a ~?, PR 2b ~? (split of the former single ~1065-line PR2) |
| 400-line budget risk | High (mitigated by the 2a/2b split) |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 (codex-shim) → PR 2a (model-panel pure signing+state) → PR 2b (model-panel ticker+wiring+manifests+specs) |
| Delivery strategy | chained, 3 PRs |
| Chain strategy | fresh-branch-off-main (per this repo's established anti-auto-close pattern) — PR2a opens fresh off main after PR1 merges; PR2b opens fresh off main after PR2a merges. Never stack a PR on another open PR's own branch. |

Decision needed before apply: No — resolved by explicit user confirmation (AskUserQuestion): 3-PR split (1 → 2a → 2b), not a single size-exception PR2.

### Suggested Work Units

| Unit | Goal | PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----|----------------------|-----------------|-------------------|
| 1 | codex-shim: `StoreUnreachable`, session classification, main.py pass, proxy.py widening | PR 1 (base=main) | `pytest kubernetes/codex-shim/tests -k backend_unreachable` | `TestClient(app)` on `/internal/session` + proxy routes, mocked `ApiException`/`MaxRetryError` | Drop `StoreUnreachable`, revert `store.py`/`session.py`/`main.py`/`proxy.py`; no persisted state |
| 2a | model-panel: pure `alerts/signing.py` + `alerts/state.py` + unit tests | PR 2a (base=main, fresh branch opened after PR1 merges) | `pytest kubernetes/model-panel/tests/test_session_alerter.py kubernetes/model-panel/tests/test_alert_webhook.py -k "signing or state"` | Pure-function unit tests, injected clock, no I/O | Delete `alerts/signing.py`, `alerts/state.py`, their tests |
| 2b | model-panel: `alerts/ticker.py`, lifespan wiring, `deployment.yaml`, D17/manifest regression, runbook, specs | PR 2b (base=main, fresh branch opened after PR2a merges) | `pytest kubernetes/model-panel/tests` | `with TestClient(app)` lifespan start/stop, stubbed transport, no live cluster | Remove `alerts/ticker.py`, lifespan import/call in `main.py`, env/Secret from `deployment.yaml` |

## Phase 1: codex-shim — `StoreUnreachable` classification (D-01/D-02/D-04)

- [x] 1.1 RED: `kubernetes/codex-shim/tests/test_session_backend_unreachable.py` — stubbed `core_v1` raising `ApiException(status=500)` on `read_namespaced_secret` → `TokenStore.read()` raises `StoreUnreachable` with `code == "k8s_api_500"`
- [x] 1.2 RED: stubbed `MaxRetryError`/`socket.timeout`/`OSError` → `StoreUnreachable(code == "k8s_transport")`
- [x] 1.3 RED: existing 404 → `SecretNotFound` test still passes unmodified (regression guard, D-03)
- [x] 1.4 RED: `write()` classifies the same exception family identically to `read()`
- [x] 1.5 GREEN: add `StoreUnreachable(RuntimeError)` to `kubernetes/codex-shim/app/store.py`; replace bare re-raise (`store.py:99`) with the status-based classifier per design's Interfaces/Contracts block
- [x] 1.6 REFACTOR: confirm no exception `str()`/`args` is read anywhere in the new path (only `getattr(exc, "status", None)`)

## Phase 2: codex-shim — sanitized reason + no-token-material (2.6)

- [x] 2.1 RED: crafted exception whose `str()` contains `"sk-secret"`-like text and a traceback — assert that substring absent from `reason`, `last_error_code`, and the `/internal/session` body
- [x] 2.2 GREEN: template `reason = f"kubernetes API secret read failed ({code})"` only from the derived `code`, never from `str(exc)`
- [x] 2.3 RED: response body audited across all seven `SessionState` values (mocked store behavior per state) — no field contains token material or raw Secret content in any case

## Phase 3: codex-shim — `session.py` state wiring (D-07)

- [x] 3.1 RED: `SessionManager._load_cached()` — `StoreUnreachable` on read sets `state == "backend_unreachable"`, `_last_error_code`, `_reason`, then re-raises
- [x] 3.2 RED: `_do_refresh_locked()` — `StoreUnreachable` on `store.write(tokens)` also classifies to `backend_unreachable`
- [x] 3.3 RED: `_cached` is not dropped/invalidated on a `StoreUnreachable` failure (D-07) — a subsequent read with a still-valid cache stays invisible to the failure
- [x] 3.4 GREEN: add `backend_unreachable` to `SessionState` Literal; implement the two `except StoreUnreachable` blocks in `kubernetes/codex-shim/app/session.py`

## Phase 4: codex-shim — `main.py` response shape (D-05)

- [x] 4.1 RED: `/internal/session` returns HTTP 200 with `state == "backend_unreachable"`, not 500, when `ensure_fresh()` raises `StoreUnreachable`
- [x] 4.2 RED: response body keys unchanged from the prior six-state shape (same explicit five-key contract)
- [x] 4.3 GREEN: add `except StoreUnreachable: pass` alongside existing `SecretNotFound`/`AuthError` passes in `kubernetes/codex-shim/app/main.py`

## Phase 5: codex-shim — `proxy.py` widening (D-06)

- [x] 5.1 RED: chat-path request while `StoreUnreachable` is raised → `503 {"state": "backend_unreachable"}`, not an opaque 500, at each of the three call sites (`:162`, `:293`, `:387`)
- [x] 5.2 GREEN: widen `(AuthError, SecretNotFound)` → `(AuthError, SecretNotFound, StoreUnreachable)` at the three call sites; add a `StoreUnreachable` branch to `_session_error_body` in `kubernetes/codex-shim/app/proxy.py`

## Phase 6: codex-shim — D17 fail-closed regression (F-1)

- [x] 6.1 RED: `assert_switch_to_cloud_allowed` raises `SwitchBlocked(session_state="backend_unreachable")` with zero cluster calls (stub client that fails the test if invoked) — confirms `ALLOWED_SESSION_STATES` requires zero edit. **Note**: the code under test (`ALLOWED_SESSION_STATES`/`assert_switch_to_cloud_allowed`) lives in `kubernetes/model-panel/app/clients/codex_shim.py`, not codex-shim — per design's own File Changes table this test physically belongs in `kubernetes/model-panel/tests/test_switch_blocked_backend_unreachable.py`, so it is implemented together with task 13.1 in Unit 2b rather than in Unit 1's commit.
- [x] 6.2 Assert `kubernetes/codex-shim/deployment.yaml` has zero diff (manifest test, D-19 boundary check for Piece 1)

## Phase 7: codex-shim — delta spec + PR 1 verification

- [x] 7.1 Confirm `openspec/specs/codex-session-state/spec.md` scenarios (Complete State Enumeration, Backend-Unreachable Classification, Sanitized Reason Contract, No-Token-Material Guarantee, D17 Gating, Response Shape Stability) each map to a passing test from Phases 1-6 (D17 Gating scenario maps to the Unit 2b test per the 6.1 note above)
- [x] 7.2 Full codex-shim suite green: `pytest kubernetes/codex-shim/tests`
- [x] 7.3 PR 1 checkpoint — ready to open against tracker/main per chosen chain strategy

## Phase 8: model-panel — `alerts/signing.py` (pure, D-18)

- [ ] 8.1 RED: `sign_v2(secret, body, ts)` equals an independently computed `hmac.new(secret.encode(), ts.encode()+b"."+body, sha256).hexdigest()` known vector
- [ ] 8.2 GREEN: implement `sign_v2` in `kubernetes/model-panel/app/alerts/signing.py`
- [ ] 8.3 RED: signature covers the exact posted bytes — re-serializing the same logical payload with different key order/separators produces a mismatching signature (regression against double-`json.dumps`)

## Phase 9: model-panel — `alerts/state.py` transition machine (D-12/D-13/D-14)

- [ ] 9.1 RED: non-alert-worthy state, `degraded_since is None` → stays `None`, emits `"none"`
- [ ] 9.2 RED: alert-worthy state, first sighting → `degraded_since` set to `now`, emits `"none"`
- [ ] 9.3 RED: same alert-worthy state, `now - since < 10` → unchanged, emits `"none"`
- [ ] 9.4 RED: same alert-worthy state, `now - since >= 10` → emits `"degraded"` exactly once (9.9s emits nothing, 10.0s emits exactly one)
- [ ] 9.5 RED: already-alerted state, 100 further ticks → emits `"none"` every time (one-shot, D-14)
- [ ] 9.6 RED: different alert-worthy state (e.g. `refresh_failed` → `expired_needs_relogin`) resets `degraded_since`, clears `alerted_state`, re-arms, fires again after 10s
- [ ] 9.7 RED: `valid` after a prior alert → emits `"recovery"` exactly once, re-arms
- [ ] 9.8 RED: `valid`/`rate_limited`/`not_configured` without a prior alert → emits `"none"`, no recovery notice
- [ ] 9.9 RED: `rate_limited`/`not_configured` sustained for any duration never alert (Excluded States scenario)
- [ ] 9.10 GREEN: implement `ALERT_WORTHY_STATES`, `SESSION_ALERT_SUSTAIN_SECONDS = 10.0`, `AlertDecision`, `SessionAlerter.observe()` in `kubernetes/model-panel/app/alerts/state.py`, using injected `time.monotonic()` `now`

## Phase 10: model-panel — alert payload content (D-20)

- [ ] 10.1 RED: a debounced alert for `expired_needs_relogin` includes `state`, sanitized `reason`, `expires_at` (or explicit absence marker), and a one-line next-action hint
- [ ] 10.2 GREEN: fixed `dict[state] -> str` `next_action` map; assemble flat JSON payload (`event`, `state`, `previous_state`, `reason`, `expires_at`, `next_action`, `sustained_seconds`, `source`)

## Phase 11: model-panel — `alerts/ticker.py` delivery (D-09/D-10/D-16/D-17/D-19)

- [ ] 11.1 RED: empty/absent secret or webhook URL → ticker never starts, zero POSTs attempted (transport stub fails the test if invoked)
- [ ] 11.2 RED: body is serialized to bytes exactly once; that same byte string is both signed and posted as `content=` (capture request, compare to signed bytes)
- [ ] 11.3 RED: `X-Webhook-Timestamp` header present on every POST, value within acceptable range of `time.time()`
- [ ] 11.4 RED: transport raising `httpx.ConnectError`/timeout is logged and does not raise; ticker survives and ticks again on the next interval
- [ ] 11.5 RED: non-2xx webhook response is treated as delivery failure — logged, not raised
- [ ] 11.6 RED: one bad tick (e.g. `CodexShimClient` raising) does not end the ticker loop; only the stop `Event` ends it
- [ ] 11.7 GREEN: implement `SessionAlertTicker` — `threading.Thread(daemon=True)` + `threading.Event`, 5s interval (`SESSION_ALERT_POLL_INTERVAL_SECONDS`), `try/except Exception: logger.exception(...); continue` around the whole tick body, explicit `httpx.Timeout(connect=2.0, read=3.0, write=3.0, pool=2.0)`, dedicated thread never reusing `app.state.executor`
- [ ] 11.8 RED: alert fires with no browser tab polling `/api/status` (server-side ticker scenario)

## Phase 12: model-panel — lifespan wiring (D-09)

- [ ] 12.1 RED: `TestClient(app)` without a `with` block spawns no ticker thread (existing 20 test modules stay unaffected)
- [ ] 12.2 RED: `with TestClient(app)` — lifespan start spawns the ticker thread; shutdown sets the stop `Event` and joins within a bounded timeout
- [ ] 12.3 GREEN: add `lifespan=` to `FastAPI(...)` in `kubernetes/model-panel/app/main.py`; construct `SessionAlerter`/`SessionAlertTicker`, store as `app.state.session_alerter`
- [ ] 12.4 Confirm `/api/status` handler is byte-unmodified — zero diff (D-08 structural guarantee)

## Phase 13: model-panel — D17/manifest regression (F-1/F-2, D-19)

- [ ] 13.1 RED: `kubernetes/model-panel/app/clients/codex_shim.py` `ALLOWED_SESSION_STATES` still rejects `"backend_unreachable"`/`"unreachable"` (regression test only, zero code change)
- [ ] 13.2 RED: `panel.js` `sessionStateClass` defaults to `"bad"` for the new states (regression test only, zero code change; F-2)
- [ ] 13.3 RED: `kubernetes/codex-shim/deployment.yaml` has zero diff; no webhook secret reference anywhere in it
- [ ] 13.4 RED: `kubernetes/model-panel/deployment.yaml` mounts `model-panel-webhook` Secret via `secretKeyRef`, sets `HERMES_WEBHOOK_URL`; `replicas == 1` / `strategy.type == Recreate` still hold (D-15 dependency)
- [ ] 13.5 GREEN: update `kubernetes/model-panel/deployment.yaml` with the env var + `secretKeyRef`

## Phase 14: model-panel — delta spec + specs companion + runbook

- [ ] 14.1 Confirm `openspec/specs/session-degradation-alerting/spec.md` scenarios each map to a passing test from Phases 8-13
- [ ] 14.2 Create `specs/020_codex_shim_session_alerts.md` numbered companion summarizing both capabilities, config surface, payload contract, threat matrix
- [ ] 14.3 Create `docs/` runbook: Hermes route (`deliver_only: true`, `deliver: telegram`) provisioning + shared-secret setup steps (manual, non-code)

## Phase 15: model-panel — final verification / PR 2 checkpoint

- [ ] 15.1 Full model-panel suite green: `pytest kubernetes/model-panel/tests`
- [ ] 15.2 Confirm no added latency/failure surfaces on `/api/status` (D-08/D-16 structural + timing assertions)
- [ ] 15.3 PR 2 checkpoint — request `size:exception`, or fall back to the 2a/2b split per the Suggested Work Units table
