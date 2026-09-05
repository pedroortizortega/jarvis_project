# JARVIS Spec 025 - Software Design Document (SDD)
## Hermes Per-Subagent Model Routing

**Estado:** Implementado (fork: patch generado, pendiente de aplicar por el
usuario; jarvis_project: config + spec en el árbol de trabajo, sin commit).
**Fecha:** 2026-08-31
**Versión:** 1.0
**Autor:** Pedro Ortiz (vía agentes `sdd-spec`/`sdd-design`/`sdd-tasks`/`sdd-apply`)

---

## 0. Por qué existe este spec

`delegate_task(tasks=[...])` (fork `~/.hermes/hermes-agent`, `tools/delegate_tool.py`)
hoy fuerza a que TODOS los hijos de un batch hereden el mismo
`creds["model"]` a nivel de llamada. No hay forma de que el LLM padre pida,
por ejemplo, dos tareas triviales en `qwen3.5-9b` y una tarea pesada en
`cloud` dentro de un mismo `delegate_task`. Esto es exactamente lo que
`switch-model.sh` / el model-panel resuelven a nivel de sesión completa
(specs previos: model.default, litellm-config.yaml), pero nada existe a
nivel de tarea individual dentro de un batch delegado.

Riesgo adicional si se implementara ingenuamente: `llama-router` corre con
`--models-max 1` sobre una única GPU (LRU eviction) — lanzar dos presets
locales distintos en paralelo produce evict/reload thrashing en vez de dos
inferencias simultáneas. Cualquier solución de model routing por tarea
necesita, además, una regla de scheduling GPU-safe.

---

## 1. Alcance de este change

**Dentro de alcance:**
- Campo opcional `model` por tarea en `delegate_task(tasks=[{model: ...}])`.
  Ausente/`null`/vacío → resuelve exactamente al comportamiento actual
  (`creds["model"]` de nivel de llamada). Aplica igual a ambos paths de
  despacho (`delegate_task` síncrono y `dispatch_async_delegation_batch`).
- Validación fail-fast contra un allowlist fijo (`qwen3.5-9b`,
  `qwen3.6-27b-q3`, `qwen3.8-27b-iq2s`, `cloud`) antes de construir o
  lanzar cualquier hijo del batch — un valor inválido rechaza la llamada
  completa (cero hijos parciales).
- Scheduling por "waves" GPU-safe: children agrupados por modelo resuelto;
  `cloud` corre siempre concurrente e inmediato; distintos presets locales
  corren secuencialmente (un grupo termina antes de que empiece el
  siguiente); concurrencia intra-grupo sigue acotada por
  `delegation.max_concurrent_children` como hoy.
- Exposición dinámica del campo `model` en el schema de function-calling
  (`tasks.items.properties.model`, `enum` sincronizado con el allowlist
  configurado) + fix de un bug de shallow-copy preexistente que corrompía
  el schema estático al mutar `tasks["items"]` in-place entre llamadas a
  `get_definitions()`.
- Allowlist externalizable vía `delegation.allowed_models` en config.yaml,
  con fallback a 4 valores hardcodeados si la key está ausente.
- Entrega del lado del fork como un archivo `.patch` generado desde un
  worktree detached — nunca se edita ni commitea el checkout real del fork.

**Fuera de alcance:**
- El coordinador lock/queue/drain completo de `specs/009` para
  `local_large` — este slice usa solo agrupación + serialización, no un
  scheduler general.
- Cualquier clasificador automático de complejidad que elija el modelo por
  el caller — la selección de modelo sigue siendo explícita, provista por
  quien llama.
- Cambios a `model.default`, el switch Local/Cloud del model-panel,
  `switch-model.sh`, las 4 entradas existentes de `litellm-config.yaml`, o
  `router-config.yaml`.
- El gap preexistente del allowlist de `litellm_callbacks.py` (`qwen3`) y
  el drift de alias en `model.default` mencionados en la propuesta — no se
  tocan ni se requieren como parte de este change.

---

## 2. Decisiones resueltas (heredadas de propuesta/diseño)

| # | Decisión | Elegido | Rechazado | Motivo |
|---|---|---|---|---|
| 1 | Cómo entregar el cambio del fork | Patch `.patch` generado desde un worktree detached; el usuario aplica/revisa/commitea | `sdd-apply` edita/commitea el fork en vivo | El fork es un repo ajeno con su propio historial; cero-touch es la única opción segura |
| 2 | Estilo de validación | `_validate_task_models(...) -> Optional[str]`, envuelto en `tool_error()` | Excepción custom | El archivo ya retorna strings de error, nunca lanza, desde `delegate_task` |
| 3 | Sitio de validación | Justo después del loop de coerción de `output_schema`, antes del loop de build | Dentro del loop de build | Fail-fast: una tarea inválida en el índice N debe abortar antes de que la tarea 0 spawnee |
| 4 | Fuente del allowlist | `delegation.allowed_models` vía `_load_config()`, con fallback a 4 constantes hardcodeadas | Hard-fail si falta la key | Aditivo: cualquier instalación sin esa key mantiene comportamiento idéntico |
| 5 | Conjunto GPU-bound | `set(allowlist) - {"cloud"}` (constante de módulo `_GPU_EXEMPT_MODELS`) | Nueva config key `gpu_exempt_models` | Superficie mínima; riesgo aceptado: una segunda entrada cloud futura se serializaría innecesariamente (bug de performance, no de correctness) |
| 6 | Concurrencia | Extensión del `DaemonThreadPoolExecutor` existente con submission por waves | Nueva capa de lock/queue/semaphore | Reutiliza el poll loop interrupt-aware, las líneas de spinner y el ordenamiento de resultados ya existentes |

---

## 3. Diseño técnico (resumen — ver `openspec/changes/hermes-subagent-model-routing/design.md` para el detalle completo)

Cuatro piezas aditivas en `tools/delegate_tool.py` (fork):

1. **Resolución por tarea**: `_resolve_task_model(task, default_model)` —
   usa `task["model"]` si es un string no vacío, si no cae al
   `creds["model"]` de nivel de llamada.
2. **Validación fail-fast**: `_validate_task_models(task_list, allowed)` —
   recorre el batch, retorna el primer error (índice, valor, opciones
   válidas) o `None`. Se llama UNA vez, compartida por ambos paths de
   despacho, antes de cualquier construcción de hijo.
3. **Scheduling GPU-safe por waves**: `_partition_gpu_waves(children,
   task_models, gpu_exempt)` agrupa los hijos ya construidos por modelo
   resuelto → `(exempt_group, local_groups)`. El loop de ejecución somete
   `exempt_group` de inmediato y luego cada `local_group` en secuencia,
   drenando (`_drain_until`, extraído del `while pending:` original) antes
   de pasar al siguiente grupo. Un batch de un solo modelo produce
   naturalmente un único grupo — sin casos especiales.
4. **Exposición dinámica de schema**: `_build_dynamic_schema_overrides()`
   agrega `tasks.items.properties.model` con `enum` derivado de
   `_get_allowed_models()` en cada `get_definitions()`. Se corrigió un bug
   preexistente de shallow-copy (`{k: dict(v)}` solo copiaba el nivel
   superior de `properties`, dejando `tasks["items"]` compartido con el
   schema estático) reemplazándolo por `copy.deepcopy` del subárbol
   `items` antes de mutarlo.

Ambos paths de despacho (`delegate_task` síncrono y
`dispatch_async_delegation_batch`) comparten el mismo punto de
construcción de hijos y la misma función `_execute_and_aggregate` — por
construcción, no hay divergencia de comportamiento entre ellos.

---

## 4. Config schema

Bloque idéntico en `~/.hermes/config.yaml` (vivo, autoritativo) y
`kubernetes/hermes/config/config.yaml` (copia de referencia en
jarvis_project):

```yaml
delegation:
  # Per-task delegate_task(tasks=[{model: ...}]) allowlist.
  # KEEP IN SYNC with the other copy (~/.hermes/config.yaml <-> jarvis_project
  # kubernetes/hermes/config/config.yaml). Verify:
  #   diff <(yq '.delegation.allowed_models' ~/.hermes/config.yaml) \
  #        <(yq '.delegation.allowed_models' kubernetes/hermes/config/config.yaml)
  allowed_models:
    - qwen3.5-9b
    - qwen3.6-27b-q3
    - qwen3.8-27b-iq2s
    - cloud
```

Anti-drift: comentario recíproco en ambas copias + el comando `diff` de
verificación, ejecutado como paso explícito de `sdd-apply` (no hay
CI/hook automatizado en este repo que lo enforce).

**Nota de entorno**: `yq` no está instalado en el host donde se ejecutó
`sdd-apply`; la verificación real se hizo con un comparador Python
(`yaml.safe_load` + comparación de listas) y confirmó igualdad exacta.
El comando `diff <(yq ...)` documentado arriba sigue siendo válido en
cualquier host que sí tenga `yq`.

---

## 5. Testing (fork, `~/.hermes/hermes-agent-worktrees/subagent-model-routing`)

TDD estricto ejecutado íntegramente dentro de un worktree git detached del
fork (nunca contra el checkout real). Suite nueva:
`tests/tools/test_delegate_model_routing.py` (24 tests). Regresión
combinada con la suite preexistente `test_delegate_output_schema.py`: 48/48
verdes. Barrido ampliado de colateral (16 archivos de test relacionados a
`delegate_*`/`async_delegation*`, 237 tests): 237/237 verdes.

Runner usado: el venv `.venv` del propio fork (`/home/pedro/.hermes/hermes-agent/.venv`,
pytest 9.1.1 + pytest-asyncio 1.3.0 ya instalados), apuntado vía
`PYTHONPATH`/invocación directa del intérprete contra el árbol del
worktree — no se instaló nada nuevo, se reutilizó el entorno de desarrollo
ya existente.

Cobertura: resolución por tarea, allowlist desde config + fallback,
rechazo fail-fast (incluyendo cero-spawn verificado con un spy sobre
`_build_child_preserving_parent_tools`), scheduling GPU-safe (no-overlap
entre presets distintos, overlap permitido entre `cloud` y cualquier local,
cap de concurrencia intra-grupo preservado, batch de un solo modelo sin
demora artificial, orden/interrupt-safety), regresión de deepcopy del
schema estático, y exposición dinámica del campo `model` con su `enum`.

No verificable antes de que el usuario aplique el patch: comportamiento
real de carga/evict de `llama-router`, ruteo real de LiteLLM a los tres
presets, latencia de primer token, integración de reinicio del gateway.

---

## 6. Riesgos

- **Riesgo de tamaño de PR**: el patch del fork (~230-260 líneas) más el
  archivo de test nuevo (~380-440 líneas combinadas) no pasan por el flujo
  de PR de este repo — es un `.patch` que el usuario aplica en su propio
  fork. El lado de jarvis_project (2 configs + este spec + artefactos
  OpenSpec, ~180-200 líneas) sí queda sujeto al flujo normal de este repo.
- **Gate preexistente de tamaño de batch**: `delegate_task` ya rechaza
  cualquier llamada donde `len(tasks) > delegation.max_concurrent_children`
  (chequeo no relacionado a este change). Esto significa que el escenario
  literal "4 tareas, cap=2" del spec original de diseño es irreproducible
  vía `delegate_task` — un grupo GPU-wave nunca puede exceder el cap
  simplemente porque el batch completo tampoco puede. Se documentó como
  desviación en `tasks.md` (Fase 4.6) y se validó el cap de forma
  equivalente (spy sobre la construcción del `ThreadPoolExecutor`).
- **Presupuesto de caracteres del schema**: el test de regresión
  `test_top_level_description_compact_and_complete` (≤2200 chars) ya
  estaba al límite (2199) antes de este change. La documentación del campo
  `model` se movió a `_build_tasks_param_description()` (sin ceiling test)
  en vez de la descripción top-level.

---

## 7. Plan de rollback

- **Fork**: no aplicar el patch, o revertirlo después de aplicado
  (`git apply -R <patch>` antes de commitear, o `git revert` después).
  Aditivo y opcional: omitir `model` por tarea reproduce el comportamiento
  de hoy exactamente.
- **jarvis_project**: revertir los dos archivos de config
  (`~/.hermes/config.yaml`, `kubernetes/hermes/config/config.yaml`) y
  este spec en un único commit si se decide no continuar.
- **Worktree**: `git -C ~/.hermes/hermes-agent worktree remove
  ~/.hermes/hermes-agent-worktrees/subagent-model-routing` una vez que el
  usuario ya no lo necesite para inspección.

---

## 8. Checklist de este spec

- [x] Campo `model` opcional por tarea, con fallback a `creds["model"]`
- [x] Validación fail-fast (cero spawn parcial) en ambos paths de despacho
- [x] Scheduling GPU-safe por waves (serialización de presets distintos,
      exención de `cloud`, cap de concurrencia intra-grupo preservado)
- [x] Fix de deepcopy del schema estático + exposición dinámica de `model`
- [x] Allowlist sincronizado en ambas copias de config (verificado)
- [x] Patch `.patch` generado, fork real sin tocar (verificado
      `git status --porcelain` antes/después, idéntico)
- [x] Este spec

---

## 9. Referencias

- `openspec/changes/hermes-subagent-model-routing/proposal.md`
- `openspec/changes/hermes-subagent-model-routing/specs/subagent-model-routing/spec.md`
- `openspec/changes/hermes-subagent-model-routing/design.md`
- `openspec/changes/hermes-subagent-model-routing/tasks.md`
- `openspec/changes/hermes-subagent-model-routing/patches/0001-subagent-model-routing.patch`
- Fork: `~/.hermes/hermes-agent/tools/delegate_tool.py`
- Worktree de trabajo: `~/.hermes/hermes-agent-worktrees/subagent-model-routing`
