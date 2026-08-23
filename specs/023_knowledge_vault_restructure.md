# JARVIS Spec 023 - Software Design Document (SDD)
## Knowledge Vault Restructure: una rama, dos carpetas (`pending/` → `knowledge/`)

**Estado:** Especificado (`sdd-spec`), pendiente de diseño/tareas/implementación.
**Fecha:** 2026-08-23
**Versión:** 1.0
**Autor:** Pedro Ortiz (vía agente `sdd-spec`)

---

## 0. Por qué existe este spec

Hoy el contenido no aprobado nunca entra al historial git del vault: las
notas cruzan cuatro directorios JSON de staging locales
(`proposals/` → `pending/` → `decisions/` → `approved/`) antes de que
`publisher.py` escriba el output canónico único en un
`/opt/knowledge-vault/vault/` plano, y existe además una rama `pending`
separada del repo bare solo para que un teléfono pueda revisar por SSH. El
resultado es un pipeline que nadie puede explicar en una frase, un
stand-in temporal (`approve_locally.py`) ya documentado como borrable, y
sin historial compartido entre lo propuesto y lo aprobado.

Estado destino: **una rama, dos carpetas**. JARVIS solo puede crear/modificar
notas en `pending/`. Una promoción humana mueve una nota a `knowledge/`.
`knowledge-vault-search` indexa únicamente `knowledge/` — nunca `pending/`,
nunca ninguna otra carpeta que se agregue después.

---

## 1. Alcance de este change

**Dentro de alcance:**
- Árbol del vault de una sola rama con exactamente dos carpetas consumidas:
  `pending/` y `knowledge/`.
- Frontera imperativa del agente: `propose-note` SKILL.md declara que JARVIS
  escribe únicamente en `pending/`, usando el template de nota existente.
- Ruta de promoción `pending/<id>.md` → `knowledge/<id>.md` preservando el id
  de la nota (los links no deben romperse).
- Alcance de búsqueda/índice restringido a `knowledge/` **por construcción**
  (allowlist de raíz, no una denylist).
- Retiro/reforma de los directorios de staging y unidades que el nuevo
  modelo vuelve redundantes.
- Migración de las notas publicadas existentes hacia `knowledge/`.

**Fuera de alcance:**
- Cambiar el formato de nota (frontmatter OKF, requisito `type`, esquema de
  id Zettelkasten).
- Reemplazar el ranking de búsqueda, los embeddings, o el contrato del
  adaptador de memory-router.
- Cualquier API real de control-plane para aprobación (sigue sin existir;
  este change remueve su stand-in, no lo construye).
- Trabajo de plugin/UI de Obsidian.

---

## 2. Decisiones resueltas (D-01 a D-06)

Las 6 decisiones de `proposal.md` quedan asentadas como hechos resueltos de
este spec — no quedan abiertas. D-01 y D-02 fueron confirmadas directamente
por el usuario (no por default de timeout); D-03 a D-06 son los defaults
propuestos, no reabiertos en la ronda de preguntas.

| ID | Pregunta | Decisión |
|---|---|---|
| D-01 | ¿Sobrevive la revisión por teléfono? | **Retirada.** `review-sync` y la rama `pending` separada se eliminan. La revisión offline por teléfono no tiene reemplazo en este change; si se quiere después, es un follow-up explícito y separado, no algo preservado en silencio. |
| D-02 | ¿Se sigue registrando el rationale? | **Sí — mandatorio, nunca se descarta en silencio.** `pending/<id>.md` conserva el frontmatter `reviewer`/`decision`/`rationale`; la promoción se niega a correr sin los tres campos, los quita antes de publicar, y los registra en el mensaje de commit de la promoción. El historial de git es el audit trail. |
| D-03 | ¿Cómo ocurre la promoción mecánicamente? | Un comando `knowledge-vault-promote` que corre el humano (`git mv` + strip + commit + push). Un `git mv` manual sigue funcionando pero es explícitamente una vía de escape no auditada. |
| D-04 | ¿Sobrevive el invariante "un solo escritor"? | **Reescopeado, no eliminado.** Nuevo invariante: JARVIS escribe solo `pending/`; solo el actor de promoción escribe `knowledge/`. Se aplica dos veces, como hoy: ownership/modo de archivo más `ReadWritePaths=`/`InaccessiblePaths=` por unidad systemd. |
| D-05 | ¿Sigue existiendo `knowledge-vault-mirror`? | **Colapsa.** El árbol del vault es el repo git; el repo bare sigue como su remoto. Una sola unidad de sync hace commit+push del árbol del vault; el paso de copia a un scratch-worktree se elimina. |
| D-06 | ¿Qué pasa con `proposals/`/`decisions/`/`approved/` + `approve_locally.py`? | **Se eliminan**, según D-02/D-05. `propose` escribe `pending/<id>.md` directamente. Sus propios docstrings ya los llaman stand-ins temporales. |

---

## 3. Estructura y frontera de escritura del agente

El vault pasa a ser un único repositorio git con una sola rama y exactamente
dos carpetas de nivel superior consumidas: `pending/` y `knowledge/`.
Cualquier otra carpeta que aparezca en el árbol (por ejemplo un futuro
`drafts/`) **no** es una etapa del lifecycle y no debe ser tratada como tal
por ningún componente.

`propose.py`, invocado según `propose-note` SKILL.md, es el único camino de
escritura de JARVIS y escribe exclusivamente `pending/<id>.md`. No existe
ningún camino de código, herramienta o skill accesible por JARVIS capaz de
escribir en `knowledge/`. Esa garantía se aplica dos veces —
ownership/modo de archivo a nivel filesystem, y `ReadWritePaths=`/
`InaccessiblePaths=` en la unidad systemd que corre el proceso de JARVIS —
consistente con D-04.

---

## 4. Contrato de promoción

La promoción es el único camino por el que una nota pasa de `pending/` a
`knowledge/`, y es un acto humano, auditado, nunca disparable por JARVIS.

- **Reviewer y rationale son mandatorios.** El comando `knowledge-vault-promote`
  se niega a correr si `pending/<id>.md` no tiene los tres campos
  `reviewer`/`decision`/`rationale` en su frontmatter.
- **Los campos de revisión se quitan antes de publicar.** `knowledge/<id>.md`
  nunca contiene `REVIEW_FIELDS` (`review.py:69`) — quedarían filtrados a la
  nota publicada si no se quitan explícitamente.
- **El audit trail se traslada al historial de git.** El mensaje del commit
  de promoción registra `reviewer` y `rationale`, así la propiedad de
  auditoría no se pierde al quitar los campos de la nota.
- **El id se preserva byte a byte.** La promoción es `git mv`, nunca un
  re-mint de id — los links intra-vault que referencian el id siguen
  resolviendo después de promover.
- **Escape hatch no auditado, pero no silencioso.** Un `git mv` manual de
  `pending/` a `knowledge/` sigue funcionando (D-03), pero un chequeo de
  validación separado corre sobre `knowledge/` y reporta cualquier nota
  publicada que aún cargue `REVIEW_FIELDS` — un `git mv` a mano nunca publica
  campos de revisión en silencio.
- **JARVIS no puede disparar promoción.** Ningún skill/tool/comando expuesto
  a JARVIS ejecuta la promoción; solo un humano corriendo el comando
  directamente puede mover una nota a `knowledge/`.

---

## 5. Alcance de búsqueda: allowlist de `knowledge/`, no denylist

`knowledge-vault-search` pasa a indexar y buscar únicamente sobre la raíz
`knowledge/` del vault, nunca sobre la raíz del vault completo. Esto es
deliberadamente una **allowlist por construcción** — el servicio solo recibe
`knowledge/` como su raíz de escaneo/índice — y no una denylist que exija
nombrar `pending/` (o cualquier carpeta futura) para excluirla.

Esa distinción es la que hace testeable el criterio de éxito central del
proposal:

- Una nota que vive únicamente en `pending/<id>.md` nunca es devuelta por
  `POST /search`, sin importar cuánto coincida su contenido con la query.
- Escribir una nota en `pending/` **no** cambia la revisión del índice — el
  walk recursivo (`_signature()`/`vault_revision()`/`build_index()`,
  `retrieval.py:24,47,92`) deja de correr sobre la raíz del vault completo y
  pasa a correr solo sobre `knowledge/`; de lo contrario cada escritura en
  `pending/` produciría churn de índice sobre contenido no aprobado.
- Una carpeta arbitraria de tercer nivel (por ejemplo `drafts/`) agregada al
  árbol del vault es invisible para `POST /search` **sin ningún cambio de
  código** — es una consecuencia directa de que el servicio nunca escanea
  nada fuera de `knowledge/`, no de una lista de exclusión que alguien deba
  recordar mantener actualizada.

Este alcance se aplica también al mount `ReadOnlyPaths=` de la unidad
systemd del servicio de búsqueda: cubre `knowledge/` y la ruta del índice,
nunca `pending/`. El servicio de búsqueda sigue siendo de solo lectura; el
único cambio es el path que puede leer.

---

## 6. Hechos de código verificados que este change debe manejar

Verificados en esta sesión, no asumidos — consistentes con `proposal.md`:

| Hecho | Archivo | Consecuencia |
|---|---|---|
| `_signature()`/`vault_revision()`/`build_index()` hacen `vault.rglob("*.md")` — recursivo | `retrieval.py:24,47,92` | Poner `pending/` bajo la raíz del vault indexaría notas pendientes y produciría churn de revisión en cada escritura. El scoping a `knowledge/` es mandatorio, no cosmético. |
| `search_vault()` valida con `vault.glob("*.md")` — plano | `search.py:39` | Devuelve cero hits en cuanto las notas viven en `knowledge/` si la raíz no se actualiza junto con la estructura. |
| `Publisher._target()` calcula ids tomados con `glob("*.md")` plano | `publisher.py:99` | El chequeo de colisión de id debe seguir a las notas hacia `knowledge/`. |
| `GitMirror._mirror_files()` es `glob("*.md")` plano | `mirror.py:65` | El mirror deja de copiar nada tras el movimiento — consistente con D-05 (el mirror colapsa). |
| Los campos de revisión viven en el propio frontmatter de la nota, un solo bloque | `review.py:33-44` | El acto de promoción debe quitar `REVIEW_FIELDS` (`review.py:69`) o se filtran a las notas publicadas. |

---

## 7. Áreas afectadas

| Área | Impacto | Descripción |
|---|---|---|
| `src/knowledge_vault/retrieval.py` | Modificado | La raíz de índice pasa a ser `knowledge/`; un walk scoped reemplaza el `rglob` de raíz de vault. |
| `src/knowledge_vault/search.py`, `serve.py` | Modificado | El guard de vault vacío y la raíz de búsqueda siguen a `knowledge/`. |
| `src/knowledge_vault/publisher.py` | Modificado/Eliminado | El renderizado + reuso de id se mueven a la promoción; el path del registro approved se elimina. |
| `src/knowledge_vault/review.py` | Modificado | La proyección apunta a `pending/` dentro del árbol del vault; el import de decisión pasa a ser la promoción. |
| `src/knowledge_vault/mirror.py`, `review_sync.py` | Modificado/Eliminado | Según D-01/D-05. |
| `src/knowledge_vault/propose.py` | Modificado | Escribe notas en `pending/`, no un spool JSON. |
| `skills/propose-note/SKILL.md` | Modificado | Regla imperativa de solo-`pending/`; sección de submission reescrita. |
| `systemd/*.service`, `*.timer`, `scripts/install-host.sh` | Modificado/Eliminado | El set de unidades se reduce; nuevo layout de paths/ownership. |
| `tests/` (113 tests, 13 archivos) | Modificado | Reescritura grande siguiendo el cambio estructural. |
| `docs/services/knowledge-vault.md` | Modificado | Diagrama de pipeline y modelo de seguridad reescritos. |

---

## 8. Riesgos

| Riesgo | Probabilidad | Mitigación |
|---|---|---|
| Notas en `pending/` se vuelven buscables/recuperables como si estuvieran aprobadas | Media | Allowlist de raíz `knowledge/`; test explícito que asegura que una nota en `pending/` nunca se devuelve y nunca afecta la revisión del índice (§5). |
| Ids/links de notas existentes se rompen durante la migración | Media | La migración preserva nombres de archivo byte a byte; solo `git mv`, nunca re-mint de id. |
| Audit trail se pierde en silencio junto con el JSON de staging | Media | D-02 hace el rationale mandatorio y lo mueve al historial de commits; se documenta como propiedad explícita del spec, nunca como detalle de implementación. |
| Revisión por teléfono se cae sin que el usuario lo note | Media | D-01 fue escalada como pregunta bloqueante y confirmada directamente por el usuario, no aplicada como default en silencio. |
| Un `git mv` manual humano evita la validación (campos de revisión sin quitar publicados) | Media | La validación de promoción también corre como chequeo sobre `knowledge/`; una nota con `REVIEW_FIELDS` se reporta, nunca se publica en silencio. |
| El change excede el presupuesto de revisión de 400 líneas | Alta | Se parte en PRs encadenados: (1) raíz de búsqueda/índice scoped, (2) lifecycle + promote, (3) unidades/installer/migración, (4) docs+skill. |

---

## 9. Plan de rollback

Rollback por slice vía `git revert` de los PRs encadenados, del más nuevo al
más viejo. Rollback de runtime: el layout de host anterior es un árbol de
paths *separado* (`/var/lib/knowledge-vault/*` + `/opt/knowledge-vault/vault`),
así que restaurar significa reactivar los timers viejos y volver a apuntar
`KNOWLEDGE_VAULT_DIR` — la migración copia notas hacia `knowledge/` y nunca
borra el vault plano viejo hasta un cleanup de follow-up, así que ningún nota
se destruye por un rollback.

---

## 10. Checklist de este spec

- [x] Alcance dentro/fuera definido
- [x] Las 6 decisiones de la propuesta (D-01 a D-06) resueltas como hechos
  asentados
- [x] Frontera de escritura del agente y contrato de promoción documentados
  como requisitos testeables
- [x] Alcance de búsqueda documentado como allowlist por construcción, con
  escenarios explícitos para `pending/` y una carpeta de tercer nivel
- [x] Hechos de código verificados (no asumidos) que este change debe manejar
- [ ] Diseño (`sdd-design`)
- [ ] Tareas (`sdd-tasks`)
- [ ] Implementación (`sdd-apply`)

---

## 11. Referencias

- `openspec/changes/knowledge-vault-restructure/proposal.md` — propuesta
  completa, incluida la ronda de preguntas resuelta (D-01/D-02 confirmadas
  directamente por el usuario).
- `openspec/changes/knowledge-vault-restructure/specs/knowledge-vault-note-lifecycle/spec.md`
  — spec delta de OpenSpec (nueva capability): lifecycle de una rama,
  frontera de escritura del agente, contrato de promoción.
- `openspec/changes/knowledge-vault-restructure/specs/knowledge-vault-search-bridge/spec.md`
  — spec delta de OpenSpec (capability modificada): alcance de búsqueda
  restringido a `knowledge/` por construcción.
- `openspec/specs/knowledge-vault-search-bridge/spec.md` — spec principal
  vigente antes de este delta.

---

**Fin del SDD**
