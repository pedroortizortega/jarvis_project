# JARVIS Spec 015 - Software Design Document (SDD)
## Memory Router: segundo adaptador de backend (Hindsight)

**Estado:** Implementado (código + tests unitarios) — sin validar contra una instancia real de Hindsight; bloqueante de despliegue heredado de spec 012 §8 resuelto (ver spec 014 §8), despliegue real pendiente de ejecutar
**Fecha:** 2026-08-19
**Versión:** 1.0
**Autor:** Pedro Ortiz (vía agente `sdd-apply`)

---

## 0. Por qué existe este spec

Spec 012 (Fase 1 de Memory Router) afirmó que "agregar un segundo backend
requiere solo un nuevo adaptador más registro — sin cambios al router", pero
la envió con exactamente un adaptador (Engram). Con un solo adaptador el
seam de plugin (`Registry` + entry points de `memory_router.backends`) no
estaba probado: el contrato `MemoryBackend` podía estar silenciosamente
sobreajustado al transporte MCP-sobre-stdio de Engram sin que nadie lo
notara hasta el segundo backend real.

Hindsight (`ghcr.io/vectorize-io/hindsight`, MIT, arXiv 2512.12818) es el
segundo backend natural: es estructuralmente distinto de Engram (transporte
HTTP en vez de subproceso stdio, namespacing nativo por memory bank), así
que su adaptador confirma o refuta el seam. Hasta que existiera un adaptador
no-stdio, cada backend futuro (Graphiti, Honcho, Cognee, Obsidian) cargaba
un riesgo de re-arquitectura sin cuantificar.

Relación con specs anteriores: extiende spec 012 (Memory Router Fase 1) —
reutiliza exactamente su `MemoryBackend` Protocol, su `Registry`, su
dispatcher de degradación y su modelo de namespaces/permisos, sin tocar
ninguno de esos archivos.

---

## 1. Alcance de este change

**Dentro de alcance:**
- `HindsightBackend` en
  `hermes-native/memory-router/src/memory_router/backends/hindsight.py`,
  implementando `capabilities()/health()/store()/search()` del
  `MemoryBackend` Protocol existente.
- Transporte HTTP propio (`_HttpJsonClient`, stdlib `urllib.request`),
  reemplazando el patrón de subproceso stdio de Engram.
- `capabilities().verbs = frozenset({"store", "search"})` — `reflect`
  explícitamente ausente y verificado ausente por test.
- Mapeo namespace → `bank_id` de Hindsight (análogo al prefijo `ns:` de
  `topic_key` en el adaptador Engram). El conjunto de namespaces declarado
  por Hindsight **no se superpone** con el de Engram.
- Auth configurable por entorno: sin auth local o bearer token de Hindsight
  Cloud, sin modo hardcodeado.
- Registro únicamente vía entry point:
  `hindsight = "memory_router.backends.hindsight:HindsightBackend"` bajo el
  grupo existente `memory_router.backends`.
- Tests unitarios con transporte HTTP stubbed (sin instancia real de
  Hindsight).

**Fuera de alcance:** cablear `/memory/reflect` end-to-end (contratos,
permisos por rol, dispatcher — decisiones de producto/seguridad
pendientes); manifiestos Kubernetes o despliegue real de Hindsight (no
existe instancia viva); decidir el modo de auth de producción para el
clúster; merge/ranking/dedup cruzado entre backends.

---

## 2. Arquitectura

```text
cliente
  → [router core: identidad → rol → namespace → permisos] (sin cambios, spec 012)
  → [registry: match por capacidad, entry points]
       |-> namespace en /global, /user/master, /agents/*  → adaptador Engram (stdio)
       |-> namespace en /projects/*                        → adaptador Hindsight (HTTP)
  → adaptador Hindsight → _HttpJsonClient (urllib.request) → Hindsight HTTP API
       |-> retain (store) / recall (search) / create (lazy bank) / health
```

Ningún archivo de router core (`app.py`, `registry.py`, `contracts.py`,
`permissions.py`, `namespaces.py`, `identity.py`, `journal.py`) fue
modificado para habilitar esto — criterio de aceptación de este change, no
una preferencia. `Registry._load_entry_points` ya llama a
`backend_class()` sin argumentos por cada entry point registrado, y
`app.py::_fallback_chain` ya re-consulta el registry por cada namespace
candidato del fallback jerárquico; ambos mecanismos funcionan sin cambios
para un segundo adaptador.

Implementación: mismo paquete Python 3.11 `memory-router`, sin
dependencias nuevas — `_HttpJsonClient` usa `urllib.request` de la stdlib.

---

## 3. Propiedad de namespaces (la decisión diferida de spec 012)

Hindsight es dueño exclusivo de **`("/projects/*",)`**. El adaptador Engram
se redujo de sus cuatro namespaces originales a
**`("/global", "/user/master", "/agents/*")`** — ya no declara
`/projects/*`.

| Punto | Razonamiento |
|---|---|
| Por qué `/projects/*` | Es la única raíz naturalmente multi-instancia y acotada de las cuatro que reconoce `namespaces.py` — mapea 1:1 sobre el modelo de memory bank de Hindsight (un bank por proyecto). `/global` y `/user/master` son singletons; dividirlos le daría a Hindsight un solo bank y no probaría nada del seam. |
| Por qué reducir Engram | `namespaces.py` admite exactamente cuatro formas y Engram las cubría todas. La no-superposición solo es alcanzable reduciendo Engram — `engram.py` es un adaptador, no router core, así que esto respeta el criterio de aceptación de "sin cambios al core". Ningún test afirmaba el tuple original de 4 namespaces de Engram. |
| Costo de migración | Ninguno. El router sigue sin desplegar (spec 012 §8), así que no existe dato `ns:/projects/*` en Engram que migrar. |
| Beneficio del seam | Una búsqueda en `/projects/foo` ahora recorre Hindsight → (sin resultados) → `/agents/{id}` Engram → `/global` Engram, ejercitando el fallback cross-backend con cero cambios al router. |

---

## 4. Contrato del adaptador Hindsight

```python
class HindsightBackend:
    def __init__(self, *, transport=None, base_url=None, auth_mode=None,
                 token=None, bank_prefix=None, timeout=None): ...
    def capabilities(self) -> Capabilities: ...   # verbs={"store","search"}, namespaces=("/projects/*",)
    def health(self) -> Health: ...                # GET /health, nunca lanza
    def store(self, req: StoreRequest) -> StoreResult: ...   # → retain
    def search(self, req: SearchRequest) -> SearchResult: ... # → recall
```

`transport(method, url, headers, body) -> (status, bytes)` es el seam de
test, exactamente análogo a `spawn` en el adaptador Engram: se inyecta un
stub en los tests, nunca toca la red real.

### Superficie de wire format (revisable)

```python
ENDPOINTS = {
    "retain": "/v1/banks/{bank_id}/retain",   # POST {content, metadata} -> {id}
    "recall": "/v1/banks/{bank_id}/recall",   # POST {query}             -> {results:[{content,score}]}
    "create": "/v1/banks",                    # POST {bank_id}
    "health": "/health",                      # GET
}
```

`ENDPOINTS` es la única superficie de formato de wire, aislada en un solo
dict — no se verificó contra documentación autoritativa de Hindsight ni
contra una instancia real (ver §7, preguntas abiertas).

### Mapeo namespace → bank_id

`/projects/lector-ine` → `projects-lector-ine`: se quita la barra inicial,
se reemplazan las barras restantes por `-`, se pasa a minúsculas, y se
antepone el prefijo opcional `HINDSIGHT_BANK_PREFIX`. El resultado se
re-valida contra `^[a-z0-9][a-z0-9_-]*$` — falla cerrado si un namespace
malicioso (traversal, wildcard) no produce un segmento de path legal, en
defensa en profundidad de la validación que ya hace `namespaces.py` aguas
arriba.

### Ciclo de vida del bank (lazy create)

`retain` que responde `404` dispara una única secuencia
`POST /v1/banks` (create) → reintento de `retain` una vez. Cualquier otra
falla, o una segunda falla tras el reintento, se propaga como
`BackendUnavailableError`.

### Config por entorno

| Variable | Default |
|---|---|
| `HINDSIGHT_BASE_URL` | `http://hindsight.mcps.svc.cluster.local:8080` |
| `HINDSIGHT_AUTH_MODE` | `bearer` si hay token, si no `none` |
| `HINDSIGHT_TOKEN` | `""` |
| `HINDSIGHT_BANK_PREFIX` | `""` |
| `HINDSIGHT_TIMEOUT_SECONDS` | `10` |

Sin modo de auth hardcodeado en código: `HINDSIGHT_AUTH_MODE` es explícito,
con default derivado únicamente de si hay token configurado.

Fuente:
`hermes-native/memory-router/src/memory_router/backends/hindsight.py`.

---

## 5. Semántica degradada

Sin cambios respecto a spec 012 §5 — el dispatcher existente ya maneja
`BackendUnavailableError` sin modificación. Cualquier falla de transporte
(conexión, status no-2xx, respuesta indecodificable) del adaptador
Hindsight lanza `BackendUnavailableError("hindsight", reason)` y nada más:

- `store` degradado → el router encola en el journal durable y responde
  `202 {"status": "pending", ...}`.
- `search` degradado → el router devuelve resultados de los backends sanos
  más un marcador explícito `unavailable` para Hindsight.

El token de auth nunca aparece en un `reason` de
`BackendUnavailableError` ni en `Health.reason` — verificado por test.

---

## 6. Amenazas consideradas

| Frontera | Respuesta de diseño | Test |
|---|---|---|
| Selección de namespace / bank_id | `bank_id` derivado solo de un namespace ya validado por `namespaces.py`, vía función sanitizadora pura, re-validado contra `^[a-z0-9][a-z0-9_-]*$`, falla cerrado | `test_memory_router_hindsight_adapter.py::BankIdSanitizerTests` |
| Construcción de la request saliente (reemplaza la fila de subproceso de spec 012) | URL solo desde config del router (`base_url` + `ENDPOINTS` + `bank_id` sanitizado); `content`/`metadata`/`query` del llamador viajan solo en el body JSON; el conjunto de headers es fijo; timeout siempre configurado | `HindsightAdapterSecurityTests` |
| Manejo de secretos | Token solo desde entorno, nunca logueado, nunca interpolado en un `reason` de error | `HindsightAdapterSecretHandlingTests` |
| Degradación de backend | Error de conexión, status no-2xx, JSON malformado → siempre `BackendUnavailableError`, nunca otro tipo de excepción | `HindsightAdapterDegradationTests` |

---

## 7. Convivencia y rollback

Engram sigue sirviendo `/global`, `/user/master` y `/agents/*` sin cambios
de comportamiento (solo se redujo su tuple declarado de namespaces).
Rollback: eliminar `backends/hindsight.py`, su archivo de test, la línea de
entry point en `pyproject.toml`, y restaurar `/projects/*` en
`engram.py`. Como el registro es vía entry point y ningún archivo de router
core fue tocado, el rollback restaura exactamente el comportamiento de
Fase 1. Sin migración de datos — el router sigue sin desplegar.

---

## 8. Preguntas abiertas / bloqueantes heredados

- **Bloqueante de despliegue (heredado de spec 012 §8):** resuelto — ver
  spec 014 §8 (propiedad del namespace `mcps` confirmada por el owner del
  proyecto: creado vía OpenCode como hub de servicios MCP para la red
  local). Este change sigue sin desplegar nada; el adaptador solo se
  probó contra un transporte HTTP stubbed.
- Las rutas y payloads exactos de `ENDPOINTS` están **sin verificar**
  contra una instancia real de Hindsight o documentación autoritativa —
  `ENDPOINTS` es la única superficie revisable si cambian.
- ¿Hindsight auto-crea banks en `retain` (dejando la rama 404 como código
  muerto), o devuelve otro status para un bank desconocido?
- Naming de banks: ¿Hindsight impone un límite de longitud o un charset
  más estricto que `[a-z0-9_-]`? Nombres de proyecto largos podrían
  necesitar truncamiento con sufijo hash.
- Esquema de resultado de `recall`: ¿el score viene normalizado de forma
  comparable al de Engram, de cara a un futuro ranking cruzado?
- Validación contra una instancia viva de Hindsight sigue siendo un
  follow-up explícito — solo hay prueba a nivel unitario.

---

## 9. Checklist de implementación

- [x] `HindsightBackend` (`backends/hindsight.py`) — `capabilities/health/store/search`, con test
- [x] `_HttpJsonClient` (transporte HTTP inyectable, stdlib `urllib.request`), con test
- [x] `_bank_id()` — sanitización namespace→bank, falla cerrado ante traversal/wildcard, con test
- [x] Lazy create-on-404 con reintento único, con test
- [x] Auth config-driven (`none`/`bearer`, sin hardcode), con test
- [x] Degradación (`BackendUnavailableError` en los tres casos: conexión, status, JSON malformado), con test
- [x] Secretos nunca en `reason` de error, con test
- [x] `isinstance(HindsightBackend(), MemoryBackend)`, con test
- [x] `capabilities().verbs` excluye `reflect`, con test
- [x] Namespaces Hindsight/Engram sin superposición, con test
- [x] `backends/engram.py` — namespaces reducidos a `("/global", "/user/master", "/agents/*")`
- [x] `pyproject.toml` — entry point `hindsight` registrado
- [x] Verificación manual: `Registry().all_backends()` devuelve ambos adaptadores tras reinstalar el paquete
- [x] `git diff` confirma cero cambios en router core
- [ ] Validación contra instancia real de Hindsight — fuera de alcance, follow-up explícito
- [ ] Despliegue real — desbloqueado (spec 014 §8), pendiente de ejecutar

---

## 10. Referencias

- `openspec/changes/hindsight-backend/proposal.md`, `design.md`,
  `specs/memory-backend-adapters/spec.md` (delta) — artefactos SDD
  completos de este change.
- `specs/014_memory_router.md` (spec 012 en el checklist de esta doc,
  numerado 014 en `specs/`) — Fase 1 de Memory Router, base que este
  change extiende sin modificar.
- `hermes-native/memory-router/src/memory_router/backends/hindsight.py` —
  código fuente del adaptador.
- `tests/test_memory_router_hindsight_adapter.py` — suite de tests
  unitarios (`python -m unittest discover -s tests`).

---

**Fin del SDD**
