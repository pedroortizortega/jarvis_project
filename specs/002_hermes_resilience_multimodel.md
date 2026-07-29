# 002 — Resiliencia de Hermes, multi-modelo (vLLM + Codex) e integración con el entorno de desarrollo

Extiende el spec 001 (no lo reemplaza). Asume que Parte A y B de 001 ya están configuradas.

## Objetivo

1. Que Hermes siga funcionando aunque `pc-master` esté apagado/reiniciando, sin depender solo de las réplicas de RPi.
2. Que `pc-master` se despierte solo (Wake-on-LAN) cuando haga falta el LLM local, disparado por LiteLLM (no por Hermes ni por polling).
3. Que Hermes use un modelo local chico (vLLM) para el día a día, y tu suscripción Codex (ChatGPT Plus/Pro) para tareas pesadas (diseño, investigación profunda, debugging) — vía **Hermes Profiles**, sin mezclar ambas identidades de LLM en el mismo perfil.
4. Que Hermes (corriendo en el pod de `pc-master`) pueda operar sobre tus proyectos reales de desarrollo en esa misma máquina.

Asunciones a confirmar:
- La placa madre de `pc-master` soporta Wake-on-LAN por cable (BIOS habilitado) — pendiente de verificar.
- Tenés 2+ Raspberry Pi en el clúster; una se puede dedicar al modelo de fallback sin afectar a `hermes-agent`.
- `sshd` corre en `pc-master` (uso normal de escritorio) — el puerto 22 ya está permitido en el firewall del spec 001.

---

## Parte A — Réplica de Hermes en `pc-master` + resiliencia ante caídas del master

### A1. Réplica de Hermes en pc-master

Nuevo Deployment `hermes-agent-master` en el namespace `hermes-agents`, mismo image que `hermes-agent` (spec 001, B8), con:

```yaml
spec:
  template:
    spec:
      nodeSelector:
        workload: llm
      tolerations:
        - {key: dedicated, operator: Equal, value: llm, effect: NoSchedule}
```

Esto la deja convivir con vLLM/LiteLLM en el mismo nodo sin tocar sus taints actuales.

### A2. Cascada de fallback en LiteLLM (para cuando pc-master está caído)

`litellm-config` (spec 001, A9) suma dos escalones más al modelo primario `qwen3`:

1. `qwen3` → vLLM en `pc-master` (como hoy).
2. `qwen-fallback-rpi` → modelo chico (ver A3) corriendo en una RPi dedicada.
3. `cloud-fallback` → API externa de pago por uso (proveedor y key **pendientes de definir** — no confundir con tu suscripción ChatGPT Plus/Pro, que no sirve como backend programático para este propósito).

### A3. Modelo chico de fallback en RPi

Deployment de **Ollama** (más simple que llama.cpp puro en ARM64; API OpenAI-compatible nativa) en una RPi dedicada, label nueva `workload=agent-fallback` para no competir con `hermes-agent`. Modelo sugerido: algo del orden de 1.5B cuantizado — a confirmar según RAM real de la RPi elegida.

### A4. Wake-on-LAN bajo demanda, disparado por LiteLLM

Microservicio `wol-trigger` (namespace `hermes-agents`), corriendo en una RPi con `hostNetwork: true` (necesario para que el magic packet salga a la LAN física y no quede atrapado en la red overlay de Flannel). Expone `POST /wake`, envía el paquete a la MAC de `pc-master`, y aplica un **cooldown** (ej. 5 min) para evitar spam si siguen llegando requests mientras el PC arranca.

LiteLLM usa su `failure_callback` para invocar `wol-trigger:8080/wake` cuando falla una request al modelo primario `qwen3` — antes o junto con caer al fallback de RPi. Hermes no necesita saber nada de esto: sigue hablando solo con LiteLLM.

> Pendiente: confirmar sintaxis exacta de `failure_callback` contra la versión de LiteLLM instalada, y la MAC/soporte BIOS de WOL de `pc-master`.

---

## Parte B — Multi-modelo con Hermes Profiles: vLLM para el día a día, Codex para tareas pesadas

### B1. Dos perfiles separados

```bash
hermes profile create main
hermes profile create heavy --description "Diseño, investigación profunda, debugging — tareas que requieren razonamiento fuerte"
```

- **`main`**: `model.provider: custom`, `base_url` apuntando a LiteLLM (`http://litellm.llms.svc.cluster.local:4000/v1`) o directo a un vLLM chico. Uso cotidiano, sin gastar tu suscripción.
- **`heavy`**: `model.provider: codex` (Codex OAuth — usa tu cuenta ChatGPT Plus/Pro, sin API key). Reservado para trabajo pesado.

Ambos perfiles conviven en el **mismo pod** (`hermes-agent-master`), mismo PVC — no hace falta un pod aparte por perfil.

### B2. Login de Codex OAuth dentro del pod

Codex/ChatGPT Plus son **device-code providers**: no requieren túnel SSH ni browser local.

```bash
kubectl exec -it deploy/hermes-agent-master -n hermes-agents -- hermes model
# elegís "Codex OAuth" dentro del perfil `heavy`
# Hermes muestra una URL + código corto — se abre en cualquier navegador (celular, laptop)
```

El token queda en `~/.hermes/auth.json` **del perfil `heavy`**. Para que sobreviva reinicios del pod, ese directorio debe estar en un PVC montado en `hermes-agent-master` (no en las réplicas de RPi).

### B3. Enrutamiento de tareas entre perfiles

Dos mecanismos documentados por Hermes:
- **`delegate_task`** (tool de subagente): delegación puntual dentro de una sesión.
- **Kanban dispatcher**: usa la `--description` del perfil para decidir a qué perfil asignar cada tarea.

> Pendiente de verificar en la práctica: si la clasificación "qué es una tarea pesada" es automática por descripción semántica, o si requiere pedir explícitamente la delegación al perfil `heavy`.

### B4. Limitación conocida: memoria NO se comparte entre perfiles

Cada perfil tiene `config.yaml`, `.env` y memoria (`~/.hermes/memories/`, `state.db`) completamente aislados por diseño. `main` y `heavy` van a acumular memoria y skills por separado salvo que se configure un **external memory provider** compartido (`hermes memory setup`) — no investigado en profundidad todavía, queda como ítem abierto si se necesita continuidad de memoria entre ambos perfiles.

### B5. Por qué NO se replica el login de Codex hacia las RPi

El token de `auth.json` es una identidad personal única. Usarlo desde múltiples réplicas concurrentes (RPi + master a la vez) arriesga:
- Chocar con los límites de uso de tu plan mucho antes que con un backend propio.
- Zona gris de ToS de OpenAI (uso de suscripción de consumidor para automatizar un servicio con múltiples instancias concurrentes).
- Requeriría un volumen RWX entre nodos (NFS u otro), ya que `local-path-provisioner` es RWO y local al nodo.

Por eso Codex queda **solo en el perfil `heavy` de `hermes-agent-master`**, no en las réplicas de RPi.

---

## Parte C — Integración con el entorno de desarrollo (pc-master como PC de trabajo)

Recomendado: `terminal.backend: ssh`, apuntando Hermes *de vuelta* al sistema operativo host de `pc-master` (no al filesystem del propio contenedor). La doc de Hermes recomienda este backend explícitamente por seguridad ("el agente no puede modificar su propio código").

```yaml
terminal:
  backend: ssh
```
```
TERMINAL_SSH_HOST=192.168.1.10   # IP del host — NO "localhost": el pod tiene su propio network namespace
TERMINAL_SSH_USER=pedro
TERMINAL_SSH_KEY=/run/secrets/hermes-ssh/id_rsa
```

La clave privada se monta como `Secret` de Kubernetes en el pod. `sshd` ya corre en `pc-master` como parte de su uso normal de escritorio, y el puerto 22 ya está permitido en el firewall (spec 001, A0).

Ventajas sobre compartir la carpeta de proyectos vía `hostPath`:
- Hermes ve el entorno real completo (compiladores, Docker, git, IDEs) sin replicar ese toolchain dentro de la imagen del pod.
- Sin problemas de UID/permisos entre el usuario del contenedor y el usuario real del host.
- Aísla al agente de su propio código.

Alternativa más simple si se prefiere evitar la clave SSH: `hostPath` montando la carpeta de proyectos + `terminal.backend: local` — más simple de configurar, pero ata la imagen del pod a tener instalado todo lo necesario para desarrollar, y exige que el UID del contenedor coincida con el del usuario host.

---

## Parte D — Integración con Telegram (mensajería)

Como el pod ya corre `gateway run` como proceso principal (Parte A1), la forma más simple es configurar todo por variables de entorno en `/opt/data/.env` y reiniciar el pod para que las tome — en vez del wizard interactivo (`hermes gateway setup`), que asume que vos arrancás el gateway a mano en primer plano.

### D1. Crear el bot (fuera del clúster, en Telegram)
1. Abrí Telegram, buscá **@BotFather**.
2. Mandale `/newbot`, elegí un nombre y un username terminado en `bot` (ej. `pedro_hermes_bot`).
3. Te da un token tipo `123456789:ABCdefGHIjklMNOpqrSTUvwxYZ`. **No lo compartas** — quien lo tenga controla el bot.

### D2. Conseguir tu user ID numérico
Mandale un mensaje a **@userinfobot** (o **@get_id_bot**) — responde con tu ID numérico (no tu `@username`). Guardalo para el paso siguiente.

### D3. Configurar el token y el allowlist en el pod

```bash
kubectl exec -it deploy/hermes-agent-master -n hermes-agents -- bash

# dentro del pod, reemplazá por tus valores reales:
cat >> /opt/data/.env <<'ENVEOF'
TELEGRAM_BOT_TOKEN=<tu-token-de-botfather>
TELEGRAM_ALLOWED_USERS=<tu-id-numerico>
ENVEOF
```

> **Sin `TELEGRAM_ALLOWED_USERS` el gateway rechaza a todo el mundo** por seguridad — no te lo saltees. Para múltiples usuarios: `TELEGRAM_ALLOWED_USERS=id1,id2`.

### D4. Reiniciar el pod para que el gateway tome la config nueva

```bash
kubectl -n hermes-agents rollout restart deployment/hermes-agent-master
kubectl -n hermes-agents rollout status deployment/hermes-agent-master
```

### D5. Verificar

```bash
kubectl -n hermes-agents logs deploy/hermes-agent-master --tail=30 | grep -i telegram
```
Debería verse que el gateway de Telegram arrancó. Mandale un mensaje al bot desde Telegram — con `TELEGRAM_ALLOWED_USERS` ya seteado, responde directo sin pairing.

### D6. Notas para uso en grupos (no solo DM)

Por defecto, el "privacy mode" del bot solo ve comandos `/` en grupos. Para chat libre en grupo: BotFather → `/mybots` → tu bot → **Bot Settings → Group Privacy → Turn off**, y sacar/reagregar el bot al grupo después del cambio (no toma efecto en grupos ya unidos).

Alternativa a la allowlist fija: dejar que usuarios nuevos pidan acceso por DM y aprobar manualmente:
```bash
hermes pairing list                          # ver pendientes/aprobados
hermes pairing approve telegram <código>     # aprobar
hermes pairing revoke telegram <user-id>     # revocar
```

---

## Parte E — Versionar la configuración de Hermes en el repo

`/opt/data` (el PVC) es el único lugar donde hoy vive la configuración real de Hermes — si se pierde el volumen, se pierde todo. `kubernetes/hermes/config/` en el repo guarda una copia versionada de los archivos declarativos (no de estado/sesión/caché), que se **popula en el pod al primer arranque** vía un init container, sin pisar ediciones en vivo posteriores.

### E1. Qué se versiona y por qué

| Archivo | Versionado | Motivo |
|---|---|---|
| `SOUL.md` | Sí — `kubernetes/hermes/config/SOUL.md` | Personalidad/identidad custom (persona "JARVIS/Master"), ya existía en el repo antes de este spec. |
| `config.yaml` | Sí — `kubernetes/hermes/config/config.yaml` | Config declarativa: `model.*` (provider, base_url, context_length), `terminal.backend: ssh`, etc. Sin secretos — las credenciales viven en `.env`, aparte. Exportado el 2026-07-27 desde el pod en producción. |
| `.env` (tokens, API keys) | **No** | Contiene secretos (`TELEGRAM_BOT_TOKEN`, etc.) — nunca a git. Gestionar como `Secret` de k8s si se quiere versionar la *estructura* (no los valores). |
| `memories/`, `sessions/`, `state.db`, cachés | No | Estado que crece con el uso normal, no configuración. |
| Skills custom (ej. `pc-master-write-paths`) | Pendiente — no versionada todavía | Ver "Pendientes" al final del spec. |

> Nota: los perfiles `main`/`heavy` de la Parte B **nunca se llegaron a crear** en el pod real (no existe `/opt/data/profiles/`) — lo que hoy corre es el `model.*` seteado directo sobre el perfil default, que es exactamente lo que quedó capturado en `config.yaml`. Crear los perfiles de verdad sigue pendiente.

### E2. ConfigMap + init container que los popula

```bash
# Genera el ConfigMap a partir de los archivos reales del repo (repetir cada vez que se actualice alguno)
kubectl -n hermes-agents create configmap hermes-config-seed \
  --from-file=SOUL.md=kubernetes/hermes/config/SOUL.md \
  --from-file=config.yaml=kubernetes/hermes/config/config.yaml \
  --dry-run=client -o yaml | kubectl apply -f -
```

En `hermes-agent-master.yaml`, un init container adicional (además de `fix-ssh-key-perms`, Fase 1.4) copia estos archivos al PVC **solo si todavía no existen**:

```yaml
        - name: seed-hermes-config
          image: busybox:1.36
          command:
            - sh
            - -c
            - |
              [ -f /opt/data/SOUL.md ] || cp /seed/SOUL.md /opt/data/SOUL.md
              [ -f /opt/data/config.yaml ] || cp /seed/config.yaml /opt/data/config.yaml
              chown 1000:1000 /opt/data/SOUL.md /opt/data/config.yaml 2>/dev/null || true
          volumeMounts:
            - {name: hermes-config-seed, mountPath: /seed, readOnly: true}
            - {name: hermes-home, mountPath: /opt/data}
```
Y el volumen correspondiente:
```yaml
        - name: hermes-config-seed
          configMap:
            name: hermes-config-seed
```
```bash
kubectl apply -f hermes-agent-master.yaml
kubectl -n hermes-agents rollout status deployment/hermes-agent-master
```

> **Por qué "solo si no existen" y no un mount directo de solo lectura:** `config.yaml` lo edita el propio Hermes en caliente (`hermes config set`, perfiles, etc.). Montarlo como `ConfigMap` de solo lectura rompería esos comandos. La semilla solo aplica en un PVC nuevo/vacío (ej. si se recrea el volumen); en un pod ya inicializado, no toca nada.

### E3. Mantener sincronizado el repo con la config en vivo

No hay sync automático en ninguna dirección — el init container de E2 solo siembra en un PVC vacío, nunca vuelve a copiar sobre un archivo que ya existe.

**Repo → pod** (editaste `SOUL.md`/`config.yaml` en el repo y querés que el pod lo tome ya, sin esperar a que se recree el PVC):
```bash
# actualiza el ConfigMap (referencia para el próximo PVC nuevo)
kubectl -n hermes-agents create configmap hermes-config-seed \
  --from-file=SOUL.md=kubernetes/hermes/config/SOUL.md \
  --from-file=config.yaml=kubernetes/hermes/config/config.yaml \
  --dry-run=client -o yaml | kubectl apply -f -

# empuja el contenido directo al PVC del pod que ya está corriendo
kubectl -n hermes-agents exec -i deploy/hermes-agent-master -- sh -c 'cat > /opt/data/SOUL.md' < kubernetes/hermes/config/SOUL.md
kubectl -n hermes-agents exec -i deploy/hermes-agent-master -- sh -c 'cat > /opt/data/config.yaml' < kubernetes/hermes/config/config.yaml

# el gateway lee la configuración al iniciar
kubectl -n hermes-agents rollout restart deployment/hermes-agent-master
kubectl -n hermes-agents rollout status deployment/hermes-agent-master

# verificar
diff <(kubectl -n hermes-agents exec deploy/hermes-agent-master -- cat /opt/data/SOUL.md) kubernetes/hermes/config/SOUL.md && echo "OK: coinciden"
diff <(kubectl -n hermes-agents exec deploy/hermes-agent-master -- cat /opt/data/config.yaml) kubernetes/hermes/config/config.yaml && echo "OK: coinciden"
```

**Pod → repo** (editaste algo con `hermes config set` o el dashboard y querés que sobreviva a la pérdida del PVC), re-exportarlo a mano:
```bash
kubectl -n hermes-agents exec deploy/hermes-agent-master -- cat /opt/data/config.yaml > kubernetes/hermes/config/config.yaml
kubectl -n hermes-agents exec deploy/hermes-agent-master -- cat /opt/data/SOUL.md > kubernetes/hermes/config/SOUL.md
# revisar que no se coló ningún secreto antes de commitear
kubectl -n hermes-agents create configmap hermes-config-seed \
  --from-file=SOUL.md=kubernetes/hermes/config/SOUL.md \
  --from-file=config.yaml=kubernetes/hermes/config/config.yaml \
  --dry-run=client -o yaml | kubectl apply -f -
```

### Troubleshooting: `Context compression failed after 3 attempts` en conversaciones cortas

Síntoma en `/opt/data/logs/agent.log`:
```
agent.conversation_compression: Compression made no progress (session=...) — skipping boundary rewrite.
... "failure_class":"no_progress" ...
agent.conversation_loop: Context length exceeded: N tokens. Cannot compress further.
```
Pasa incluso con conversaciones chicas (excedentes de apenas 30-60 tokens sobre el límite), y se repite seguido — no es un caso aislado.

**Causa:** los defaults de Hermes protegen `protect_first_n: 3` + `protect_last_n: 20` mensajes (23 mínimo) de cualquier compresión. Estos valores están pensados para APIs comerciales con ventanas de contexto mucho más grandes que la nuestra (98304, compartiendo GPU con el desktop). Si la conversación tiene pocos mensajes, esa zona protegida cubre *toda* la conversación — no queda nada "del medio" para resumir, y el algoritmo aborta en vez de recortar algo mínimo. Confirmado en el log: una sesión de 131 mensajes sí comprimió bien (`messages=131->93`), porque ahí sobraba margen fuera de la zona protegida; sesiones de 1-2 mensajes con un excedente chico, no.

**Fix aplicado:**
```bash
kubectl -n hermes-agents exec deploy/hermes-agent-master -- hermes config set compression.protect_first_n 1
kubectl -n hermes-agents exec deploy/hermes-agent-master -- hermes config set compression.protect_last_n 8
```
Y re-exportar a `kubernetes/hermes/config/config.yaml` (ver "Pod → repo" arriba).

**Mitigación manual mientras tanto:** si una sesión puntual se traba así, `/compress` (reintentar) o `/new` (sesión nueva) — no afecta la memoria persistente, solo el historial de esa conversación.

**Efecto "bola de nieve" y segundo fix inicial:** aun con `protect_first_n`/`protect_last_n` bajos, una conversación puede crecer más rápido de lo que la compresión logra seguirle el ritmo — cada intento fallido agrega su propio mensaje de error al historial, agrandando el próximo intento. Se configuró:
```bash
kubectl -n hermes-agents exec deploy/hermes-agent-master -- hermes config set compression.threshold 0.35
```
Esta configuración por sí sola no resolvió el problema: Hermes eleva el umbral a 75% en modelos con contexto menor a 512K. El umbral efectivo pasó a ser aproximadamente 73,728 tokens, no 34,406. La corrección definitiva queda en la siguiente sección.

> Nota: además, un `/compress` sobre una conversación muy grande (~39k tokens en un caso real) puede disparar el bug de Triton/GDN documentado en el spec 001 (troubleshooting de A8.4) — el crash del pod de vLLM y el loop de contexto son problemas relacionados pero independientes; los dos fixes (`VLLM_TRITON_FORCE_FIRST_CONFIG=1` en vLLM + `compression.threshold` más bajo en Hermes) atacan cada uno por su lado.

### Troubleshooting: `ContextWindowExceededError` de LiteLLM aunque la compresión está habilitada

**Síntoma observado:** LiteLLM rechaza la petición con un HTTP 400 similar a:

```text
This model's maximum context length is 98304 tokens. However, you requested
65536 output tokens and your prompt contains at least 32769 input tokens.
```

Después Hermes intenta compactar tres veces y termina con `Context length exceeded: max compression attempts (3) reached`.

**Causas confirmadas:**

1. El proveedor anuncia una ventana total de 98,304 tokens, pero Hermes dejaba que el modelo usara su máximo nativo de salida: 65,536 tokens. Por lo tanto, cualquier prompt de 32,769 tokens o más se rechaza antes de generar una respuesta.
2. `compression.protect_last_n: 8` no significa "compactar cada ocho mensajes". Solo impide resumir los últimos ocho mensajes. Las salidas extensas de `kubectl`, lecturas de archivos y herramientas siguen acumulando tokens.
3. `compression.threshold: 0.35` no se aplicaba como 35% para este modelo. Hermes establece un mínimo de 75% en modelos de menos de 512K de contexto, dejando el disparador efectivo en ~73.7K tokens.
4. La compactación reduce mensajes, no garantiza reducir tokens. Un resumen puede ser más denso y aumentar la estimación. Los reintentos y sus mensajes de error también agrandan el historial.

**Configuración definitiva versionada:** `kubernetes/hermes/config/config.yaml` contiene estos valores para Qwen con ventana de 98,304 tokens:

```yaml
model:
  context_length: 98304
  max_tokens: 32768

compression:
  threshold_tokens: 50000
  protect_first_n: 1
  protect_last_n: 8
  proactive_prune_tokens: 40000
  max_attempts: 5
```

- `max_tokens: 32768` limita la reserva de salida. Con un prompt de hasta 50K tokens, el presupuesto teórico queda por debajo de los 98,304 tokens.
- `threshold_tokens: 50000` tiene prioridad como límite absoluto y evita el mínimo interno del 75% aplicado a `threshold`.
- `proactive_prune_tokens: 40000` recorta o resume resultados viejos y grandes de herramientas antes de que el historial alcance el umbral de compactación.
- `max_attempts: 5` es una red de seguridad; no sustituye los límites anteriores.

#### Corrección paso a paso en un clúster existente

Ejecutar desde la raíz de este repositorio. No se deben guardar tokens, API keys ni archivos `.env` en Git.

1. Definir variables para el Deployment:

```bash
export NS=hermes-agents
export DEPLOY=hermes-agent-master
export POD="$(kubectl get pod -n "$NS" -l app="$DEPLOY" -o jsonpath='{.items[0].metadata.name}')"
```

2. Respaldar la configuración viva en el PVC:

```bash
kubectl exec -n "$NS" "$POD" -- sh -c \
  'cp /opt/data/config.yaml /opt/data/config.yaml.bak-$(date +%Y%m%dT%H%M%SZ)'
```

3. Aplicar la configuración versionada al ConfigMap, que será la semilla de futuros PVC:

```bash
kubectl -n "$NS" create configmap hermes-config-seed \
  --from-file=SOUL.md=kubernetes/hermes/config/SOUL.md \
  --from-file=config.yaml=kubernetes/hermes/config/config.yaml \
  --dry-run=client -o yaml | kubectl apply -f -
```

4. Copiar la misma configuración al PVC actual. El init container no lo hace porque está diseñado para no sobrescribir cambios vivos:

```bash
kubectl -n "$NS" exec -i deploy/"$DEPLOY" -- sh -c \
  'cat > /opt/data/config.yaml' < kubernetes/hermes/config/config.yaml
```

5. Reiniciar y esperar el rollout:

```bash
kubectl -n "$NS" rollout restart deployment/"$DEPLOY"
kubectl -n "$NS" rollout status deployment/"$DEPLOY"
```

6. Confirmar la configuración ya cargada:

```bash
kubectl -n "$NS" exec deploy/"$DEPLOY" -- hermes config get model
kubectl -n "$NS" exec deploy/"$DEPLOY" -- hermes config get compression
```

7. Empezar una conversación nueva con `/new`. Una sesión que ya excedió el contexto conserva el historial sobredimensionado y no se corrige al reiniciar el pod.

#### Replicación en otro servidor o Raspberry Pi

1. Copiar este repositorio al host destino y ajustar en `kubernetes/hermes/hermes-agent-master.yaml` el `nodeSelector`, tolerations, `HERMES_UID`, `HERMES_GID` y las variables SSH para ese host.
2. Cambiar `model.base_url` y `model.context_length` en `kubernetes/hermes/config/config.yaml` si el LiteLLM/modelo remoto usa otros valores. Ajustar `max_tokens` y `threshold_tokens` para que `threshold_tokens + max_tokens` quede claramente por debajo de `context_length`.
3. Crear en el clúster destino los Secrets requeridos, especialmente `hermes-ssh-key`, sin copiarlos al repositorio.
4. Aplicar el PVC, el ConfigMap generado desde `kubernetes/hermes/config/` y finalmente el Deployment:

```bash
kubectl apply -f kubernetes/hermes/hermes-master-pvc.yaml
kubectl -n hermes-agents create configmap hermes-config-seed \
  --from-file=SOUL.md=kubernetes/hermes/config/SOUL.md \
  --from-file=config.yaml=kubernetes/hermes/config/config.yaml \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f kubernetes/hermes/hermes-agent-master.yaml
kubectl -n hermes-agents rollout status deployment/hermes-agent-master
```

5. Verificar que Hermes muestra `max_tokens: 32768` y `threshold_tokens: 50000` antes de abrir sesiones largas.

**Seguridad:** las credenciales externas deben inyectarse mediante `Secret` o un gestor de secretos. Si alguna API key fue escrita directamente en un Deployment o expuesta por un `kubectl get ... -o yaml`, debe rotarse y reemplazarse por una referencia `secretKeyRef`.

---

## Pendientes / a confirmar antes de implementar

- Crear de verdad los perfiles `main`/`heavy` (Parte B) — hoy no existen; el `model.*` corre sobre el perfil default (ver Parte E1).
- Versionar la skill custom `pc-master-write-paths` (y cualquier otra que se cree) en `kubernetes/hermes/config/skills/` — todavía solo vive en el PVC.
- Evaluar si versionar `.env.example` (plantilla sin secretos) y/o `memories/` (con el trade-off de conflictos que editarlos a la vez implica) — decisión pendiente, no bloqueante.
- MAC address y soporte BIOS de WOL en `pc-master`.
- Qué RPi específica aloja el modelo de fallback (A3) y si tiene RAM suficiente sin ahogar a `hermes-agent`.
- Proveedor y API key del cloud-fallback (A2), separado de tu suscripción ChatGPT Plus/Pro.
- Sintaxis exacta de `failure_callback` en la versión de LiteLLM instalada.
- Si la clasificación de tareas del kanban dispatcher (B3) es automática o requiere delegación explícita.
- External memory provider para compartir memoria/skills entre perfiles `main`/`heavy` (B4), si se decide que hace falta.
- Generar la clave SSH dedicada para Hermes y confirmar que `sshd` en `pc-master` acepta auth por clave (no solo contraseña).

---

## Guía de implementación por fases (manual, copy-paste)

Pensada para ejecutar vos mismo, en orden. Cada fase deja algo usable antes de pasar a la siguiente.

### Fase 1 — Hermes corriendo en pc-master, usable de una vez (Parte C + base de Parte A)

Objetivo de esta fase: tener un pod de Hermes en el clúster, con memoria/perfiles persistentes y acceso real a tus proyectos vía SSH, para poder empezar a usarlo hoy mismo (todavía sin Codex ni fallback — eso es Fase 2 y 3).

#### 1.1 Imagen de Hermes

No hay imagen publicada en un registry (Docker Hub/GHCR), pero el proyecto sí mantiene un `Dockerfile` oficial en su repo — lo usamos tal cual en vez de armar uno propio:

```bash
# en pc-master
git clone https://github.com/NousResearch/hermes-agent.git
cd hermes-agent

docker build -t hermes-agent:local .

# k3s usa containerd, no el docker daemon del host — hay que importar la imagen
docker save hermes-agent:local | sudo k3s ctr images import -
```

> Nota: el `Dockerfile` oficial usa `s6-overlay` como PID 1 (entrypoint `/init`) y remapea el usuario interno vía las env vars `HERMES_UID`/`HERMES_GID` — por eso el Deployment de 1.4 **no** sobreescribe el entrypoint ni el `command` con algo tipo `sleep infinity`; hay que dejar que el init propio del proyecto arranque el `gateway`, igual que hace su `docker-compose.yml` de referencia.

#### 1.2 PVC para persistir `~/.hermes` (config, perfiles, memoria, auth)

```yaml
# hermes-master-pvc.yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: hermes-master-home
  namespace: hermes-agents
spec:
  accessModes: ["ReadWriteOnce"]
  storageClassName: local-path
  resources:
    requests:
      storage: 10Gi
```
```bash
kubectl apply -f hermes-master-pvc.yaml
```

#### 1.3 Clave SSH dedicada (Hermes pod → host `pc-master`)

```bash
# en pc-master, como tu usuario normal
ssh-keygen -t ed25519 -f ~/.ssh/hermes_agent_key -N "" -C "hermes-agent-master"

# autorizar esa clave para conectarse a tu propia cuenta
cat ~/.ssh/hermes_agent_key.pub >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys

# meterla como Secret de k8s (la privada)
kubectl create secret generic hermes-ssh-key \
  -n hermes-agents \
  --from-file=id_ed25519=$HOME/.ssh/hermes_agent_key
```

> **Importante si tu shell de login es `fish`/`zsh`/otra no-POSIX** (verificalo con `echo $SHELL`): Hermes asume que puede correr comandos estilo `sh`/`bash` (usa cosas como `$?`, `&&`/`||` encadenados de forma POSIX) al ejecutar por SSH. Con `fish` como login shell, esto rompe con errores de sintaxis reales (ej. `fish: $? no es el código de salida`, o exit codes como 126) que Hermes malinterpreta como "el directorio no existe" y puede entrar en un loop repitiendo el mismo diagnóstico fallido. Hermes no tiene una opción de config para forzar el shell remoto — se soluciona forzando `bash` **solo para esta clave específica**, vía `command=` en `authorized_keys` (no afecta tus sesiones SSH/interactivas normales, que siguen en fish):
> ```bash
> # reemplazá <TU-CLAVE-PUBLICA-HERMES> por el contenido real de ~/.ssh/hermes_agent_key.pub (después de "ssh-ed25519 " y antes del comentario)
> sed -i 's|^ssh-ed25519 \(<TU-CLAVE-PUBLICA-HERMES>\) hermes-agent-master$|command="bash -c \\"$SSH_ORIGINAL_COMMAND\\"" ssh-ed25519 \1 hermes-agent-master|' ~/.ssh/authorized_keys
> ```
> Verificá que funcionó:
> ```bash
> kubectl -n hermes-agents exec deploy/hermes-agent-master -- ssh -i /run/secrets/hermes-ssh/id_ed25519 -o StrictHostKeyChecking=no pedro@127.0.0.1 "pwd; echo EXIT:\$?"
> # debe imprimir el path real y EXIT:0, sin errores de sintaxis
> ```

#### 1.4 Deployment `hermes-agent-master`

```yaml
# hermes-agent-master.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: hermes-agent-master
  namespace: hermes-agents
spec:
  replicas: 1
  strategy:
    type: Recreate   # evita dos pods con hostNetwork:true peleando por el mismo puerto en el nodo
  selector: {matchLabels: {app: hermes-agent-master}}
  template:
    metadata: {labels: {app: hermes-agent-master}}
    spec:
      hostNetwork: true          # requerido por el proyecto (ver docker-compose.yml oficial: network_mode: host)
      dnsPolicy: ClusterFirstWithHostNet   # para que, con hostNetwork, siga resolviendo DNS interno del cluster (litellm.llms.svc...)
      nodeSelector:
        workload: llm
      tolerations:
        - {key: dedicated, operator: Equal, value: llm, effect: NoSchedule}
      # OpenSSH rechaza la clave privada si tiene CUALQUIER permiso de grupo/otros,
      # sin importar el grupo — y el volumen del Secret siempre queda con dueño root
      # (fsGroup solo cambia el grupo, no el owner). Como el proceso corre como uid 1000
      # (no root), no hay defaultMode que deje al mismo tiempo "legible por 1000" y
      # "sin bits de grupo/otros". Se resuelve copiando la clave a un emptyDir con el
      # dueño y permisos exactos antes de arrancar el contenedor principal.
      initContainers:
        - name: fix-ssh-key-perms
          image: busybox:1.36
          command:
            - sh
            - -c
            - |
              cp /secret-src/id_ed25519 /ssh-key/id_ed25519
              chown 1000:1000 /ssh-key/id_ed25519
              chmod 600 /ssh-key/id_ed25519
          volumeMounts:
            - {name: ssh-key-secret, mountPath: /secret-src, readOnly: true}
            - {name: ssh-key, mountPath: /ssh-key}
      containers:
        - name: hermes-agent
          image: hermes-agent:local
          imagePullPolicy: IfNotPresent
          # el entrypoint NO se sobreescribe (queda /init de s6-overlay), pero SÍ hace
          # falta pasar args=["gateway","run"] explícitamente — sin esto, el proceso
          # principal arranca el REPL interactivo de `hermes`, que sin una tty real
          # (fd=0 no es terminal) cierra la sesión sola e infla un crash-loop.
          args: ["gateway", "run"]
          env:
            - name: HERMES_UID
              value: "1000"   # ajusta a `id -u` de tu usuario real en pc-master
            - name: HERMES_GID
              value: "1000"   # ajusta a `id -g` de tu usuario real en pc-master
            - name: TERMINAL_SSH_HOST
              value: "127.0.0.1"   # con hostNetwork:true el pod comparte la red del host — localhost YA es pc-master
            - name: TERMINAL_SSH_USER
              value: "pedro"          # ajusta a tu usuario real
            - name: TERMINAL_SSH_KEY
              value: "/run/secrets/hermes-ssh/id_ed25519"
          volumeMounts:
            - {name: hermes-home, mountPath: /opt/data}   # mismo path que usa su docker-compose.yml oficial
            - {name: ssh-key, mountPath: /run/secrets/hermes-ssh, readOnly: true}
          resources:
            requests: {cpu: "250m", memory: 512Mi}
            limits: {memory: 1Gi}
      volumes:
        - name: hermes-home
          persistentVolumeClaim: {claimName: hermes-master-home}
        - name: ssh-key-secret
          secret:
            secretName: hermes-ssh-key
        - name: ssh-key
          emptyDir: {}
```
```bash
kubectl apply -f hermes-agent-master.yaml
kubectl -n hermes-agents rollout status deployment/hermes-agent-master
```

> Si el pod queda `Pending`/rechazado por el admission controller con un error de tipo "hostNetwork not allowed", tu Pod Security Admission está en modo `restricted` para ese namespace — hace falta etiquetar `hermes-agents` como `pod-security.kubernetes.io/enforce=privileged` (`kubectl label ns hermes-agents pod-security.kubernetes.io/enforce=privileged`). En un k3s recién instalado sin PSA configurado explícitamente esto no debería pasar.

#### 1.5 Primer uso: configurar el backend SSH y probar

El proceso principal del contenedor ya es el `gateway` de Hermes (arrancado por `/init`) — el binario `hermes` sigue disponible para usarlo por CLI en paralelo, apuntando a los mismos datos en `/opt/data`:

```bash
kubectl exec -it deploy/hermes-agent-master -n hermes-agents -- bash

# dentro del pod:
hermes config set terminal.backend ssh
hermes   # arranca la sesión interactiva de CLI (independiente del proceso gateway)

# dentro de la sesión de Hermes, pedile algo que toque un archivo real, ej:
# "listá los archivos de ~/Documentos/Projects/jarvis_project"
# si ves tus archivos reales, el backend SSH está funcionando.
```

Con esto ya tenés un Hermes usable hoy, con memoria persistente (sobrevive reinicios del pod porque `/opt/data` está en el PVC) y acceso real a tu entorno de desarrollo. Todavía usa el modelo/provider que traiga por defecto — eso se ajusta en la Fase 2.

---

### Fase 2 — Codex OAuth + Profiles (`main` / `heavy`) dentro de pc-master

Requiere que la Fase 1 esté funcionando (pod corriendo, PVC montado).

#### 2.1 Crear los dos perfiles

```bash
kubectl exec -it deploy/hermes-agent-master -n hermes-agents -- bash

hermes profile create main
hermes profile create heavy --description "Diseño, investigación profunda, debugging — tareas que requieren razonamiento fuerte"
```

#### 2.2 Configurar `main` contra LiteLLM (modelo del día a día)

```bash
hermes profile switch main
hermes config set model.provider custom
hermes config set model.base_url http://litellm.llms.svc.cluster.local:4000/v1
hermes config set model.default qwen3
hermes config set model.context_length 131072   # debe coincidir con --max-model-len de vllm.yaml (extendido vía YaRN, ver troubleshooting en spec 001 A8); con menos, el propio system prompt de Hermes (28 tools + 65 skills, ~18-25k tokens) más max_tokens=65536 fijo no entran
```

#### 2.3 Login de Codex OAuth en el perfil `heavy`

```bash
hermes profile switch heavy
hermes model
# elegís "Codex OAuth" en el menú
```

Hermes va a imprimir una URL corta + código. Abrí esa URL en cualquier navegador (tu celular sirve, no hace falta estar en la misma red) y aprobá el acceso con tu cuenta de ChatGPT Plus/Pro. El token queda guardado dentro del perfil `heavy`, bajo `/opt/data` (el PVC), así que sobrevive reinicios del pod.

Verificá:
```bash
hermes profile switch heavy
hermes   # sesión interactiva, probá una pregunta simple
```

#### 2.4 Confirmar que el enrutamiento entre perfiles funciona

```bash
hermes profile switch main
hermes
# dentro de la sesión: pedile algo claramente pesado, ej.
# "necesito que investigues a fondo tal librería y me expliques sus trade-offs de diseño"
# fijate si delega a `heavy` (delegate_task) o si tenés que pedirlo explícito la primera vez
```

Si no delega solo, anotalo — es el ítem "pendiente de verificar" del spec (B3): puede que haga falta pedir la delegación de forma explícita en vez de confiar en la clasificación automática.

---

### Fase 3 — Fallback de réplicas hacia las Raspberry Pi

Esto le da a las réplicas de RPi (`hermes-agent`, spec 001 B8) un camino cuando `pc-master`/vLLM no responde. No depende de las Fases 1-2.

#### 3.1 Elegir y etiquetar la RPi dedicada al modelo de fallback

```bash
# desde pc-master, elegí una RPi que NO sea la que ya corre hermes-agent si querés aislar carga
kubectl label node rpi-2 workload=agent-fallback
```

#### 3.2 Desplegar Ollama con un modelo chico en esa RPi

```yaml
# ollama-fallback.yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: ollama-models
  namespace: hermes-agents
spec:
  accessModes: ["ReadWriteOnce"]
  storageClassName: local-path
  resources:
    requests:
      storage: 10Gi
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ollama-fallback
  namespace: hermes-agents
spec:
  replicas: 1
  selector: {matchLabels: {app: ollama-fallback}}
  template:
    metadata: {labels: {app: ollama-fallback}}
    spec:
      nodeSelector:
        workload: agent-fallback
      containers:
        - name: ollama
          image: ollama/ollama:latest
          ports: [{containerPort: 11434}]
          volumeMounts:
            - {name: models, mountPath: /root/.ollama}
          resources:
            requests: {cpu: "1", memory: 2Gi}
            limits: {memory: 4Gi}
      volumes:
        - name: models
          persistentVolumeClaim: {claimName: ollama-models}
---
apiVersion: v1
kind: Service
metadata:
  name: ollama-fallback
  namespace: hermes-agents
spec:
  selector: {app: ollama-fallback}
  ports: [{port: 11434, targetPort: 11434}]
  type: ClusterIP
```
```bash
kubectl apply -f ollama-fallback.yaml
kubectl -n hermes-agents rollout status deployment/ollama-fallback

# descargar el modelo chico (una vez desplegado el pod)
kubectl -n hermes-agents exec deploy/ollama-fallback -- ollama pull qwen2.5:1.5b
```

#### 3.3 Sumar el escalón de fallback a LiteLLM

Edita el ConfigMap `litellm-config` (spec 001, A9) agregando esta entrada a `model_list`:

```yaml
      - model_name: qwen-fallback-rpi
        litellm_params:
          model: openai/qwen2.5:1.5b
          api_base: http://ollama-fallback.hermes-agents.svc.cluster.local:11434/v1
          api_key: "not-needed"
```

Y en `router_settings` (o el campo de fallback que uses en tu versión de LiteLLM):
```yaml
    router_settings:
      fallbacks:
        - qwen3: ["qwen-fallback-rpi"]
```
```bash
kubectl apply -f litellm-config.yaml
kubectl -n llms rollout restart deployment/litellm
```

#### 3.4 Probar el fallback

```bash
# apaga (o escala a 0) vllm para simular la caída del master
kubectl -n llms scale deployment/vllm --replicas=0

# pegale al gateway pidiendo el modelo primario
curl http://192.168.1.240:4000/v1/chat/completions \
  -H "Authorization: Bearer sk-CAMBIA-ESTA-CLAVE" \
  -H "Content-Type: application/json" \
  -d '{"model": "qwen3", "messages": [{"role":"user","content":"hola"}]}'
# debería responder igual, sirviéndose desde qwen-fallback-rpi

# restaurar
kubectl -n llms scale deployment/vllm --replicas=1
```

---

### Fase 4 — Temas restantes

#### 4.1 Wake-on-LAN

```bash
# 1. confirmar soporte WOL en BIOS de pc-master (reiniciar, revisar en la config de energía/APM)
# 2. confirmar que el driver de la NIC lo soporta:
sudo ethtool eth0 | grep -A1 "Supports Wake-on"
# debe listar "g" en "Supports Wake-on" y "g" en "Wake-on" (si no, activarlo):
sudo ethtool -s eth0 wol g

# 3. anotar la MAC:
ip link show eth0 | grep ether
```

Con la MAC confirmada, el microservicio `wol-trigger` (diseño en Parte A4 del spec) queda como una tarea de implementación aparte — no depende de las Fases 1-3 y se puede hacer en paralelo.

#### 4.2 Proveedor del cloud-fallback (A2)

Pendiente de decisión — sacar una API key de OpenAI o Anthropic (pago por uso, **no** tu suscripción ChatGPT Plus/Pro) y agregarla como `Secret` en `llms` cuando se defina.

#### 4.3 External memory provider (compartir memoria entre perfiles `main`/`heavy`)

Investigar `hermes memory setup` cuando las Fases 1-3 estén estables — no es bloqueante para el uso diario.

---

### Fase 5 — Conectar Telegram (Parte D)

Independiente de las Fases 1-4; solo requiere que el pod `hermes-agent-master` esté corriendo (Fase 1).

#### 5.1 Crear el bot y conseguir tu user ID
1. En Telegram, hablale a **@BotFather** → `/newbot` → elegí nombre y username terminado en `bot`. Guardá el token que te da.
2. Hablale a **@userinfobot** para conseguir tu ID numérico.

#### 5.2 Configurar y reiniciar

```bash
kubectl exec -it deploy/hermes-agent-master -n hermes-agents -- bash

cat >> /opt/data/.env <<'ENVEOF'
TELEGRAM_BOT_TOKEN=<tu-token-de-botfather>
TELEGRAM_ALLOWED_USERS=<tu-id-numerico>
ENVEOF
exit

kubectl -n hermes-agents rollout restart deployment/hermes-agent-master
kubectl -n hermes-agents rollout status deployment/hermes-agent-master
```

#### 5.3 Verificar

```bash
kubectl -n hermes-agents logs deploy/hermes-agent-master --tail=30 | grep -i telegram
```
Mandale un mensaje al bot desde Telegram — debería responder directo (ya está en la allowlist).
