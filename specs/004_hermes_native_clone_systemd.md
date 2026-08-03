# 004 - Instalacion nativa y clonacion segura de Hermes con systemd

## Relacion con los specs anteriores

Este spec extiende los specs 001, 002 y 003. Cambia el lugar donde corre la
instancia principal de Hermes:

- Kubernetes sigue sirviendo vLLM y LiteLLM.
- Hermes principal corre directamente en el host mediante systemd, como el
  usuario local y con `terminal.backend: local`.
- Las Raspberry Pi y servidores adicionales reciben una instalacion nativa
  reproducible, pero no una copia concurrente e indiscriminada de todos los
  tokens, sesiones y bases de datos del agente principal.

El Deployment `hermes-agent-master` del spec 002 queda como mecanismo de
rollback durante la transicion. No debe ejecutarse al mismo tiempo que la
instancia systemd si ambos usan el mismo bot de Telegram.

El manifiesto base `kubernetes/hermes/hermes-agent-master.yaml` debe declarar
`replicas: 0` mientras systemd sea el primary. El rollback a una replica es una
accion operativa explicita; el estado seguro del repo nunca debe arrancar un
segundo consumidor del bot mediante un `kubectl apply` rutinario.

## Objetivo

Definir el procedimiento y el contrato de un futuro script que permita:

1. Instalar Hermes Agent nativo en un servidor `amd64` o una Raspberry Pi
   `arm64`.
2. Aplicar la configuracion declarativa, SOUL, skills custom y los nueve
   perfiles Codex definidos en el spec 003.
3. Restaurar opcionalmente memoria y estado desde una instancia existente.
4. Instalar el gateway como servicio systemd ejecutado por un usuario sin
   privilegios.
5. Mantener secretos, OAuth y estado mutable fuera de Git.
6. Evitar que varias maquinas consuman simultaneamente el mismo bot de Telegram
   o escriban sobre la misma base SQLite.

## Estado verificado de la migracion inicial

La migracion realizada el 2026-07-28 produjo esta linea base:

| Elemento | Estado verificado |
| --- | --- |
| Host | `trantor`, CachyOS/Arch, `amd64` |
| Usuario de servicio | `pedro`, UID/GID 1000 |
| Version de Hermes | `0.19.0`, instalacion Git nativa |
| Directorio | `/home/pedro/.hermes` |
| Ejecutable | `/home/pedro/.local/bin/hermes` |
| Modelo default | `qwen3` mediante LiteLLM |
| LiteLLM nativo | `http://192.168.1.241:4000/v1` |
| Terminal | `local`, ejecutado como `pedro` |
| Memoria | `MEMORY.md`, `USER.md` y 39 sesiones restauradas |
| Perfiles | Nueve perfiles Luna/Terra/Sol restaurados |
| SQLite | 3.53.1 en el runtime nativo; tres DB con `integrity_check=ok` |
| Telegram | Token valido, modo polling, sin webhook |
| Kubernetes | `Deployment/hermes-agent-master` escalado a 0 |
| systemd | Pendiente de instalar al momento de escribir este spec |

Respaldos locales creados durante la migracion:

```text
/tmp/hermes-local-before-migration-20260728.tar.gz
/tmp/hermes-pod-live-backup-20260728.zip
/tmp/hermes-pvc-cold-20260728.tar.gz
```

El backup frio del PVC contiene 9,306 entradas, ocupa 88 MiB comprimido y fue
validado con `gzip --test`. Su SHA-256 verificado es:

```text
76920a243e882a1c6844a5fbee5cb90b6d783872a34115c310a75f3d1e8ff28c
```

Los archivos bajo `/tmp` no son una estrategia de backup permanente. Deben
moverse a almacenamiento cifrado o eliminarse despues de confirmar la
migracion, porque pueden contener tokens, OAuth, memoria personal y sesiones.

## Decisiones de arquitectura

### Hermes nativo por host

Cada maquina tiene su propio:

```text
~/.hermes/
├── config.yaml
├── SOUL.md
├── .env
├── auth.json
├── memories/
├── profiles/
├── sessions/
├── skills/
└── state.db
```

No se monta `/home/pedro` desde Kubernetes ni se comparte `state.db` por NFS.
Hermes accede a los archivos y herramientas del host directamente como el
usuario configurado en systemd.

### LiteLLM permanece centralizado

Las instalaciones nativas no pueden depender del DNS interno
`*.svc.cluster.local`, salvo que el host tenga una integracion DNS explicita con
Kubernetes. Deben usar la IP de LAN de MetalLB o un DNS estable de la LAN:

```yaml
model:
  base_url: https://litellm.home.arpa/v1
```

El futuro script recibe esta URL como parametro `--litellm-url`. No debe
hardcodear la IP observada en `trantor`.

### Un solo gateway por identidad de mensajeria

Telegram usa long polling. Dos gateways con el mismo `TELEGRAM_BOT_TOKEN`
compiten por `getUpdates` y Telegram termina una de las conexiones. Por eso:

- Solo el rol `primary` arranca automaticamente con el token del bot principal.
- Un `standby` puede tener un respaldo cifrado, pero su gateway queda detenido
  y deshabilitado hasta ser promovido.
- Un `worker` RPi no recibe el token principal. Puede no ejecutar gateway o usar
  un bot diferente.
- Antes de promover un standby se debe comprobar que el primary anterior esta
  apagado o que su gateway esta detenido.

### Estado mutable no compartido

SQLite, sesiones y memoria no admiten sincronizacion bidireccional entre hosts.
Se permiten tres modos de provisionamiento:

| Modo | Contenido | Uso |
| --- | --- | --- |
| `bootstrap` | Config, SOUL, profiles y skills; sin secretos ni sesiones | Nueva RPi o servidor independiente |
| `memory-seed` | `bootstrap` + `MEMORY.md` y `USER.md` | Nueva instancia con contexto inicial, que luego diverge |
| `primary-restore` | Backup completo en frio, incluido estado y credenciales | Recuperacion o traslado del unico primary |

`primary-restore` nunca debe ejecutarse sobre dos gateways activos a la vez.

## Clasificacion de archivos

| Ruta | Git | Backup cifrado | Clonar a workers |
| --- | --- | --- | --- |
| `config.yaml` | Si, sin secretos | Si | Si, con overlay por host |
| `SOUL.md` | Si | Si | Si |
| Skills custom | Si | Si | Si |
| `profiles.yaml` | Si | Si | Si |
| `.env` | No | Si | No; crear uno por host |
| `auth.json` | No | Si | No por defecto |
| `memories/MEMORY.md` | Opcional | Si | Solo con `memory-seed` |
| `memories/USER.md` | Opcional | Si | Solo con `memory-seed` |
| `state.db` | No | Si | Solo `primary-restore` |
| `sessions/` | No | Si | Solo `primary-restore` |
| `cron/` y `kanban.db` | No | Si | Solo `primary-restore` |
| `logs/`, caches y locks | No | No necesario | No |
| `hermes-agent/`, `venv/` | No | No | Reinstalar por arquitectura |

Nunca copiar `venv/`, binarios o `node_modules` entre `amd64` y `arm64`.

## Estructura objetivo del repositorio

El futuro script debe apoyarse en una estructura independiente de Kubernetes:

```text
hermes-native/
├── config/
│   ├── config.yaml
│   └── SOUL.md
├── profiles/
│   └── profiles.yaml
├── skills/
│   └── <skills-custom-versionadas>/
├── env/
│   └── hermes.env.example
├── vendor/
│   └── install-hermes.sh
└── scripts/
    ├── install-hermes.sh
    ├── reconcile-profiles.sh
    ├── backup-hermes.sh
    └── promote-hermes.sh
```

Mientras esa estructura se implementa, las fuentes declarativas actuales son:

```text
kubernetes/hermes/config/config.yaml
kubernetes/hermes/config/SOUL.md
kubernetes/hermes/profiles/profiles.yaml
```

`kubernetes/hermes/profiles/bootstrap-profiles.sh` no se puede reutilizar tal
cual: ejecuta todos los comandos mediante `kubectl`. Debe extraerse la logica de
reconciliacion a un script local y dejar el script Kubernetes como wrapper.

Los skills custom actuales deben exportarse y versionarse antes de considerar
completo el instalador. Entre los detectados en la instancia migrada estan:

```text
skills/deep-research/
skills/web-search-fallback/
skills/autonomous-agents/deep-research-subagent/
skills/subagent-isolation-pattern/
```

No copiar automaticamente todo `skills/`: Hermes instala 69 skills bundled y
puede actualizarlos. El repo debe contener solo skills propias o modificadas.

## Contrato del futuro instalador

Nombre propuesto:

```bash
hermes-native/scripts/install-hermes.sh
```

Interfaz minima:

```text
install-hermes.sh \
  --role primary|standby|worker \
  --mode bootstrap|memory-seed|primary-restore \
  --user <usuario> \
  --litellm-url <url> \
  --hermes-commit <sha-completo> \
  [--state-archive <archivo>] \
  [--state-sha256 <sha256>] \
  [--state-manifest <archivo>] \
  [--memory-seed <directorio>] \
  [--force-restore] \
  [--enable-browser] \
  [--allow-insecure-http] \
  [--start]
```

Variables equivalentes para automatizacion no interactiva:

```text
HERMES_ROLE
HERMES_MODE
HERMES_USER
HERMES_HOME
HERMES_COMMIT
HERMES_LITELLM_URL
HERMES_STATE_ARCHIVE
HERMES_STATE_SHA256
HERMES_STATE_MANIFEST
HERMES_FORCE_RESTORE
HERMES_START
```

Reglas obligatorias:

1. Usar `set -euo pipefail`.
2. Rechazar ejecucion como root para la instalacion del runtime; usar root solo
   al instalar la unidad systemd y paquetes del sistema.
3. Detectar `uname -m` y la distribucion desde `/etc/os-release`.
4. Ser idempotente: una segunda ejecucion reconcilia, no destruye memoria.
5. Crear backup antes de sobrescribir cualquier `~/.hermes` existente.
6. No imprimir valores de `.env`, `auth.json` ni tokens en logs.
7. No aceptar secretos por argumentos de proceso. Leerlos desde un archivo
   `0600`, stdin interactivo o un gestor de secretos.
8. Fijar una revision de Hermes mediante un SHA Git completo. La version de
   referencia es `v0.19.0`; el commit debe validarse en `amd64` y `arm64` al
   convertir este spec en script.
9. No iniciar el servicio si falla `hermes doctor`, la conectividad de LiteLLM
   o la integridad del estado restaurado.
10. Nunca activar Telegram en `standby` o `worker` con el token del primary.
11. Resolver una sola vez el home real del usuario mediante la base de cuentas,
    definir `SERVICE_HOME` y `HERMES_HOME="$SERVICE_HOME/.hermes"`, y usar esas
    rutas en todos los pasos. No mezclar `$HOME` del invocador, `/root` y el home
    del usuario de servicio.
12. Instalar y reconciliar el runtime como `HERMES_USER`; usar sudo solo para
    paquetes y la unidad de sistema.
13. Dejar cualquier gateway detenido por defecto. `--start` solo se acepta para
    `role=primary` despues de una comprobacion de fencing.
14. Rechazar combinaciones incompatibles: `bootstrap` no acepta archive,
    `memory-seed` requiere seed y `primary-restore` requiere archive, SHA-256 y
    manifest de inventario.
15. Aplicar un seed solo sobre un destino no inicializado. Repetir un restore
    requiere `--force-restore` y confirmacion explicita.

## Procedimiento paso a paso

### Fase 0 - Preflight

Resolver primero las rutas del usuario destino. Ejemplo conceptual:

```bash
HERMES_USER="<usuario>"
SERVICE_HOME="$(getent passwd "$HERMES_USER" | cut -d: -f6)"
HERMES_HOME="$SERVICE_HOME/.hermes"
HERMES_BIN="$SERVICE_HOME/.local/bin/hermes"
test -n "$HERMES_HOME"
test -d "$SERVICE_HOME"
```

Ejecutar el preflight en el contexto de ese usuario:

```bash
id
uname -m
uname -s
getent hosts github.com
curl --fail --silent --show-error --max-time 10 \
  "${HERMES_LITELLM_URL%/v1}/health/readiness"
```

Validar:

- Usuario no root y home escribible.
- Arquitectura `x86_64`, `aarch64` o `arm64`.
- DNS y HTTPS hacia GitHub/Nous Research.
- LiteLLM accesible desde el host destino.
- Hora sincronizada con NTP; OAuth y TLS fallan con relojes desviados.
- Al menos 4 GiB libres sin browser. Si se habilita Chromium, calcular y exigir
  margen adicional antes de instalarlo.
- La URL de LiteLLM es HTTPS. Para mantener temporalmente el HTTP actual de la
  LAN se requiere `--allow-insecure-http` y una key revocable distinta por host;
  nunca la master key compartida.

El script debe abortar antes de modificar archivos si el preflight falla.

### Fase 1 - Dependencias del sistema

#### Debian, Ubuntu y Raspberry Pi OS 64-bit

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl git jq unzip sqlite3 ffmpeg ripgrep
```

#### Arch y CachyOS

```bash
sudo pacman -S --needed ca-certificates curl git jq unzip sqlite ffmpeg ripgrep
```

El instalador oficial administra su propio `uv`, Python y entorno virtual. No
debe reutilizar Python ni paquetes globales del sistema.

El browser es opt-in mediante `--enable-browser` en servidores. En `arm64`
permanece deshabilitado por defecto y el script debe reportar la capacidad como
no disponible si Playwright/Chromium no es compatible con la combinacion de SO
y arquitectura. No debe marcar toda la instalacion como fallida si CLI,
gateway y terminal funcionan.

### Fase 2 - Respaldar una instalacion local existente

No crear un tar crudo mientras el gateway escribe SQLite. Crear un directorio
privado y usar la herramienta oficial:

```bash
umask 077
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_dir="$HERMES_HOME/backups-before-install"
install -d -m 700 "$backup_dir"
"$HERMES_BIN" backup \
  --output "$backup_dir/hermes-before-install-${timestamp}.zip"
unzip -t "$backup_dir/hermes-before-install-${timestamp}.zip"
chmod 600 "$backup_dir/hermes-before-install-${timestamp}.zip"
sha256sum "$backup_dir/hermes-before-install-${timestamp}.zip"
```

Capturar la salida del comando y fallar si contiene `Backup incomplete` o
warnings de archivos omitidos. Extraer el ZIP en staging y verificar contra un
inventario propio las DB y rutas criticas; `unzip -t` por si solo solo demuestra
que el contenedor ZIP abre.

Para una garantia fuerte antes de reemplazar estado, detener primero el gateway
y crear una copia fria allowlisted. Solo se omite el backup cuando
`HERMES_HOME` no existe o esta realmente vacio. Si hay datos pero el CLI no esta
disponible, no continuar: detener escritores, crear el backup frio con el helper
versionado y validar manifest + DB. Antes de `--force-restore`, la copia fria es
obligatoria. No incluir checkout, venv, browser, `node_modules` ni caches
regenerables. Cifrar el backup antes de sacarlo del host.

### Fase 3 - Instalar el runtime nativo

El script remoto de instalacion es mutable. Para automatizacion debe guardarse
una copia revisada bajo `hermes-native/vendor/install-hermes.sh` y versionar su
SHA-256. No ejecutar un `curl | bash` ni confiar en que `--commit` protege el
codigo que corre antes del checkout.

```bash
install_script="hermes-native/vendor/install-hermes.sh"
printf '%s  %s\n' "$EXPECTED_INSTALLER_SHA256" "$install_script" | sha256sum -c -
chmod 700 "$install_script"
```

Para `amd64`, instalacion headless por defecto:

```bash
bash "$install_script" \
  --skip-setup \
  --non-interactive \
  --skip-browser \
  --hermes-home "$HERMES_HOME" \
  --commit "$HERMES_COMMIT"
```

Para RPi `arm64`:

```bash
bash "$install_script" \
  --skip-setup \
  --non-interactive \
  --skip-browser \
  --hermes-home "$HERMES_HOME" \
  --commit "$HERMES_COMMIT"
```

Si `--enable-browser` fue solicitado y el preflight de dependencias/espacio
termino correctamente, omitir `--skip-browser` solo en una arquitectura
soportada.

Verificar inmediatamente:

```bash
"$HERMES_BIN" --version
test -x "$HERMES_HOME/hermes-agent/venv/bin/hermes"
test "$(git -C "$HERMES_HOME/hermes-agent" rev-parse HEAD)" = "$HERMES_COMMIT"
```

El script debe ejecutar esta fase como `HERMES_USER`, comprobar el SHA completo
y guardar la version en su log, sin guardar secretos.

### Fase 4 - Restaurar en staging, si corresponde

Esta fase ocurre antes de aplicar config nativa, skills y perfiles. Nunca
extraer un archive sin validar directamente sobre `HERMES_HOME`.

#### Modo `bootstrap`

No restaurar `state.db`, sesiones, OAuth ni `.env`. Es el modo por defecto para
RPi y nuevos servidores.

#### Modo `memory-seed`

Solo se permite si `memories/MEMORY.md` y `memories/USER.md` todavia no existen.
Copiar ambos desde un directorio validado, asignar propietario al usuario de
servicio y permisos `0600`. Registrar el checksum aplicado. Desde ese punto,
cada maquina mantiene su propia memoria.

#### Modo `primary-restore`

Requisitos previos:

1. Gateway local y primary anterior detenidos.
2. Deployment Kubernetes escalado a cero.
3. `--state-archive`, `--state-sha256` y `--state-manifest` presentes.
4. Destino respaldado antes de restaurar.
5. Destino no inicializado, salvo uso explicito de `--force-restore`.

Validar el digest antes de abrir el archive:

```bash
printf '%s  %s\n' "$HERMES_STATE_SHA256" "$HERMES_STATE_ARCHIVE" | \
  sha256sum -c -
```

Extraer en un directorio temporal `0700` ubicado en el mismo filesystem que
`HERMES_HOME`. Antes de copiar, rechazar:

- Rutas absolutas o componentes `..`.
- Symlinks, hardlinks, devices, sockets y FIFOs no esperados.
- `hermes-agent/`, `venv/`, browser, `node_modules/`, caches y binarios.
- PID, locks y `gateway_state.json` del host anterior.
- Archivos que no pertenezcan al inventario firmado del backup.

El manifest del backup debe declarar archivos, tamaños, modos y SHA-256, ademas
de la lista de DB esperadas. Debe validarse contra un digest o firma obtenidos
por un canal distinto al archive. Un tar frio del PVC puede contener mas datos
que el destino nativo; se copia desde staging mediante una allowlist de estado:

```text
SOUL.md
auth.json
memories/
sessions/
skills/ (solo rutas custom declaradas por el manifest)
profiles/
cron/
kanban.db
state.db
channel_directory.json
pairing/
state/
```

`.env` no se activa desde el archive: se inyecta en la Fase 8 desde un secreto
separado. Los skills bundled se reinstalan; solo se promueven skills custom
identificados por el manifest.

Comprobar en staging todas las bases declaradas por el manifest, incluidas las
que existan dentro de perfiles:

```bash
find "$restore_staging" -type f -name '*.db' -print0 | \
  while IFS= read -r -d '' db; do
    test "$(sqlite3 -readonly "$db" 'PRAGMA integrity_check;')" = "ok"
  done
```

Una DB esperada pero ausente es error. Despues de validar, reemplazar de forma
atomica solo las rutas allowlisted y volver a verificar todas las DB ya en el
destino. Registrar el digest en
`$HERMES_HOME/.restore-history/<sha256>.json`. Una segunda ejecucion con el
mismo digest se omite; aplicar otro archive sobre estado existente requiere
`--force-restore`.

### Fase 5 - Aplicar configuracion declarativa

Copiar de forma atomica `config.yaml` y `SOUL.md` despues del restore. Antes de
reemplazarlos, guardar copias con timestamp dentro de un directorio `0700`
fuera de Git.

Aplicar los overlays por host usando el binario y home resueltos:

```bash
HERMES_HOME="$HERMES_HOME" "$HERMES_BIN" config set \
  model.base_url "$HERMES_LITELLM_URL"
HERMES_HOME="$HERMES_HOME" "$HERMES_BIN" config set terminal.backend local
```

El archivo `.env` debe contener tambien:

```dotenv
TERMINAL_ENV=local
```

Esto es necesario porque `TERMINAL_ENV=ssh` tiene precedencia sobre
`config.yaml`. Durante la migracion inicial esa variable hizo que
`hermes status` siguiera mostrando SSH aunque el YAML ya indicaba `local`.

Rechazar o eliminar del secreto nativo estas variables obsoletas:

```text
TERMINAL_SSH_HOST
TERMINAL_SSH_USER
TERMINAL_SSH_KEY
```

### Fase 6 - Instalar skills custom

Sincronizar los skills custom antes de clonar perfiles:

```text
hermes-native/skills/* -> $HERMES_HOME/skills/*
```

Reglas:

- No borrar skills que Hermes haya creado localmente.
- No reemplazar todo `$HERMES_HOME/skills`.
- Registrar por checksum que archivos se agregaron o actualizaron.
- Fallar si un skill versionado no contiene su `SKILL.md` requerido.
- No colocar API keys dentro de `SKILL.md`, scripts o referencias.

Los MCP se configuran declarativamente, pero sus credenciales se inyectan por
`.env`. Brave debe usar una clave nueva: la anterior estuvo escrita en texto
plano dentro del Deployment y debe considerarse expuesta.

### Fase 7 - Reconciliar perfiles Codex

El script local debe usar
`kubernetes/hermes/profiles/profiles.yaml` como fuente de verdad y reproducir la
matriz del spec 003. Al instalar skills primero, un perfil nuevo los recibe al
clonar el default.

Para cada perfil:

1. Crear con `hermes profile create <nombre> --clone-from default --no-alias`
   solo si no existe.
2. Eliminar el `.env` clonado solo dentro de la misma transaccion de creacion;
   nunca eliminar un `.env` preexistente en una reconciliacion posterior.
3. Configurar `model.provider=openai-codex`.
4. Configurar `model.base_url=https://chatgpt.com/backend-api/codex`.
5. Configurar modelo y `agent.reasoning_effort` segun `profiles.yaml`.
6. Configurar `terminal.backend=local`.
7. Aplicar la descripcion declarativa.
8. No copiar `auth.json` ni tokens a cada perfil.

Ejemplo conceptual:

```bash
profile_home="$HERMES_HOME/profiles/terra-medium"
HERMES_HOME="$profile_home" "$HERMES_BIN" config set model.provider openai-codex
HERMES_HOME="$profile_home" "$HERMES_BIN" config set \
  model.base_url https://chatgpt.com/backend-api/codex
HERMES_HOME="$profile_home" "$HERMES_BIN" config set \
  model.default gpt-5.6-terra
HERMES_HOME="$profile_home" "$HERMES_BIN" config set --force \
  agent.reasoning_effort medium
HERMES_HOME="$profile_home" "$HERMES_BIN" config set terminal.backend local
```

Parsear YAML con PyYAML desde el entorno administrado por Hermes o con `yq`, no
mediante expresiones regulares.

La revision fijada de Hermes debe contener la correccion de fallback del
credential pool descrita en el spec 003. En cualquier host, validar por test de
codigo que el runtime contiene esa ruta de fallback. Si la revision oficial no
incluye la correccion, se debe mantener y verificar el parche versionado antes
de instalar.

Autenticar Codex una sola vez en cada maquina autorizada:

```bash
HERMES_HOME="$HERMES_HOME" "$HERMES_BIN" auth
```

Solo despues de autenticar un host autorizado, ejecutar para los nueve perfiles
la validacion sin inferencia del spec 003 y comprobar que la fuente sea el pool
root. En un worker deliberadamente no autenticado, los perfiles quedan
instalados como `unauthenticated` y esto no falla el bootstrap; cualquier
intento de inferencia Codex debe fallar cerrado con una instruccion de login.

No copiar automaticamente `auth.json` del primary a las RPi. Si una RPi necesita
Codex, autenticarla explicitamente y considerar cuota, concurrencia y politicas
del proveedor.

### Fase 8 - Crear `.env` sin exponer secretos

Crear el archivo con permisos restrictivos sin truncar uno existente:

```bash
if [ ! -e "$HERMES_HOME/.env" ]; then
  install -m 600 /dev/null "$HERMES_HOME/.env"
else
  chmod 600 "$HERMES_HOME/.env"
fi
```

Variables esperadas para el primary:

```dotenv
TERMINAL_ENV=local
OPENAI_API_KEY=<clave-de-LiteLLM-o-placeholder-requerido>
TELEGRAM_BOT_TOKEN=<token-del-primary>
TELEGRAM_ALLOWED_USERS=<ids-numericos>
BRAVE_API_KEY=<clave-rotada>
```

El ejemplo versionado debe contener nombres y comentarios, nunca valores
reales. El script puede recibir `--env-file`, pero debe parsearlo como dotenv,
no ejecutarlo como shell, validar las keys permitidas para el rol y copiarlo con
modo `0600` sin imprimir su contenido. El directorio `HERMES_HOME`,
`credentials/` y cualquier store de secretos deben quedar `0700`; `.env` y
`auth.json`, `0600`.

Politica por rol:

| Variable | primary | standby | worker |
| --- | --- | --- | --- |
| `TERMINAL_ENV` | `local` | `local` | `local` |
| `OPENAI_API_KEY` | Si | Si | Si |
| `TELEGRAM_BOT_TOKEN` principal | Si | Guardado solo cifrado | No |
| `TELEGRAM_ALLOWED_USERS` | Si | Junto al token cifrado | No |
| `BRAVE_API_KEY` | Opcional | Opcional | Opcional |

El archivo activo de un standby o worker debe ser rechazado si contiene el
token del bot principal. El standby conserva esos secretos unicamente fuera de
`HERMES_HOME`, en almacenamiento cifrado, hasta el momento de promocion.

### Fase 9 - Validacion previa a systemd

```bash
HERMES_HOME="$HERMES_HOME" "$HERMES_BIN" doctor
HERMES_HOME="$HERMES_HOME" "$HERMES_BIN" profile list
HERMES_HOME="$HERMES_HOME" "$HERMES_BIN" config get model.base_url
HERMES_HOME="$HERMES_HOME" "$HERMES_BIN" config get terminal.backend
HERMES_HOME="$HERMES_HOME" "$HERMES_BIN" -z 'Responde exactamente: OK'
```

Resultado minimo:

- SQLite moderno y sin advisory de WAL-reset.
- Perfil default y nueve perfiles Codex visibles.
- `model.base_url` accesible desde el host.
- Backend `local` tanto en config como en `hermes status`.
- One-shot responde `OK`.
- No hay errores de permisos sobre `~/.hermes`.

Telegram solo se valida para un primary que vaya a ser promovido. No hacer
`source` de `.env` ni expandir el token en el argv de `curl`. Usar un helper que
lea dotenv como datos y construya la request dentro del proceso. Ejemplo con el
Python administrado por Hermes:

```python
import json
import os
import urllib.request
from dotenv import dotenv_values

home = os.environ["HERMES_HOME"]
token = dotenv_values(os.path.join(home, ".env")).get("TELEGRAM_BOT_TOKEN")
if not token:
    raise SystemExit("TELEGRAM_BOT_TOKEN ausente")

for method in ("getMe", "getWebhookInfo"):
    url = f"https://api.telegram.org/bot{token}/{method}"
    with urllib.request.urlopen(url, timeout=15) as response:
        payload = json.load(response)
    if payload.get("ok") is not True:
        raise SystemExit(f"Telegram {method} fallo")
```

`getMe` debe devolver `"ok":true`. En este diseño `getWebhookInfo.url` queda
vacio porque Hermes usa polling. Standby y worker deben omitir esta request y
comprobar que no poseen el token principal activo.

### Fase 10 - Instalar systemd

#### Instalacion inicial, primary o standby

```bash
sudo "$HERMES_BIN" gateway install \
  --system \
  --run-as-user "$HERMES_USER" \
  --no-start-now \
  --no-start-on-login
```

Capturar de la salida el nombre real como `HERMES_UNIT`. Los flags `--no-*` no
son suficientes para reconciliar una unidad ya existente: deshabilitarla y
detenerla explicitamente, y hacer lo mismo con cualquier unidad user/legacy
detectada para ese usuario:

```bash
sudo systemctl disable --now "$HERMES_UNIT"
test "$(systemctl is-enabled "$HERMES_UNIT" 2>/dev/null || true)" = "disabled"
test "$(systemctl is-active "$HERMES_UNIT" 2>/dev/null || true)" = "inactive"
```

La instalacion siempre queda detenida. Para un primary, `--start` invoca el
flujo de promocion solo despues de demostrar fencing:

1. Deployment Kubernetes en cero.
2. Ningun pod, servicio systemd local/user, proceso manual o host standby usa el
   mismo token.
3. El operador confirma cual host sera el unico primary.
4. Secrets del primary ya inyectados y validados.

Despues, permitir que Hermes habilite e inicie su unidad:

```bash
sudo "$HERMES_BIN" gateway install \
  --force \
  --system \
  --run-as-user "$HERMES_USER" \
  --start-now \
  --start-on-login
```

Sin `--start`, incluso `role=primary` termina instalado pero detenido. Un
standby nunca acepta `--start` desde el instalador; se promueve mediante
`promote-hermes.sh`, que repite fencing y cambia primero su rol persistido.

#### Worker sin mensajeria ni cron

No instalar gateway. Hermes queda disponible por CLI, SSH, scripts o como
destino explicito de trabajo.

El script no debe construir manualmente una unidad si la version instalada de
Hermes proporciona `gateway install`. El CLI conoce la ruta real del entorno
virtual y el formato de unidad compatible con esa version.

Verificacion:

```bash
HERMES_HOME="$HERMES_HOME" "$HERMES_BIN" gateway status
systemctl list-unit-files 'hermes*' --no-pager
systemctl list-units 'hermes*' --all --no-pager
```

Usar el nombre de unidad que reporte el instalador. No asumirlo antes de
crearla. Resultado correcto por rol:

- Primary con `--start`: unidad habilitada y activa.
- Primary sin `--start`: unidad instalada, deshabilitada e inactiva.
- Standby: unidad instalada, deshabilitada e inactiva.
- Worker: unidad ausente.

### Fase 11 - Validacion funcional

Los pasos de Telegram y reinicio aplican solo al primary activo:

1. Enviar `ping` al bot desde un usuario incluido en
   `TELEGRAM_ALLOWED_USERS`.
2. Confirmar respuesta del agente.
3. Confirmar que no aparece un error de polling concurrente.
4. Ejecutar una consulta default contra Qwen/LiteLLM.
5. Si Codex fue autorizado en ese host, ejecutar una consulta corta con
   `terra-low` o `terra-medium`; si no, validar solo su configuracion sin
   inferencia.
6. Pedir una operacion de terminal no destructiva como `pwd` y comprobar que
   se ejecuta como el usuario del servicio.
7. Reiniciar el servicio y repetir Telegram:

```bash
sudo systemctl restart <unidad-reportada-por-hermes>
hermes gateway status
```

8. Reiniciar el host y verificar arranque automatico para el rol primary.

En standby se valida que la unidad permanezca deshabilitada/inactiva. En worker
se valida que no exista unidad y que el CLI pueda consultar LiteLLM.

## Migracion desde el PVC de Kubernetes

Este es el procedimiento reproducible usado en `trantor`.

### 1. Identificar el workload y PVC

```bash
kubectl -n hermes-agents get deployment hermes-agent-master -o yaml
kubectl -n hermes-agents get pvc hermes-master-home -o yaml
```

En la migracion real, el PVC `hermes-master-home` estaba montado en
`/opt/data`, era RWO `local-path` y vivia fisicamente en `trantor`.

### 2. Crear backup oficial como contingencia

```bash
pod="$(kubectl -n hermes-agents get pod \
  -l app=hermes-agent-master \
  -o jsonpath='{.items[0].metadata.name}')"

kubectl -n hermes-agents exec "$pod" -c hermes-agent -- \
  su -s /bin/sh hermes -c \
  '/opt/hermes/.venv/bin/hermes backup -o /opt/data/backups/pre-systemd-migration.zip'

kubectl -n hermes-agents cp \
  "$pod:/opt/data/backups/pre-systemd-migration.zip" \
  ./hermes-pod-live-backup.zip \
  -c hermes-agent
```

Este backup es contingencia, no sustituye la copia fria para SQLite.

### 3. Detener todas las escrituras

```bash
kubectl -n hermes-agents scale deployment/hermes-agent-master --replicas=0
kubectl -n hermes-agents wait \
  --for=delete pod/"$pod" \
  --timeout=120s
```

### 4. Montar el PVC en un pod temporal

El pod temporal debe usar el mismo PVC, node selector y toleration del workload
original. Ejemplo:

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: hermes-migration
  namespace: hermes-agents
spec:
  restartPolicy: Never
  nodeSelector:
    workload: llm
  tolerations:
    - key: dedicated
      operator: Equal
      value: llm
      effect: NoSchedule
  containers:
    - name: migration
      image: busybox:1.36
      command: ["sleep", "infinity"]
      volumeMounts:
        - name: hermes-home
          mountPath: /mnt/hermes
  volumes:
    - name: hermes-home
      persistentVolumeClaim:
        claimName: hermes-master-home
```

### 5. Crear y verificar el backup frio

```bash
umask 077
backup_dir="./hermes-migration-$(date -u +%Y%m%dT%H%M%SZ)"
install -d -m 700 "$backup_dir"

kubectl -n hermes-agents exec hermes-migration -- \
  tar -C /mnt/hermes -czf /tmp/hermes-pvc-cold.tar.gz .

kubectl -n hermes-agents cp \
  hermes-migration:/tmp/hermes-pvc-cold.tar.gz \
  "$backup_dir/hermes-pvc-cold.tar.gz"

chmod 600 "$backup_dir/hermes-pvc-cold.tar.gz"
gzip --test "$backup_dir/hermes-pvc-cold.tar.gz"
sha256sum "$backup_dir/hermes-pvc-cold.tar.gz" \
  > "$backup_dir/hermes-pvc-cold.tar.gz.sha256"
kubectl -n hermes-agents delete pod hermes-migration --wait=true
```

Antes de considerar util el artifact, extraerlo en staging `0700`, generar un
manifest allowlisted con SHA-256 por archivo y la lista de DB esperadas, y
ejecutar `PRAGMA integrity_check` sobre cada DB. Firmar el manifest o guardar su
digest por un canal separado. Cifrar archive, manifest y checksum antes de
copiarlos fuera del host. Borrar staging, la copia dentro del pod y los
plaintext locales solo despues de verificar el artifact cifrado.

No borrar el PVC ni el Deployment. Mantenerlos como rollback hasta completar
la validacion de systemd y un reinicio real del host.

## Particularidades de Raspberry Pi

1. Usar Raspberry Pi OS Lite 64-bit o Debian ARM64.
2. No restaurar runtime, venv, caches ni binarios provenientes de `amd64`.
3. Instalar con `--skip-browser` inicialmente.
4. Usar `bootstrap` o `memory-seed`, nunca `primary-restore` salvo que la RPi
   sea promovida formalmente como unico primary.
5. Configurar `model.base_url` con la IP/DNS LAN de LiteLLM, no con DNS de
   Kubernetes.
6. Mantener `terminal.backend: local`; los comandos se ejecutan sobre esa RPi,
   no sobre `trantor`.
7. Limitar concurrencia y tareas pesadas segun RAM/temperatura de la RPi.
8. No copiar el bot principal ni OAuth Codex por defecto.

Si se desea que una RPi controle otro host, eso es un rol distinto y debe usar
SSH con una clave dedicada y restringida. No debe mezclarse silenciosamente con
el modo local de este spec.

## Particularidades del servidor `amd64`

1. Instalar browser/Chromium solo si `hermes doctor` indica que faltan
   dependencias y la funcion se necesita.
2. Mantener el servicio como usuario no root.
3. No habilitar sudo general para Hermes. Las operaciones privilegiadas deben
   estar allowlisted mediante sudoers o una herramienta remota separada.
4. El usuario del servicio debe ser propietario de todo `~/.hermes`.
5. Si el servidor tambien ejecuta k3s, Hermes nativo sigue usando la IP de
   MetalLB para evitar depender del DNS de pods.

## Control remoto desde otro nodo

El control basico se realiza por SSH:

```bash
ssh pedro@trantor 'hermes gateway status'
ssh pedro@trantor 'sudo systemctl restart <unidad-hermes>'
ssh pedro@trantor 'systemctl is-active <unidad-hermes>'
```

No permitir sudo sin contraseña para comandos arbitrarios. Si se automatiza,
el sudoers debe autorizar solo `start`, `stop`, `restart` y `status` de la unidad
concreta de Hermes. Para varias maquinas, Ansible debe ser la capa de
orquestacion; el nodo Kubernetes master no debe montar homes remotos.

## Promocion de standby

Procedimiento obligatorio:

1. Confirmar que el primary anterior no puede ejecutar el gateway.
2. Obtener el ultimo backup frio o consistente disponible.
3. Verificar checksum y restaurar con el servicio standby detenido.
4. Inyectar secretos desde almacenamiento cifrado.
5. Ejecutar `hermes doctor` y los `PRAGMA integrity_check`.
6. Habilitar e iniciar la unidad systemd.
7. Validar Telegram y una inferencia.
8. Marcar el host anterior como standby antes de volverlo a encender.

No automatizar promocion solo con un ping fallido. Sin fencing existe riesgo de
split-brain: dos gateways y dos copias de memoria escribiendo al mismo tiempo.

## Backup periodico

En el primary:

```bash
HERMES_HOME="$HERMES_HOME" "$HERMES_BIN" backup \
  -o "$HERMES_HOME/hermes-backup-$(date -u +%Y%m%dT%H%M%SZ).zip"
```

El futuro `backup-hermes.sh` debe:

1. Usar `umask 077` y un directorio `0700`.
2. Crear el backup con la herramienta oficial y fallar si lo reporta incompleto.
3. Verificar que el ZIP abre correctamente.
4. Generar un manifest de archivos y DB esperadas.
5. Calcular SHA-256 y firmar el manifest.
6. Cifrar antes de copiar fuera del host.
7. Aplicar retencion por cantidad y antiguedad.
8. No subir backups a Git.

Para una migracion critica o antes de promover un standby, preferir una copia
en frio con el gateway detenido.

## Fencing del knowledge vault

El vault canonico (Obsidian) vive en el host y comparte el modelo de autoridad
de este spec: Kubernetes es plano de control, systemd es primary.

Invariantes:

1. **Un solo escritor.** Solo la unidad `knowledge-vault-publisher.service`
   escribe el vault canonico. Toma un `flock` no bloqueante sobre
   `/var/lib/knowledge-vault/publisher/publisher.lock`; una segunda instancia
   falla en vez de escribir.
2. **Sin segundo gateway.** `knowledge-proposals-api` no tiene token de
   Telegram, no usa `hostNetwork` y no reemplaza al gateway de Hermes. La
   relacion con `hermes-agent-master.yaml` no cambia: sigue en `replicas: 0`.
3. **El cluster no toca el vault.** Los manifiestos de
   `kubernetes/knowledge-proposals/` no montan `hostPath` ni el vault. Los
   registros aprobados se exportan y el publisher del host los tira (pull);
   nunca hay push desde el cluster.
4. **Escritura atomica.** El publisher escribe un temporal en el mismo
   filesystem y hace `os.replace`. Si algo falla, la nota publicada previa
   queda intacta y el fallo se registra.

Permisos de filesystem en el host:

| Ruta | Dueño | Modo | Acceso |
|---|---|---|---|
| `/opt/knowledge-vault/vault` | `knowledge-vault-publisher:knowledge-vault` | `0750` | escritura solo del publisher; lectura del grupo (Hermes, OpenCode) |
| `/var/lib/knowledge-vault/publisher` | `knowledge-vault-publisher:knowledge-vault` | `0700` | estado y lock del publisher |
| `/var/lib/knowledge-vault/approved` | `knowledge-vault-review:knowledge-vault` | `0750` | el publisher lo lee en modo solo lectura |
| `/var/lib/knowledge-vault/proposals` | `knowledge-vault-review:knowledge-vault` | `0750` | spool de propuestas del control plane; review lo lee solo lectura |
| `/var/lib/knowledge-vault/pending` | `knowledge-vault-review:knowledge-vault` | `0750` | area visible en Obsidian, fuera del vault publicado |
| `/var/lib/knowledge-vault/decisions` | `knowledge-vault-review:knowledge-vault` | `0750` | decisiones humanas exportadas hacia el control plane |

Dos unidades `oneshot`, cada una con su usuario:

| Unidad | Usuario | Escribe |
|---|---|---|
| `knowledge-vault-publisher.service` | `knowledge-vault-publisher` | vault canonico y su estado |
| `knowledge-vault-review.service` | `knowledge-vault-review` | pending y decisions; `InaccessiblePaths` sobre el vault |

La proyeccion nunca pisa un archivo pending existente: el revisor puede estar
editandolo. Una decision malformada se reporta y se queda en su lugar para
corregirla; no se descarta.

Hermes y OpenCode consumen el vault en solo lectura. Las copias en iCloud u
Obsidian movil son copias: nunca son autoridad de publicacion.

La unidad se instala deshabilitada y solo se habilita despues de que una
propuesta de prueba revisada se publique correctamente.

## Rollback a Kubernetes

Solo si el gateway systemd esta deshabilitado y detenido:

```bash
sudo systemctl disable --now <unidad-hermes>
test "$(systemctl is-enabled <unidad-hermes> 2>/dev/null || true)" = "disabled"
test "$(systemctl is-active <unidad-hermes> 2>/dev/null || true)" = "inactive"
kubectl -n hermes-agents scale deployment/hermes-agent-master --replicas=1
kubectl -n hermes-agents rollout status deployment/hermes-agent-master
kubectl -n hermes-agents logs deployment/hermes-agent-master --since=5m
```

La memoria generada despues de la migracion no vuelve automaticamente al PVC.
Si se necesita conservarla, hacer primero un backup nativo y restaurarlo al PVC
con ambos gateways detenidos. La restauracion hacia Kubernetes debe usar un pod
temporal y staging, limpiar o reemplazar atomicamente solo las rutas mutables,
corregir owner UID/GID y verificar todas las DB antes de activar el Deployment.
Despues se reaplican `kubernetes/hermes/config/config.yaml`, el backend SSH y los
Secrets Kubernetes; no se debe arrancar el pod con la URL LAN/backend local del
host nativo ni copiar su `.env` directamente al PVC.

## Idempotencia y codigos de salida del script

El futuro instalador debe usar estos resultados:

| Codigo | Significado |
| --- | --- |
| 0 | Instalacion y validacion correctas |
| 10 | Preflight o arquitectura no soportada |
| 20 | Fallo al instalar runtime/dependencias |
| 30 | Configuracion declarativa invalida |
| 40 | Restauracion o integridad SQLite fallida |
| 50 | LiteLLM/modelo inaccesible |
| 60 | Estado systemd distinto al requerido por el rol |
| 70 | Telegram invalido o conflicto de gateway |
| 80 | Secreto faltante para el rol solicitado |

Una segunda ejecucion debe:

- Conservar memorias, sesiones, OAuth y skills creados localmente.
- Reconciliar solo archivos declarativos y perfiles.
- Crear backup antes de sobrescribir config.
- No reinstalar systemd innecesariamente si ya es correcto.
- No reiniciar el gateway si no hubo cambios efectivos.
- Mostrar un resumen sin valores secretos.
- No volver a aplicar `memory-seed` ni `state-archive` ya registrados.
- Rechazar un restore nuevo sobre estado existente sin `--force-restore`.

## Criterios de aceptacion

### En servidor primary

- Hermes nativo responde por CLI y Telegram.
- El servicio arranca despues de reiniciar el host sin login interactivo.
- `hermes status` muestra terminal `local`.
- Qwen responde mediante la URL LAN de LiteLLM.
- Los nueve perfiles Codex aparecen y resuelven el auth root cuando han sido
  autorizados.
- Todas las DB declaradas por el manifest, incluidas las de perfiles, devuelven
  `ok` y ninguna esperada falta.
- El Deployment Kubernetes permanece en cero.

### En Raspberry Pi worker

- El instalador detecta `arm64` y no copia artefactos `amd64`.
- Hermes responde por CLI contra LiteLLM.
- Config, SOUL, custom skills y perfiles coinciden con la fuente declarativa.
- No existe el token de Telegram principal.
- No existe copia automatica de `auth.json` del primary.
- La memoria local puede divergir sin escribir en `trantor`.

### Seguridad

- Ningun `.env`, `auth.json`, token, clave Brave o clave SSH aparece en Git.
- Los secretos locales tienen permisos `0600`.
- La clave Brave expuesta en Kubernetes fue rotada.
- Solo un gateway usa el bot principal.
- Hermes y systemd ejecutan como usuario no root.
- LiteLLM usa HTTPS o, durante la transicion, una key revocable por host y la
  excepcion explicita `--allow-insecure-http`; nunca la master key por HTTP.

## Trabajo pendiente para implementar el script

1. Crear `hermes-native/` con config nativa separada del YAML orientado a pods.
2. Exportar y revisar los cuatro skills custom detectados.
3. Crear `hermes.env.example` sin valores.
4. Extraer un reconciliador local desde `bootstrap-profiles.sh`.
5. Vendorizar y revisar el instalador oficial, y versionar su SHA-256.
6. Elegir y fijar `HERMES_COMMIT` despues de validar la version en `amd64` y
   `arm64`.
7. Implementar deteccion Debian/Arch y politica de browser por arquitectura.
8. Implementar backup, manifest, restore y limpieza segura de estado efimero.
9. Generar el manifest del backup frio existente antes de usarlo con el script.
10. Cambiar el manifiesto Kubernetes a replicas cero o crear overlay de rollback.
11. Instalar el servicio systemd pendiente en `trantor` y documentar el nombre
   real de la unidad creado por Hermes.
12. Rotar la clave Brave expuesta y configurar el nuevo secreto fuera de Git.
13. Ejecutar una prueba completa en una RPi limpia y otra en un servidor limpio.
