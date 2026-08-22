# JARVIS Spec 017 - Software Design Document (SDD)
## Memory Router: adaptador Cognee para `reflect` en `/projects/*`

**Estado:** Validado parcialmente contra una instancia real de Cognee (2026-08-22) — encontró y corrigió 4 diferencias reales de wire format (§9.1). `health()` y la aceptación del request de `reflect()` confirmados en vivo con el adaptador real del repo; el round-trip completo (ingesta→grafo→búsqueda) quedó bloqueado por un límite de infraestructura ajeno al adaptador (extracción estructurada de `cognify` incompatible con `codex-shim`, ver §9.1). Bloqueante de despliegue heredado de spec 012 §8 / spec 015 §8 / spec 016 §8 resuelto (ver spec 014 §8); despliegue de una instancia real de Cognee en el clúster sigue fuera de alcance de esta validación
**Fecha:** 2026-08-20
**Versión:** 1.0
**Autor:** Pedro Ortiz (vía agente `sdd-apply`)

---

## 0. Por qué existe este spec

`reflect` funciona, pero solo en un namespace. Spec 016 hizo real
`Dispatcher.reflect()` y le dio a `/user/master` conclusiones derivadas vía
Honcho; cualquier otra raíz seguía seleccionando cero backends
reflect-capaces y devolviendo `no_backend`. `/projects/*` es el único par
namespace+verbo en toda la matriz sin ningún reclamante — Engram y Hindsight
son dueños de `store`/`search` en las cuatro raíces, y Honcho es dueño de
`reflect` únicamente en `/user/master`.

Cognee (topoteretes, open source) cierra exactamente ese hueco. Su pipeline
ECL construye un knowledge graph y `GRAPH_COMPLETION` devuelve una respuesta
sintetizada por LLM sobre ese grafo — una conclusión derivada, no un hit de
retrieval, que es semántica de `reflect`, no de `search`. El trabajo de
proyecto es donde la síntesis cross-documento realmente rinde.

A diferencia de spec 016 (honcho-backend), este change es **puramente
aditivo**: el pipeline de reflect ya existe. `contracts.py` y `registry.py`
no necesitan ningún cambio funcional. `app.py` sí requiere un cambio
funcional mínimo y contestado explícitamente — ver D-06 más abajo — porque
la propuesta original afirmaba "solo docstring" y el hallazgo F-2 mostró que
eso es incompatible con el estado `empty` aprobado.

---

## 1. Alcance de este change

**Dentro de alcance:**
- `CogneeBackend` en
  `hermes-native/memory-router/src/memory_router/backends/cognee.py` —
  adaptador HTTP sobre `/recall` con `search_type=GRAPH_COMPLETION`, seam
  `transport(method, url, headers, body)` inyectable, config vía entorno,
  replicando la forma de `backends/honcho.py`.
- `capabilities()` = nombre `cognee`, `verbs = frozenset({"reflect"})`,
  `namespaces = ("/projects/*",)`. Sin `store`, sin `search`.
- `permissions.py`: `reflect` agregado a
  `_ROLE_TABLE["scientist"]["projects"]` (antes `{"search"}`) y a
  `_ROLE_TABLE["jarvis"]["projects"]` (antes `{"store", "search"}`).
  `coder` se mantiene en `{"store", "search"}` — sin reflect. Mismo patrón
  que el reflect de `/user/master` en spec 016.
- Línea de entry point bajo el grupo existente `memory_router.backends`.
- Manejo explícito de grafo vacío/sin poblar: devuelve un `ReflectResult`
  explícito, nunca una conclusión fabricada.
- Tests unitarios con transporte stubbed; sin instancia viva de Cognee.

**Fuera de alcance:** `store`/`search` en Cognee (las cuatro raíces ya están
reclamadas para esos verbos); un path de ingestión (`/remember`,
add+cognify) que pueble el grafo de Cognee — diferido, misma postura que la
ingestión diferida de Honcho; infraestructura de despliegue de Cognee
(Postgres, vector DB, API key de LLM, manifiestos k8s); el modo `CHUNKS` /
retrieval crudo de Cognee — eso es search-shaped y queda fuera; reflect en
`/agents/*` o `/global`.

---

## 2. Arquitectura

```text
cliente
  → [router core: identidad → rol → namespace → permisos] (mismo pipeline
     que store/search/context/reflect-honcho, extendido sin bypass)
  → [registry: match por capacidad, entry points]
       |-> namespace en /global, /user/master(store/search), /agents/*  → adaptador Engram (stdio)
       |-> namespace en /projects/*                                      → adaptador Hindsight (HTTP)
       |-> namespace /user/master, verbo reflect                         → adaptador Honcho (HTTP)
       |-> namespace /projects/*, verbo reflect                          → adaptador Cognee (HTTP)
  → adaptador Cognee → _HttpJsonClient (urllib.request) → Cognee `/recall`
       |-> POST /recall {query, search_type: GRAPH_COMPLETION, datasets:[id]} / GET /healthz
```

`contracts.py` y `registry.py` no fueron modificados: `ReflectRequest`/
`ReflectResult`/`Conclusion`/`ReflectiveBackend` ya existían y encajan tal
cual, y `Registry.backends_for` solo llama a `capabilities()` vía
`fnmatch`, nunca `isinstance` — `/projects/foo` matchea `/projects/*` sin
ningún cambio de código. La coexistencia con Honcho es por construcción:
los patrones de namespace son disjuntos (`/user/master` vs `/projects/*`),
así que `backends_for` siempre devuelve como máximo uno de los dos
adaptadores para cualquier namespace validado.

Implementación: mismo paquete Python 3.11 `memory-router`, sin
dependencias nuevas — `_HttpJsonClient` usa `urllib.request` de la stdlib,
idéntico al patrón de `honcho.py` y `hindsight.py` (D-07).

---

## 3. Reutilización del contrato `ReflectiveBackend` (sin cambio)

`CogneeBackend` implementa `ReflectiveBackend` estructuralmente (sin
herencia, siguiendo la convención Protocol del código base), NO
`MemoryBackend` — no existen métodos `store`/`search` en la clase, así que
`isinstance(CogneeBackend(), MemoryBackend)` es `False` y queda asertado
falso: la clase nunca puede ser seleccionada accidentalmente para un verbo
que no sirve, independientemente de la tabla de capacidades (D-08).
`contracts.py` no requiere ningún dataclass ni Protocol nuevo — `spec 016`
ya dejó `ReflectiveBackend`/`ReflectRequest`/`ReflectResult`/`Conclusion`
listos para un segundo adaptador reflect-only.

---

## 4. Contrato del adaptador Cognee

```python
class CogneeBackend:   # implementa ReflectiveBackend, NO MemoryBackend
    def __init__(self, *, transport=None, base_url=None, auth_mode=None,
                 token=None, dataset_prefix=None, timeout=None): ...
    def capabilities(self) -> Capabilities:
        return Capabilities(name="cognee", verbs=frozenset({"reflect"}),
                            namespaces=("/projects/*",), hierarchical_search=False)
    def health(self) -> Health: ...          # GET /healthz, nunca lanza
    def reflect(self, req: ReflectRequest) -> ReflectResult: ...
```

`transport(method, url, headers, body) -> (status, bytes)` es el mismo seam
de test que `honcho.py` y `hindsight.py`.

### Superficie de wire format (revisable, sin verificar)

```python
ENDPOINTS = {
    "recall": "/recall",     # POST {query, search_type, datasets:[id]} -> {result|answer: str}
    "health": "/healthz",
}
SEARCH_TYPE = "GRAPH_COMPLETION"   # respuesta sintetizada del grafo, no retrieval CHUNKS
```

`ENDPOINTS` + `_HttpJsonClient` + `_dataset_id` son la única superficie de
formato de wire, aislada en una sola clase — no se verificó contra
documentación autoritativa de Cognee ni contra una instancia real (ver §8,
preguntas abiertas).

### Mapeo namespace → dataset (`_dataset_id`, D-01/D-03/D-04)

```python
def _dataset_id(self, namespace: str) -> str:
    prefix = "/projects/"
    if not namespace.startswith(prefix):
        raise BackendUnavailableError("cognee", "namespace is not a project namespace")
    project = namespace[len(prefix):]
    if not project or "/" in project or ".." in project or "*" in project or "?" in project:
        raise BackendUnavailableError("cognee", "namespace does not yield a legal dataset id")
    dataset = f"{self._dataset_prefix}{project}"      # SIN case-folding, SIN sustitución (D-03)
    if not _DATASET_RE.match(dataset):
        raise BackendUnavailableError("cognee", "namespace does not yield a legal dataset id")
    return dataset
```

Divergencia deliberada respecto a `honcho.py._peer_ref` (spec 016): Honcho
normaliza (`.lower()`) su namespace antes de mapearlo — un enfoque
razonable porque `/user/master` es un namespace único y fijo. Cognee, en
cambio, mapea *un dataset por proyecto* (D-01): normalizar reescribiendo
(minúsculas, sustitución de caracteres) no es inyectivo y colapsaría
`/projects/Foo` y `/projects/foo` en un único dataset — exactamente la
fuga cross-project que D-01 existe para prevenir. Por eso `_dataset_id`
**rechaza, nunca reescribe** (D-03): un nombre de proyecto que no es ya un
identificador de dataset legal falla cerrado en vez de colisionar con otro.
También difiere en el tipo de excepción: `_peer_ref` lanza `ValueError`
(un 500 no capturado latente, documentado como deuda en spec 016);
`_dataset_id` lanza `BackendUnavailableError("cognee", …)` porque
`Dispatcher.reflect` solo captura ese tipo (D-04) — un mapeo rechazado se
degrada limpiamente en vez de escapar como excepción no manejada.

`_DATASET_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")` — deliberadamente
conservador; ver §8 preguntas abiertas sobre el charset real de Cognee.

### Semántica de `/recall` (síncrono, D-02/D-05)

A diferencia de Honcho (Dialectic es asíncrono → `pending`), `/recall` de
Cognee es **síncrono**: la llamada se completa, el grafo se consulta, y
puede no tener contenido relevante. Por eso:
- `2xx` con `result`/`answer` no vacío → `ReflectResult(status="ready",
  conclusions=(Conclusion(confidence=0.0),))` — confianza `0.0` porque
  `GRAPH_COMPLETION` devuelve prosa sintetizada sin score numérico; `0.0`
  se lee como "sin puntuar", nunca un número inventado (D-05).
- `2xx` con respuesta vacía/ausente/solo-whitespace →
  `ReflectResult(status="empty")` — **nunca** `"pending"`, porque
  reintentar una consulta síncrona cuyo grafo no cambiará sin un paso de
  ingestión sería engañoso (D-02).
- Error de conexión, status no-2xx, o JSON malformado →
  `BackendUnavailableError("cognee", reason)` únicamente; ningún otro tipo
  de excepción escapa.

### Config por entorno

| Variable | Default |
|---|---|
| `COGNEE_BASE_URL` | `http://cognee.mcps.svc.cluster.local:8000` |
| `COGNEE_AUTH_MODE` | `bearer` si hay token, si no `none` |
| `COGNEE_TOKEN` | `""` |
| `COGNEE_DATASET_PREFIX` | `jarvis-` |
| `COGNEE_TIMEOUT_SECONDS` | `10` |

Prefijo revalidado junto con el nombre completo del dataset contra
`_DATASET_RE`, así que un prefijo malformado también falla cerrado.
Construcción con cero argumentos funciona bajo `Registry._load_entry_points()`
(arg explícito > var de entorno > fallback, vía `_env_default`).

Fuente: `hermes-native/memory-router/src/memory_router/backends/cognee.py`.

---

## 5. Pipeline del dispatcher — el mapeo `empty` (D-06)

`Dispatcher.reflect()` no se toca funcionalmente salvo por tres líneas y un
docstring. El hallazgo F-2, verificado leyendo el código (no asumido):
`status` inicializa en `"no_backend"` y solo se reasigna cuando
`result.status == "ready"` o `"pending"`. Un backend que devuelve
`"empty"` no matchea ninguna rama, y la respuesta reporta
`{"status": "no_backend"}` — una mentira: un backend fue seleccionado, fue
alcanzado, y sí respondió. Honcho nunca ejercita esto porque solo emite
`ready`/`pending`; Cognee, por la decisión aprobada D-02, sí emite `empty`.

Diff funcional completo agregado (precedencia:
`ready` > `pending` > `empty` > `degraded` > `no_backend`):

```python
            elif result.status == "pending" and status != "ready":
                status = "pending"
            elif result.status == "empty" and status not in ("ready", "pending"):
                status = "empty"
```

El comportamiento `ready`/`pending` de Honcho queda **bit-a-bit sin
cambios** — nunca emite `"empty"`, así que la nueva rama nunca se ejercita
para su path.

```text
POST /memory/reflect  {role, namespace: "/projects/hermes", query}
  -> _authenticate -> _validate_namespace   # F-1: "/projects/a/b" muere aquí, 400
  -> _authorize(verb="reflect")             # scientist|jarvis permiten, coder 403
  -> registry.backends_for(verb="reflect", namespace="/projects/hermes")
       fnmatch("/projects/hermes", "/projects/*") -> [CogneeBackend]
       fnmatch("/projects/hermes", "/user/master") -> Honcho NO seleccionado
  -> 200 {"namespace", "status", "conclusions", "unavailable"}
```

Docstring corregido (reemplaza la afirmación desactualizada de spec 016
"single `/user/master` namespace… since `/user/master` has no parent",
que dejó de ser cierta en cuanto `/projects/*` también reflect):

> Read-oriented derived-conclusion query over a single validated namespace.
> Runs the exact same identity -> namespace -> permission pipeline as
> store/search/context (never bypassed), authorizing the "reflect" verb.
> Mirrors `context()` — one namespace, no `_fallback_chain`: a derived
> conclusion is scoped to the namespace it was derived from and is never
> inherited from a parent. Which namespaces are reflect-capable is a
> registry/capabilities question, not a dispatcher one; the dispatcher is
> namespace-agnostic here.

---

## 6. Permisos

Diff exacto de `_ROLE_TABLE` (`permissions.py`) — dos filas:

```python
     "scientist": {
-        "projects": frozenset({"search"}),
+        "projects": frozenset({"search", "reflect"}),
     "jarvis": {
-        "projects": frozenset({"store", "search"}),
+        "projects": frozenset({"store", "search", "reflect"}),
```

| Rol | `projects` verbos tras el cambio |
|---|---|
| `coder` | `{"store", "search"}` — **sin cambios**, reflect denegado |
| `scientist` | `{"search", "reflect"}` |
| `jarvis` | `{"store", "search", "reflect"}` |

Ninguna fila `reflect` se agrega a `global`, `user_master`, `agents_self`,
`agents_other` en este change — `authorize()` ya resuelve un verbo ausente
vía `.get(kind, frozenset())`, así que quedan denegados por defecto sin
código nuevo, verificado por test. `_namespace_kind` no requiere cambio:
`/projects/…` ya mapea a `"projects"`.

Fuente: `hermes-native/memory-router/src/memory_router/permissions.py`.

---

## 7. Amenazas consideradas

| Frontera | Aplicabilidad | Respuesta de diseño | Test |
|---|---|---|---|
| Aislamiento cross-project | Aplicable — el riesgo principal | Un dataset por proyecto (D-01); mapeo inyectivo por rechazo, nunca reescritura (D-03); dataset revalidado tras el prefijo | `DatasetIdTests` |
| Ruteo de namespace | Aplicable | Namespaces anidados ya muertos en validación (F-1); el adaptador re-rechaza `/`, `..`, wildcards en defensa en profundidad | `test_rejects_nested_project_namespace`, `test_reflect_on_nested_project_namespace_returns_400_invalid_namespace` |
| Autorización | Aplicable | Filas explícitas en `projects` solo para dos roles; deny-by-default sin cambios en el resto | `ProjectsReflectPermissionsTests` |
| Construcción de la request saliente | Aplicable | URL solo desde config del router + dataset sanitizado; `query` del llamador solo en el body JSON; headers fijos; timeout siempre configurado | `CogneeAdapterOutboundConstructionTests` |
| Manejo de secretos | Aplicable | Token solo desde entorno, nunca logueado; razones de error sin token ni datos del llamador | `CogneeAdapterSecretHandlingTests` |
| Costo/abuso de LLM | Aplicable, diferido | `GRAPH_COMPLETION` factura una llamada LLM por reflect; sin rate limit en esta porción, sin instancia viva que facturar | Ninguno — marcado como precondición operacional |
| Subprocess / VCS / PR automation | N/A — solo HTTP, sin shell, sin VCS | — | Ninguno |

---

## 8. Convivencia y rollback

Solo registro, a diferencia de spec 016. (1) Eliminar la línea del entry
point, `backends/cognee.py`, y su archivo de test —
`backends_for(verb="reflect", namespace="/projects/x")` vuelve a devolver
vacío y `Dispatcher.reflect()` responde `no_backend`, su comportamiento
previo al change. (2) Revertir las dos filas de `permissions.py`. Ambos
pasos son reverts de código puro en una feature branch; sin migración de
datos, sin estado almacenado, sin limpieza del lado Cognee.

**Preguntas abiertas (sin resolver, no bloqueantes para merge):**
- Formato de wire de `/recall` — path, claves de request (`search_type`,
  `datasets` vs `dataset_ids`/`dataset_name`), clave de respuesta (`result`
  vs `answer` vs una lista), y si `datasets` toma nombres o UUIDs — sin
  verificar contra una instancia viva o documentación autoritativa.
- Charset legal de identificador de dataset de Cognee sin verificar;
  `_DATASET_RE` es deliberadamente conservador; si Cognee acepta `.` y
  mayúsculas, ensanchar el regex desbloquea `/projects/Jarvis.v2` sin
  tocar nada más — el ensanchamiento debe permanecer inyectivo.
- Si `reflect` alguna vez necesita el path de ingestión de Cognee
  (`/remember`, add + cognify) — diferido, misma postura que la ingestión
  diferida de Honcho (spec 016).
- Si los datasets se aprovisionan por proyecto de antemano o de forma
  perezosa en el primer reflect, y qué devuelve un reflect contra un
  dataset inexistente (404 → degraded, o 200-vacío → `empty`) —
  inverificable sin instancia; el adaptador trata non-2xx como degraded
  por defecto.
- `honcho.py._peer_ref` lanza `ValueError` sin capturar — un 500 no
  manejado latente. Cognee lo evita (D-04); arreglar Honcho es un
  follow-up separado, deliberadamente fuera de este change.

---

## 9. Checklist de implementación

- [x] `CogneeBackend` (`backends/cognee.py`) — `capabilities/health/reflect`,
  con test
- [x] `_HttpJsonClient` (transporte HTTP inyectable, stdlib
  `urllib.request`), con test
- [x] `_dataset_id()` — mapeo namespace→dataset, rechaza-nunca-reescribe,
  falla cerrado ante traversal/wildcard/mayúsculas/punto, `BackendUnavailableError`
  no `ValueError` (D-04), con test
- [x] Semántica `ready`/`empty`, nunca fabricada, nunca `pending` para el
  caso síncrono vacío (D-02), con test
- [x] Confianza `0.0` en toda conclusión (D-05), con test
- [x] Auth config-driven (`none`/`bearer`, sin hardcode), con test
- [x] Degradación (`BackendUnavailableError` en los tres casos: conexión,
  status, JSON malformado), con test
- [x] Secretos nunca en `reason` de error ni en la razón de rechazo de
  dataset, con test
- [x] `isinstance(CogneeBackend(), ReflectiveBackend)` y
  `not isinstance(CogneeBackend(), MemoryBackend)`, con test
- [x] Selección de namespace: solo `/projects/*`, vacío para
  `/user/master`/`/global`/`/agents/*`, con test
- [x] Coexistencia con Honcho: namespaces disjuntos, tests de Honcho
  re-corridos sin modificar y en verde
- [x] `Dispatcher.reflect()` — mapeo `empty` de 3 líneas (D-06) + docstring
  corregido, con test; precedencia `ready > pending > empty > degraded > no_backend`
  verificada
- [x] `_ROLE_TABLE` — `reflect` agregado en `projects` para
  `scientist`/`jarvis`; `coder` sin cambios (denegado), con test
  tabla-driven
- [x] `validate_namespace("/projects/a/b")` rechazado (F-1), con test a
  nivel de namespace y a nivel de dispatcher (400 `invalid_namespace`)
- [x] `pyproject.toml` — entry point `cognee` registrado
- [x] `git diff --stat` confirma cero cambios en `contracts.py` y
  `registry.py`
- [x] Suite completa (`python -m unittest discover -s tests`) verde
- [x] Validación contra instancia real de Cognee (2026-08-22, parcial) — ver §9.1
- [ ] Despliegue real de una instancia de Cognee en el clúster — sigue
  sin ejecutarse; esta validación corrió contra un container Docker
  efímero ya destruido
- [ ] Path de ingestión de Cognee (`/api/v1/add` + `/api/v1/cognify`) —
  diferido; confirmado en vivo que `/add` funciona (multipart file
  upload, no texto plano), `cognify` bloqueado por el límite de §9.1

---

### 9.1 Validación contra una instancia real (2026-08-22, parcial)

Levantada `cognee/cognee:main` (self-hosted, SQLite embebido) contra
`codex-shim` para el LLM y `local-embeddings` (spec 021,
`intfloat/multilingual-e5-large`, vía `EMBEDDING_PROVIDER=openai_compatible`)
para embeddings. La versión corrida (`1.5.1-local`) trae **dos
generaciones de API simultáneas**: la vieja (`add`/`cognify`/`search`) y
una nueva (`remember`/`recall`/`forget`/`improve`) — se investigó el
`openapi.json` real de la instancia en vivo antes de decidir, no
documentación desactualizada.

Encontradas y corregidas **4 diferencias reales de wire format** en
`hermes-native/memory-router/src/memory_router/backends/cognee.py`,
confirmadas contra el `openapi.json` real de la instancia corriendo:

| | Adaptador (bug) | Real |
|---|---|---|
| Ruta | `POST /recall` | `POST /api/v1/search` |
| Health | `GET /healthz` | `GET /health` |
| Campo de tipo de búsqueda | `search_type` (snake_case) | `searchType` (camelCase) |
| Forma de la respuesta | objeto único `{result\|answer: str}` | **array** de `{search_result, dataset_id, dataset_name}` (`SearchResult[]`) |

Circuito parcial probado con el propio `CogneeBackend` del repo (no curl
crudo): `health()` → OK. `reflect()` mandó el request corregido
(`searchType` camelCase, ruta real) y el server devolvió un error de
negocio real y específico (`404 DatasetNotFoundError`), no un 404 de
ruta inexistente ni un 422 de validación — confirma que el wire format
del request es correcto, aunque el round-trip completo no cerró.

**Límite real encontrado, no un bug del adaptador:** `cognify` (la
construcción del grafo) usa
`NativeLiteLLMAdapter.acreate_structured_output` de Cognee, que exige
salida JSON estructurada estricta del LLM. `codex-shim` no preserva esa
restricción al traducir hacia la sesión OAuth de Codex — el modelo
devuelve JSON con nombres de campo plausibles pero incorrectos
(`source`/`target` en vez de `source_node_id`/`target_node_id`/etc.),
Cognee reintenta con backoff creciente (32s, 64s, ...) sin converger.
Confirmado también en vivo (ingesta real por `POST /api/v1/add`, que
exige `multipart/form-data` con un archivo real, no texto plano en el
body — otro detalle no documentado). Con una API key real de OpenAI en
vez de `codex-shim` esto probablemente funcionaría (mismo patrón que
usamos para el LLM de Graphiti/Honcho/Hindsight vía `codex-shim` sin
problema — el problema es específico de la restricción de schema
estricto, no de `codex-shim` en general), pero se decidió no gastar una
key real para esto y dejarlo documentado como límite conocido en vez de
forzarlo.

Suite completa del repo (356 tests) verde después de los fixes.
Containers de prueba destruidos al terminar; ninguna credencial quedó en
disco.

---

## 10. Referencias

- `openspec/changes/cognee-backend/proposal.md`, `design.md`,
  `specs/{memory-access-control,memory-backend-adapters}/spec.md` (delta)
  — artefactos SDD completos de este change.
- `specs/014_memory_router.md` — Fase 1 de Memory Router (spec 012), base
  original.
- `specs/015_hindsight_backend.md` — segundo adaptador (Hindsight),
  puramente aditivo.
- `specs/016_honcho_backend.md` — tercer adaptador (Honcho), primer verbo
  `reflect` real, primer change no-aditivo a `contracts.py`/`app.py`/
  `permissions.py`; este change reutiliza su contrato `ReflectiveBackend`
  sin modificarlo.
- `hermes-native/memory-router/src/memory_router/backends/cognee.py` —
  código fuente del adaptador.
- `hermes-native/memory-router/src/memory_router/app.py` — dispatcher y
  superficies REST/MCP.
- `hermes-native/memory-router/src/memory_router/permissions.py` — tabla de
  roles.
- `tests/test_memory_router_cognee_adapter.py`,
  `tests/test_memory_router_app.py`, `tests/test_memory_router_permissions.py`,
  `tests/test_memory_router_namespaces.py` — suites de tests unitarios
  (`python -m unittest discover -s tests`).

---

**Fin del SDD**
