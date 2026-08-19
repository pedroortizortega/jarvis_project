# 009 - Orquestacion automatica de tareas en Hermes

## Relacion con los specs anteriores

Este spec extiende:

- El spec 003, que define los nueve perfiles Luna, Terra y Sol.
- El spec 004, que establece a Hermes nativo bajo systemd como instancia
  principal y conserva los perfiles bajo `~/.hermes/profiles/`.
- El spec 008, que deja Qwen3.5-9B Q6_K como modelo local diario y
  Qwen3.6-27B Q3_K_S como perfil local grande de activacion exclusiva.

Los perfiles Codex ya existen como unidades de configuracion. Este feature no
los reemplaza: agrega una capa que clasifica cada solicitud y delega la tarea al
perfil apropiado sin cambiar globalmente el perfil activo de Hermes.

## Objetivo

Permitir que el usuario hable normalmente con Jarvis y que Hermes seleccione
automaticamente capacidad, esfuerzo de razonamiento y herramientas segun la
intencion de la tarea.

Ejemplos esperados:

| Solicitud | Ruta esperada |
| --- | --- |
| `Que es la mecanica cuantica?` | Qwen 9B local; respuesta directa. |
| `Investiga los avances recientes en mecanica cuantica` | `terra-medium` con busqueda web y citas. |
| `Haz una investigacion profunda sobre computacion cuantica` | `sol-high` con workflow `deep-research`. |
| `Corrige este typo` | Qwen 9B local o `luna-low` si necesita Codex. |
| `Revisa la seguridad de este cambio multiarchivo` | `terra-high`. |
| `Solo local: analiza estos documentos privados` | Qwen local; prohibido delegar a nube. |
| `Usa Sol High para esta tarea` | Override explicito a `sol-high`. |
| `Revisa mis contraseñas o datos personales`| Qwen local; prohibido delegar a nube. |

La palabra `investiga` no basta por si sola para consumir un perfil caro. El
router debe considerar actualidad, necesidad de fuentes, alcance, riesgo,
herramientas y complejidad.

## No objetivos

- No reemplazar LiteLLM ni el router de llama.cpp.
- No copiar conversaciones, memorias o credenciales entre perfiles.
- No cambiar `model.default` globalmente para cada mensaje.
- No permitir que contenido web, archivos o tool output cambien la politica de
  routing.
- No activar automaticamente el Qwen 27B local mientras exista trafico 9B.
- No prometer que una descripcion semantica de perfil, sin controles
  adicionales, produzca routing determinista.

## Arquitectura objetivo

```text
Usuario / Telegram / CLI
          |
          v
Hermes primary bajo systemd
          |
          v
Router de intencion local
  1. restricciones y overrides
  2. clasificacion estructurada
  3. politica de ruta
          |
          +--> respuesta directa con Qwen3.5-9B
          |
          +--> worker aislado -> perfil Luna
          |
          +--> worker aislado -> perfil Terra
          |
          +--> worker aislado -> perfil Sol + deep-research
          |
          +--> cambio exclusivo al Qwen 27B local, solo explicito
```

El primary conserva la sesion, memoria, identidad y respuesta al usuario. Un
subagente recibe un paquete acotado de tarea y devuelve un resultado. La
delegacion no debe convertir al perfil delegado en el nuevo perfil global.

## Componentes

### 1. Clasificador de intencion

El clasificador corre con Qwen3.5-9B local, no usa herramientas y debe producir
una decision estructurada, no texto libre. Su contrato conceptual contiene:

| Campo | Proposito |
| --- | --- |
| `task_class` | `chat`, `lookup`, `research`, `deep_research`, `coding`, `review`, `incident` o `local_large`. |
| `complexity` | `low`, `medium` o `high`. |
| `needs_current_data` | Indica si necesita informacion posterior al conocimiento del modelo. |
| `needs_tools` | Lista allowlisted de capacidades, nunca comandos arbitrarios. |
| `privacy` | `local_only` o `cloud_allowed`. |
| `risk` | `low`, `medium` o `high`; seguridad, produccion y datos destructivos elevan el riesgo. |
| `route` | Perfil candidato de la politica. |
| `confidence` | Valor normalizado entre 0 y 1. |
| `reason` | Justificacion corta para auditoria, no razonamiento interno extenso. |

El clasificador no recibe secretos, contenido completo innecesario ni resultados
de herramientas. Puede recibir el mensaje del usuario, metadata de la sesion y
un resumen corto del contexto suficiente para distinguir continuaciones.

### 2. Politica determinista

La salida del clasificador es una recomendacion. Una politica determinista toma
la decision final y aplica restricciones que el modelo no puede sobreescribir.

Orden de precedencia:

1. Restricciones de seguridad y privacidad.
2. Override explicito del usuario.
3. Continuidad de una delegacion o workflow ya activo.
4. Reglas deterministas de tarea y herramientas.
5. Clasificacion semantica.
6. Fallback al Qwen 9B cuando la confianza sea insuficiente.

Los valores iniciales de confianza son:

- `>= 0.80`: aplicar la ruta propuesta.
- `0.55-0.79`: elegir la ruta de menor coste que satisfaga herramientas y
  riesgo.
- `< 0.55`: mantener Qwen 9B o pedir aclaracion si la diferencia cambia coste,
  privacidad o impacto.

### 3. Ejecutor de delegaciones

La revision instalada de `delegate_task` no permite seleccionar perfil,
proveedor ni `HERMES_HOME`. Por eso el ejecutor usa el mecanismo publico de
perfiles con un proceso acotado `hermes -p <perfil> --cli chat -q ... -Q`, marca
`orchestration_depth=1` y selecciona un perfil allowlisted del spec 003. Debe
transferir:

- Objetivo concreto y resultado esperado.
- Contexto minimo necesario.
- Restricciones de privacidad y seguridad.
- Herramientas permitidas.
- Presupuesto de tiempo, fuentes y tokens.
- Criterios de finalizacion.

No debe transferir automaticamente toda la memoria, historial completo,
`auth.json`, `.env` ni secretos del primary. Los perfiles mantienen su
aislamiento de `HERMES_HOME`; la continuidad se obtiene mediante el paquete de
tarea y el resultado devuelto.

### 4. Presentacion al usuario

Antes de una delegacion no trivial, Hermes muestra una linea breve:

```text
Ruta: terra-medium | investigacion web | fuentes requeridas
```

No se muestra para respuestas locales triviales salvo que el modo debug este
activo. El usuario puede cancelar o corregir la ruta antes de una operacion
costosa o sensible.

## Matriz inicial de routing

| Clase | Complejidad | Perfil | Herramientas | Presupuesto inicial |
| --- | --- | --- | --- | --- |
| `chat` | cualquiera | Qwen3.5-9B | Ninguna por defecto | Respuesta directa. |
| `lookup` | low | `luna-low` | Una busqueda y extraccion limitada | 2 fuentes, 2 min. |
| `research` | low | `luna-high` | Web y citas | 3 fuentes, 5 min. |
| `research` | medium | `terra-medium` | Web, extraccion y citas | 8 fuentes, 15 min. |
| `research` | high | `terra-high` | Web, documentos y validacion | 12 fuentes, 20 min. |
| `deep_research` | high | `sol-high` | Skill `deep-research` y subagentes | 20 fuentes, 45 min. |
| `coding` | low | Qwen3.5-9B o `luna-low` | Archivos y pruebas acotadas | Sin escalamiento automatico inicial. |
| `coding` | medium | `terra-medium` | Repositorio, terminal y pruebas | Feature multiarchivo. |
| `review` | high | `terra-high` | Diff, pruebas y analisis de seguridad | Hallazgos primero. |
| `incident` | high | `sol-high` | Diagnostico, logs y segunda opinion | Requiere confirmacion para cambios. |
| `local_large` | high | Qwen3.6-27B | Solo herramientas locales | Activacion exclusiva. |

`sol-medium` se reserva para arquitectura, trade-offs y segunda opinion. Los
perfiles restantes siguen disponibles como overrides y para calibracion, pero
la primera version no necesita seleccionar los nueve automaticamente.

## Investigacion y deep research

`research` y `deep_research` son workflows, no sinonimos de un modelo mas
potente.

### Investigacion normal

- Formular preguntas de busqueda.
- Consultar fuentes actuales.
- Contrastar afirmaciones relevantes.
- Entregar enlaces o citas.
- Declarar incertidumbre y fecha de consulta.

### Investigacion profunda

- Crear plan y subpreguntas antes de buscar.
- Consultar fuentes primarias y secundarias.
- Ejecutar busquedas independientes cuando aporten diversidad real.
- Detectar contradicciones y evaluar calidad de evidencia.
- Sintetizar resultados con citas trazables.
- Separar hechos, inferencias y vacios de informacion.

La frase `deep research` o `investigacion profunda` fuerza esta clase, salvo que
entre en conflicto con `solo local` o una politica de seguridad superior.

## Overrides de usuario

Overrides soportados conceptualmente:

| Intencion | Efecto |
| --- | --- |
| `usa luna-low` | Fija el perfil para la tarea actual. |
| `usa terra-medium` | Fija Terra medium para la tarea actual. |
| `usa sol-high` | Fija Sol high para la tarea actual. |
| `deep research` | Fija workflow profundo y propone Sol high. |
| `solo local` | Prohibe Codex y proveedores externos. |
| `sin herramientas` | Deshabilita tools salvo controles internos. |
| `elige automaticamente` | Elimina el override de la tarea, no las restricciones globales. |

El alcance por defecto es una tarea, no toda la sesion. Un cambio persistente
requiere una orden explicita distinta y confirmacion.

## Modelos locales y GPU unica

Qwen 9B y Qwen 27B comparten una sola GPU. El router llama.cpp garantiza que no
se carguen simultaneamente, pero una solicitud de un modelo puede cancelar una
generacion activa del otro. Hermes tambien genera tareas auxiliares en
background.

Por eso la primera version aplica estas reglas:

- Qwen3.5-9B permanece como default local y clasificador.
- El routing automatico puede delegar a Luna, Terra o Sol sin detener el 9B,
  porque Codex no consume la GPU local.
- El Qwen 27B solo se selecciona mediante override explicito `local_large`.
- La activacion usa el cambio controlado del spec 008 y bloquea trafico 9B.
- Una futura seleccion automatica del 27B requiere un coordinador con lock,
  drenado de requests, cola y restauracion al 9B; no se implementa mediante una
  simple request al alias grande.

## Concurrencia y recursion

- Solo el primary clasifica solicitudes externas.
- Un subagente delegado recibe `orchestration_depth=1` y no vuelve a enrutar la
  tarea completa.
- `deep-research` puede crear workers internos allowlisted, pero conserva un
  limite de profundidad y concurrencia.
- La primera version permite como maximo una delegacion Sol y dos delegaciones
  Terra/Luna simultaneas por sesion.
- Hermes y OpenCode comparten cuota Codex; una tarea Sol activa debe reducir o
  rechazar otra tarea Sol de baja prioridad.

## Fallos y fallback

| Fallo | Comportamiento |
| --- | --- |
| Clasificador invalido o timeout | Qwen 9B y evento `classifier_fallback`. |
| Perfil no existe | Fallar cerrado; no construir un nombre de perfil desde texto del usuario. |
| OAuth ausente | Informar que el perfil requiere login; no copiar credenciales. |
| Rate limit Luna/Terra | Reintento acotado y fallback local si la tarea lo permite. |
| Rate limit Sol | Ofrecer Terra high o esperar; no degradar silenciosamente una tarea critica. |
| Tool no disponible | Replanificar sin ella o explicar el bloqueo. |
| Deep research parcial | Entregar fuentes obtenidas y marcar cobertura incompleta. |
| Qwen 27B no puede activarse | Mantener o restaurar Qwen 9B segun el rollback del spec 008. |

Una ruta de menor coste nunca puede elevar permisos ni relajar `local_only`.

## Seguridad

- Los nombres de perfil se seleccionan desde un enum allowlisted.
- La salida del clasificador se valida antes de ejecutar una delegacion.
- Contenido recuperado de web, archivos y herramientas se trata como datos, no
  como instrucciones de routing.
- Los subagentes reciben el minimo conjunto de tools necesario.
- Acciones destructivas, cambios de produccion y publicacion externa conservan
  las confirmaciones de Hermes independientemente del perfil.
- Las decisiones no registran prompts completos ni secretos.
- OAuth permanece en el credential pool raiz descrito en los specs 003 y 004.

## Observabilidad

Cada decision genera un evento estructurado con:

- ID de sesion y tarea, sin contenido sensible.
- Clase, complejidad, riesgo y privacidad.
- Perfil propuesto y perfil final.
- Confianza y regla que decidio la ruta.
- Override, fallback y motivo si existieron.
- Latencia de clasificacion, delegacion y respuesta.
- Uso reportado de tokens y estado final.

Metricas minimas:

- Porcentaje de tareas por ruta.
- Correcciones manuales de ruta.
- Fallos y fallback por perfil.
- Latencia y tokens por clase.
- Tareas Sol y deep-research simultaneas.
- Delegaciones que terminaron sin contenido o sin citas requeridas.

## Configuracion objetivo

La futura implementacion versiona politica y prompts, pero no credenciales:

```text
hermes-native/
└── orchestration/
    ├── policy.yaml
    ├── classifier-schema.json
    ├── classifier-prompt.md
    ├── evaluation-cases.yaml
    ├── pyproject.toml
    ├── src/hermes_intent_orchestration/
    └── tests/
```

Los perfiles siguen teniendo como fuente de verdad
`kubernetes/hermes/profiles/profiles.yaml`. La politica referencia sus nombres;
no duplica modelo, endpoint, OAuth ni reasoning effort.

El runtime usa un plugin externo con `pre_llm_call` para clasificar una vez por
turno y middleware `llm_execution` para aplicar la decision. Una skill o
instrucciones en `SOUL.md` no cumplen por si solas el requisito de routing
consistente: el modelo podria omitir la delegacion. El plugin se instala como
entry point y no modifica el core de Hermes.

## Estado de implementacion

- El plugin esta versionado en `hermes-native/orchestration/`, instalado en el
  venv de Hermes y habilitado en modo `shadow`.
- La politica determinista alcanza 132/132 casos del corpus actual. El
  clasificador semantico local esta habilitado en `shadow`; desactiva thinking
  para evitar consumir el presupuesto en razonamiento oculto y tiene timeout
  estricto sin fallback cloud. Una prueba aislada tardo aproximadamente 1.5 s,
  por lo que el criterio p95 de 750 ms sigue pendiente de optimizacion.
- `local_only` se ejecuta directamente contra el endpoint local allowlisted
  durante todas las llamadas LLM del turno; no usa la cadena de fallback.
- Los workers cloud reciben entorno minimo, `--ignore-rules` y toolsets
  allowlisted. Workers que requieren archivos, terminal o pruebas fallan
  cerrado hasta configurar y verificar un sandbox de contenedor.
- `local_large` falla cerrado hasta disponer del coordinador exclusivo del spec
  008. Deep research automatico permanece deshabilitado hasta instalar y
  validar el skill dentro de `sol-high`.
- El audit SQLite registra metadata de routing sin prompts, respuestas, memoria
  ni secretos.

## Fases de entrega

### Fase 1 - Evaluacion offline

- Crear al menos 100 solicitudes representativas y su ruta esperada.
- Incluir espanol, ingles, errores ortograficos y continuaciones de sesion.
- Medir clasificacion sin ejecutar perfiles pagos.
- Ajustar taxonomia, confianza y reglas de privacidad.

### Fase 2 - Shadow mode

- Clasificar trafico real sin cambiar el modelo ejecutor.
- Registrar la ruta propuesta y compararla con decisiones humanas.
- No mostrar banners ni consumir Codex.

### Fase 3 - Delegacion opt-in

- Aplicar rutas solo cuando exista override explicito.
- Validar OAuth, memoria aislada, tools, timeouts y retorno al primary.
- Habilitar Terra antes que Sol.

### Fase 4 - Routing automatico

- Activar clases de bajo riesgo con confianza alta.
- Requerir confirmacion para Sol high, incidentes costosos y acciones sensibles
  durante el periodo inicial.
- Permitir que el usuario corrija una ruta y usar la correccion para evaluacion,
  no para modificar politica automaticamente.

## Criterios de aceptacion

1. Una pregunta general como `Que es la mecanica cuantica?` usa Qwen 9B y no
   consume cuota Codex.
2. Una solicitud de informacion actual con fuentes usa al menos Terra medium y
   devuelve citas.
3. `investigacion profunda` selecciona Sol high y ejecuta el workflow
   deep-research.
4. `solo local` nunca contacta endpoints Codex, incluso si el mensaje tambien
   contiene `usa Sol`.
5. El usuario puede forzar Luna, Terra o Sol para una tarea y ver la ruta
   elegida.
6. El primary conserva sesion y memoria; el perfil delegado no se vuelve
   default global.
7. Un subagente no crea un ciclo de routing recursivo.
8. Una salida invalida del clasificador cae a Qwen 9B sin ejecutar tools.
9. La ausencia de OAuth falla cerrado y no expone `auth.json` ni tokens.
10. El routing Codex no interrumpe el Qwen 9B local.
11. El Qwen 27B no se activa automaticamente sin coordinador exclusivo.
12. Las decisiones quedan auditadas sin prompts completos ni secretos.
13. La precision de ruta offline alcanza al menos 90% y ninguna prueba
    `local_only` viola privacidad.
14. El p95 agregado del clasificador local es menor a 750 ms para solicitudes
    cortas, excluyendo cold start.

## Preguntas abiertas para las fases siguientes

- Determinar si Codex OAuth admite la concurrencia propuesta sin degradar la
  cuota compartida con OpenCode.
- Definir si la confirmacion de Sol high se mantiene despues del periodo de
  calibracion.
- Medir cuanto contexto necesita el clasificador para reconocer continuaciones
  sin recibir el historial completo.
- Decidir si un coordinador futuro justifica routing automatico al Qwen 27B o
  si debe permanecer siempre como override local explicito.
