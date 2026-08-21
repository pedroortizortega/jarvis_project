# JARVIS Spec 020 - Software Design Document (SDD)
## codex-shim: clasificación de estado de sesión + alertas Telegram debounced

**Estado:** Implementado (código + tests unitarios) — sin validar contra Hermes real (la ruta webhook vive fuera del tracking de este repo, ver §7); PR 1 (codex-shim) y PR 2a (model-panel signing+state) ya mergeados; este spec cierra PR 2b (ticker + wiring + manifests + runbook)
**Fecha:** 2026-08-20
**Versión:** 1.0
**Autor:** Pedro Ortiz (vía agente `sdd-apply`)

---

## 0. Por qué existe este spec

Antes de este change, un fallo del API server de Kubernetes al leer el
Secret `codex-shim-auth` (`store.py:99`) se re-lanzaba sin clasificar:
`SessionManager` lo dejaba escapar como excepción cruda y `/internal/
session` respondía con un 500 opaco. El panel no tenía forma de distinguir
"el cluster está caído" de "no hay sesión configurada" (`not_configured`,
404 del Secret) ni de un fallo de refresh de OAuth (`refresh_failed`). Y
aunque el panel mostrara el estado correcto, nadie lo veía si no había una
pestaña del navegador con `panel.js` sondeando `/api/status` en ese momento
exacto.

Este change cierra ambos huecos: (1) codex-shim clasifica el fallo de
conectividad al API server como un séptimo estado explícito,
`backend_unreachable`, sanitizado y sin material de token; (2) model-panel
corre un ticker de servidor, independiente de cualquier pestaña del
navegador, que detecta una degradación sostenida (≥10s) de la sesión de
Codex y envía una alerta de Telegram vía el webhook `deliver_only` de
Hermes, firmada con HMAC-SHA256 V2, con debounce de una sola vez por
transición.

Dos mitades independientemente revertibles, unidas solo por el contrato
JSON ya existente de `/internal/session`.

---

## 1. Alcance de este change

**Dentro de alcance:**
- `StoreUnreachable(RuntimeError)` en `kubernetes/codex-shim/app/store.py`:
  clasifica cualquier excepción no-404 de `read_namespaced_secret` en
  `read()`/`write()` como `k8s_api_<status>` (con status entero) o
  `k8s_transport` (timeouts, `MaxRetryError`, `OSError`, cualquier
  excepción de transporte sin `status`).
- `SessionState` gana el literal `"backend_unreachable"`
  (`kubernetes/codex-shim/app/session.py`), mapeado en `_load_cached()` y
  `_do_refresh_locked()` exactamente como ya se mapea `SecretNotFound` →
  `not_configured`.
- `main.py`: un `except StoreUnreachable: pass` adicional junto a los ya
  existentes — `/internal/session` responde **200**, nunca 500, con el
  mismo contrato de cinco claves.
- `proxy.py`: los tres call sites del chat path (`:162`, `:293`, `:387`)
  amplían su tupla de excepciones capturadas a `(AuthError, SecretNotFound,
  StoreUnreachable)` → `503 {"state": "backend_unreachable"}` en vez de un
  500 opaco.
- `kubernetes/model-panel/app/alerts/` (paquete nuevo):
  - `signing.py` — `sign_v2(secret, body, ts)`, HMAC-SHA256 puro sobre
    `f"{ts}.".encode() + body`, sin re-serialización.
  - `state.py` — `SessionAlerter.observe(session, now)`, máquina de
    transición pura, `now` siempre inyectado (sin reloj propio).
  - `ticker.py` — `SessionAlertTicker`: hilo daemon dedicado + `threading
    .Event`, poll cada 5s, entrega inline con timeout explícito, captura
    total de excepciones por tick.
- `main.py` de model-panel: `lifespan=` en `FastAPI(...)`, construcción del
  alerter/ticker, `app.state.session_alerter`. `/api/status` con diff cero.
- `kubernetes/model-panel/deployment.yaml`: `HERMES_WEBHOOK_URL` (env
  plano) + `MODEL_PANEL_WEBHOOK_SECRET` (`secretKeyRef` sobre el Secret
  `model-panel-webhook`).
- Runbook de producción para el aprovisionamiento manual del lado Hermes.
- Tests: clasificación de store, sanitización, wiring de `session.py`/
  `main.py`/`proxy.py`, regresión D17, máquina de transición, firma,
  entrega del ticker, wiring del lifespan, regresión de manifiestos.

**Fuera de alcance:**
- Dead-man's switch: si la red del cluster se corta por completo, el POST
  de alerta también falla — el estado queda diagnosticable en el panel
  pero la alerta puede no entregarse. Un heartbeat externo es un follow-up.
- Aprovisionamiento del lado Hermes (la ruta `deliver_only` en sí) —
  vive fuera del tracking git de este repo; ver el runbook.
- Un nuevo estado `not_authorized` separado para 403 — hoy se clasifica
  como `backend_unreachable` con `last_error_code = "k8s_api_403"`, que ya
  distingue el caso en el payload sin una máquina de estados nueva.
- Persistencia del estado de debounce entre reinicios del pod (D-15): es
  puramente en memoria; un reinicio durante una degradación sostenida
  re-arma y puede volver a alertar ~10s después — comportamiento
  intencional, no un bug.

---

## 2. Arquitectura

```text
codex-shim (kubernetes/codex-shim)
  TokenStore.read()/write()
    try: read_namespaced_secret / patch_namespaced_secret
    except status == 404            -> SecretNotFound      -> not_configured
    except (todo lo demás)          -> StoreUnreachable      -> backend_unreachable
                                        code = k8s_api_<status> | k8s_transport
                                        reason = plantilla desde `code`, nunca str(exc)
  SessionManager
    _load_cached()/_do_refresh_locked(): mapean StoreUnreachable igual que SecretNotFound
  main.py  /internal/session         -> 200 {state, reason, last_error_code, ...} (nunca 500)
  proxy.py chat path (3 call sites)  -> 503 {"state": "backend_unreachable"} (nunca 500 opaco)

model-panel (kubernetes/model-panel)
  lifespan startup
    -> SessionAlertTicker.start()          (daemon thread, solo si secret+url están seteados)
         loop cada 5s hasta stop Event:
           CodexShimClient.get_session_status()   (cliente existente, timeout existente)
             raise -> {"state": "unreachable", ...}   (espeja main.py:397)
           SessionAlerter.observe(session, monotonic())
             "none"                -> siguiente tick
             "degraded"|"recovery" -> body = json.dumps(payload).encode()   (una sola vez)
                                       ts   = str(int(time.time()))
                                       POST HERMES_WEBHOOK_URL
                                         X-Webhook-Signature-V2: sign_v2(secret, body, ts)
                                         X-Webhook-Timestamp:    ts
                                       except Exception -> logger.warning, continue
  lifespan shutdown -> stop.set(); thread.join(timeout=5.0)
  GET /api/status -> sin cambios, diff cero, nunca toca el alerter
```

---

## 3. Contrato de `StoreUnreachable` (codex-shim)

```python
class StoreUnreachable(RuntimeError):
    def __init__(self, code: str, reason: str) -> None:
        super().__init__(reason)
        self.code = code      # "k8s_api_<status>" | "k8s_transport"
        self.reason = reason  # plantilla; nunca derivado de str(exc)
```

Clasificación por posición, no por `isinstance`: dentro del `try` que envuelve
únicamente `read_namespaced_secret`/`patch_namespaced_secret`, cualquier
excepción que no sea un 404 es un fallo de la llamada a la API. Esto evita
importar `ApiException`/`MaxRetryError` de `kubernetes`/`urllib3` en
`session.py` (que hoy no depende de ese paquete) y cubre cualquier
excepción futura del cliente sin mantener una lista de tipos.

**Garantía de no-material-de-token:** `reason` se construye siempre desde
`code`, nunca desde `str(exc)`. `code` solo puede ser `"k8s_api_<int>"` o
`"k8s_transport"` — estructuralmente imposible que contenga un traceback,
un header de respuesta o bytes del Secret.

---

## 4. Contrato de `SessionAlerter` (model-panel)

```python
ALERT_WORTHY_STATES = frozenset({
    "expired_needs_relogin", "refresh_failed", "backend_unreachable", "unreachable",
})
SESSION_ALERT_SUSTAIN_SECONDS = 10.0        # wall-clock, medido con time.monotonic()
SESSION_ALERT_POLL_INTERVAL_SECONDS = 5.0

class SessionAlerter:
    degraded_since: Optional[float]
    alerted_state: Optional[str]
    def observe(self, session: dict, now: float) -> AlertDecision: ...
```

Tabla de transición (idéntica a design.md §Interfaces/Contracts):

| Observado | `degraded_since` | `alerted_state` | Emite |
|---|---|---|---|
| no alert-worthy, era `None` | sigue `None` | `None` | `none` |
| alert-worthy, primera vez | se fija a `now` | `None` | `none` |
| mismo alert-worthy, `now - since < 10` | sin cambio | `None` | `none` |
| mismo alert-worthy, `now - since >= 10` | sin cambio | ← estado | `degraded` (una vez) |
| mismo alert-worthy, ya alertado | sin cambio | sin cambio | `none` (one-shot) |
| alert-worthy distinto | reinicia a `now` | se limpia | `none`, luego `degraded` a los 10s |
| `valid` tras haber alertado | `None` | `None` | `recovery` |
| `valid`/`rate_limited`/`not_configured` sin alerta previa | `None` | `None` | `none` |

`rate_limited` (auto-resuelve) y `not_configured` (estado estable
pre-bootstrap, no una regresión) están deliberadamente excluidos de
`ALERT_WORTHY_STATES`, como una constante separada de
`ALLOWED_SESSION_STATES` (D17) — hoy son complementarias por coincidencia,
no por acoplamiento.

---

## 5. Payload de alerta

```json
{
  "event": "session_degraded",
  "state": "expired_needs_relogin",
  "previous_state": "valid",
  "reason": "kubernetes API secret read failed (k8s_api_500)",
  "expires_at": null,
  "next_action": "Re-run bootstrap_login.md to restore the Codex session.",
  "sustained_seconds": 10.3,
  "source": "model-panel"
}
```

JSON plano con campos nombrados (no texto pre-renderizado), porque la ruta
`deliver_only` de Hermes renderiza un template sobre el body posteado —
ver el runbook de producción para el mapeo completo campo por campo y un
template mínimo de Telegram.

---

## 6. Firma HMAC-SHA256 V2

```python
def sign_v2(secret: str, body: bytes, timestamp: str) -> str:
    return hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
```

Espeja el contrato ya existente de Hermes
(`gateway/platforms/webhook.py:1136-1161`): header
`X-Webhook-Signature-V2`, `X-Webhook-Timestamp` obligatorio (unix
segundos, sin fallback a V1), ventana de ±300s, comparación con
`hmac.compare_digest`. El body se serializa a `bytes` **una sola vez** — la
firma se calcula y el POST se envía sobre exactamente los mismos bytes,
nunca un `json.dumps` re-serializado, porque un cambio de orden de claves
o separadores produciría una firma válida-pero-distinta que Hermes
rechazaría.

---

## 7. Amenazas consideradas

| Vector | Respuesta de diseño | Test |
|---|---|---|
| Material de token/Secret en `reason` | `reason` templada solo desde `code`, nunca `str(exc)` | excepción fabricada con texto tipo token → ausente en todas las superficies |
| Radio de explosión del secreto de firma | Secret `model-panel-webhook` solo en model-panel; `codex-shim/deployment.yaml` diff cero | test de manifiesto en ambas direcciones |
| Construcción de la request saliente | URL solo desde env del operador, nunca del caller; headers fijos; timeouts explícitos | timeout siempre seteado; ninguna entrada del caller llega a URL/headers |
| Replay / forgery | V2 con timestamp obligatorio; firma sobre bytes exactos; fail-closed si falta el secret | vector de firma; mismatch de re-serialización detectado |
| Autorización fail-closed (D17) | `backend_unreachable`/`unreachable` excluidos por el allow-list existente, sin editar `ALLOWED_SESSION_STATES` | `SwitchBlocked` con cero llamadas al cluster |
| Disponibilidad del monitor | El tick completo está envuelto en `try/except Exception`; solo el stop `Event` termina el loop | un tick malo no termina el loop |
| Alert storm / auto-DoS | Sustain wall-clock + one-shot por transición; intervalo de 5s por debajo del cap de 30s de refresh proactivo | outage largo emite exactamente una alerta |
| Shell/subproceso/VCS | N/A — solo HTTP y el cliente de Kubernetes | — |

Cobertura E2E real (Hermes → Telegram) **no realizada** — la ruta Hermes
vive fuera del tracking git de este repo (fuera de alcance). Verificación
manual vía el runbook de producción.

---

## 8. Convivencia y rollback

Sin migración de datos, sin schema, sin estado de alerta persistido
(D-15). La Pieza 1 (codex-shim) es aditiva: desplegar el shim convierte un
500 en un 200 clasificado; revertirlo restaura el 500. La Pieza 2
(model-panel) está inerte hasta que existan `HERMES_WEBHOOK_URL` y el
Secret `model-panel-webhook` — desplegar el código sin ellos no arranca
ningún ticker y no cambia ningún comportamiento observable, así que el
paso de manifest/Secret es el verdadero feature flag.

Rollback de la Pieza 2: borrar las dos entradas de env (el ticker deja de
arrancar en el próximo restart), luego revertir el módulo `app/alerts/` y
la línea de `lifespan=` en `main.py`.

---

## 9. Checklist de implementación

- [x] `StoreUnreachable` + clasificación en `store.py` (`read()`/`write()`)
- [x] Sanitización de `reason` (plantilla desde `code`, nunca `str(exc)`)
- [x] `backend_unreachable` en `SessionState` + wiring de `session.py`
- [x] `main.py` de codex-shim: `/internal/session` responde 200, no 500
- [x] `proxy.py`: 503 clasificado en los tres call sites del chat path
- [x] Regresión D17 (`ALLOWED_SESSION_STATES` sin editar, cero llamadas al cluster)
- [x] `alerts/signing.py` (`sign_v2`, puro)
- [x] `alerts/state.py` (`SessionAlerter`, tabla de transición completa)
- [x] `alerts/ticker.py` (hilo daemon, entrega HMAC V2, fail-closed, captura total)
- [x] Wiring de `lifespan=` en `main.py` de model-panel, `/api/status` diff cero
- [x] `deployment.yaml` de model-panel: env + `secretKeyRef`; `deployment.yaml` de codex-shim diff cero
- [x] Regresión de `panel.js` (`sessionStateClass` sin cambios, default `"bad"`)
- [x] Runbook de producción (aprovisionamiento manual del lado Hermes)
- [x] Este spec numerado

---

## 10. Referencias

- `openspec/changes/codex-shim-session-alerts/design.md` — decisiones
  arquitectónicas completas (D-01 a D-20), hallazgos verificados (F-1 a
  F-7), forecast de entrega.
- `openspec/specs/codex-session-state/spec.md` — spec delta de codex-shim.
- `openspec/specs/session-degradation-alerting/spec.md` — spec delta de
  model-panel.
- `docs/production/model-panel-session-alerts-runbook.md` — runbook de
  aprovisionamiento manual del lado Hermes.
- `docs/services/model-panel.md` — doc de servicio actualizado con las
  nuevas env vars.
