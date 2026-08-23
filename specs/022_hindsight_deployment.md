# JARVIS Spec 022 - Software Design Document (SDD)
## Hindsight Deployment: real in-cluster instance in `mcps`

**Estado:** Especificado (`sdd-spec`), pendiente de diseño/tareas/implementación.
**Fecha:** 2026-08-22
**Versión:** 1.0
**Autor:** Pedro Ortiz (vía agente `sdd-spec`)

---

## 0. Por qué existe este spec

`HindsightBackend` (spec 015) ships, está registrado por entry point y
probado unitariamente contra un transporte fake — pero nada responde en
`hindsight.mcps.svc.cluster.local`. El adaptador es código muerto en
producción: los stores/searches de `/projects/*` enrutan a un backend
permanentemente inalcanzable, memory-router degrada en silencio, y nunca
se valida memoria real de Hindsight de punta a punta. Este spec cierra el
ítem abierto de checklist de `specs/015_hindsight_backend.md`: "Despliegue
real de una instancia de Hindsight en el clúster".

Además, el default hardcodeado `HINDSIGHT_BASE_URL` del adaptador usa el
puerto **8080**, mientras que el default real de `HINDSIGHT_API_PORT` de
Hindsight es **8888**. Sin corregir, es un footgun de deploy-time
permanente: cada operador futuro debe recordar un override para compensar
un default incorrecto en código.

---

## 1. Alcance de este change

**Dentro de alcance:**
- Manifiestos nuevos en `kubernetes/mcps/` (`hindsight-deployment.yaml`,
  `hindsight-service.yaml`, `hindsight-pvc.yaml`, `hindsight-configmap.yaml`),
  siguiendo la convención plana `hindsight-*.yaml` ya usada por
  `memory-router-*.yaml`.
- Imagen `ghcr.io/vectorize-io/hindsight:latest` — primera imagen de este
  repo sin Dockerfile local.
- Postgres embebido persistido vía PVC en `/home/hindsight/.pg0`,
  `storageClassName: local-path`, igual que `memory-router-pvc.yaml`.
- LLM vía `codex-shim` (`http://codex-shim.llms.svc.cluster.local:8080/v1`),
  autenticado con una **copia duplicada** del secret bearer interno de
  codex-shim, llevada a `mcps` (los Secrets de k8s son namespace-scoped).
- Embeddings vía el proveedor `onnx` propio de Hindsight
  (`HINDSIGHT_API_EMBEDDINGS_PROVIDER=onnx`,
  `HINDSIGHT_API_EMBEDDINGS_ONNX_MODEL_ID=intfloat/multilingual-e5-small`)
  + su reranker, 100% dentro del pod. **Enmendado durante la propuesta**
  respecto del plan original (`bge-small-en-v1.5`, inglés-only): el
  proveedor `onnx` soporta swap de modelo multilingüe y maneja los
  prefijos `query:`/`passage:` de forma nativa dentro de Hindsight — ver
  §3.
- **Auth bearer desde el día uno**: secret generado y compartido, referenciado
  como `HINDSIGHT_API_TENANT_API_KEY` en el server y como `HINDSIGHT_TOKEN`
  + `HINDSIGHT_AUTH_MODE=bearer` en memory-router.
- **Fix de puerto en código**: default de `hindsight.py` de `8080` → `8888`,
  test correspondiente, y §4 de `specs/015`.
- Cobertura de bootstrap para los nuevos secrets, consistente con
  `kubernetes/mcps/bootstrap/03-create-secrets.sh`.
- Tests de manifiesto que verifican YAML parseado (imagen, puerto `8888`,
  mount path de PVC, refs de secret, ClusterIP, ausencia de Ingress).
- Este spec numerado y su spec delta de OpenSpec.

**Fuera de alcance:**
- Cablear Hindsight a `local-embeddings` (spec 021) — evaluado y
  **descartado** para este backend, no simplemente diferido (ver §3): el
  proveedor `onnx` empaquetado de Hindsight ya cubre la necesidad
  multilingüe con prefijado correcto, algo que `local-embeddings`
  (endpoint OpenAI-compatible, sin señal `input_type`) no puede ofrecer.
- Ingress o exposición externa. Solo consumidor intra-clúster.
- Construir o forkear una imagen de Hindsight.
- Postgres externo, HA, réplicas > 1, backups de la DB embebida.
- Cambiar el wire format del adaptador (verbos, mapeo de bank) — solo
  cambia el puerto default.
- Gestión de claves multi-tenant más allá de la única tenant key.

---

## 2. Decisiones ya resueltas (ronda de preguntas de la propuesta)

Las 5 preguntas abiertas de `proposal.md` quedan resueltas así, como
decisiones asentadas de este spec — no quedan abiertas:

| # | Pregunta | Decisión |
|---|---|---|
| 1 | Sizing de recursos | `requests: 1 CPU / 2Gi`, `limits: 4 CPU / 6Gi` — aceptado tal como se propuso. Sin precedente exacto (Postgres embebido + bge-small + reranker es un perfil distinto al de `local-embeddings`); punto de partida conservador, se monitorea después. |
| 2 | Tamaño de PVC | `10Gi` — aceptado tal como se propuso. Cubre datos de Postgres + pesos de modelo cacheados; `local-path` no expande PVCs in-place trivialmente. |
| 3 | Tag de imagen | `:latest` + `imagePullPolicy: Always` para el día uno — aceptado. Pinnear un digest queda como follow-up una vez validada una versión conocida-buena. |
| 4 | Orden de rollout de auth | Ambos Deployments (Hindsight + memory-router) aterrizan juntos en el mismo apply. Un `401` por auth ausente/desalineada es una falla ruidosa y esperada — **sin** período de gracia con auth deshabilitada. |
| 5 | Fuente y dimensión de embedding/idioma | **Resuelto: proveedor `onnx` empaquetado + `intfloat/multilingual-e5-small`** (384-dim, multilingüe, prefijado `query:`/`passage:` nativo). Reemplaza tanto el plan original (`bge-small-en-v1.5`, inglés-only) como la alternativa considerada de cablear `local-embeddings` — ver §3 para el razonamiento completo. |

Adicional, ya decidido en la propuesta (no abierto):
- Pod Hindsight autocontenido en `mcps`, imagen
  `ghcr.io/vectorize-io/hindsight:latest`, Postgres embebido vía PVC en
  `/home/hindsight/.pg0` (`storageClassName: local-path`), LLM vía
  codex-shim con secret bearer interno duplicado en `mcps`, embeddings vía
  el proveedor `onnx` empaquetado de Hindsight con
  `intfloat/multilingual-e5-small`.
- **Fix de puerto en código**, no un override de env que compense un
  default incorrecto: `HINDSIGHT_BASE_URL` default cambia de `8080` a
  `8888` en `hermes-native/memory-router/src/memory_router/backends/hindsight.py`,
  su test en `tests/test_memory_router_hindsight_adapter.py:42`, y la
  tabla de config §4 de `specs/015_hindsight_backend.md`.
- **Sin NetworkPolicy** para `mcps` — no-op explícito, misma razón que
  `local-embeddings`: no existe default-deny en `mcps`, ClusterIP + sin
  Ingress es el límite real, y el tráfico amplio intra-namespace entre
  memory-router y Hindsight es el objetivo de diseño.
- **Sin Ingress** — solo consumidor intra-clúster.
- Un solo Deployment, estrategia `Recreate`, `replicas: 1` (RWO PVC +
  Postgres embebido no toleran dos escritores).

---

## 3. Fuente de embedding: `onnx` empaquetado con modelo multilingüe

El plan original de este change usaba el proveedor `local`
(SentenceTransformers) de Hindsight con su default `bge-small-en-v1.5`
(384 dims, optimizado para inglés). Durante la ronda de preguntas de la
propuesta se evaluó como alternativa cablear Hindsight al servicio propio
`local-embeddings` (spec 021, `multilingual-e5-large`, 1024 dims) para
cumplir con la política del proyecto de que los backends de memoria usen
ese servicio.

Investigación en vivo contra la documentación real de Hindsight
(`github.com/vectorize-io/hindsight`, `hindsight-docs/docs/developer/
configuration.md`) encontró una tercera opción que domina a ambas: el
proveedor **`onnx`** de Hindsight (distinto de `local`) soporta swap de
modelo vía `HINDSIGHT_API_EMBEDDINGS_ONNX_MODEL_ID` — incluyendo
`intfloat/multilingual-e5-small` (multilingüe, 384 dims) — y **maneja los
prefijos `query:`/`passage:` de forma nativa dentro de Hindsight mismo**
(`HINDSIGHT_API_EMBEDDINGS_ONNX_QUERY_PREFIX`/`_PASSAGE_PREFIX`, defaults
`"query: "`/`"passage: "`).

Esa última propiedad es la que descarta a `local-embeddings` para este
backend específico: memory-router nunca genera embeddings — es Hindsight
quien llama internamente a su proveedor de embeddings, tanto para
almacenar (`retain`) como para buscar (`recall`). Si esa llamada fuera al
endpoint OpenAI-compatible de `local-embeddings`, el cliente de Hindsight
(shape estándar `input`/`model`/`encoding_format`) no tiene forma de
mandar la señal custom `input_type` que distingue query de passage — el
resultado sería embeddings sin prefijo en ambos lados, simétrico pero con
peor ranking que el prefijado correcto. El proveedor `onnx` no tiene ese
problema porque el prefijo lo agrega el propio Hindsight antes de llamar
al modelo.

**Decisión: `HINDSIGHT_API_EMBEDDINGS_PROVIDER=onnx` +
`HINDSIGHT_API_EMBEDDINGS_ONNX_MODEL_ID=intfloat/multilingual-e5-small`,
día uno.** Resuelve el idioma (español + inglés) y el prefijado
correctamente, sin agregar una dependencia de red cross-namespace
(`mcps`→`llms`) ni el problema de degradación de `local-embeddings`.

Sigue existiendo una **puerta de un solo sentido** de dimensión/modelo:
una vez que existan vectores persistidos con `multilingual-e5-small`,
cambiar de modelo o dimensión más adelante exige re-embedding, no un
cambio de config — igual que con cualquier embedder. Se documenta como
compromiso consciente, no como limitación aceptada a regañadientes: a
diferencia del plan original, este no deja una brecha de calidad conocida
en español que dependa de un follow-up futuro para cerrarse.

Excepción registrada a la política del proyecto ("los backends de memoria
nuevos/cambiados usan `local-embeddings`"): para Hindsight específicamente,
el camino bundled es objetivamente mejor (prefijado correcto, sin
dependencia de red extra) que enrutar a `local-embeddings` — la política
sigue como default a evaluar primero, no como regla absoluta.

---

## 4. Invariantes de runtime (namespace `mcps`)

| Invariante | Garantía |
|---|---|
| DNS/puerto in-cluster | `hindsight.mcps.svc.cluster.local:8888` |
| Auth | Bearer obligatorio; una request sin token o con token incorrecto recibe `401` |
| Persistencia | Postgres embebido en PVC `10Gi` (`local-path`) montado en `/home/hindsight/.pg0`; sobrevive restart/reschedule del pod |
| LLM | Exclusivamente vía `codex-shim.llms.svc.cluster.local:8080/v1`, con el secret bearer interno duplicado en `mcps` |
| Embeddings/reranking | Proveedor `onnx` empaquetado de Hindsight (`intfloat/multilingual-e5-small` + reranker), sin dependencia externa, prefijado `query:`/`passage:` nativo |
| Réplicas/estrategia | `replicas: 1`, `Recreate` — RWO PVC + Postgres embebido no toleran dos escritores |
| Imagen | `ghcr.io/vectorize-io/hindsight:latest`, `imagePullPolicy: Always` (pin de digest es follow-up) |
| Sizing | requests `1 CPU / 2Gi`, limits `4 CPU / 6Gi` |
| Red | Solo `ClusterIP`; sin Ingress; sin NetworkPolicy propia (no-op explícito) |
| UID/GID de runtime (D-02) | Verificado en vivo (no asumido) — ver §4.1 |

### 4.1 UID/GID de runtime verificado (D-02)

`docker inspect -f '{{.Config.User}}' ghcr.io/vectorize-io/hindsight:latest`
devuelve el `USER` no-numérico de la imagen, `hindsight` — insuficiente
para `runAsUser`/`runAsGroup`/`fsGroup`, que requieren un valor numérico
(un `USER` no-numérico con `runAsNonRoot: true` hace que el kubelet
rechace el contenedor, el mismo bug que documenta
`memory-router-deployment.yaml:35-37`).

Resuelto ejecutando el contenedor:

```
$ docker run --rm --entrypoint id ghcr.io/vectorize-io/hindsight:latest
uid=1000(hindsight) gid=1000(hindsight) groups=1000(hindsight)
```

**`runAsUser: 1000`, `runAsGroup: 1000`, `fsGroup: 1000`** —
verificado contra la imagen real (`ghcr.io/vectorize-io/hindsight:latest`,
digest `sha256:a0e937366261b8a8f20ebcaf13758c689c381dcbbf01684e4375c2787c8c666d`
al momento de la verificación), no asumido desde la convención `10001` de
este repo para imágenes propias. Valor aplicado en
`kubernetes/mcps/hindsight-deployment.yaml`.

---

## 5. Testing — convención a seguir

Los manifiestos nuevos viven bajo `kubernetes/mcps/`, no bajo un
subdirectorio de servicio con su propio `pytest.ini` (a diferencia de
`kubernetes/local-embeddings/` o `kubernetes/model-panel/`, que son
servicios FastAPI construidos en este repo). `mcps` ya tiene manifiestos
existentes (`memory-router-*.yaml`) probados con el patrón de tests de
manifiesto (`unittest`, YAML parseado) bajo el `tests/` raíz del repo,
consistente con `openspec/config.yaml` (`strict_tdd: true`,
`python -m unittest discover -s tests`). Este change sigue esa misma
convención — no la de `pytest.ini` de spec 021 — porque no introduce
código de aplicación propio, solo manifiestos YAML declarativos más el
fix de puerto en `hindsight.py`, que ya vive bajo la suite `unittest`
existente de `memory-router`.

---

## 6. Riesgos (heredados de la propuesta)

| Riesgo | Probabilidad | Mitigación |
|---|---|---|
| `:latest` envía en silencio una versión de Hindsight que rompe | Impacto medio/alto | `imagePullPolicy: Always` + follow-up de pin de digest; test de manifiesto asegura la ref de imagen |
| Postgres embebido corrupto por restart no-graceful / dos escritores | Media | `Recreate`, `replicas: 1`, PVC RWO, `terminationGracePeriodSeconds` generoso |
| Sizing de recursos es una estimación (Postgres + 2 modelos en un pod) | Alta | requests conservadores, limits con headroom, medir después; undersizing se manifiesta como OOMKill (ruidoso, no silencioso) |
| El secret bearer diverge entre los dos Deployments | Media | Un solo Secret, referenciado por ambos — nunca dos literales |
| El secret duplicado de codex-shim diverge del original en `llms` | Media | El bootstrap script copia desde la fuente de verdad en vez de regenerar |
| Descarga del primer modelo al arrancar necesita egress y demora el readiness | Media | `startupProbe` con threshold de fallo generoso; modelos cacheados en el PVC (costo único, no por-restart) |
| Modelo/dimensión de embedding sigue siendo puerta de un solo sentido (cualquier swap futuro exige re-embed) | Baja | `intfloat/multilingual-e5-small` es la elección del día uno, documentada en §3; riesgo materialmente menor que el plan original porque el recall en español ya no depende de una migración futura |
| `readOnlyRootFilesystem` rompe escrituras de Postgres/cache de modelo | Media | Mount de PVC con escritura + `HOME` en el PVC; si la imagen no puede correr read-only, se documenta la desviación explícitamente |

---

## 7. Convivencia y rollback

Split rollback, porque el change tiene dos mitades independientes.

**Manifiestos (aditivo, aislado):** `kubectl delete deployment/service/
configmap hindsight -n mcps` devuelve el clúster al estado de hoy —
el backend Hindsight de memory-router vuelve a "unavailable", que el
router ya degrada de forma correcta (requisito de indisponibilidad
parcial de `memory-backend-adapters`). Conservar o borrar el PVC es
independiente; conservarlo preserva memorias almacenadas para un reintento.
Quitar `HINDSIGHT_TOKEN`/`HINDSIGHT_AUTH_MODE` de memory-router restaura
auth `none`.

**Código (default de puerto):** revertir el default de una línea más su
test restaura `8080`. Seguro en aislamiento porque nada escuchaba en
`8080`; la única forma de que esto rompa a alguien es si ya había
desplegado con un override explícito de `HINDSIGHT_BASE_URL`, que sigue
ganando sobre el default en cualquier caso.

Revertir el commit elimina manifiestos, spec y fix de puerto juntos. Las
memorias persistidas en Hindsight son el único artefacto no reversible, y
son datos nuevos, no una migración de datos existentes.

---

## 8. Checklist de este spec

- [x] Alcance dentro/fuera definido
- [x] Las 5 preguntas abiertas de la propuesta resueltas como decisiones
- [x] Tradeoff de dimensión de embedding documentado explícitamente (§3)
- [x] Invariantes de runtime (auth, persistencia, LLM, embeddings, red)
- [x] Conflicto de convención de testing verificado y resuelto (§5)
- [x] Diseño (`sdd-design`) — `openspec/changes/hindsight-deployment/design.md`
- [x] Tareas (`sdd-tasks`) — `openspec/changes/hindsight-deployment/tasks.md`
- [x] Implementación (`sdd-apply`) — PR #56, PR #57
- [x] Aplicado en `trantor` y validado en vivo contra el clúster real (§8.1)

---

## 8.1 Validación contra el clúster real (2026-08-22)

Aplicado en `trantor` (`kubectl apply` vía `bootstrap/03-create-secrets.sh` +
`05-deploy-manifests.sh`). Resultado, punto por punto contra los criterios
de éxito de `proposal.md`:

- [x] Pod `hindsight` `Running` 1/1, `hindsight-data` PVC `Bound` (`10Gi`,
  `local-path`). D-03 (`readOnlyRootFilesystem: true`) sobrevivió el primer
  arranque sin CrashLoop — riesgo de diseño resuelto, no solo diseñado.
- [x] `GET /health` responde `200` `{"status":"healthy","database":"connected",...}`
  in-cluster, **sin** requerir auth — D-12 resuelto: el tenant key no
  bloquea los probes.
- [x] Pod `memory-router` reconectó sano tras el rollout conjunto.
- [ ] ~~Un request sin auth es rechazado~~ — **BUG real encontrado**, ver
  abajo. Corregido en este mismo change antes de cerrar el checklist.

### Bug encontrado: `HINDSIGHT_API_TENANT_API_KEY` sola no activa nada

`PUT /v1/default/banks/{id}` **sin** header `Authorization` devolvió `200`
y creó el bank, con `HINDSIGHT_API_TENANT_API_KEY` correctamente seteada
en el pod (confirmado leyendo el env real del container, no asumido). El
proposal y este spec (§2, ítem 4) asumían que setear el tenant key
alcanzaba para activar el auth bearer — asunción incorrecta, no verificada
contra la doc real de Hindsight hasta este punto.

Causa raíz (`hindsight-docs/docs/developer/configuration.md`,
`github.com/vectorize-io/hindsight`): hacen falta **dos** variables, no
una. `HINDSIGHT_API_TENANT_API_KEY` es solo el secreto compartido contra
el que se valida — quien realmente **activa** la comprobación es
`HINDSIGHT_API_TENANT_EXTENSION=hindsight_api.extensions.builtin.tenant:ApiKeyTenantExtension`.
Sin esa segunda variable, el key queda cargado en memoria pero sin ningún
efecto — ninguna ruta lo consulta.

**Fix**: agregar `HINDSIGHT_API_TENANT_EXTENSION` al env block de
`hindsight-deployment.yaml`, con un test de manifiesto nuevo
(`test_tenant_auth_extension_enabled`) que falla si falta. Re-desplegado y
re-verificado en vivo: request sin token → `401`; mismo request con el
bearer real → `200`. Ver rama/PR `fix/hindsight-tenant-auth-extension`.

**Lección**: la doc pública de Hindsight documenta claramente el patrón de
dos variables — el proposal/spec original solo miró una. Confirma, otra
vez, que "el config existe y parece razonable" no reemplaza probarlo
contra el servicio real antes de dar por cerrado un criterio de éxito de
seguridad.

---

## 9. Referencias

- `openspec/changes/hindsight-deployment/proposal.md` — propuesta completa.
- `openspec/changes/hindsight-deployment/specs/hindsight-service/spec.md`
  — spec delta de OpenSpec (nueva capability), fuente normativa de
  requisitos y escenarios del servicio desplegado.
- `openspec/changes/hindsight-deployment/specs/memory-backend-adapters/spec.md`
  — spec delta de OpenSpec para el fix de puerto y el cambio de auth
  desplegada del adaptador Hindsight.
- `specs/015_hindsight_backend.md` — spec del adaptador `HindsightBackend`,
  cuyo ítem de checklist de despliegue cierra este change.
- `specs/021_local_embeddings_service.md` — servicio de embeddings
  multilingüe propio; evaluado y no elegido para Hindsight (ver §3).
- `https://github.com/vectorize-io/hindsight/blob/main/hindsight-docs/docs/developer/configuration.md`
  — documentación real de los proveedores de embeddings de Hindsight
  (`local`, `onnx`, `openai`, ...), fuente de la decisión de §3.
- `hermes-native/memory-router/src/memory_router/backends/hindsight.py` —
  código fuente del adaptador (fix de puerto).
- `kubernetes/mcps/` — directorio de manifiestos donde aterrizan los
  nuevos `hindsight-*.yaml`.

---

**Fin del SDD**
