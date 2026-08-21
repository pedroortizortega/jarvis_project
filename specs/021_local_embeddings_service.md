# JARVIS Spec 021 - Software Design Document (SDD)
## Local Embeddings Service: endpoint `/v1/embeddings` compatible con OpenAI, self-hosted, sin egress externo

**Estado:** Especificado (fase spec de SDD) — pendiente diseño (`sdd-design`), tareas (`sdd-tasks`) y aplicación (`sdd-apply`); ningún código ni manifiesto existe todavía
**Fecha:** 2026-08-20
**Versión:** 1.0
**Autor:** Pedro Ortiz (vía agente `sdd-spec`)

---

## 0. Por qué existe este spec

El clúster no tiene ningún endpoint de embeddings. Todo backend de memoria
que necesita vectores (`embedding.model_config` de Honcho, el `embedder` de
Graphiti, la config de embeddings de Cognee, y más adelante Hindsight /
knowledge-vault / Engram) hoy apunta a OpenAI: egress externo, costo por
token, y una dependencia dura de una API key. `llama-router` no es un
sustituto — pide `nvidia.com/gpu: "1"` y queda inalcanzable cada vez que la
spec 012 le entrega la GPU a Cloud, y sus modelos Qwen de chat no están
optimizados para embeddings.

Este change introduce una utilidad de embeddings **durable, de propósito
general y cluster-wide** — solo CPU, cero llamadas externas, cero costo por
token, siempre arriba sin importar el estado del handoff de GPU. Desbloquear
la validación en vivo de Honcho/Graphiti/Cognee es el primer caso de uso
motivador, no el techo del alcance.

---

## 1. Alcance de este change

**Dentro de alcance:**
- Nuevo directorio de servicio `kubernetes/local-embeddings/` (namespace
  `llms`), hermano de `codex-shim`/`model-panel`, siguiendo exactamente sus
  convenciones de manifiestos.
- App FastAPI que expone `POST /v1/embeddings` (contrato compatible con
  OpenAI: `input` como string o lista, `model` reflejado tal cual se pidió),
  más `GET /healthz` y `GET /v1/models`.
- `fastembed` (ONNX Runtime, sin torch/CUDA) con un modelo fijo y una
  dimensión fija, horneados en la imagen en build time, para que el egress
  en runtime sea cero.
- Manifiestos: `Dockerfile`, `deployment.yaml`, `service.yaml`, `rbac.yaml`,
  `kustomization.yaml` — mismos patrones de seguridad que `codex-shim`/
  `model-panel` (`runAsNonRoot`, `readOnlyRootFilesystem: true`, `drop:
  [ALL]`, `RuntimeDefault`, sin request de GPU).
- Contrato de consumo: `LOCAL_EMBEDDINGS_BASE_URL=http://local-embeddings
  .llms.svc.cluster.local:8080/v1`, espejando `CODEX_SHIM_BASE_URL`.
- Este spec numerado y el spec delta de OpenSpec en
  `openspec/changes/local-embeddings-service/specs/local-embeddings/spec.md`.

**Fuera de alcance:**
- Desplegar los contenedores de Honcho / Graphiti / Cognee (bloqueante
  separado).
- Cablear la config de cualquier consumidor a este servicio — eso aterriza
  con cada consumidor.
- Reranking, `/v1/completions`, chat, o cualquier endpoint que no sea de
  embeddings.
- Autenticación. Solo interno al clúster, sin Ingress — se acepta y se
  ignora un header `Authorization` por compatibilidad con clientes.
- Un `NetworkPolicy` propio del servicio (ver §7 y Decisiones abajo).
- Almacenamiento, indexado o migración de vectores existentes.
- Aceleración por GPU, autoscaling, servir múltiples modelos.

---

## 2. Decisiones ya resueltas (ronda de preguntas de la propuesta)

Las 5 preguntas abiertas de `proposal.md` quedan resueltas así, como
decisiones asentadas de este spec — no quedan abiertas:

| # | Pregunta | Decisión |
|---|---|---|
| 1 | Modelo y dimensión | `intfloat/multilingual-e5-small`, **384 dims**. Se prefiere sobre `BAAI/bge-small-en-v1.5` (el default inglés de fastembed) porque las memorias son español/mixtas y la calidad de recall multilingüe pesa más que el ahorro marginal de RAM/latencia del modelo solo-inglés. |
| 2 | Nombre del directorio/servicio | `kubernetes/local-embeddings/` (directorio), Service `local-embeddings`, DNS `local-embeddings.llms.svc.cluster.local`. **No** `local-embeddings-service` — ese nombre fue solo naming informal de chat, superado. |
| 3 | Compatibilidad de nombre de modelo | Se acepta **cualquier** string en `model` en el request y siempre se sirve el modelo fijo (`intfloat/multilingual-e5-small`), reflejando en la respuesta el nombre que pidió el cliente. Sin rechazo estricto de nombres desconocidos — cero parcheo de config por consumidor. |
| 4 | Mismatch de dimensión | **Nunca** se rellena (pad) ni se trunca un vector para simular otra dimensión (p. ej. 1536) para consumidores que asumen el default de OpenAI. Cada consumidor declara su dimensión real (384) en su propia config cuando se cablee — eso queda fuera de alcance aquí. Pad/truncate produciría silenciosamente vectores incorrectos que rompen cosine similarity — es un "nunca" duro, no un default blando. |
| 5 | Techo de batch | **256** inputs por request como máximo. Por encima se rechaza con un 4xx claro — nunca se trunca el batch silenciosamente ni se degrada. |
| 6 | Prefijos `query:`/`passage:` de e5 (decisión post-diseño, 2026-08-21) | Campo opcional `input_type: "query"\|"passage"` en el request. Ausente → embed verbatim (default, sin cambios para cualquier cliente OpenAI-SDK estándar que no conoce el campo). Presente → antepone el prefijo correspondiente antes de embeber. Aditivo en el wire, nunca rompe compatibilidad. Reemplaza la decisión original de diseño (D-10 verbatim-sin-excepción) — se construye ahora, en este mismo change, no se difiere. |

Adicional, ya decidido en la propuesta (no abierto):
- **NetworkPolicy:** ninguna agregada, no-op explícito — `kubernetes/policy/
  netpol-llms.yaml` no tiene default-deny en `llms`; ClusterIP + sin
  Ingress es el único límite de red intencional; el consumo amplio
  intra-namespace es el objetivo de diseño explícito.
- **Auth:** ninguna requerida; se acepta y se ignora un header
  `Authorization` por compatibilidad con SDKs de OpenAI.
- **Modelo horneado en build time**, no descargado en runtime — preserva
  `readOnlyRootFilesystem: true` y mantiene el egress en runtime en cero.

---

## 3. Contrato HTTP (compatible con OpenAI)

```text
POST /v1/embeddings
  body:  { "input": string | string[], "model": string, "encoding_format"?: "float"|"base64", "dimensions"?: int, "input_type"?: "query"|"passage" }
  200:   { "object": "list",
           "data": [ { "object": "embedding", "index": int, "embedding": float[384] }, ... ],
           "model": <mismo string que se recibió en la request>,
           "usage": { "prompt_tokens": int, "total_tokens": int } }
  4xx:   "encoding_format" != "float" (p. ej. "base64")
         "dimensions" presente y != 384
         len(input) > 256
         "input" vacío o de tipo inválido
         "input_type" presente y != "query"|"passage"

GET /healthz
  200 solo después de que el modelo terminó de cargar; no-ready antes

GET /v1/models
  200: { "object": "list", "data": [ { "id": "intfloat/multilingual-e5-small", "object": "model", ... } ] }
```

El nombre de modelo del request (`model`) nunca se valida contra una
lista — se acepta cualquier string y se refleja tal cual en la respuesta,
mientras la inferencia siempre corre sobre el modelo fijo. Ver el spec
delta de OpenSpec (`specs/local-embeddings/spec.md`) para el detalle
completo de requisitos y escenarios, que es la fuente normativa de este
contrato.

---

## 4. Invariantes de runtime (namespace `llms`)

| Invariante | Garantía |
|---|---|
| Sin egress externo | Modelo ONNX resuelto 100% desde la imagen; cero llamadas de red salientes para servir un request |
| Sin GPU | El Deployment no pide `nvidia.com/gpu`; corre en cualquier nodo CPU |
| `readOnlyRootFilesystem: true` | El cache del modelo vive en la imagen (ruta de solo lectura); `HOME=/tmp` + `emptyDir` para cualquier escritura runtime |
| Solo ClusterIP | Sin Ingress; alcanzable únicamente dentro del clúster |
| Sin NetworkPolicy propia | No-op explícito — consumo intra-namespace amplio es el diseño deseado |
| Dimensión fija | 384, siempre — nunca pad/truncate para simular otra dimensión |
| Techo de batch | 256 inputs por request; por encima, 4xx, nunca truncado |

---

## 5. Testing — convención a seguir (conflicto detectado y resuelto)

`openspec/config.yaml` declara `strict_tdd: true` y `test_command: python
-m unittest discover -s tests` como convención repo-wide. Sin embargo,
ambos servicios hermanos bajo `kubernetes/` — `kubernetes/codex-shim/
pytest.ini` y `kubernetes/model-panel/pytest.ini` — usan `pytest` con
`asyncio_mode = auto`, no `unittest`. Se verificó explícitamente (no se
asumió):

- Ambos directorios `kubernetes/codex-shim/` y `kubernetes/model-panel/`
  tienen `pytest.ini` presente (`testpaths = tests`, `asyncio_mode = auto`).
- No existe ningún workflow de CI en este repo (`.github/workflows/` no
  existe) — ni la convención de `config.yaml` ni la de `pytest.ini` están
  siendo "ejecutadas" por nada automatizado hoy; ambas son solo
  convenciones locales invocadas manualmente.

**Decisión:** este servicio sigue la convención `pytest.ini` de sus
hermanos `kubernetes/`, no el `unittest discover` de `openspec/
config.yaml`. Razón: es un servicio FastAPI con endpoints async, igual que
`codex-shim`/`model-panel`; `pytest-asyncio` (`asyncio_mode = auto`) es el
patrón ya establecido dos-de-dos para probar rutas async de FastAPI en
`kubernetes/`, mientras que `unittest discover` no tiene soporte nativo
para tests async sin boilerplate adicional. Como ninguna CI aplica
ninguna de las dos convenciones hoy, no hay evidencia de que romper el
patrón de los hermanos `kubernetes/` tenga beneficio, y sí hay precedente
directo en contra. `sdd-tasks`/`sdd-apply` deben crear `kubernetes/
local-embeddings/pytest.ini` con la misma forma que `codex-shim`/
`model-panel`, y NO usar `python -m unittest discover -s tests` para este
directorio. Esta desviación de `openspec/config.yaml` queda documentada
aquí explícitamente, no asumida en silencio.

---

## 6. Riesgos (heredados de la propuesta, sin cambios)

| Riesgo | Probabilidad | Mitigación |
|---|---|---|
| La dimensión es una puerta de un solo sentido una vez persistidos vectores | Alto impacto | Fijada en este spec (384, `multilingual-e5-small`); un cambio futuro es una migración de re-embedding, no un ajuste de config |
| Latencia de CPU en batches grandes bloquea el event loop | Media | Inferencia en threadpool; techo de batch de 256 con 4xx claro por encima |
| Un consumidor manda parámetros de OpenAI que se ignoran (`encoding_format`, `dimensions`) | Media | Comportamiento explícito en el spec delta: se honra `float`, se rechaza `base64` y cualquier `dimensions` != 384 con error claro, nunca en silencio |
| `readOnlyRootFilesystem` rompe escrituras de cache de ONNX/HF | Media | Ruta de cache horneada de solo lectura + `HOME=/tmp` con `emptyDir`, asertado por un test de manifiesto |

---

## 7. Convivencia y rollback

Totalmente aditivo y aislado. Ningún consumidor existe todavía que apunte
a este servicio — desplegarlo no cambia el comportamiento observable de
nada más en el clúster. `kubectl delete -k kubernetes/local-embeddings/`
elimina Deployment/Service/SA sin romper ningún otro componente. Revertir
el commit elimina el directorio y `specs/021`. Sin estado persistido, sin
schema, sin migración — el servicio es stateless. Si algún consumidor ya
hubiera almacenado vectores contra él, el rollback exigiría re-embedding
de ese consumidor; ese acoplamiento solo existe una vez que un consumidor
se cablee, lo cual está fuera de alcance de este change.

---

## 8. Checklist de este spec

- [x] Alcance dentro/fuera definido
- [x] Las 5 preguntas abiertas de la propuesta resueltas como decisiones
- [x] Contrato HTTP completo (`/v1/embeddings`, `/healthz`, `/v1/models`)
- [x] Invariantes de runtime (sin egress, sin GPU, ClusterIP, sin NetworkPolicy)
- [x] Conflicto de convención de testing verificado y resuelto (pytest.ini)
- [x] Diseño (`sdd-design`) — completo, ver `openspec/changes/local-embeddings-service/design.md` (D-01..D-15, threat matrix, delivery forecast)
- [x] Tareas (`sdd-tasks`) — completo, ver `openspec/changes/local-embeddings-service/tasks.md`
- [ ] Implementación (`sdd-apply`) — pendiente

### 8.1 Resumen de tareas (chained PRs)

El forecast de `design.md` excedía el budget de 400 líneas revisadas en
ambas slices propuestas (~640 y ~745). `sdd-tasks` re-cortó el trabajo en
tres PRs encadenados siguiendo las costuras ya identificadas en el diseño,
cada uno por debajo de ~400 líneas autoría:

- **PR 1 — Core puro + adaptor fastembed** (~365 líneas): `app/embeddings.py`
  (validación, batching, ensamblado de respuesta, taxonomía de error),
  `app/model.py` (carga lazy de `fastembed`), `test_embeddings_core.py`
  (`unittest.TestCase`). Sin servidor, sin cluster — embedder falso inyectado.
- **PR 2 — Capa HTTP** (~280 líneas): `app/main.py` (lifespan, 3 rutas,
  semáforo, envelope de error OpenAI), `test_api.py` (`TestClient`,
  pytest-only), `pytest.ini`.
- **PR 3 — Manifiestos + enforcement + cierre de spec** (~290 líneas):
  `Dockerfile`, `deployment.yaml`, `service.yaml`, `rbac.yaml`,
  `kustomization.yaml`, `test_local_embeddings_manifest.py`, el bridge
  `tests/test_local_embeddings.py` (D-15), y el cierre de este checklist.

Detalle completo, orden RED/GREEN/REFACTOR y comandos de test enfocados en
`openspec/changes/local-embeddings-service/tasks.md`.

---

## 9. Referencias

- `openspec/changes/local-embeddings-service/proposal.md` — propuesta
  completa (intent, alcance, approach, riesgos, rollback).
- `openspec/changes/local-embeddings-service/specs/local-embeddings/spec.md`
  — spec delta de OpenSpec, fuente normativa de requisitos y escenarios.
- `kubernetes/codex-shim/`, `kubernetes/model-panel/` — convenciones de
  manifiestos y testing que este servicio sigue.
- `openspec/config.yaml` — config repo-wide de SDD; ver §5 para la
  desviación documentada de su `test_command`.
