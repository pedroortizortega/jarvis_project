---
name: spec-designer
description: Use before designing or planning any project or feature.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [openspec, specification, requirements, architecture, planning]
    related_skills: [plan, test-driven-development]
---

# Spec Designer — OpenSpec-aligned

## Overview

Diseña proyectos, cambios y funcionalidades antes de escribir código. Convierte una intención en artefactos revisables y trazables siguiendo el modelo de OpenSpec: propuesta, especificaciones delta, diseño técnico y tareas de implementación.

Esta skill es deliberadamente una fase de **diseño y planificación**. No implementa código ni marca tareas como realizadas. Su resultado debe permitir que otra persona o agente implemente el cambio sin adivinar requisitos, límites o criterios de aceptación.

Fuente metodológica: [OpenSpec](https://openspec.dev/) y su documentación oficial. Si la documentación actual contradice esta skill, prevalece OpenSpec y la skill debe actualizarse.

## When to Use

Usar para:

- cualquier proyecto nuevo antes de iniciar la implementación;
- cualquier feature, cambio de comportamiento, migración o integración;
- aclarar una idea imprecisa antes de estimarla;
- definir alcance, requisitos, arquitectura, criterios de aceptación y plan;
- revisar o corregir artefactos existentes en `openspec/`;
- preparar un cambio para posterior ejecución mediante OpenSpec.

No usar para:

- una corrección trivial cuyo comportamiento esperado ya está inequívocamente especificado;
- ejecutar tareas ya aprobadas;
- depurar una incidencia sin relación con requisitos o diseño;
- sustituir la documentación oficial de OpenSpec o inventar comandos no disponibles.

## Principios obligatorios

1. **Especificar antes de implementar.** No escribir ni modificar código de producto durante esta fase.
2. **Comportamiento antes que implementación.** Los requisitos explican qué debe observarse; las decisiones sobre cómo construirlo pertenecen a `design.md`.
3. **Una intención por cambio.** Si el alcance contiene varias intenciones independientes, dividirlo en cambios separados.
4. **Trazabilidad completa.** Cada requisito debe enlazar con al menos un escenario y con una o más tareas.
5. **Supuestos visibles.** Etiquetar hechos confirmados, supuestos, decisiones y preguntas abiertas.
6. **Contexto real.** Inspeccionar el repositorio, sus especificaciones vigentes y restricciones antes de proponer arquitectura. No inventar rutas, APIs, dependencias ni capacidades.
7. **Ceremonia proporcional.** Un cambio pequeño puede requerir un diseño breve; riesgos elevados, migraciones o decisiones transversales requieren mayor detalle.
8. **Revisión humana.** Presentar los artefactos para aprobación antes de pasar a implementación.

## Flujo de trabajo

### 1. Explorar el contexto

- Resumir el objetivo en una frase.
- Identificar usuarios, problema, valor esperado y comportamiento actual.
- Inspeccionar el repositorio y `openspec/specs/` cuando existan.
- Registrar restricciones funcionales, técnicas, operativas, seguridad, privacidad, accesibilidad, rendimiento y compatibilidad que sean relevantes.
- Separar datos confirmados de inferencias y supuestos.
- Si falta un dato indispensable que cambia materialmente el diseño, formular **una sola pregunta precisa**.

**Criterio de finalización:** existe una intención única, límites preliminares y suficiente contexto verificable para redactar una propuesta.

### 2. Delimitar el cambio

Definir:

- `change-id` breve en kebab-case;
- objetivo y motivación;
- alcance incluido y fuera de alcance;
- usuarios y partes afectadas;
- dependencias y restricciones;
- riesgos principales;
- métricas o señales de éxito cuando sean pertinentes.

Dividir el trabajo si no puede describirse con una sola intención sin recurrir repetidamente a “y también”.

**Criterio de finalización:** un revisor puede explicar qué cambia y qué no cambia sin leer el diseño técnico.

### 3. Elaborar `proposal.md`

La propuesta cubre el **porqué** y el **qué**:

```markdown
# Proposal: <título>

## Intent
<problema y resultado deseado>

## Context
<estado actual y evidencia>

## Scope
- <incluido>

## Out of Scope
- <excluido>

## Affected Capabilities
- <dominio o capacidad>

## Success Criteria
- <resultado observable>

## Risks and Dependencies
- <riesgo, dependencia o restricción>

## Assumptions
- <supuesto explícito>

## Open Questions
- <solo preguntas todavía bloqueantes>
```

No esconder decisiones técnicas irreversibles dentro del alcance. Si ya son restricciones confirmadas, declararlas como tales; si son opciones, evaluarlas en el diseño.

**Criterio de finalización:** la propuesta justifica el cambio, fija sus fronteras y no depende de detalles de implementación para ser comprendida.

### 4. Elaborar especificaciones delta

Crear una especificación por capacidad afectada bajo:

`openspec/changes/<change-id>/specs/<capability>/spec.md`

Usar exclusivamente las secciones pertinentes:

- `## ADDED Requirements`: comportamiento nuevo;
- `## MODIFIED Requirements`: comportamiento existente que cambia; incluir la versión nueva completa;
- `## REMOVED Requirements`: comportamiento eliminado e indicar el motivo.

Para una capacidad nueva, añadir `## Purpose` con una o dos frases. Antes de elegir ADDED o MODIFIED, comprobar si el requisito ya existe en `openspec/specs/<capability>/spec.md`.

Formato normativo:

```markdown
## ADDED Requirements

### Requirement: <nombre observable>
The system SHALL <un comportamiento verificable>.

#### Scenario: <caso concreto>
- GIVEN <precondición>
- WHEN <evento o acción>
- THEN <resultado observable>
- AND <resultado adicional, si corresponde>
```

Reglas de calidad:

- cada requisito expresa un solo comportamiento;
- usar `MUST` o `SHALL` para obligaciones, `SHOULD` solo si se admite una excepción justificada y `MAY` para comportamiento opcional;
- evitar términos vagos como “rápido”, “robusto”, “adecuado” o “fácil” sin una medida observable;
- no incluir bibliotecas, clases, tablas o algoritmos en requisitos de comportamiento;
- cada requisito tiene al menos un escenario que lo ejercita de verdad;
- cubrir los casos críticos, errores, límites, permisos, concurrencia, reintentos y recuperación cuando sean relevantes;
- nombrar cada escenario por el comportamiento concreto que verifica.

**Criterio de finalización:** un tester sin conocimiento del código puede determinar si cada requisito pasa o falla.

### 5. Elaborar `design.md`

El diseño cubre el **cómo** y debe incluir únicamente el detalle proporcional al riesgo:

```markdown
# Design: <título>

## Context and Constraints
## Goals and Non-Goals
## Proposed Architecture
## Components and Responsibilities
## Data and Control Flows
## Interfaces and Contracts
## Key Decisions
## Alternatives Considered
## Security, Privacy and Safety
## Reliability and Failure Modes
## Performance and Capacity
## Compatibility and Migration
## Observability and Operations
## Testing Strategy
## Rollback Strategy
## Unresolved Decisions
```

Para cada decisión importante registrar decisión, motivo, alternativas evaluadas, consecuencias, trade-offs y el supuesto o evidencia de apoyo.

Incluir diagramas solo si reducen ambigüedad. Evitar una arquitectura grandiosa para un problema modesto; las catedrales técnicas son encantadoras hasta que alguien debe mantenerlas.

**Criterio de finalización:** el diseño explica cómo satisfacer todos los requisitos, trata los riesgos relevantes y conserva una ruta de reversión razonable.

### 6. Elaborar `tasks.md`

Convertir el diseño en una secuencia verificable:

```markdown
# Tasks

## 1. <fase o componente>
- [ ] 1.1 <acción concreta> — Requisitos: REQ-001 — Verificación: <prueba o evidencia>
- [ ] 1.2 <acción concreta> — Requisitos: REQ-002 — Verificación: <prueba o evidencia>
```

Cada tarea debe:

- producir un resultado concreto;
- ser suficientemente pequeña para verificarla de manera independiente;
- indicar dependencias cuando no sean obvias;
- enlazar requisitos o escenarios aplicables;
- incluir prueba, inspección o evidencia de finalización;
- reservar tareas explícitas para migración, documentación, observabilidad, despliegue y rollback cuando correspondan.

No marcar casillas: esta skill planifica, no implementa.

**Criterio de finalización:** todos los requisitos están cubiertos por tareas y ninguna tarea introduce comportamiento fuera del alcance aprobado.

### 7. Revisar y validar

Realizar una revisión cruzada:

- propuesta ↔ alcance;
- requisitos ↔ escenarios;
- requisitos ↔ diseño;
- diseño ↔ tareas;
- tareas ↔ verificación;
- riesgos ↔ mitigaciones y rollback.

Si OpenSpec CLI está instalado y el proyecto está inicializado, ejecutar:

```bash
openspec validate <change-id>
openspec show <change-id>
```

No instalar OpenSpec, inicializar el repositorio ni sobrescribir artefactos existentes sin autorización cuando esas acciones produzcan cambios externos o potencialmente conflictivos. Si la CLI no está disponible, efectuar una validación estructural manual y declararlo.

**Criterio de finalización:** no existen requisitos huérfanos, escenarios imposibles de probar, tareas sin propósito ni contradicciones conocidas entre artefactos.

### 8. Entregar para aprobación

Presentar:

1. intención y alcance resumidos;
2. artefactos creados o propuestos y sus rutas reales;
3. decisiones principales y alternativas descartadas;
4. supuestos y preguntas abiertas;
5. riesgos, estrategia de validación y rollback;
6. estado de validación OpenSpec;
7. declaración explícita: **no se escribió código de producto**.

Detenerse aquí. La implementación requiere una instrucción posterior y explícita.

## Ubicación de artefactos

Cuando el proyecto ya usa OpenSpec, respetar esta estructura:

```text
openspec/
├── specs/
│   └── <capability>/spec.md
├── changes/
│   └── <change-id>/
│       ├── proposal.md
│       ├── design.md
│       ├── tasks.md
│       └── specs/
│           └── <capability>/spec.md
└── config.yaml
```

`openspec/specs/` describe el comportamiento vigente. `openspec/changes/` contiene cambios propuestos. Al archivar, OpenSpec integra los deltas en las especificaciones vigentes y conserva el cambio como historial de auditoría.

Si el proyecto no usa OpenSpec, explicar la estructura propuesta y pedir autorización antes de inicializarlo. Se puede entregar un diseño en texto durante la conversación, pero debe conservar la misma separación conceptual entre propuesta, requisitos, diseño y tareas.

## Resultado mínimo obligatorio

Toda ejecución debe producir o presentar:

- objetivo e intención;
- alcance y fuera de alcance;
- requisitos observables;
- escenarios de aceptación;
- diseño técnico proporcional;
- alternativas y trade-offs relevantes;
- riesgos y mitigaciones;
- plan de implementación trazable;
- estrategia de pruebas y verificación;
- rollback cuando el cambio pueda afectar datos, disponibilidad o compatibilidad;
- supuestos y preguntas abiertas;
- estado de validación.

## Common Pitfalls

1. **Confundir requisitos con diseño.** Mover detalles de implementación a `design.md`.
2. **Crear un cambio gigantesco.** Dividir por intención y capacidad entregable.
3. **Usar ADDED para modificar conducta existente.** Consultar primero la especificación vigente.
4. **Modificar parcialmente un requisito.** En MODIFIED incluir su nueva versión completa.
5. **Escribir escenarios que parafrasean el requisito.** Usar condiciones y resultados concretos.
6. **Cubrir solo el happy path.** Añadir errores y bordes que serían costosos si fallaran.
7. **Inventar el repositorio.** Inspeccionar archivos y declarar lo que no pudo verificarse.
8. **Planificar sin reversión.** Incluir rollback para migraciones y cambios operativos.
9. **Empezar a programar.** Detenerse tras entregar los artefactos para aprobación.
10. **Declarar validación sin ejecutarla.** Distinguir validación con CLI de revisión manual.

## Verification Checklist

- [ ] La intención del cambio cabe en una frase.
- [ ] Alcance y fuera de alcance están explícitos.
- [ ] Se inspeccionaron las especificaciones vigentes aplicables.
- [ ] Cada requisito contiene un único comportamiento observable.
- [ ] Cada requisito usa correctamente MUST/SHALL/SHOULD/MAY.
- [ ] Cada requisito tiene al menos un escenario GIVEN/WHEN/THEN.
- [ ] Casos críticos, errores y límites relevantes están cubiertos.
- [ ] ADDED/MODIFIED/REMOVED coincide con el estado actual.
- [ ] El diseño satisface todos los requisitos.
- [ ] Alternativas y trade-offs importantes están documentados.
- [ ] Seguridad, datos, operaciones y compatibilidad se trataron cuando aplican.
- [ ] Cada requisito es trazable a tareas y verificaciones.
- [ ] Existe rollback cuando el riesgo lo requiere.
- [ ] OpenSpec CLI se ejecutó o la limitación quedó declarada.
- [ ] No se escribió código de producto durante el diseño.
- [ ] Los artefactos están listos para revisión y aprobación.
