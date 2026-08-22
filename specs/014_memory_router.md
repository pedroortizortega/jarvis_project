# JARVIS Spec 012 - Software Design Document (SDD)
## Memory Router: capa unificada de acceso a memoria (Fase 1 — Engram)

**Estado:** Fase 1 desplegada en vivo a `mcps` y con los 4 clientes onboardeados (2026-08-21). Pod `Running`, `/healthz` verificado en 200 vía port-forward y vía Ingress con mTLS real para `pedro-claude-code`/`codex`/`opencode`/`hermes-gateway` (handshake completo, certificado propio, rechazo confirmado sin cert de cliente).
**Fecha:** 2026-08-19
**Versión:** 1.0
**Autor:** Pedro Ortiz (vía agente `sdd-apply`)

---

## 0. Por qué existe este spec

La memoria de los agentes está fragmentada: Engram Cloud (spec 011) es el
único backend real hoy, alcanzable solo vía `engram mcp` por-cliente, sin
namespaces ni permisos por identidad. Hay cinco backends más planificados
(Hindsight, Graphiti, Honcho, Cognee, Obsidian), cada uno con su propio
protocolo. Sin una capa de ruteo única, cada agente tendría que integrar
los seis backends por su cuenta.

`memory-router` centraliza el acceso a memoria detrás de una superficie
única, namespaced y con permisos, desplegada como un tercer tenant de
`mcps`. Esta Fase 1 prueba la arquitectura completa contra Engram, antes de
que exista ningún segundo backend.

Relación con specs anteriores: extiende spec 011 (Engram Cloud) — reutiliza
su único camino de acceso soportado (`engram mcp --tools=agent` por stdio),
sin asumir ningún transporte MCP-sobre-HTTP (spec 011 §6 confirma que
`/mcp` devuelve `404` de la propia app).

---

## 1. Alcance de la Fase 1

**Dentro de alcance:**
- Servicio `memory-router`, tercer tenant `mcps`, solo Tailnet.
- Doble superficie: servidor MCP (shim stdio delgado) + REST
  (`POST /memory/store`, `/memory/search`, `/memory/reflect`,
  `GET /agents/context`, `GET /projects/context`).
- Namespaces desde el día uno: `/global`, `/user/master`,
  `/projects/{name}`, `/agents/{name}`; fallback jerárquico de búsqueda
  proyecto → agente → global.
- Contrato de adaptador de backend (capacidades, store/search/health) con
  exactamente una implementación: Engram.
- Modelo de permisos por rol (`coder`, `scientist`, `jarvis`) como reglas
  namespace+verbo, deny-by-default.
- Onboarding de `pedro-claude-code`, `codex`, `opencode`, `hermes-gateway`.

**Fuera de alcance:** implementar Hindsight/Graphiti/Honcho/Cognee/Obsidian;
merge/ranking cruzado entre backends; cambiar el despliegue de Engram en sí;
exposición a Internet público.

---

## 2. Arquitectura

```text
cliente (mTLS + bearer, Tailnet)
  → Ingress Traefik (RequireAndVerifyClientCert, TLSOption mcps-memory-router-mtls)
  → [resolver de identidad] → [validador de rol] → [validador de namespace] → [motor de permisos]
  → [registry de backends: match por capacidad] → [dispatcher]
       |-> sano: adapter.store/search
       |-> degradado (store): journal durable → drainer → adapter
       |-> degradado (search): omitir + marcador "unavailable"
  → adaptador Engram → subproceso `engram mcp --tools=agent` → engram-cloud.mcps.svc:8080
```

La superficie MCP es un shim stdio delgado (`memory-router-mcp`, console
script) que los clientes lanzan localmente y que llama al servicio REST —
mismo dispatcher, mismas decisiones de ruteo, para garantizar paridad
MCP/REST (ver §4).

Implementación: Python 3.11, stdlib puro (`http.server.ThreadingHTTPServer`,
sin FastAPI/ASGI nuevo) — 4 clientes Tailnet no justifican un stack nuevo.
Router sin estado propio de request; solo el journal de escritura y la base
local de Engram son durables.

---

## 3. Contrato de adaptador de backend

```python
@dataclass(frozen=True)
class Capabilities:
    name: str
    verbs: frozenset[str]          # {"store", "search"}; "reflect" ausente en Fase 1
    namespaces: tuple[str, ...]    # patrones glob que este backend acepta
    hierarchical_search: bool

class MemoryBackend(Protocol):
    def capabilities(self) -> Capabilities: ...
    def health(self) -> Health: ...                        # ok | degraded | down
    def store(self, req: StoreRequest) -> StoreResult: ...   # committed | pending
    def search(self, req: SearchRequest) -> SearchResult: ...
```

Registro vía grupo de entry points `memory_router.backends`, igual patrón
que `hermes_agent.plugins`. Agregar el backend #2 es: nuevo paquete + entry
point + fila de config — sin tocar el código del router.

**Adaptador Engram (referencia):** lanza `engram mcp --tools=agent` con
`ENGRAM_CLOUD_SERVER`, `ENGRAM_CLOUD_TOKEN` (por identidad),
`ENGRAM_CLOUD_AUTOSYNC=1` — el camino de acceso probado en spec 011.
`argv` fijo, sin shell, sin `env`/`argv` controlados por el llamador
(amenaza mitigada explícitamente, ver §7). Engram no tiene namespaces
propios: el adaptador codifica el namespace como un prefijo reservado de
`topic_key` (`ns:/projects/foo/...`) dentro del proyecto `jarvis_project`.
`store` → `mem_save`; `search` → `mem_search`. `reflect` está ausente de
sus capacidades.

Fuente: `hermes-native/memory-router/src/memory_router/backends/engram.py`.

---

## 4. Namespaces y roles

### 4.1 Namespaces

Exactamente cuatro raíces soportadas: `/global`, `/user/master`,
`/projects/{name}`, `/agents/{name}`. El llamador **debe declarar
explícitamente** el namespace en cada request — el router nunca lo infiere
de la identidad ni del contenido. Cualquier otra forma (traversal `..`,
wildcards `*`/`?`, raíz desconocida, namespace ausente) se rechaza
fail-closed (`src/memory_router/namespaces.py`).

`store` escribe únicamente en el namespace declarado, sin fallback nunca.
`search` aplica fallback jerárquico cuando el namespace declarado no
produce resultados:

| Namespace declarado | Cadena de fallback |
|---|---|
| `/projects/{n}` | `/projects/{n}` → `/agents/{identidad-llamador}` → `/global` |
| `/agents/{n}` | `/agents/{n}` → `/global` |
| `/global`, `/user/master` | sin fallback (ya es el destino final) |

Cada paso del fallback respeta el chequeo de permisos del rol para ese
namespace; un namespace de fallback no permitido para el rol se salta en
silencio (no es un error — el namespace *declarado* sí falla explícito si
no está permitido).

### 4.2 Roles (Fase 1)

Exactamente tres roles: `coder`, `scientist`, `jarvis`. Ningún otro rol es
reconocido. El mapeo identidad de cliente → rol(es) permitido(s) es
**config del lado del router**, nunca auto-declarado por el llamador — el
llamador solo declara *con cuál* de sus roles permitidos actúa en la
request actual.

| Identidad | Roles permitidos |
|---|---|
| `pedro-claude-code` | `coder` |
| `codex` | `coder` |
| `opencode` | `coder`, `scientist` |
| `hermes-gateway` | `jarvis` |

Tabla de autorización namespace+verbo (deny-by-default; cualquier
combinación no listada se deniega con `403`):

| Rol | `/global` | `/user/master` | `/projects/{n}` | `/agents/{self}` | `/agents/{other}` | `admin/*` |
|---|---|---|---|---|---|---|
| `coder` | search | deny | store+search | store+search | deny | deny |
| `scientist` | store+search | search | search | store+search | deny | deny |
| `jarvis` | store+search | store+search | store+search | store+search | store+search | deny |

Fuente: `hermes-native/memory-router/src/memory_router/permissions.py`.

---

## 5. Semántica degradada (backend no disponible)

**`store` degradado:** el router nunca cae en `200` comprometido ni en un
`5xx` genérico ni descarta el write. Encola la entrada en un journal NDJSON
append-only con `fsync` en cada escritura (PVC, `replicas: 1` +
`Recreate` para garantizar un único escritor) y responde `202
{"status": "pending", "queue_id": ...}`. El journal sobrevive un reinicio
del proceso — verificado por test (`tests/test_memory_router_journal.py`,
reabre el archivo en una instancia nueva y confirma que las entradas
persisten, incluida recuperación ante una línea final truncada por un
crash a mitad de escritura).

**`search` degradado:** el router devuelve los resultados disponibles de
los backends sanos más un marcador explícito `unavailable: [{backend,
reason}]` por cada backend caído — nunca falla toda la request por un solo
backend caído (`200`, no `5xx`).

**`reflect`:** `POST /memory/reflect` devuelve `501 Not Implemented` sin
ninguna lógica ni llamada a ningún backend — placeholder explícito de la
Fase 1 (decisión de producto: Engram no tiene concepto de
"experiencia/lección"; la semántica real llega con Hindsight).

---

## 6. Amenazas consideradas (Fase 1)

| Frontera | Respuesta de diseño | Test |
|---|---|---|
| Selección de namespace | Solo se aceptan las 4 formas literales; traversal/wildcards/raíz desconocida se rechazan antes de autorizar, fail-closed | `test_memory_router_namespaces.py` |
| Subproceso adaptador (`engram mcp`) | `argv` fijo, sin shell, sin `env`/`argv` controlados por el llamador; secretos solo desde el Secret montado; crash → `degraded`, nunca falla toda la request | `test_memory_router_engram_adapter.py` |
| Identidad/bearer | CN de certificado de cliente (forward de Traefik) resuelve a identidad conocida vía config del router; bearer comparado con `hmac.compare_digest` | `test_memory_router_identity.py` |
| Rol auto-declarado | El rol declarado se valida contra el conjunto permitido por identidad (config server-side); un rol fuera de ese conjunto se rechaza aunque el rol exista globalmente | `test_memory_router_permissions.py` |

---

## 7. Convivencia y rollback

El acceso directo a Engram (spec 011) sigue disponible durante el rollout
como camino de rollback — no como opción paralela de estado estable una vez
que el router esté sano (decisión de producto). Rollback: eliminar solo los
recursos `memory-router` de `mcps` y revocar su onboarding de cliente;
Engram, Brave y Graphify quedan intactos. Sin migración de datos.

---

## 8. Bloqueante de despliegue (resuelto)

Los manifiestos de `kubernetes/mcps/memory-router-*.yaml` (Fase 7 de este
change) están **autorados pero aún no aplicados a ningún clúster**. Spec
011 §0 había dejado como pregunta abierta el origen del namespace `mcps`
preexistente (~32h más viejo que el propio spec de Engram Cloud, sin
manifiestos versionados en ningún repo).

**Resolución (2026-08-20):** Pedro confirma que `mcps` fue creado vía
OpenCode como namespace hub para desplegar servicios tipo MCP accesibles
desde el resto de las PCs de su red local. No se encontró evidencia
independiente en el repo (ningún spec, manifiesto o script documenta esa
creación) ni en el clúster (los eventos del namespace ya expiraron por
retención). Verificado por inspección directa (`kubectl -n mcps get all,
ingress,pvc,configmap,secret`) que el namespace hoy solo contiene lo que
spec 011 desplegó (`engram-cloud`, `engram-postgres` y sus recursos
asociados) — no hay recursos huérfanos de origen desconocido conviviendo
ahí. Con la propiedad confirmada por el owner del proyecto y sin señales
en contra, el prerequisito de spec 011 §0 queda resuelto: `kubectl apply`
de los manifiestos de memory-router sobre `mcps` es seguro.

Preguntas abiertas adicionales (heredadas de `design.md`):
- ¿El router usa una única identidad Engram compartida o un proxy por
  token de cliente? Fase 1 asume una identidad única del router — la
  atribución vive en el namespace, no en los principals de Engram.
- Retención/alertas del journal cuando un backend queda caído más de N
  horas.
- Nombre real del header de Traefik con el CN del cliente (placeholder
  actual: `X-Forwarded-Client-Cert-Cn`, ver `app.py`).

**Hallazgo del despliegue real (2026-08-21):** un `entryPoint` de Traefik
dedicado (`memoryrouter`, `:8444`) **no alcanza** para aislar el mTLS de
memory-router del de Engram. Traefik resuelve el certificado de servidor
por SNI (hostname) contra un store global compartido por toda la
instancia, no por entrypoint — confirmado con handshake TLS real: al
compartir `Host(\`trantor.tail07dff9.ts.net\`)` con `engram-tailnet`, el
puerto dedicado servía igual el certificado de Engram y nunca pedía
certificado de cliente (`RequireAndVerifyClientCert` no se aplicaba).
Solución real: hostname propio (`memory-router.trantor.tail07dff9.ts.net`)
con su propio certificado de servidor — recién ahí Traefik indexa un
slot de SNI distinto y aplica la política mTLS correcta. Verificado con
control negativo: sin cert de cliente, `tlsv13 alert certificate
required`. Cualquier otro servicio futuro que necesite mTLS aislado en
este mismo Traefik va a necesitar el mismo patrón (hostname propio, no
solo puerto propio).

---

## 9. Checklist de implementación (Fase 1)

- [x] Contratos (`contracts.py`) — dataclasses/enum/Protocol, con test
- [x] Validación de namespace (`namespaces.py`) — fail-closed, con test
- [x] Identidad (`identity.py`) — CN→identidad, bearer con `hmac.compare_digest`, con test
- [x] Permisos (`permissions.py`) — 3 roles, deny-by-default, con test
- [x] Journal durable (`journal.py`) — NDJSON append-only + fsync, con test de durabilidad ante reinicio
- [x] Registry (`registry.py`) — selección por capacidad vía entry points, con test
- [x] Adaptador Engram (`backends/engram.py`) — `argv`/`env` fijos, degradación explícita, con test
- [x] App: dispatcher + REST + shim MCP + `/memory/reflect` = 501 (`app.py`), con test, incluida paridad MCP/REST
- [x] `pyproject.toml`: grupo de entry points `memory_router.backends` + console scripts
- [x] Manifiestos `kubernetes/mcps/memory-router-*.yaml` (6 archivos) — aplicados a `mcps` (2026-08-21)
- [x] Despliegue real a clúster — pod `Running`, `/healthz` 200 vía port-forward y vía Ingress con mTLS real, control negativo confirmado (§8)
- [x] Onboarding real de los 4 clientes sobre el router — `pedro-claude-code`, `codex`, `opencode`, `hermes-gateway` probados end-to-end (cert + bearer), los 4 responden `/healthz` 200 vía Ingress con mTLS
- [ ] Adaptadores de backend #2–6 (Hindsight, Graphiti, Honcho, Cognee, Obsidian) — fuera de alcance de esta fase

---

## 10. Referencias

- `openspec/changes/memory-router/proposal.md`, `design.md`,
  `specs/*/spec.md` — artefactos SDD completos de este change.
- `specs/011_engram_cloud_centralized.md` — único backend real hoy; camino
  de acceso que reutiliza el adaptador de esta fase.
- `hermes-native/memory-router/src/memory_router/` — código fuente.
- `tests/test_memory_router_*.py` — suite de tests unitarios
  (`python -m unittest discover -s tests`).

---

**Fin del SDD**
