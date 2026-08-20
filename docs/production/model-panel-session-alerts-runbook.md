# Production runbook: Hermes webhook route for model-panel session alerts

Manual, external provisioning steps for `session-degradation-alerting`
(**specs/020_codex_shim_session_alerts.md**). Nothing in this repo's CI or
`kubectl apply -k kubernetes/model-panel` depends on these steps — the
`SessionAlertTicker` fail-closes (logs once, starts no thread, sends zero
POSTs) until both the model-panel Secret and the Hermes route exist
(design D-19). This is provisioning-only, never merge-blocking.

## What you're wiring up

```
model-panel (in-cluster)          Hermes (host service, outside k8s)
  SessionAlertTicker        --->    POST /webhooks/{route}
  every 5s, alert-worthy             X-Webhook-Signature-V2: hmac-sha256
  state sustained >=10s               (shared secret, timestamp + body)
                                      X-Webhook-Timestamp: unix seconds
                                     deliver_only: true, deliver: telegram
                                       -> renders payload -> Telegram
```

model-panel is the only caller; codex-shim never sends outbound alerts
(`kubernetes/codex-shim/deployment.yaml` has zero diff — asserted by
`kubernetes/codex-shim/tests/test_session_backend_unreachable.py`).

## 1. Generate the shared signing secret

Same value goes in both places below. Treat it like any other bearer
credential — never commit it, never log it.

```bash
openssl rand -hex 32
```

## 2. Create the k8s Secret (model-panel only)

```bash
kubectl -n llms create secret generic model-panel-webhook \
  --from-literal=secret="<paste the value from step 1>"
```

`kubernetes/model-panel/deployment.yaml` mounts this Secret's `secret` key
into `MODEL_PANEL_WEBHOOK_SECRET` via `secretKeyRef` and sets
`HERMES_WEBHOOK_URL` to Hermes's route path (see step 3). Confirm the
mount after `kubectl apply -k kubernetes/model-panel`:

```bash
kubectl -n llms get deployment model-panel -o jsonpath='{.spec.template.spec.containers[0].env}'
```

## 3. Configure the Hermes webhook route

`hermes-agent`'s webhook gateway config lives outside this repo's git
tracking (proposal Out of Scope) — edit it on the Hermes host directly.
Add a `deliver_only` route matching the path used in
`HERMES_WEBHOOK_URL`:

```yaml
webhooks:
  routes:
    - path: /webhooks/model-panel-session-alerts
      deliver_only: true
      deliver: telegram
      secret: "<same value as step 1>"
```

The exact config key names depend on the deployed Hermes gateway version —
match whatever `gateway/platforms/webhook.py`'s route schema expects on
that host (its `X-Webhook-Signature-V2` contract is: lowercase hex
`hmac.new(secret, f"{timestamp}.".encode() + body, sha256).hexdigest()`,
`X-Webhook-Timestamp` required, ±300s window, HMAC compared with
`hmac.compare_digest`). Restart/reload the Hermes gateway process after
editing.

## 4. Telegram template

`deliver_only: true` renders a template over the posted JSON body. The
payload's fields (design D-20, `specs/020_codex_shim_session_alerts.md`):

| Field | Example | Notes |
|---|---|---|
| `event` | `"session_degraded"` \| `"session_recovered"` | |
| `state` | `"expired_needs_relogin"` | Current session state |
| `previous_state` | `"valid"` | State before this transition, if known |
| `reason` | `"kubernetes API secret read failed (k8s_api_500)"` | Already sanitized by codex-shim/model-panel — never raw exception text |
| `expires_at` | `null` or ISO timestamp | |
| `next_action` | `"Re-run bootstrap_login.md to restore the Codex session."` | One-line operator hint, fixed per-state |
| `sustained_seconds` | `10.3` | `null` on recovery |
| `source` | `"model-panel"` | |

A minimal template that works with no further Hermes-side changes:

```
{{ event }}: {{ state }} (was {{ previous_state }})
{{ reason }}
{{ next_action }}
```

If the route is left with a bare passthrough template, the operator sees
raw JSON in Telegram — usable for validation, not ideal for daily use;
ship a concrete template before relying on this for on-call.

## 5. Verify delivery

1. Confirm the Secret and route exist (steps 2-3).
2. Roll the model-panel Deployment so the ticker picks up the new env vars:
   `kubectl -n llms rollout restart deployment/model-panel`.
3. Force a degraded state (e.g. stop `codex-shim` briefly, or revoke its
   Kubernetes Secret read access) and watch for a Telegram message within
   ~15s of the 10s sustain threshold (5s poll interval, D-11/D-12).
4. Restore the session and confirm exactly one recovery notice arrives.
5. `kubectl -n llms logs deployment/model-panel | rg -i "session alert ticker"`
   — startup logs a warning once if the Secret/URL are missing; delivery
   failures log a warning per failed tick, never a crash (D-16/D-17).

## Rollback

Delivery-only, not code: delete the Hermes route and the
`model-panel-webhook` Secret. The ticker fail-closes to a silent no-op on
its next restart (D-19) — no code change or redeploy required to stop
alerting. Reverting the code itself (removing `app/alerts/`, the lifespan
wiring, and the `deployment.yaml` env vars) is a separate, independent
step documented in the change's design (`Migration / Rollout`).

## See also

- `specs/020_codex_shim_session_alerts.md`
- `openspec/specs/session-degradation-alerting/spec.md`
- [model-panel service doc](../services/model-panel.md)
