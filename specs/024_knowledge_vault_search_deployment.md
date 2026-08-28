# JARVIS Spec 024 - Software Design Document (SDD)
## Knowledge Vault Search Deployment: persistent host bridge + third live memory-router backend

**Estado:** Especificado (`sdd-spec`), pendiente de diseño/tareas/implementación.
**Fecha:** 2026-08-27
**Versión:** 1.0
**Autor:** Pedro Ortiz (vía agente `sdd-spec`)

---

## 0. Por qué existe este spec

`KnowledgeVaultBackend` (spec 018) está completo y es, entre los cinco
adaptadores de memory-router, el único validado de punta a punta contra un
`serve.py` real con **cero bugs de wire format** (`specs/018` §8.1). Pero
esa validación corrió el proceso a mano, en primer plano, con un directorio
de credenciales falseado — nada responde hoy en
`knowledge-vault-search.mcps.svc.cluster.local:8088`. El último ítem
abierto de `specs/018` §8 — "Despliegue real de `serve.py` como servicio
persistente" — es exactamente esta brecha, y es puramente operacional: cero
cambios de código en el adaptador o en `serve.py`.

Consecuencia hoy: `/global` selecciona tanto Engram como knowledge-vault,
pero la mitad de knowledge-vault está permanentemente inalcanzable. El
router degrada correctamente sobre esa ausencia — tan correctamente que
nadie lo nota.

---

## 1. Alcance de este change

**Dentro de alcance:**
- Habilitar + iniciar `knowledge-vault-search.service` de forma persistente
  en `trantor` (`systemctl enable --now`), con el token provisto por
  `install-host.sh` (PR #75), no hand-generado.
- Aplicar `kubernetes/mcps/knowledge-vault-search-endpoints.yaml` (Service
  headless sin selector + EndpointSlice manual → `10.42.0.1:8088`) y
  agregarlo a `bootstrap/05-deploy-manifests.sh`.
- Cablear memory-router: Secret `knowledge-vault-search-token` en `mcps` +
  `KNOWLEDGE_VAULT_TOKEN`/`KNOWLEDGE_VAULT_AUTH_MODE=bearer` en
  `memory-router-deployment.yaml`. Sin override de
  `KNOWLEDGE_VAULT_BASE_URL` — el default del adaptador ya nombra este
  Service.
- Cobertura de `bootstrap/03-create-secrets.sh` (mirror desde el archivo del
  host, nunca regenerar).
- Tests de manifiesto (Service/EndpointSlice + refs de secret de
  memory-router).
- Validación en vivo contra el clúster real: `/global` devuelve un hit real
  del vault con `backend == "knowledge-vault"` junto a hits de Engram.
- Cerrar el ítem de checklist de `specs/018` §8; auditar y corregir
  `specs/014` §9 completo (Decisión 4).
- Este spec numerado y su spec delta de OpenSpec.

**Fuera de alcance:**
- Cualquier cambio a `KnowledgeVaultBackend` o al wire format de `serve.py`.
- Ingress o exposición LAN del bridge — `10.42.0.1` es deliberadamente
  inalcanzable fuera del clúster.
- Containerizar el bridge o moverlo dentro del clúster.
- `store`/`reflect` para knowledge-vault.
- Normalización de score cross-backend y semántica de `limit` mergeado bajo
  convivencia `/global` (`specs/018` §7, sigue sin dueño).
- NetworkPolicy para `mcps` — no-op explícito, misma postura que spec 022.
- Desplegar los adaptadores restantes (Graphiti, Honcho, Cognee).

---

## 2. Decisiones resueltas (ronda de preguntas de la propuesta, 2026-08-27)

Las 4 decisiones de `proposal.md` quedan asentadas como hechos resueltos de
este spec — no quedan abiertas.

| # | Pregunta | Decisión |
|---|---|---|
| D-1 | Binding `10.42.0.1` de D-06 (spec 018) | **Se mantiene** `10.42.0.1` + bearer. Acoplamiento single-node/flannel documentado como restricción explícita y aceptada en este spec (§4). mTLS diferido hasta que memory-router pueda correr fuera de `trantor`. |
| D-2 | Transporte/rotación de token | `03-create-secrets.sh` hace mirror del archivo del host hacia el Secret de `mcps`; el archivo del host sigue siendo la fuente de verdad, el script nunca regenera. Rotación documentada como runbook ordenado de cuatro pasos (§5), no automatizada. |
| D-3 | Timeout de rebuild de `5s` | **Medir antes de habilitar.** Tiempo de `build_index` contra el `/opt/knowledge-vault/tree` real, número registrado en este spec (§6), la constante solo sube si la medición lo justifica. La medición es una tarea que **bloquea** el paso de habilitar, no un follow-up posterior. |
| D-4 | Alcance de la corrección de `specs/014` §9 | **Más amplio que el fix mínimo.** Se audita el checklist completo de §9 contra el estado real de despliegue de los seis backends (no solo la línea obsoleta), de forma que §9 quede verdadero de punta a punta (§7). |

---

## 3. Tres mitades independientes, ordenadas host → clúster → router

1. **Host**: la unidad ya existe y el token ahora lo provisiona el
   instalador. Esta mitad es "habilitar y verificar", más arreglar
   cualquier gap que exponga el primer `systemctl enable --now` real —
   exactamente la clase de brecha que encontraron PR #75, PR #71, y
   `specs/022` §8.1 cada vez que algo se corrió de verdad por primera vez.
2. **Clúster**: el manifiesto ya existe y nunca fue aplicado. Aplicarlo es
   aditivo y reversible; nada en `mcps` depende de él todavía.
3. **Router**: un Secret + dos env vars en un Deployment existente. Por la
   lección de `specs/022` §8.1 Bug 2, esto es un cambio de *manifiesto*
   únicamente — `kubectl apply` + `rollout restart` alcanza, sin rebuild de
   imagen, porque no hay cambios de código de adaptador.

El orden importa por ruidosidad de falla: probar `curl` desde un pod
*antes* de decirle el token al router, para que un `401` del lado del
router solo pueda significar token incorrecto, nunca "no hay nada
escuchando".

---

## 4. Invariante aceptado: acoplamiento a `10.42.0.1` (D-1)

`10.42.0.1` es la IP del bridge tal como la ve flannel en el único nodo
actual (`trantor`). Este binding **rompe** si: cambia el CNI, se agrega un
segundo nodo, o memory-router se reprograma fuera de `trantor`. Se acepta
explícitamente como restricción de día uno — no un descuido — porque
memory-router y `trantor` son, hoy, coextensivos. Un test de manifiesto
(`kubernetes/mcps/knowledge-vault-search-endpoints.yaml`) asegura la
dirección del EndpointSlice para que un cambio nunca sea accidental. mTLS
en lugar de la coupling de IP fija queda diferido hasta que exista una
razón real para correr memory-router fuera de este nodo.

---

## 5. Runbook de rotación de token (D-2, cuatro pasos, no automatizado)

1. Regenerar el token en el host: sobreescribir
   `/etc/knowledge-vault/search-token` en `trantor` (fuente de verdad).
2. Re-correr `bootstrap/03-create-secrets.sh` para que el Secret
   `knowledge-vault-search-token` en `mcps` haga mirror del nuevo valor.
3. `kubectl rollout restart deployment/memory-router -n mcps` para que el
   pod recoja el Secret actualizado.
4. Verificar en vivo: un `search` en `/global` sigue devolviendo hits
   `backend == "knowledge-vault"` — no solo un `200`, sino un hit real, para
   confirmar que el token nuevo efectivamente autentica.

Ningún paso está automatizado; un token viejo en el Secret produce un `401`
silencioso hasta que se ejecutan estos cuatro pasos en orden.

---

## 6. Medición del timeout de rebuild (D-3)

`serve.py` fija hoy un timeout de `5s` para el rebuild inline del índice
cuando detecta una revisión de vault obsoleta (`knowledge-vault-search-bridge`,
Requirement "Bounded Inline Index Rebuild"). Antes de habilitar el servicio
de forma persistente, `build_index` se mide contra el vault real en
`/opt/knowledge-vault/tree` (post-restructure, spec 023) — esta es una
tarea que **bloquea** el paso de habilitar, ejecutada durante `sdd-apply`,
no una verificación posterior:

- **Medición registrada:** *pendiente — se completa durante `sdd-apply`,
  contra el vault real de `trantor`, y este número se transcribe aquí antes
  de que la unidad se habilite de forma persistente.*
- **Regla de decisión:** si la medición es holgadamente menor a `5s` (con
  margen para el crecimiento esperado del vault en el corto plazo), el
  timeout actual se confirma sin cambios. Si se acerca o excede `5s`, la
  constante sube, justificada explícitamente por el número medido — nunca
  por intuición.
- Si Decisión 3 termina moviendo la constante, ese cambio de código es la
  única excepción al "cero cambios de adaptador/`serve.py`" de este change,
  y se refleja como un delta `MODIFIED` sobre el Requirement "Bounded
  Inline Index Rebuild" de `knowledge-vault-search-bridge` en el momento en
  que el número lo justifique.

---

## 7. Auditoría de `specs/014` §9 contra el estado real de los seis backends (D-4)

`specs/014` §9 hoy tiene una sola línea obsoleta: "Adaptadores de backend
#2–6 (Hindsight, Graphiti, Honcho, Cognee, Obsidian) — fuera de alcance de
esta fase". Decisión 4 pide auditar el checklist completo, no solo esa
línea. Estado real verificado contra los specs de cada adaptador y de sus
companions de despliegue:

| # | Backend | Adaptador (código) | Servicio desplegado | Estado real |
|---|---|---|---|---|
| 1 | Engram | spec 011 | Sí, producción | Desplegado y en vivo — sin cambios de este audit. |
| 2 | Hindsight | spec 015 | Sí — spec 022 | Desplegado y en vivo (`specs/022` §8.1, validado contra clúster real). |
| 3 | Graphiti | spec 019 | No | Código de adaptador completo, sin companion de despliegue — sigue fuera de alcance de fase. |
| 4 | Honcho | spec 016 | No | Código de adaptador completo, sin companion de despliegue — sigue fuera de alcance de fase. |
| 5 | Cognee | spec 017 | No | Código de adaptador completo, sin companion de despliegue — sigue fuera de alcance de fase. |
| 6 | Knowledge-vault | spec 018 | Sí — este spec (024) | Desplegado y en vivo tras este change (previamente: validado a mano, sin servicio persistente). |

**Corrección aplicada a `specs/014` §9:** la línea obsoleta se reemplaza por
una que refleje ese estado de a-3: dos de los cinco adaptadores restantes
(Hindsight, knowledge-vault) están desplegados y en vivo; los otros tres
(Graphiti, Honcho, Cognee) siguen con código completo pero sin companion de
despliegue, explícitamente fuera de alcance de este change y del anterior.
"Obsidian" se retira del listado — nunca existió como adaptador propio;
era terminología heredada de una iteración anterior del vault, ya
reemplazada por "knowledge-vault" en specs 018/023.

---

## 8. Riesgos (heredados de la propuesta)

| Riesgo | Probabilidad | Mitigación |
|---|---|---|
| Primer `systemctl enable --now` real crashea en un prerequisito no visto — este patrón ya ocurrió tres veces (PR #71, PR #75, `specs/022` §8.1) | Alta | "Correrlo de verdad y leer el journal" es tarea obligatoria, no verificación tardía. Cualquier gap se arregla en `install-host.sh`, nunca a mano. |
| Token del host y Secret de k8s divergen tras una rotación, degradando `/global` a solo-Engram en silencio | Media | Fuente de verdad única (archivo del host); el script hace mirror, nunca regenera; runbook de rotación ordenado (§5); la validación exige un hit real, no solo `200`. |
| `10.42.0.1` se rompe por cambio de CNI, nodo nuevo, o reprogramación de memory-router | Baja hoy, alto impacto | §4; acoplamiento single-node documentado; test de manifiesto asegura la dirección. |
| Deadline de `5s` no medido produce `503`s en `/global` al crecer el vault | Media | §6 — medir primero, número registrado antes de habilitar. |
| Resultados mergeados de `/global` se vuelven ruidosos o lentos con un segundo backend respondiendo de verdad | Media | Fuera de alcance explícitamente, pero la validación en vivo mira calidad del output mergeado, no solo la existencia de un hit. |
| Indisponibilidad del bridge se vuelve visible al usuario en vez de degradar en silencio | Baja | El adaptador ya levanta `BackendUnavailableError` y el dispatcher degrada; un bridge caído devuelve `/global` al baseline pre-change (solo-Engram), no a un outage. |

---

## 9. Plan de rollback

Tres mitades independientes y reversibles individualmente — ninguna
requiere rebuild de imagen.

- **Router**: quitar `KNOWLEDGE_VAULT_TOKEN`/`KNOWLEDGE_VAULT_AUTH_MODE` de
  `memory-router-deployment.yaml`, `kubectl apply` + `rollout restart`. El
  adaptador cae a `auth_mode="none"`, el bridge lo rechaza, `/global`
  degrada a solo-Engram — exactamente el comportamiento de hoy.
- **Clúster**: `kubectl delete -f
  kubernetes/mcps/knowledge-vault-search-endpoints.yaml`. Nada más en
  `mcps` lo referencia.
- **Host**: `systemctl disable --now knowledge-vault-search.service`. El
  vault, el índice, y el pipeline de promote/sync quedan intactos — la
  unidad solo lee.

Revertir el commit elimina el cableado de manifiesto, el script de secret,
los tests, y este spec juntos. No hay datos que migrar ni escrituras que
deshacer — es un camino de solo lectura sobre un corpus que ya existe.

---

## 10. Checklist de este spec

- [x] Alcance dentro/fuera definido
- [x] Las 4 decisiones de la propuesta resueltas como hechos asentados
- [x] Restricción de node-locality documentada explícitamente (§4)
- [x] Runbook de rotación de token, ordenado y no automatizado (§5)
- [x] Medición del timeout de rebuild como tarea que bloquea el habilitado
  (§6) — número pendiente de registrar durante `sdd-apply`
- [x] Auditoría de `specs/014` §9 contra el estado real de los seis
  backends (§7)
- [x] Diseño (`sdd-design`) — `openspec/changes/knowledge-vault-search-deployment/design.md`
- [x] Tareas (`sdd-tasks`) — `openspec/changes/knowledge-vault-search-deployment/tasks.md`
- [ ] Implementación (`sdd-apply`) — **parcial**: manifiestos, bootstrap wiring
  (secret block 7, apply-list ordering), manifest tests (Fases 2-3) hechos y
  en verde; medición del timeout (Fase 1), habilitación real del host con
  evidencia (Fase 4), apply al clúster real + curl proof (Fase 5), secret +
  router wiring en vivo (Fase 6), y E2E en `/global` (Fase 7) requieren root
  en `trantor` y/o `kubectl apply` contra el clúster real — fuera del
  alcance de lo que este agente puede ejecutar; runbook para el usuario en
  el reporte de `sdd-apply`
- [ ] Validado en vivo contra el clúster real

---

## 11. Referencias

- `openspec/changes/knowledge-vault-search-deployment/proposal.md` —
  propuesta completa, incluida la ronda de decisiones resueltas
  (2026-08-27).
- `openspec/changes/knowledge-vault-search-deployment/specs/knowledge-vault-search-service/spec.md`
  — spec delta de OpenSpec (nueva capability): contrato del servicio
  desplegado.
- `openspec/changes/knowledge-vault-search-deployment/specs/memory-backend-adapters/spec.md`
  — spec delta de OpenSpec (capability modificada): configuración desplegada
  del adaptador knowledge-vault.
- `specs/018_knowledge_vault_backend.md` — spec del adaptador
  `KnowledgeVaultBackend`, cuyo ítem de checklist de despliegue cierra este
  change.
- `specs/014_memory_router.md` — §9, corregido por este change (Decisión 4).
- `specs/022_hindsight_deployment.md` — precedente directo: mismo patrón de
  despliegue (host/clúster/router), y origen de la lección de Bug 2 (cambios
  de manifiesto vs. cambios de código de imagen).
- `specs/023_knowledge_vault_restructure.md` — origen de
  `KNOWLEDGE_VAULT_DIR=/opt/knowledge-vault/tree` y la raíz `knowledge/`
  que este servicio indexa.
- `kubernetes/mcps/knowledge-vault-search-endpoints.yaml` — manifiesto
  aplicado por este change.
- `hermes-native/knowledge-vault/systemd/knowledge-vault-search.service`,
  `scripts/install-host.sh` — unidad y provisión del token en el host.

---

**Fin del SDD**
