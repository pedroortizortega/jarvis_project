# JARVIS Spec 019 - Software Design Document (SDD)
## Memory Router: adaptador Graphiti para `reflect` en `/global` y `/agents/*`

**Estado:** Validado contra una instancia real de Graphiti (2026-08-21) — encontró y corrigió 2 bugs reales en `ENDPOINTS` (§9.1); bloqueante de despliegue heredado de spec 012 §8 / spec 015 §8 / spec 016 §8 / spec 017 §8 resuelto (ver spec 014 §8), despliegue real de memory-router en `mcps` completo (spec 014 §9) — infraestructura dedicada de Graphiti (Neo4j/FalkorDB en el clúster) sigue fuera de alcance de esta fase
**Fecha:** 2026-08-20
**Versión:** 1.0
**Autor:** Pedro Ortiz (vía agente `sdd-apply`)

---

## 0. Por qué existe este spec

`reflect` ya funciona en `/user/master` (Honcho, spec 016) y `/projects/*`
(Cognee, spec 017). `/global` y `/agents/*` seguían seleccionando cero
backends reflect-capaces y devolviendo `no_backend` — las últimas dos
celdas sin reclamar en toda la matriz namespace×verbo para este verbo.

Graphiti (Zep AI, open source) es un knowledge graph temporal: se ingieren
episodios, un LLM extrae entidades y relaciones, y los edges cargan
intervalos de validez. `search_facts` devuelve hechos derivados sobre
episodios acumulados con una dimensión temporal — una conclusión derivada,
no un hit de retrieval, que es semántica de `reflect`. `group_id` namespacea
episodios, mapeando limpiamente sobre los namespaces del router (la misma
forma que usa `bank_id` de Hindsight).

Este change cierra el verbo `reflect` como una regla explicable ("cada raíz
fija reflecta") en vez de una lista de excepciones. Es, al igual que
cognee-backend, **puramente aditivo**: `contracts.py`, `app.py` y
`registry.py` no requieren ningún cambio funcional — verificado con
`git diff --stat` en cada uno.

---

## 1. Alcance de este change

**Dentro de alcance:**
- `GraphitiBackend` en
  `hermes-native/memory-router/src/memory_router/backends/graphiti.py` —
  adaptador HTTP sobre `search_facts`, seam
  `transport(method, url, headers, body)` inyectable, config vía entorno,
  replicando la forma de `backends/cognee.py`.
- `capabilities()` = nombre `graphiti`, `verbs = frozenset({"reflect"})`,
  `namespaces = ("/global", "/agents/*")`. Sin `store`, sin `search`.
- `permissions.py`: `reflect` agregado a cinco filas —
  `scientist.global`, `scientist.agents_self`, `jarvis.global`,
  `jarvis.agents_self`, `jarvis.agents_other`. `coder` se mantiene
  sin cambios en ninguna fila.
- Línea de entry point bajo el grupo existente `memory_router.backends`.
- Mapeo namespace → `group_id`: un grupo fijo compartido para `/global`,
  un grupo dedicado por agente para `/agents/{name}`, fail-closed ante
  cualquier namespace que no produzca un `group_id` legal.
- Filtrado temporal: solo hechos actualmente válidos (`invalid_at` ausente)
  se convierten en `Conclusion`; si el filtrado vacía el resultado, es
  `empty`, nunca `ready` con una tupla vacía.
- Tests unitarios con transporte stubbed; sin instancia viva de Graphiti.

**Fuera de alcance:** `store`/`search` en Graphiti (las cuatro raíces ya
están reclamadas para esos verbos); un path de ingestión (`add_episode`)
que pueble el grafo — diferido, misma postura que la ingestión diferida de
Honcho y Cognee; infraestructura de despliegue (Neo4j/FalkorDB, manifiestos
k8s, API key de LLM, control de costos); herramientas mutantes de Graphiti
(`delete_entity_edge`, `delete_episode`, `clear_graph`) — un adaptador
reflect-only nunca muta; `get_episodes` (listado crudo, es search-shaped);
`search_nodes` (resúmenes de entidad — diferido, ver D-05); exponer los
intervalos de validez temporal como campo de primera clase en `Conclusion`.

---

## 2. Arquitectura

```text
cliente
  → [router core: identidad → rol → namespace → permisos] (mismo pipeline
     que store/search/context/reflect-honcho/reflect-cognee, extendido sin
     bypass)
  → [registry: match por capacidad, entry points]
       |-> namespace en /global, /user/master(store/search), /agents/*(store/search) → adaptador Engram (stdio)
       |-> namespace en /projects/*                                                    → adaptador Hindsight (HTTP)
       |-> namespace /user/master, verbo reflect                                       → adaptador Honcho (HTTP)
       |-> namespace /projects/*, verbo reflect                                        → adaptador Cognee (HTTP)
       |-> namespace /global, /agents/*, verbo reflect                                 → adaptador Graphiti (HTTP)
  → adaptador Graphiti → _HttpJsonClient (urllib.request) → Graphiti `/search/facts`
       |-> POST /search/facts {query, group_ids:[id], max_facts} / GET /healthz
```

`contracts.py`, `app.py` y `registry.py` no fueron modificados:
`ReflectRequest`/`ReflectResult`/`Conclusion`/`ReflectiveBackend` ya
existían y encajan tal cual (spec 016); `app.py` ya mapea `status == "empty"`
(landed por spec 017, F-1 de este change); y `Registry.backends_for` solo
llama a `capabilities()` vía `fnmatch`, nunca `isinstance` (F-3) —
`/agents/foo` matchea `/agents/*` y `/global` matchea el literal exacto
`/global`, ambos sin ningún cambio de código.

La coexistencia con Honcho y Cognee es por construcción: los tres
patrones de namespace reflect son disjuntos —
`/user/master` (Honcho), `/projects/*` (Cognee), `/global` + `/agents/*`
(Graphiti) — así que `backends_for` siempre devuelve como máximo uno de
los tres adaptadores para cualquier namespace validado. Ningún adaptador
observa a otro.

Implementación: mismo paquete Python 3.11 `memory-router`, sin
dependencias nuevas — `_HttpJsonClient` usa `urllib.request` de la
stdlib, idéntico al patrón de `honcho.py`/`cognee.py` (D-10).

---

## 3. Reutilización del contrato `ReflectiveBackend` (sin cambio)

`GraphitiBackend` implementa `ReflectiveBackend` estructuralmente (sin
herencia, convención Protocol del código base), NO `MemoryBackend` — no
existen métodos `store`/`search` en la clase, así que
`isinstance(GraphitiBackend(), MemoryBackend)` es `False` y queda
asertado falso: la clase nunca puede ser seleccionada accidentalmente
para un verbo que no sirve, independientemente de la tabla de
capacidades (D-11). `contracts.py` no requiere ningún dataclass ni
Protocol nuevo — spec 016 ya dejó `ReflectiveBackend`/`ReflectRequest`/
`ReflectResult`/`Conclusion` listos para un tercer adaptador reflect-only.

---

## 4. Contrato del adaptador Graphiti

```python
class GraphitiBackend:   # implementa ReflectiveBackend, NO MemoryBackend
    def __init__(self, *, transport=None, base_url=None, auth_mode=None,
                 token=None, group_prefix=None, timeout=None): ...
    def capabilities(self) -> Capabilities:
        return Capabilities(name="graphiti", verbs=frozenset({"reflect"}),
                            namespaces=("/global", "/agents/*"), hierarchical_search=False)
    def health(self) -> Health: ...          # GET /healthz, nunca lanza
    def reflect(self, req: ReflectRequest) -> ReflectResult: ...
```

`transport(method, url, headers, body) -> (status, bytes)` es el mismo
seam de test que `honcho.py`/`cognee.py`.

### Superficie de wire format (revisable, sin verificar)

```python
ENDPOINTS = {
    "search_facts": "/search/facts",   # POST {query, group_ids:[id], max_facts} -> {facts: [...]}
    "health": "/healthz",
}
MAX_FACTS = 10
```

`ENDPOINTS` + `_HttpJsonClient` + `_group_id` + el bloque de mapeo
hecho→conclusión son la única superficie de formato de wire, aislada en
una sola clase — no se verificó contra documentación autoritativa de
Graphiti ni contra una instancia real (ver §8, preguntas abiertas).

### Mapeo namespace → `group_id` (`_group_id`, D-01, D-02, D-03, D-04, D-08)

```python
def _group_id(self, namespace: str) -> str:
    if namespace == "/global":
        suffix = "global"
    elif namespace.startswith("/agents/"):
        agent = namespace[len("/agents/"):]
        if not agent or "/" in agent or ".." in agent or "*" in agent or "?" in agent:
            raise BackendUnavailableError("graphiti", "namespace does not yield a legal group id")
        suffix = f"agent-{agent}"          # el infijo mantiene el mapeo inyectivo (D-02)
    else:
        raise BackendUnavailableError("graphiti", "namespace is not reflect-capable for graphiti")
    group = f"{self._group_prefix}{suffix}"   # SIN case-folding, SIN sustitución (D-03)
    if not _GROUP_RE.match(group):
        raise BackendUnavailableError("graphiti", "namespace does not yield a legal group id")
    return group
```

`_GROUP_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")` — deliberadamente
conservador, ver §8 preguntas abiertas sobre el charset real de Graphiti.

**D-01 — un grupo por agente.** `/agents/*` mapea a un `group_id` por
agente (aislamiento), nunca a un grupo compartido entre agentes. Un grupo
compartido haría que el aislamiento cross-agent dependiera de un filtro del
lado de la consulta siendo correcto en cada llamada; una regresión filtra
al agente B dentro del reflect del agente A, lavado a través de un hecho
derivado por LLM y por lo tanto imposible de atribuir. Con grupos por
agente el aislamiento es estructural. Costo: sin síntesis cross-agent —
compensado deliberadamente, y de todos modos `agents_other` reflect
sigue siendo exclusivo de `jarvis`.

**D-02 — forma del `group_id` e inyectividad.** `{prefix}global` para
`/global`, `{prefix}agent-{name}` para `/agents/{name}`. No es cosmética:
un esquema plano de un solo nivel permitiría que un agente literalmente
llamado `global` mapee al grupo global compartido. Con el infijo `agent-`,
`prefix + "agent-" + name == prefix + "global"` es insatisfacible, así que
el mapeo es inyectivo entre ambos tipos de namespace por construcción.

**D-03 — rechazar, nunca reescribir.** Igual que `_dataset_id` de Cognee
(spec 017): normalizar reescribiendo (`.lower()`, sustitución de
caracteres) no es inyectivo y colapsaría `/agents/Foo` y `/agents/foo` en
un único grupo — exactamente la fuga que D-01 existe para prevenir. Como
`namespaces.py`'s `_NAME_RE` admite mayúsculas y `.` (ver F-2 más abajo),
esto es un riesgo vivo, no teórico. `_GROUP_RE` se aplica sobre el
identificador completo con prefijo, así que un `GRAPHITI_GROUP_PREFIX`
malformado también falla cerrado.

**D-04 — tipo de excepción.** `BackendUnavailableError("graphiti", …)`,
no `ValueError`. `Dispatcher.reflect` solo captura ese tipo (`app.py`); un
`ValueError` sin capturar escaparía como un 500 no manejado en vez de
degradarse limpiamente. Las razones de error nunca ecoan el namespace ni
el token.

**D-08 — namespaces anidados.** `_group_id` re-rechaza `/`, `..`, `*`, `?`
embebidos como defensa en profundidad, aunque F-2 (más abajo) prueba que
`/agents/a/b` ya muere en `validate_namespace` antes de llegar aquí.
Mapear un namespace anidado al grupo del agente padre habría sido una
reescritura deliberadamente no inyectiva, contradiciendo D-03 — por eso
se rechaza en vez de mapear al padre.

### Semántica de `search_facts` (síncrono, D-05, D-06, D-07, D-09)

- `search_facts` devuelve hechos (edges — declaraciones de relación con
  alcance temporal), no `search_nodes` (resúmenes de entidad, que es
  search-shaped) — **solo hechos en esta primera porción** (D-05).
- Cada hecho carga `valid_at`/`invalid_at`. El adaptador filtra del lado
  del cliente: `live = [f for f in facts if not f.get("invalid_at")]`
  antes de construir conclusiones (D-06). Si el filtrado deja la lista
  vacía, el resultado es `ReflectResult(status="empty")` — **nunca**
  `"ready"` con `conclusions == ()`, que sería la forma fabricada que las
  reglas de éxito prohíben.
- `2xx` con ≥1 hecho actualmente válido → `ReflectResult(status="ready",
  conclusions=(Conclusion(confidence=0.0), ...))` — **una `Conclusion` por
  hecho sobreviviente**, en el orden de la respuesta; concatenar en una
  sola habría destruido la atribución por hecho sin ninguna ganancia
  (D-09).
- Confianza siempre `0.0` (D-07): Graphiti devuelve hechos ordenados por
  relevancia, no un score calibrado; cualquier valor no-cero sería
  inventado. `0.0` se lee como "sin puntuar", igual que Cognee.
- `2xx` con `{"facts": []}`, clave `facts` ausente, o texto de hecho en
  blanco/solo-whitespace → `ReflectResult(status="empty")`.
- Error de conexión, status no-2xx, o JSON malformado →
  `BackendUnavailableError("graphiti", reason)` únicamente; ningún otro
  tipo de excepción escapa.

### Config por entorno

| Variable | Default |
|---|---|
| `GRAPHITI_BASE_URL` | `http://graphiti.mcps.svc.cluster.local:8000` |
| `GRAPHITI_AUTH_MODE` | `bearer` si hay token, si no `none` |
| `GRAPHITI_TOKEN` | `""` |
| `GRAPHITI_GROUP_PREFIX` | `jarvis-` |
| `GRAPHITI_TIMEOUT_SECONDS` | `10` |
| `GRAPHITI_MAX_FACTS` | `10` |

Prefijo revalidado junto con el identificador completo del grupo contra
`_GROUP_RE`, así que un prefijo malformado también falla cerrado.
Construcción con cero argumentos funciona bajo
`Registry._load_entry_points()` (arg explícito > var de entorno >
fallback, vía `_env_default`).

Fuente: `hermes-native/memory-router/src/memory_router/backends/graphiti.py`.

---

## 5. Namespaces anidados en `/agents/*` (F-2, confirma un no-op de diseño)

`namespaces.py:3` — `_NAME_RE = ^[a-zA-Z0-9][a-zA-Z0-9_.-]*$` no tiene `/`
en su clase de caracteres; `validate_namespace` matchea todo el resto
después del prefijo, así que `/agents/a/b` lanza `NamespaceError` →
`400 invalid_namespace` **antes** de permisos, registry, o el adaptador.
Verificado leyendo el código, no asumido. Corolario: `_NAME_RE` sí admite
mayúsculas y `.`, así que `/agents/Jarvis.v2` es un namespace legal — esto
es lo que impulsa D-03 (rechazar, nunca reescribir mayúsculas/puntos).

No se agregó ningún manejo de namespace anidado a nivel de adaptador; los
tests documentan/bloquean este hallazgo en vez de duplicar la
responsabilidad de validación:
- `validate_namespace("/agents/a/b")` lanza `NamespaceError` (test directo).
- Una request `reflect` con `namespace="/agents/a/b"` a nivel de
  `Dispatcher` devuelve `400 invalid_namespace`, con cero llamadas al
  adaptador Graphiti (test a nivel de dispatcher).

---

## 6. Permisos

Diff exacto de `_ROLE_TABLE` (`permissions.py`) — cinco filas:

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

| Rol | `global` | `agents_self` | `agents_other` |
|---|---|---|---|
| `coder` | `{"search"}` — **sin cambios**, reflect denegado | `{"store","search"}` — sin cambios, reflect denegado | vacío, denegado |
| `scientist` | `+reflect` | `+reflect` | vacío, denegado |
| `jarvis` | `+reflect` | `+reflect` | `+reflect` |

`coder` no se toca en ninguna fila. `_namespace_kind` no requiere ningún
cambio: `/global` y `/agents/…` ya resuelven (`permissions.py:21-29`).

Fuente: `hermes-native/memory-router/src/memory_router/permissions.py`.

---

## 7. Amenazas consideradas

| Frontera | Aplicabilidad | Respuesta de diseño | Test |
|---|---|---|---|
| Aislamiento cross-agent | Aplicable — el riesgo principal | Un grupo por agente (D-01); mapeo inyectivo por rechazo, nunca reescritura (D-03); infijo `agent-` previene colisión con `/global` (D-02) | `GroupIdTests` |
| Ruteo de namespace | Aplicable | Namespaces anidados ya muertos en validación (F-2); el adaptador re-rechaza `/`, `..`, wildcards en defensa en profundidad | `NestedAgentNamespaceValidationTests`, `DispatcherLevelNestedNamespaceTests` |
| Autorización | Aplicable | Filas explícitas para dos roles; `agents_other` reflect exclusivo de `jarvis`; deny-by-default sin cambios en el resto | `GraphitiReflectPermissionsTests` |
| Construcción de la request saliente | Aplicable | URL solo desde config del router + grupo sanitizado; `query` del llamador solo en el body JSON; headers fijos; timeout siempre configurado | `GraphitiAdapterOutboundConstructionTests` |
| Manejo de secretos | Aplicable | Token solo desde entorno, nunca logueado; razones de error sin token ni datos del llamador | `GraphitiAdapterSecretHandlingTests` |
| Staleness de hechos derivados | Aplicable | Hechos expirados filtrados (D-06); ningún intervalo temporal se cuela en texto libre | `test_all_facts_expired_returns_empty_never_ready_never_pending` |
| Costo LLM / grafo | Aplicable, diferido | Path solo lectura; sin ingestión en esta porción, así que sin costo LLM del lado de escritura; `MAX_FACTS` acota el tamaño de respuesta | Ninguno — precondición operacional |
| Subprocess / shell / VCS / PR automation | N/A — solo HTTP, sin shell, sin VCS | — | Ninguno |

---

## 8. Convivencia y rollback

Solo registro, igual que spec 017. (1) Eliminar la línea del entry point,
`backends/graphiti.py`, y su archivo de test —
`backends_for(verb="reflect", namespace="/global")` vuelve a devolver
vacío y `Dispatcher.reflect()` responde `no_backend`, su comportamiento
previo al change. (2) Revertir las cinco filas de `permissions.py`. Ambos
pasos son reverts de código puro en una feature branch; sin migración de
datos, sin estado almacenado, sin limpieza del lado Graphiti (el
adaptador nunca escribe).

**Preguntas abiertas (sin resolver, no bloqueantes para merge):**
- Superficie HTTP de Graphiti sin verificar: path (`/search/facts` vs. una
  llamada de herramienta estilo MCP), claves de request (`group_ids` vs.
  `group_id`, `max_facts` vs. `limit`), y forma de la respuesta
  (`{facts: [{fact, valid_at, invalid_at, uuid}]}` vs. una lista simple).
  `ENDPOINTS` + `_HttpJsonClient` + el bloque de mapeo de hechos son la
  única superficie revisable.
- Si Graphiti soporta del lado servidor un filtro "solo actualmente
  válido"; de ser así, el filtro del lado cliente de D-06 se vuelve un
  parámetro de request y el filtro cliente queda como cinturón-y-tirantes.
- Charset legal de `group_id` de Graphiti sin verificar; `_GROUP_RE` es
  deliberadamente conservador; ensanchar debe permanecer inyectivo (D-03).
- Si los grupos se aprovisionan de antemano o de forma perezosa, y qué
  devuelve una búsqueda contra un grupo inexistente (404 → `degraded`, o
  200-vacío → `empty`).
- Sin path de ingestión (`add_episode` fuera de alcance), así que un
  despliegue real plausiblemente devuelve `empty` en la práctica hasta que
  se construya uno — comportamiento honesto de primera porción, no un
  defecto.
- `search_nodes` (D-05) queda sin reclamar. Si más adelante se quieren
  resúmenes de entidad, decidir si son conclusiones de `reflect` o un
  asunto de `search` antes de agregarlos.
- `honcho.py`'s `_peer_ref` sigue lanzando un `ValueError` sin capturar —
  un 500 latente no manejado. Graphiti lo evita (D-04); arreglar Honcho
  sigue siendo un follow-up separado.

---

## 9. Checklist de implementación

- [x] `GraphitiBackend` (`backends/graphiti.py`) —
  `capabilities/health/reflect`, con test
- [x] `_HttpJsonClient` (transporte HTTP inyectable, stdlib
  `urllib.request`), con test
- [x] `_group_id()` — mapeo namespace→grupo, rechaza-nunca-reescribe,
  falla cerrado ante traversal/wildcard/mayúsculas/punto,
  `BackendUnavailableError` no `ValueError` (D-04), con test
- [x] Infijo `agent-` mantiene inyectividad (`/agents/global` ≠ `/global`),
  con test
- [x] Filtrado temporal (D-06): solo hechos actualmente válidos; todos
  expirados → `empty`, nunca `ready` con tupla vacía, con test
- [x] Confianza `0.0` en toda conclusión (D-07), con test
- [x] Una `Conclusion` por hecho sobreviviente, orden preservado (D-09),
  con test
- [x] Auth config-driven (`none`/`bearer`, sin hardcode), con test
- [x] Degradación (`BackendUnavailableError` en los tres casos: conexión,
  status, JSON malformado), con test
- [x] Secretos nunca en `reason` de error ni en la razón de rechazo de
  grupo, con test
- [x] `isinstance(GraphitiBackend(), ReflectiveBackend)` y
  `not isinstance(GraphitiBackend(), MemoryBackend)`, con test
- [x] Selección de namespace: `/global` y `/agents/*`, vacío para
  `/user/master`/`/projects/*`, con test
- [x] Coexistencia con Honcho y Cognee: namespaces disjuntos, tests de
  Honcho/Cognee re-corridos sin modificar y en verde
- [x] `validate_namespace("/agents/a/b")` rechazado (F-2), con test a
  nivel de namespace y a nivel de dispatcher (400 `invalid_namespace`)
- [x] `_ROLE_TABLE` — `reflect` agregado en cinco filas
  (`scientist.global`, `scientist.agents_self`, `jarvis.global`,
  `jarvis.agents_self`, `jarvis.agents_other`); `coder` sin cambios, con
  test tabla-driven
- [x] `pyproject.toml` — entry point `graphiti` registrado
- [x] `git diff --stat` confirma cero cambios en `contracts.py`, `app.py`
  y `registry.py`, con test
- [x] Cierre de matriz: cada raíz fija tiene ≥1 backend reflect-capaz bajo
  el registro completo, con test
- [x] Suite completa (`python -m unittest discover -s tests`) verde
- [x] Validación contra instancia real de Graphiti (2026-08-21) — ver §9.1
- [x] Despliegue real — memory-router está desplegado y verificado en `mcps` (spec 014 §8-9); infra dedicada de Graphiti (Neo4j/FalkorDB en el clúster) sigue fuera de alcance de esta fase
- [ ] Path de ingestión de Graphiti (`add_episode`) — diferido

---

### 9.1 Validación contra una instancia real (2026-08-21)

Levantado `zepai/graphiti` (server FastAPI oficial) + Neo4j 5.26 community
en containers Docker efímeros, contra una API key real de OpenAI (LLM +
embeddings — `codex-shim` no cubre `/v1/embeddings`, solo
`/v1/chat/completions`, así que no alcanzó para este caso). Encontrados y
corregidos **2 bugs reales** en `ENDPOINTS`, confirmados contra el código
fuente de `getzep/graphiti` (`server/graph_service/routers/retrieve.py`,
`server/graph_service/main.py`) y contra el server real corriendo:

| | Antes (bug) | Real |
|---|---|---|
| Búsqueda | `POST /search/facts` | `POST /search` |
| Health | `GET /healthz` | `GET /healthcheck` |

El formato del payload (`query`/`group_ids`/`max_facts` → `{facts: [...]}`)
ya estaba bien — solo las rutas estaban mal. Circuito completo probado con
el propio `GraphitiBackend` del repo (no curl crudo): `health()` → `OK`
contra el server real; ingesta de un episodio real
(`POST /messages`) con extracción de entidades por LLM real; `reflect()`
devolvió las 3 conclusiones extraídas (`Pedro prefers dark mode.` /
`Pedro uses editor.` / `Pedro uses terminal.`) correctamente mapeadas a
`namespace=/agents/alpha`, `backend=graphiti`. Suite completa (318 tests)
verde después del fix. Containers de prueba destruidos al terminar; ninguna
credencial quedó en disco.

Sigue pendiente, fuera de alcance de esta validación: desplegar Neo4j/
FalkorDB + el server de Graphiti como infraestructura real y persistente
del clúster (esto solo probó el adaptador contra una instancia efímera
local).

---

## 10. Referencias

- `openspec/changes/graphiti-backend/proposal.md`, `design.md`,
  `specs/{memory-access-control,memory-backend-adapters}/spec.md` (delta)
  — artefactos SDD completos de este change.
- `specs/014_memory_router.md` — Fase 1 de Memory Router (spec 012), base
  original.
- `specs/015_hindsight_backend.md` — segundo adaptador (Hindsight),
  puramente aditivo.
- `specs/016_honcho_backend.md` — tercer adaptador (Honcho), primer verbo
  `reflect` real.
- `specs/017_cognee_backend.md` — cuarto adaptador (Cognee), segundo
  namespace reflect, mismo patrón `ReflectiveBackend`.
- `hermes-native/memory-router/src/memory_router/backends/graphiti.py` —
  código fuente del adaptador.
- `hermes-native/memory-router/src/memory_router/app.py` — dispatcher y
  superficies REST/MCP.
- `hermes-native/memory-router/src/memory_router/permissions.py` — tabla
  de roles.
- `tests/test_memory_router_graphiti_adapter.py`,
  `tests/test_memory_router_graphiti_coexistence.py`,
  `tests/test_memory_router_permissions.py`,
  `tests/test_memory_router_namespaces.py` — suites de tests unitarios
  (`python -m unittest discover -s tests`).

---

**Fin del SDD**
