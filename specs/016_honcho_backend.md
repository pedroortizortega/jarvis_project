# JARVIS Spec 016 - Software Design Document (SDD)
## Memory Router: verbo `reflect` cableado end-to-end (adaptador Honcho)

**Estado:** Validado contra una instancia real de Honcho, de punta a punta (2026-08-21/22) — encontró y corrigió 3 bugs reales (§9.1). Circuito completo probado: ingesta real de mensaje → derivación real (LLM vía `codex-shim`, sesión OAuth de Codex) → embeddings reales (1024 dims, vía `local-embeddings`, self-hosted) → `HonchoBackend.reflect()` del repo devolviendo el hecho correcto. Cero API keys de terceros usadas. Bloqueante de despliegue heredado de spec 012 §8 / spec 015 §8 resuelto (ver spec 014 §8); despliegue de una instancia real de Honcho en el clúster sigue fuera de alcance de esta validación (se probó contra un stack Docker Compose efímero, ya destruido)
**Fecha:** 2026-08-19
**Versión:** 1.0
**Autor:** Pedro Ortiz (vía agente `sdd-apply`)

---

## 0. Por qué existe este spec

`reflect` es el tercer verbo declarado del Memory Router y estaba muerto en
tres capas simultáneamente: `Dispatcher.reflect()` autenticaba y luego
lanzaba incondicionalmente `501 not_implemented`, con un comentario
obsoleto que afirmaba que el verbo "lands with Hindsight" — falso, spec 015
excluyó explícitamente `reflect` de Hindsight. `_ROLE_TABLE` no tenía
ninguna fila `reflect` para ningún rol, así que el deny-by-default habría
bloqueado el verbo aunque el dispatcher funcionara. Y el `MemoryBackend`
Protocol no tenía ningún método `reflect()` que un adaptador pudiera
implementar. Con las tres capas rotas a la vez, el verbo no podía
ejercitarse en absoluto.

Honcho (Plastic Labs, open source) cierra esta brecha. Su API Dialectic
corre jobs asíncronos que derivan *conclusiones* sobre un usuario — creencias
estilo teoría-de-la-mente, distintas del historial crudo de conversación que
almacena Engram. Eso es exactamente la semántica para la que se reservó
`reflect`, y como es un sistema de modelado de usuario, `/user/master` es su
namespace natural y único en esta primera porción.

Relación con specs anteriores: extiende spec 012 (Memory Router Fase 1) y
spec 015 (adaptador Hindsight) sin modificar `registry.py`. A diferencia de
spec 015, este change **sí** toca `contracts.py`, `app.py` y
`permissions.py` — es el primer change no-aditivo al core-contract desde la
Fase 1, y gasta deliberadamente esos edits core que spec 015 rechazó, en la
superficie más angosta posible.

---

## 1. Alcance de este change

**Dentro de alcance:**
- `HonchoBackend` en
  `hermes-native/memory-router/src/memory_router/backends/honcho.py` —
  transporte HTTP con un seam `transport(method, url, headers, body)`
  inyectable, auth config-driven vía entorno, replicando la forma de
  `hindsight.py`.
- `capabilities()` = nombre `honcho`, `verbs = frozenset({"reflect"})`,
  `namespaces = ("/user/master",)`. Sin `store`, sin `search` — Engram
  conserva la propiedad exclusiva de store/search en `/user/master`; esto
  es verb-scoped y no colisiona.
- `contracts.py`: nuevos dataclasses `ReflectRequest`/`ReflectResult`/
  `Conclusion`, más un Protocol `ReflectiveBackend` separado que deja la
  conformidad de `MemoryBackend` intacta para Engram y Hindsight (ninguno
  implementa `reflect`).
- `app.py`: reescritura de `Dispatcher.reflect()` para correr el pipeline
  real identidad → namespace → permiso → `backends_for(verb="reflect", ...)`,
  con degradación vía `BackendUnavailableError`. Se eliminan el comentario
  obsoleto de Hindsight y el hint `"phase": "hindsight"` en
  `_dispatch_error_payload`. Se corrigen dos bugs preexistentes: el
  handler REST nunca escribía una respuesta para reflect, y el splat
  `**body` causaba `TypeError` ante una clave inesperada en el body.
- `permissions.py`: filas explícitas de `reflect` en `_ROLE_TABLE`.
- Registro vía entry point bajo el grupo existente `memory_router.backends`.
- Tests unitarios con transporte stubbed; sin instancia viva de Honcho.

**Fuera de alcance:** `reflect` en `/projects/*`, `/agents/*`, o `/global`
(solo `/user/master`); adaptadores Graphiti, Cognee, Obsidian; aprovisionamiento
de cuenta/API-key de Honcho, manifiestos de despliegue, o la decisión
hosted-vs-self-hosted; otorgar a Honcho `store` o `search`, o cualquier
merge/dedup cross-backend con Engram; write-back de conclusiones derivadas
hacia Engram.

---

## 2. Arquitectura

```text
cliente
  → [router core: identidad → rol → namespace → permisos] (mismo pipeline
     que store/search/context, extendido para el verbo "reflect")
  → [registry: match por capacidad, entry points]
       |-> namespace en /global, /user/master(store/search), /agents/*  → adaptador Engram (stdio)
       |-> namespace en /projects/*                                      → adaptador Hindsight (HTTP)
       |-> namespace /user/master, verbo reflect                         → adaptador Honcho (HTTP)
  → adaptador Honcho → _HttpJsonClient (urllib.request) → Honcho Dialectic API
       |-> POST .../chat (dialectic query) / health
```

`registry.py` no fue modificado: `backends_for` solo llama a
`capabilities()` y nunca hace `isinstance`, así que un adaptador que no es
`MemoryBackend` (Honcho implementa `ReflectiveBackend`, no `MemoryBackend`)
enruta correctamente sin cambios. `Dispatcher.reflect()` refleja
`context()` en vez de `search()` — un solo namespace, sin
`_fallback_chain`, porque `/user/master` no tiene padre.

Implementación: mismo paquete Python 3.11 `memory-router`, sin
dependencias nuevas — `_HttpJsonClient` usa `urllib.request` de la stdlib,
idéntico al patrón de `hindsight.py`.

---

## 3. Separación del contrato: `ReflectiveBackend` vs `MemoryBackend`

La decisión de diseño central de este change: `MemoryBackend` permanece
**byte-idéntico**. Se agrega un Protocol nuevo y angosto,
`@runtime_checkable class ReflectiveBackend(Protocol)`, con
`capabilities()/health()/reflect()`. `isinstance(EngramBackend(), MemoryBackend)`
e `isinstance(HindsightBackend(), MemoryBackend)` siguen pasando con sus
tests de conformidad sin modificar — ninguno implementa `ReflectiveBackend`,
y `runtime_checkable` solo verifica presencia de métodos (duck typing
estructural), no herencia.

| Opción considerada | Por qué se rechazó |
|---|---|
| `reflect()` no-op por default en `MemoryBackend` | Todo backend reclamaría silenciosamente un verbo que no puede servir |
| Herencia ABC forzando subclase | Rompe la convención de structural typing que ya usa todo el paquete |
| **Protocol separado (elegido)** | La selección de verbo del registry ya es el gate; conformidad del Protocol base para Engram/Hindsight queda demostrablemente intacta |

El dispatcher solo llega a `reflect()` a través de
`backends_for(verb="reflect", namespace=...)` — nunca asume
estructuralmente que todo `MemoryBackend` implementa `reflect()`.

---

## 4. Contrato del adaptador Honcho

```python
class HonchoBackend:
    def __init__(self, *, transport=None, base_url=None, auth_mode=None,
                 token=None, workspace_id=None, timeout=None): ...
    def capabilities(self) -> Capabilities: ...   # verbs={"reflect"}, namespaces=("/user/master",)
    def health(self) -> Health: ...                # GET /healthz, nunca lanza
    def reflect(self, req: ReflectRequest) -> ReflectResult: ...  # → dialectic chat
```

`transport(method, url, headers, body) -> (status, bytes)` es el seam de
test, exactamente análogo al de `hindsight.py`.

### Superficie de wire format (revisable, sin verificar)

```python
ENDPOINTS = {
    "dialectic": "/v2/workspaces/{workspace_id}/peers/{peer_id}/chat",  # POST {query} -> {content}|202
    "health": "/healthz",
}
```

`ENDPOINTS` es la única superficie de formato de wire, aislada en un solo
dict — no se verificó contra documentación autoritativa de Honcho ni contra
una instancia real (ver §8, preguntas abiertas).

### Mapeo namespace → peer ref

`/user/master` → `(workspace_id, "master")`, donde `workspace_id` viene de
`HONCHO_WORKSPACE_ID` (default `"jarvis"`). Ambos componentes se
re-validan contra `^[a-z0-9][a-z0-9_-]*$` — falla cerrado ante traversal o
wildcard, en defensa en profundidad de la validación que ya hace
`namespaces.py` aguas arriba (mismo patrón que `_bank_id` en spec 015).

### Semántica asíncrona de Dialectic

Honcho corre jobs asíncronos: `reflect` nunca bloquea ni sintetiza una
conclusión placeholder. Un `202` o un body `2xx` vacío produce
`ReflectResult(status="pending")`; un `2xx` con `content` produce
`ReflectResult(status="ready", conclusions=(...))`.

### Config por entorno

| Variable | Default |
|---|---|
| `HONCHO_BASE_URL` | `http://honcho.mcps.svc.cluster.local:8000` |
| `HONCHO_AUTH_MODE` | `bearer` si hay token, si no `none` |
| `HONCHO_TOKEN` | `""` |
| `HONCHO_WORKSPACE_ID` | `jarvis` |
| `HONCHO_TIMEOUT_SECONDS` | `10` |

Sin modo de auth hardcodeado en código, idéntico al patrón de spec 015.

Fuente: `hermes-native/memory-router/src/memory_router/backends/honcho.py`.

---

## 5. Pipeline del dispatcher (`Dispatcher.reflect()`)

```text
POST /memory/reflect
  -> _parse_reflect_body(body)            # role, namespace, query — sin TypeError por **body
  -> _authenticate -> _validate_namespace -> _authorize(verb="reflect")
  -> registry.backends_for(verb="reflect", namespace)   # namespace único, sin fallback chain
       |- []                              -> {"status": "no_backend", "conclusions": [], "unavailable": []}
       |- ReflectResult.status "ready"    -> conclusions[]
       |- ReflectResult.status "pending"  -> {"status": "pending", "conclusions": []}
       `- BackendUnavailableError         -> unavailable[]; status "degraded" si todos fallan
  -> 200 {"namespace", "status", "conclusions", "unavailable"}
```

Ausencia de backend reflect-capaz nunca produce fallo genérico ni éxito
fabricado — devuelve `"status": "no_backend"` explícito y distinguible. Un
transporte caído nunca se propaga como fallo de request — se degrada a
`"status": "degraded"` con el backend listado en `unavailable`, exactamente
el mismo contrato de degradación que store/search desde spec 012.

El REST handler (`do_POST`) ahora **sí escribe una respuesta** para
`/memory/reflect` — antes llamaba a `dispatcher.reflect` y no respondía
nada, un bug real corregido en este change. `RestClient.reflect` normaliza
vía `_parse_reflect_body` en vez de pasar `**kwargs` crudo, cerrando el
`TypeError` ante una clave inesperada del body.

---

## 6. Permisos

`_ROLE_TABLE` — filas `reflect` agregadas únicamente sobre `user_master`:

| Rol | `reflect` en `user_master` |
|---|---|
| `jarvis` | permitir |
| `scientist` | permitir |
| `coder` | denegar |

Ninguna fila `reflect` se agrega a `global`, `projects`, `agents_self`, o
`agents_other` — `authorize()` ya resuelve un verbo ausente vía
`_ROLE_TABLE.get(...).get(kind, frozenset())`, así que `reflect` en
cualquier otro namespace kind queda denegado por defecto sin código nuevo,
verificado explícitamente por test en vez de asumido.

Fuente: `hermes-native/memory-router/src/memory_router/permissions.py`.

---

## 7. Amenazas consideradas

| Frontera | Aplicabilidad | Respuesta de diseño | Test |
|---|---|---|---|
| Selección de namespace | Aplicable | `reflect` acotado a un único namespace validado, sin fallback chain; workspace/peer ids re-validados, falla cerrado | `PeerRefTests` |
| Autorización | Aplicable | Filas explícitas solo en `user_master`; deny-by-default sin cambios en el resto | `ReflectPermissionsTests` |
| Construcción de la request saliente | Aplicable | URL solo desde config del router + ids sanitizados; `query` del llamador viaja solo en el body JSON; conjunto de headers fijo; timeout siempre configurado | `HonchoAdapterReflectTests` |
| Manejo de secretos | Aplicable | Token solo desde entorno, nunca logueado, nunca interpolado en un `reason` de error | `HonchoAdapterSecretHandlingTests` |
| Subprocess / VCS / PR automation | N/A — solo HTTP, sin shell, sin VCS | — | Ninguno |

---

## 8. Convivencia y rollback

Dos partes, porque este change no es solo registro. (1) Eliminar la línea
del entry point, `backends/honcho.py`, y su archivo de test — esto solo
deshabilita el enrutamiento de reflect, ya que
`backends_for(verb="reflect")` devuelve vacío y reflect se degrada al
`no_backend` explícito, no a un crash. (2) Revertir los commits de
`contracts.py`/`app.py`/`permissions.py` para restaurar el stub `501`.
Ambas partes son reverts de código puro en una feature branch; sin
migración de datos, sin estado almacenado, sin limpieza del lado Honcho más
allá de borrar un workspace no usado.

---

## 9. Checklist de implementación

- [x] `contracts.py` — `ReflectRequest`/`ReflectResult`/`Conclusion`/
  `ReflectiveBackend`, `MemoryBackend` sin tocar, con test
- [x] `HonchoBackend` (`backends/honcho.py`) — `capabilities/health/reflect`,
  con test
- [x] `_HttpJsonClient` (transporte HTTP inyectable, stdlib
  `urllib.request`), con test
- [x] `_peer_ref()` — mapeo namespace→peer ref, falla cerrado ante
  traversal/wildcard, con test
- [x] Semántica `ready`/`pending`, nunca fabricada, con test
- [x] Auth config-driven (`none`/`bearer`, sin hardcode), con test
- [x] Degradación (`BackendUnavailableError` en los tres casos: conexión,
  status, JSON malformado), con test
- [x] Secretos nunca en `reason` de error, con test
- [x] `isinstance(HonchoBackend(), ReflectiveBackend)` y
  `not isinstance(HonchoBackend(), MemoryBackend)`, con test
- [x] `isinstance(EngramBackend(), MemoryBackend)` y
  `isinstance(HindsightBackend(), MemoryBackend)` sin modificar, verificado
  verde
- [x] `Dispatcher.reflect()` reescrito — pipeline real, nunca `501`, con
  test
- [x] REST handler `/memory/reflect` ahora responde (bug corregido), con
  test
- [x] `RestClient.reflect` normaliza vía `_parse_reflect_body` (bug
  corregido), con test
- [x] Comentario `"lands with Hindsight"` y hint `"phase": "hindsight"`
  eliminados, verificado por test de grep sobre el código fuente
- [x] `_ROLE_TABLE` — filas `reflect` para `jarvis`/`scientist` en
  `user_master`; `coder` sin cambios (denegado), con test tabla-driven
- [x] Paridad MCP/REST para `memory_reflect`, con test
- [x] `pyproject.toml` — entry point `honcho` registrado
- [x] `git diff --stat` confirma cero cambios en `registry.py`
- [x] Suite completa (`python -m unittest discover -s tests`) verde: 161
  tests
- [x] Validación contra instancia real de Honcho (2026-08-21/22) — ver §9.1
- [x] Despliegue real — memory-router desplegado y verificado en `mcps`
  (spec 014 §8-9); instancia real de Honcho en el clúster sigue fuera de
  alcance de esta validación (probado contra Docker Compose efímero)
- [ ] Confirmar si `reflect` alguna vez necesita un path de ingestión
  (alimentar a Honcho con contenido de conversación) — diferido por el
  proposal

---

### 9.1 Validación contra una instancia real (2026-08-21/22)

Levantado `plastic-labs/honcho` (self-hosted, Postgres+Redis+API+deriver
vía Docker Compose) contra dos servicios propios, ninguno de terceros:
`codex-shim` (`llms/codex-shim` en el clúster) para el LLM del dialectic
chat y de la derivación de entidades, vía la sesión OAuth de Codex; y
`local-embeddings` (`llms/local-embeddings`, spec 021, self-hosted,
`intfloat/multilingual-e5-large`, 1024 dims) para los embeddings —
cerrando la dependencia de una API key de OpenAI que había bloqueado la
primera validación en vivo.

Encontrados y corregidos **3 bugs reales** en
`hermes-native/memory-router/src/memory_router/backends/honcho.py`,
confirmados contra el código fuente real de `plastic-labs/honcho`
(`src/main.py`, `src/routers/peers.py`, `src/schemas/api.py`) y contra el
server corriendo:

| | Antes (bug) | Real |
|---|---|---|
| Prefijo de API | `/v2` | `/v3` |
| Health | `/healthz` | `/health` |
| Timeout configurable | Ignorado — `_default_transport` hardcodeaba `timeout=10` sin importar `self._timeout` | Honrado de verdad vía `functools.partial` (bug sistémico, se repetía idéntico en los 5 adaptadores HTTP — corregido en todos) |

También confirmado: `DialecticResponse` real solo tiene el campo
`content` — no existe `confidence` en absoluto. `result.get("confidence",
0.0)` del adaptador nunca falla, pero el valor siempre es el default
`0.0`, nunca algo que Honcho realmente mande — documentado, no es bug.

Circuito completo probado con el propio `HonchoBackend` del repo (no
curl crudo): `health()` → OK contra el server real; creada una sesión
real (`POST /v3/workspaces/jarvis/sessions`) y enviado un mensaje real
(`POST .../messages`); esperada la derivación real (LLM vía
`codex-shim`); `reflect()` devolvió el hecho correcto extraído
(`"Pedro prefiere el modo oscuro en todos sus editores y terminales."`)
vía el dialectic chat real. Un detalle operativo encontrado en el camino:
el bootstrap de un Honcho self-hosted nuevo necesita correr
`scripts/configure_embeddings.py --yes` después de las migraciones para
ajustar la dimensión de la columna `pgvector` (crea `vector(1536)` por
default sin importar `EMBEDDING_VECTOR_DIMENSIONS`) — documentado acá
porque no está en ningún README de Honcho de forma obvia. Suite completa
del repo (356 tests) verde después de los fixes. Containers de prueba
destruidos al terminar; ninguna credencial quedó en disco.

Sigue pendiente, fuera de alcance de esta validación: desplegar Postgres
+ Redis + el server de Honcho como infraestructura real y persistente
del clúster (esto solo probó el adaptador contra una instancia efímera
local).

---

## 10. Referencias

- `openspec/changes/honcho-backend/proposal.md`, `design.md`,
  `specs/{memory-router-interfaces,memory-access-control,memory-backend-adapters}/spec.md`
  (delta) — artefactos SDD completos de este change.
- `specs/014_memory_router.md` — Fase 1 de Memory Router (spec 012), base
  que este change extiende.
- `specs/015_hindsight_backend.md` — segundo adaptador (Hindsight), primer
  change puramente aditivo; este change es el primer change no-aditivo
  desde entonces.
- `hermes-native/memory-router/src/memory_router/backends/honcho.py` —
  código fuente del adaptador.
- `hermes-native/memory-router/src/memory_router/app.py` — dispatcher y
  superficies REST/MCP.
- `hermes-native/memory-router/src/memory_router/permissions.py` — tabla de
  roles.
- `tests/test_memory_router_honcho_adapter.py`,
  `tests/test_memory_router_app.py`, `tests/test_memory_router_permissions.py`
  — suites de tests unitarios (`python -m unittest discover -s tests`).

---

**Fin del SDD**
