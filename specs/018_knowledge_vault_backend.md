# JARVIS Spec 018 - Software Design Document (SDD)
## Memory Router: puente de búsqueda knowledge-vault en `/global`

**Estado:** Implementado (código + tests unitarios en ambos lados) — bloqueante de despliegue heredado de spec 012 §8 / spec 015 §8 / spec 016 §8 / spec 017 §8 resuelto (ver spec 014 §8), despliegue real pendiente de ejecutar
**Fecha:** 2026-08-20
**Versión:** 1.0
**Autor:** Pedro Ortiz (vía agente `sdd-apply`)

---

## 0. Por qué existe este spec

`/global` está pensado como la fuente primaria de JARVIS para conocimiento
definicional y transversal, pero hoy solo Engram responde `search` ahí —
y Engram guarda memoria de sesión, no material de referencia curado.
Mientras tanto, el repo ya corre un corpus curado y aprobado por humano:
`knowledge-vault`, cinco unidades systemd en `trantor` publicando a
`/opt/knowledge-vault/vault`, con una búsqueda híbrida léxica+semántica
local que funciona (`knowledge_vault/search.py` → `VaultHit(note, title,
excerpt)`). Ese corpus era inalcanzable desde memory-router. Este change
agrega el path de lectura que falta para que el conocimiento aprobado
responda búsquedas en `/global` junto a Engram, no en su lugar.

A diferencia de spec 016/017 (Honcho/Cognee, adaptadores `reflect`-only),
este es el primer adaptador `search`-only del router — necesita un
Protocol nuevo (`SearchOnlyBackend`) porque `MemoryBackend` exige `store()`,
y el adaptador deliberadamente no lo implementa.

---

## 1. Alcance de este change

**Dentro de alcance:**
- `KnowledgeVaultBackend` en
  `hermes-native/memory-router/src/memory_router/backends/knowledge_vault.py`
  — adaptador HTTP con seam `transport(method, url, headers, body)`
  inyectable y config vía entorno, replicando la forma de
  `backends/honcho.py`/`cognee.py`.
- `capabilities()` = nombre `knowledge-vault`, `verbs =
  frozenset({"search"})`, `namespaces = ("/global",)`,
  `hierarchical_search=False`. Sin `store`, sin `reflect`.
- `SearchOnlyBackend` Protocol en `contracts.py` (`capabilities`/`health`/
  `search`), mismo precedente que `ReflectiveBackend` — `MemoryBackend`
  queda byte-idéntico.
- Un wrapper HTTP de solo lectura nuevo en `trantor`,
  `knowledge_vault/serve.py`, exponiendo `search_vault()` sobre una
  superficie autenticada pequeña (`POST /search`, `GET /healthz`), más su
  unidad systemd — código y unit file únicamente, sin desplegar.
- Mapeo `VaultHit` → `SearchHit(namespace="/global",
  backend="knowledge-vault", content, score)`.
- Vault vacío, índice stale/no disponible, y fallo de transporte
  manejados explícitamente — nunca un hit fabricado.
- Línea de entry point bajo `memory_router.backends`; tests unitarios en
  ambos lados con transporte stubbed — sin servicio vivo requerido.

**Fuera de alcance:** el plugin Local REST API de Obsidian y cualquier
acceso al vault crudo personal del usuario (rechazado: expone
lectura/escritura cuando solo se necesita búsqueda, y depende de que un
laptop esté online mientras `trantor` está siempre disponible); `store`/
`reflect` para este backend — las escrituras al vault siguen detrás del
pipeline existente propose/review/approve/publish; el pipeline de
knowledge-vault en sí — ya construido, este change solo agrega un path de
lectura desde su salida; despliegue real del wrapper a `trantor`; otros
namespaces distintos de `/global`; cambiar el comportamiento de `/global`
de Engram.

---

## 2. Arquitectura

```text
cliente
  → [router core: identidad → rol → namespace → permisos]  # search en
     global ya estaba permitido para coder/scientist/jarvis — sin cambio
  → [registry: match por capacidad, entry points]
       |-> namespace /global, verbo search  → [EngramBackend, KnowledgeVaultBackend]  # ambos, F-1
  → Dispatcher.search() itera cada backend seleccionado y extiende una
     sola lista de hits — sin cirugía en app.py/registry.py/permissions.py
  → adaptador KnowledgeVaultBackend
       -> guard namespace == "/global" (D-05)   # si no, BackendUnavailableError, 0 llamadas HTTP
       -> _HttpJsonClient (urllib.request) → knowledge-vault-search bridge
            POST /search {"query": str, "limit": int} → {"hits": [{note,title,excerpt,score}]}
            GET /healthz  → {"status": "ok"}
```

**Host** — `knowledge_vault/serve.py`: `ThreadingHTTPServer` (misma clase
que `memory_router/app.py` ya usa; el paquete mantiene cero dependencias
de runtime) exponiendo exactamente `POST /search` y `GET /healthz` sobre
`search_vault()` existente. Token bearer desde una credencial systemd,
enlazado a una dirección local (`10.42.0.1`, la puerta de enlace `cni0` —
D-06), solo lectura sobre vault e índice.

**Router** — `backends/knowledge_vault.py`: la forma de `honcho.py`
verbatim (`_env_default`, `_default_transport`, `_HttpJsonClient`, un
único diccionario `ENDPOINTS`, el seam `transport`), con `reflect()`
cambiado por `search()` y el mapeador namespace→identificador eliminado
en vez de reemplazado (D-05) — no hay nada que mapear: un solo namespace
fijo, un solo corpus fijo.

`contracts.py` gana un solo Protocol; todo lo demás en el router queda
sin tocar — verificado con test (`git diff --stat` vacío contra
`origin/main` para `app.py`/`registry.py`/`permissions.py`, y comparación
byte-a-byte del bloque `MemoryBackend`).

---

## 3. Hallazgos verificados (leídos del código, no asumidos)

- **F-1 — La convivencia en `/global` no necesita ningún diff en
  `app.py`.** `_fallback_chain` devuelve `["/global"]` para `/global`, y
  el loop interno itera **todos** los backends que
  `backends_for(verb="search", namespace=candidate)` devuelve, extendiendo
  sus hits en una sola lista. Engram y knowledge-vault se mergean por
  construcción.
- **F-2 — La selección es duck-typed.** `Registry.backends_for` solo lee
  `capabilities()`; ningún `isinstance` en ningún lado. Un objeto que no
  es `MemoryBackend` selecciona correctamente hoy.
- **F-3 — `SearchHit` no tiene campo de metadata**, y
  `Dispatcher.search` proyecta exactamente
  `namespace/backend/content/score`. El id de nota solo puede llegar al
  llamador dentro de `content` a menos que el dataclass cambie (D-04).
- **F-4 — El score existe y se descartaba.** `Retriever.search` construye
  `RetrievalHit(..., score)`, `search_vault` filtra por
  `hit.score < MIN_RELEVANCE` y nunca lo llevaba a `VaultHit`. No hay
  nada que computar, solo conservar (D-01).
- **F-5 — El rebuild de índice stale es síncrono e incancelable.**
  `search_vault` llama `build_index(vault, index_path)` inline, sin
  deadline hook dentro del loop — una comprobación de deadline dentro del
  rebuild requeriría editar `build_index` mismo. `build_index` escribe
  vía `write_atomic`, así que un rebuild abandonado no puede dejar un
  índice a medio escribir (D-02).
- **F-6 — Networking nodo/pod**, verificado desde
  `kubernetes/knowledge-proposals/networkpolicy.yaml`: nodo LAN de
  `trantor` `192.168.100.13` en `192.168.100.0/24`; CIDR de pods flannel
  `10.42.0.0/24`; dirección host `cni0` `10.42.0.1`. El namespace `mcps`
  no tiene NetworkPolicy hoy (solo `knowledge-proposals` la tiene) (D-06).

---

## 4. Decisiones de arquitectura

| # | Decisión | Elección y razón |
|---|---|---|
| D-01 | Score surfacing | `VaultHit` gana un cuarto campo `score: float = 0.0`, poblado en `search_vault` desde `hit.score`. Campo con default: `main()` y el único consumidor (`test_search.py`) leen atributos y nunca construyen un `VaultHit`, así que nada se rompe. |
| D-02 | Acotar el rebuild inline | Toda la llamada a `search_vault()` corre en un `ThreadPoolExecutor` y `future.result(timeout=...)`, guardado por un `threading.Lock` single-flight. El hilo no se cancela al expirar el timeout — sigue en background y la *siguiente* request es rápida, así el servicio se auto-sana en vez de trabarse. |
| D-03 | Qué devuelve un rebuild expirado | `503` con razón, no `200 {"hits": []}` — `200`-vacío es indistinguible de "el vault no tiene nada sobre esto", una mentira sobre un corpus que puede tener la respuesta. Resultados genuinamente vacíos (vault vacío, ninguna nota sobre `MIN_RELEVANCE`) siguen siendo `200 {"hits": []}`. |
| D-04 | Forma de `SearchHit.content` | Embeber: `content = f"{note} — {title}\n{excerpt}"`. Extender `SearchHit` cambiaría un dataclass que todo backend y ambas proyecciones del dispatcher comparten, rompiendo la barra "`contracts.py` gana un Protocol, todo lo demás sin tocar" por una ganancia cosmética. |
| D-05 | Guard de namespace | Guard de dos líneas `namespace == "/global"` lanzando `BackendUnavailableError`, sin mapeador — no hay nada que mapear: un namespace fijo, un corpus fijo, sin identificador por namespace en el wire. `BackendUnavailableError`, no `ValueError` — `Dispatcher.search` solo captura el primero. |
| D-06 | Alcanzabilidad in-cluster → host | Bind del servicio a la puerta de enlace `cni0` `10.42.0.1:8088`, con un `Service` sin selector + `EndpointSlice` gestionado manualmente en `mcps` apuntando a esa dirección. `10.42.0.1` es alcanzable *solo* desde pods en este nodo y nunca desde la LAN — la dirección más restrictiva que es realmente alcanzable. |
| D-07 | Cliente HTTP del router | `urllib.request`, idéntico a `honcho.py`/`cognee.py`/`hindsight.py`. Cero dependencias de runtime. |
| D-08 | Conformancia de contrato | `SearchOnlyBackend` únicamente — la clase no declara `store`, así que `isinstance(backend, MemoryBackend)` es `False` y queda asertado falso. `MemoryBackend` no se toca en absoluto. |

---

## 5. Contrato del adaptador knowledge-vault

```python
# contracts.py — aditivo; MemoryBackend byte-idéntico
@runtime_checkable
class SearchOnlyBackend(Protocol):
    def capabilities(self) -> Capabilities: ...
    def health(self) -> Health: ...
    def search(self, req: SearchRequest) -> SearchResult: ...
```

```python
# backends/knowledge_vault.py
ENDPOINTS = {"search": "/search", "health": "/healthz"}
NAMESPACE = "/global"

class KnowledgeVaultBackend:          # SearchOnlyBackend, NO MemoryBackend
    def __init__(self, *, transport=None, base_url=None, auth_mode=None,
                 token=None, limit=None, timeout=None): ...
    def capabilities(self) -> Capabilities:
        return Capabilities(name="knowledge-vault", verbs=frozenset({"search"}),
                            namespaces=("/global",), hierarchical_search=False)
    def health(self) -> Health: ...   # GET /healthz, nunca lanza
    def search(self, req: SearchRequest) -> SearchResult: ...
```

### Wire format (dueño el host, ambos lados lo implementan)

```
POST /search   Authorization: Bearer <token>
  -> {"query": str, "limit": int}
  <- 200 {"hits": [{"note": "0007-....md", "title": "…", "excerpt": "…", "score": 0.83}]}
  <- 401 {"error": "unauthenticated"}          token ausente/incorrecto
  <- 400 {"error": "invalid_body"}             no-JSON, query ausente/vacía, limit no-int
  <- 503 {"error": "index_rebuild_timeout"}    D-03
GET /healthz  -> 200 {"status": "ok"}          liveness sin autenticación, no toca ningún archivo del vault
Cualquier otro path/método -> 404 / 405. No hay verbo de escritura que alcanzar.
```

### Config por entorno

| Lado | Variable | Default |
|---|---|---|
| Host | `KNOWLEDGE_VAULT_DIR` / `KNOWLEDGE_VAULT_INDEX` | valores de unidad existentes, reutilizados verbatim |
| Host | `KNOWLEDGE_VAULT_SEARCH_HOST` / `_PORT` | `10.42.0.1` / `8088` (D-06) |
| Host | `KNOWLEDGE_VAULT_SEARCH_TIMEOUT_SECONDS` | `5` (D-02 deadline) |
| Host | `KNOWLEDGE_VAULT_SEARCH_LIMIT_MAX` | `20` — `limit` del llamador clamped a `1..MAX` |
| Host | token | `LoadCredential=search-token:/etc/knowledge-vault/search-token`, leído de `$CREDENTIALS_DIRECTORY`; nunca env var, nunca en el repo; comparado con `hmac.compare_digest` |
| Router | `KNOWLEDGE_VAULT_BASE_URL` | `http://knowledge-vault-search.mcps.svc.cluster.local:8088` |
| Router | `KNOWLEDGE_VAULT_AUTH_MODE` / `KNOWLEDGE_VAULT_TOKEN` | `bearer` si hay token, si no `none` / `""` |
| Router | `KNOWLEDGE_VAULT_LIMIT` | `5` — coincide con el default de `search_vault` |
| Router | `KNOWLEDGE_VAULT_TIMEOUT_SECONDS` | `10` — debe exceder el deadline de 5s del host |

Arg explícito de constructor > var de entorno > fallback vía
`_env_default`, así que la construcción con cero argumentos bajo
`Registry._load_entry_points()` funciona.

Fuentes:
`hermes-native/knowledge-vault/src/knowledge_vault/serve.py`,
`hermes-native/memory-router/src/memory_router/backends/knowledge_vault.py`.

---

## 6. Amenazas consideradas

| Frontera | Aplicabilidad | Respuesta de diseño | Test |
|---|---|---|---|
| Nueva superficie de red sobre conocimiento curado | Aplicable — el riesgo principal | Solo dos rutas; bearer auth antes de cualquier lectura del vault; bind a `10.42.0.1`, inalcanzable desde la LAN (D-06) | `AuthTests`, `RouteTests` (host) |
| Path de escritura / mutación del vault | Aplicable | Ningún verbo de escritura existe en código; unidad con `ReadOnlyPaths=` para vault+índice y **sin** `ReadWritePaths=` | `RouteTests.test_handler_defines_no_mutating_methods`, `ReadOnlyTests` |
| Ruteo de namespace | Aplicable | `/global` único y fijo; el adaptador re-guarda fail-closed sin mapeador que pueda fallar (D-05) | `KnowledgeVaultNamespaceGuardTests` |
| Autorización | Aplicable, sin cambios | `search` en `global` ya permitido para los tres roles; `permissions.py` cero diff | `GlobalCoexistenceTests`, suite de permisos re-corrida sin modificar |
| Construcción de la request saliente | Aplicable | URL solo desde config del router; `query` del llamador solo en el body JSON; headers fijos; `limit` elegido por el router y clamped por el host | `KnowledgeVaultAdapterOutboundConstructionTests` |
| Manejo de secretos | Aplicable | Token del host vía credencial systemd (nunca archivo del repo, nunca env var); token del router vía entorno; ninguno logueado ni ecoado en una razón | `KnowledgeVaultAdapterSecretHandlingTests`, `SecretHandlingTests` (host) |
| Denial of service vía rebuild | Aplicable | Rebuild acotado por deadline + lock single-flight (D-02); `503`, no una request colgada (D-03) | `BoundedRebuildTests` |
| Subprocess / VCS / PR automation | N/A — solo llamadas HTTP e in-process, sin shell, sin VCS | — | Ninguno |

---

## 7. Convivencia y rollback

Solo registro del lado router. (1) Eliminar la línea del entry point,
`backends/knowledge_vault.py`, y su test —
`backends_for(verb="search", namespace="/global")` vuelve a devolver solo
Engram, exactamente el comportamiento previo al change. (2) Revertir la
adición de `SearchOnlyBackend` a `contracts.py` (aditiva, sin llamador
existente). (3) En el host, `systemctl disable --now
knowledge-vault-search.service` y eliminar la unidad; el vault, su
índice, y el pipeline de publicación quedan sin tocar. Sin migración de
datos, sin estado almacenado, sin escrituras que deshacer.

**Preguntas abiertas (sin resolver, no bloqueantes para merge):**
- El deadline de rebuild de 5s es un valor de partida, no medido — nadie
  cronometró `build_index` contra el `/opt/knowledge-vault/vault` real.
- mTLS si el router alguna vez se mueve fuera de `trantor` — D-06 es
  correcto solo mientras ambos lados comparten el nodo.
- `10.42.0.1` es específico de flannel — verificado desde
  `knowledge-proposals/networkpolicy.yaml`, pero un cambio de CNI lo
  movería.
- El namespace `mcps` no tiene NetworkPolicy hoy (F-6) — si algún día
  gana un default-deny, una regla de egress hacia `10.42.0.1/32:8088` se
  vuelve prerrequisito.
- Comparabilidad de score cross-backend explícitamente fuera de alcance
  — `SearchHit.backend` es el discriminador honesto hasta que alguien
  posea la normalización.
- Semántica de `limit` bajo merge — el adaptador pide 5 y Engram devuelve
  su propia cuenta; nadie limita el total mergeado.

---

## 8. Checklist de implementación

- [x] `search.py` — `VaultHit.score` (D-01), con test
- [x] `serve.py` — `POST /search`/`GET /healthz`, bearer auth vía
  `hmac.compare_digest`, con test
- [x] `serve.py` — rebuild acotado por `ThreadPoolExecutor` + lock
  single-flight + `future.result(timeout=...)` (D-02/D-03), con test
- [x] `serve.py` — vault vacío / query bajo `MIN_RELEVANCE` → `200
  {"hits": []}`, con test
- [x] `serve.py` — mtimes de vault/índice sin cambio tras una búsqueda
  exitosa sin rebuild, con test
- [x] `systemd/knowledge-vault-search.service` — `Type=simple`,
  `LoadCredential=`, `ReadOnlyPaths=` vault+índice, sin
  `ReadWritePaths=`
- [x] `pyproject.toml` (knowledge-vault) — entry point
  `knowledge-vault-search-serve`
- [x] `SearchOnlyBackend` Protocol (`contracts.py`), `MemoryBackend`
  byte-idéntico verificado con test
- [x] `KnowledgeVaultBackend` (`backends/knowledge_vault.py`) —
  `capabilities/health/search`, con test
- [x] Guard de namespace fail-closed (D-05), `BackendUnavailableError`
  no `ValueError`, con test
- [x] Round trip score/content (D-01/D-04), con test
- [x] Degradación (`BackendUnavailableError` en conexión, status,
  JSON malformado), con test
- [x] Secretos nunca en `reason` de error ni en headers cuando
  `auth_mode="none"`, con test
- [x] Construcción de request saliente (`query` hostil solo en body,
  `limit` int elegido por el adaptador, timeout siempre), con test
- [x] `pyproject.toml` (memory-router) — entry point `knowledge-vault`
  registrado
- [x] Convivencia: `/global` selecciona ambos backends, hits mergeados,
  tests de Engram-only `/global` re-corridos sin modificar y en verde
- [x] `git diff --stat` confirma cero cambios en `app.py`/`registry.py`/
  `permissions.py`
- [x] Suite completa de knowledge-vault (`python -m unittest discover -s
  tests -v`) verde — 113 tests
- [x] Suite completa de memory-router (`python -m unittest discover -s
  tests`) verde
- [ ] Validación contra el servicio real en `trantor` — fuera de
  alcance, follow-up explícito
- [ ] Despliegue real — desbloqueado (spec 014 §8), pendiente de ejecutar

---

## 9. Referencias

- `openspec/changes/obsidian-backend/proposal.md`, `design.md`,
  `specs/{knowledge-vault-search-bridge,memory-router-interfaces,
  memory-backend-adapters}/spec.md` (delta) — artefactos SDD completos de
  este change. El nombre del change SDD conserva su etiqueta histórica
  `obsidian-backend`; el módulo y adaptador reales se llaman
  `knowledge-vault`.
- `specs/014_memory_router.md` — Fase 1 de Memory Router, base original.
- `specs/016_honcho_backend.md`, `specs/017_cognee_backend.md` —
  precedente de `ReflectiveBackend`; este change agrega el análogo
  `SearchOnlyBackend`.
- `hermes-native/knowledge-vault/src/knowledge_vault/serve.py` — puente
  HTTP de solo lectura del host.
- `hermes-native/memory-router/src/memory_router/backends/knowledge_vault.py`
  — código fuente del adaptador.
- `docs/services/knowledge-vault.md` — documentación de las 6 unidades
  systemd y su config.
- `kubernetes/mcps/knowledge-vault-search-endpoints.yaml` — `Service` +
  `EndpointSlice` sin desplegar (D-06).
- `hermes-native/knowledge-vault/tests/test_serve.py`,
  `tests/test_memory_router_knowledge_vault_adapter.py`,
  `tests/test_memory_router_app.py` (`GlobalCoexistenceTests`) — suites
  de tests unitarios.

---

**Fin del SDD**
