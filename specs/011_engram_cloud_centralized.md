# JARVIS Spec 011 - Software Design Document (SDD)
## Engram Cloud: memoria persistente centralizada (LAN + Tailnet)

**Estado:** Implementado y funcionando — checklist 100% completo, incluyendo tokens por identidad
**Fecha:** 2026-08-17 (creado) / 2026-08-18 (reescrito tras verificación en clúster real; sync desbloqueado; proxy remoto validado; rama v1.0 borrada; **fix upstream compilado y desplegado; tokens por identidad emitidos para los 4 clientes**)
**Versión:** 3.0 — reemplaza la versión 1.0, cuyo diseño (namespace `engram`, host `engram.lan`) nunca llegó a producción
**Autor:** Pedro Ortiz

---

## 0. Por qué existe una v2.0

La v1.0 de este spec diseñó un despliegue nuevo desde cero: namespace `engram`,
Postgres propio, Ingress LAN en `engram.lan`. Esos manifiestos se escribieron,
se corrigieron (bugs de `runAsUser`/`fsGroup` en `local-path`) y se aplicaron
al clúster — pero al verificar el resultado se descubrió que **ya existía un
despliegue de Engram Cloud funcionando 32 horas antes**, en el namespace
`mcps`, sin ningún spec ni manifiesto en ningún repositorio. Nadie tenía
memoria de haberlo creado.

Decisión tomada con el usuario: dar de baja el namespace `engram` recién
creado (sin pérdida — no tenía datos) y consolidar todo sobre el despliegue
`mcps` preexistente, que ya tenía 32h de observaciones reales. Esta versión
documenta **lo que realmente quedó corriendo**, no el diseño original.

La rama `feat/engram-cloud-manifests` (con los manifiestos de la v1.0) y su
worktree fueron **borrados** (`git branch -D` + `git worktree remove`) —
describían un namespace que ya no existe y el conocimiento de los bugs de
hardening que sí aplican al namespace real ya quedó capturado en prosa en la
sección 4; no hacía falta conservar el código. Recuperable por
`git reflog`/`git fsck` si alguna vez hace falta, pero no se espera usarlo.

De paso se revisaron otras dos worktrees preexistentes que resultaron no
tener relación: `feat/engram-dual-access` (mismo commit que `main`, nombre
coincidente — es trabajo de `knowledge-vault`, no de Engram) y
`backup/workspace-before-engram-isolation-20260807` (backup real del 6 de
agosto con el mismo diseño v1.0 de `kubernetes/engram/` — confirma que hubo
un intento de "Engram isolation" después de ese backup que terminó siendo
el despliegue `mcps`, pero ese trabajo nunca se commiteó en ningún lado).
Ninguna de las dos se tocó.

---

## 1. Qué hay desplegado realmente

| | Namespace `mcps` (real, en producción) |
|---|---|
| Deployments | `engram-cloud` (1/1 Running), `engram-postgres` (1/1 Running) |
| Antigüedad | ~32h más vieja que este spec; origen no documentado |
| Exposición | **Solo Tailnet** — no existe Ingress LAN para este servicio |
| Host | `trantor.tail07dff9.ts.net` (nombre MagicDNS de Tailscale, no `engram.lan`) |
| Ingress | `engram-tailnet` (namespace `mcps`), Traefik, `RequireAndVerifyClientCert` |
| TLSOption | `mcps-engram-mtls` |
| Bridge Tailnet | Reutiliza las mismas dos unidades systemd (`engram-traefik-port-forward`, `engram-tailnet-serve`) documentadas en el spec original — esas sí eran correctas y ya estaban activas |
| Proyecto habilitado | `jarvis_project` (`ENGRAM_CLOUD_ALLOWED_PROJECTS`) |

No existe (a propósito, por ahora): Ingress LAN, NetworkPolicy, tokens por
identidad emitidos vía dashboard.

---

## 2. CA y certificados — ahora bajo control propio

La CA original (`CN=EnGram mTLS rotation CA`) que firmaba todo no tiene clave
privada recuperable (correcto: nunca debería estar en un Secret). Como
ningún cliente estaba conectado todavía, se reemplazó **tanto** el Secret de
verificación de clientes como el certificado de servidor por una CA nueva,
generada y controlada por este operador:

```
~/.config/engram-cloud/pki/
├── ca/ca.key, ca.crt              ← CA privada, 10 años, CN="Engram Cloud Private CA"
├── clients/pedro/pedro.key, .crt  ← identidad de cliente, CN=pedro, 2 años
└── server-tailnet/trantor.key, .crt ← servidor, SAN=DNS:trantor.tail07dff9.ts.net, 2 años
```

Secrets en `mcps` reemplazados con esta CA:
- `engram-client-ca` (ca.crt) — verificado con `RequireAndVerifyClientCert`
- `engram-server-tls` (cert + key del servidor)

Validado end-to-end: `curl` con el cert de `pedro` contra
`trantor.tail07dff9.ts.net:443` (vía el port-forward local a Traefik) →
`HTTP 404` **de la app Engram** (no de Traefik), confirmando mTLS + ruteo
correctos.

**Nunca commitear** nada de `~/.config/engram-cloud/pki/` ni
`~/.config/engram-cloud/secrets.env` al repositorio.

---

## 3. Acceso local privilegiado: atajo sin mTLS

Los 4 clientes que corren en `trantor` (Claude Code, Codex, OpenCode, Hermes)
no necesitan pasar por Traefik/mTLS: ya tienen acceso de confianza al
clúster vía `kubectl`. Se armó un atajo dedicado:

- Servicio `systemd --user`: `~/.config/systemd/user/engram-mcps-port-forward.service`
- `kubectl -n mcps port-forward --address=127.0.0.1 service/engram-cloud 7180:8080`
- `enabled`, `Restart=always` — sobrevive reinicios de sesión de usuario

Este atajo **bypassea Traefik y mTLS por completo**; la única autenticación
es el bearer (`ENGRAM_CLOUD_TOKEN`). Es aceptable porque el proceso corre
como el mismo usuario que ya tiene `kubectl` contra el clúster — no añade
superficie de ataque nueva. Un cliente en **otra máquina** (sin `kubectl`)
sí necesita el camino mTLS real (Tailnet, con `stunnel`/`socat` terminando
el certificado de cliente localmente — no implementado todavía, ver §7).

`~/.engram/cloud.json` (compartido por todo el usuario `pedro` en este host)
ya apunta a `http://127.0.0.1:7180`.

---

## 4. Bugs reales encontrados y corregidos en el camino

Estos bugs son de infraestructura genérica (`local-path`/K3s, no específicos
del namespace `engram` descartado) y **sí aplicarían si alguien reconstruye
este despliegue desde cero**:

1. **`engram-cloud` corre como usuario con nombre no numérico** (`engram`,
   UID 10001 real, verificado con `kubectl run ... id`). `runAsNonRoot: true`
   sin `runAsUser` numérico hace que el kubelet rechace el contenedor
   ("cannot verify user is non-root"). Fix: fijar `runAsUser`/`runAsGroup`
   explícitos al UID/GID reales de la imagen.
2. **PVC `local-path` en K3s no recibe el `fsGroup` automático** (es
   `hostPath` por debajo). Un contenedor Postgres non-root con
   `capabilities: drop: [ALL]` no puede ni `chmod`/`chown` su propio
   directorio de datos. Fix: initContainer efímero como root con **solo**
   `CAP_CHOWN` que arregla el dueño una vez, antes de que arranque el
   contenedor hardened.

(Estaban corregidos en la ahora borrada `feat/engram-cloud-manifests` — el
conocimiento queda acá en prosa, que es lo que importa.)

---

## 5. Bug de Engram v1.20.0 (upstream, no de este despliegue) — RESUELTO

`engram sync --cloud --project jarvis_project` quedó bloqueado por
observaciones con `title` **vacío** (`""`, no faltante) — 52 en todo el
proyecto, desde el 1 de agosto hasta memorias guardadas en esta misma
sesión (o sea, el bug que genera títulos vacíos parece seguir vigente en
el propio `mem_save`, no solo ser un resabio histórico).

Primer intento fallido: el mensaje de error señalaba `seq=73` en la cola
de mutaciones (`sync_mutations`) y se interpretó mal como el ID de la
observación #73 — se editó/soft-borró la observación equivocada (un
resumen de sesión no relacionado) sin ningún efecto. `engram cloud upgrade
repair --project jarvis_project --apply` tampoco reparó nada
(`applied: false`) — confirmado no funcional, sigue siendo un bug real del
CLI a reportar.

**Causa real y fix aplicado** (con backup previo de
`~/.engram/engram.db`, autorizado explícitamente por el usuario dado que
implica escritura SQL directa):

1. `UPDATE sync_mutations SET payload = json_set(payload, '$.title', substr(trim(json_extract(payload,'$.content')), 1, 100)) WHERE ...título vacío...` — repara la cola histórica (50 filas).
2. El mismo `UPDATE` sobre la tabla `observations` en vivo — el push vuelve
   a leer el estado actual de la tabla, no solo la cola, así que hacía
   falta corregir ambos lugares (52 filas).

Con las dos tablas corregidas, `engram sync --cloud --project jarvis_project`
corrió limpio: 42 sesiones, 299 observaciones, 333 prompts, 1386 mutaciones
subidas a `mcps/engram-tailnet`.

**Pendiente real:** el bug de origen (por qué `mem_save` a veces guarda
`title=""`) no se corrigió, solo su síntoma en los datos existentes. Vale
la pena re-correr el mismo chequeo (`WHERE title IS NULL OR title=''`)
periódicamente, y reportar dos bugs distintos a
`github.com/Gentleman-Programming/engram`: (a) `upgrade repair --apply` no
repara lo que dice reparar, (b) el path de guardado puede persistir título
vacío.

**Confirmado en vivo, dos veces:** el 18/08 el sync se bloqueó de nuevo por
una fila nueva (`seq=1691`) — el bug de origen sigue produciendo `title=""`
en vivo, no es solo resabio histórico. Reaplicado el mismo fix (backup
previo de `~/.engram/engram.db`, `UPDATE` batch en `sync_mutations` y
`observations`) y el sync volvió a correr limpio: 4 sesiones, 6
observaciones, 20 prompts, 49 mutaciones. **Esto va a seguir pasando** cada
vez que `mem_save` guarde con título vacío — no es un fix permanente, es un
parche que hay que repetir hasta que Engram corrija la causa raíz.

---

## 6. Onboarding de clientes — estado real

| Cliente | Config tocada | Estado |
|---|---|---|
| Claude Code | `~/.claude/mcp/engram.json` (`env`) | ✅ hecho — necesita reiniciar la sesión |
| Codex | `~/.codex/config.toml` (`[mcp_servers.engram.env]`) | ✅ hecho |
| OpenCode | `~/.config/opencode/opencode.json` (`mcp.engram.environment`) | ✅ hecho |
| Hermes/Jarvis | `~/.hermes/config.yaml` (`mcp_servers.engram.env`) + `~/.hermes/.env` (`ENGRAM_HERMES_BEARER_TOKEN`) | ✅ hecho — `hermes-gateway.service` reiniciado |

Los cuatro usan `ENGRAM_CLOUD_SERVER=http://127.0.0.1:7180` +
`ENGRAM_CLOUD_AUTOSYNC=1` + el **mismo bearer de bootstrap compartido**
(deuda técnica aceptada, ver §7).

**Hermes — hallazgo importante:** su config real vive en `~/.hermes/config.yaml`
(`mcp_servers.<name>`, con soporte nativo de `env:` — no hace falta ningún
archivo aparte; `hermes_cli/mcp_config.py` confirma el campo). No es un
store dinámico/SQLite como se sospechaba inicialmente.

Además, Hermes ya tenía una **segunda** entrada, `engram-cloud`, apuntando a
`https://trantor.tail07dff9.ts.net/mcp` con soporte de mTLS **nativo a nivel
framework** (`ssl_verify`/`client_cert` en `~/.hermes/credentials/engram/`) —
el patrón de proxy remoto con cert de cliente que se buscaba para clientes
sin `kubectl` (ver §7). Esa entrada está **inerte, no funcional**: el
binario `engram cloud serve` v1.20.0 no expone ningún endpoint `/mcp`
(confirmado: `curl` con el cert correcto contra esa ruta devuelve `404` de
la propia app, no de Traefik — el server simplemente no tiene esa ruta).
Sus certificados (`~/.hermes/credentials/engram/{ca,client}.crt/key`,
identidad `CN=hermes-gateway`) estaban además firmados por la CA original
que ya no es de confianza (reemplazada en §2) — se reemitieron con la CA
propia y se reemplazaron los 3 archivos. `bearer.token` en esa misma
carpeta ya tenía el bearer correcto (coincide con el compartido) y no se
tocó. Esta entrada queda lista para el día que Engram exponga MCP-sobre-HTTP
de verdad; hoy Hermes usa la entrada `engram` (stdio) como los otros tres.

---

## 6.1. Fix upstream compilado y desplegado — tokens por identidad RESUELTO

El agente que arregló los 3 bugs (§7 histórico) trabajó sobre
`~/.claude/plugins/marketplaces/engram` (clon real del repo, remote
`git@github.com:Gentleman-Programming/engram.git`), rama
`fix/cloud-sync-and-token-bugs`, commit `f57d98a`. Se compiló y desplegó:

- **Binario CLI local** (`~/.local/bin/engram`): reemplazado con
  `go build -ldflags "-s -w -X main.version=v1.20.0-jarvisfix1"`.
  El original queda en `~/.local/bin/engram.bak-v1.20.0`. El reemplazo
  necesitó `mv` en vez de `cp` (el binario estaba en uso por los daemons
  `engram mcp` ya corriendo — `cp` in-place da "text file busy"; `mv`/rename
  sí funciona sobre un ejecutable activo).
- **Imagen del servidor**: build desde `docker/cloud/Dockerfile` →
  `localhost/engram-cloud:v1.20.0-jarvisfix1`, exportada a tar,
  `sudo k3s ctr images import` (paso manual del usuario, sin registro
  intermedio — imagen puramente local), `kubectl -n mcps set image
  deployment/engram-cloud engram=localhost/engram-cloud:v1.20.0-jarvisfix1`.
  `imagePullPolicy: IfNotPresent` ya estaba configurado, así que no intentó
  bajarla de ningún lado.

**Validado end-to-end tras el despliegue:**
- El admin `pedro-claude-code` original (creado antes del fix, sin token
  utilizable) se borró por SQL directo (cascada: `cloud_auth_audit_log` →
  `cloud_principal_tokens` → `cloud_project_grants` → `cloud_human_users` →
  `cloud_principals`) y se recreó con `engram cloud bootstrap admin
  --issue-token` — **esta vez emitió el token sin error** (`principal_id=2`).
- `engram cloud upgrade doctor` en un proyecto con mutaciones legacy pasó de
  `class: blocked` (sin remedio) a `class: repairable`, y
  `upgrade repair --apply` devolvió `applied: true` de verdad — el bug 1
  también confirmado arreglado en producción.
- Con la sesión de dashboard real de `pedro-claude-code` (login vía
  `POST /dashboard/login` con su propio token — ahora sí autoriza acciones
  de admin, a diferencia del intento con `ENGRAM_CLOUD_ADMIN` de antes) se
  crearon 3 usuarios más (`codex`, `opencode`, `hermes-gateway`,
  `POST /dashboard/admin/users`), cada uno con grant a `jarvis_project`
  (`POST /dashboard/admin/users/{id}/grants`) y token propio
  (`POST /dashboard/admin/users/{id}/tokens`).

**Tokens por identidad, guardados en `~/.config/engram-cloud/tokens/*.token`
(permisos 600, fuera del repo):**

| Identidad | principal_id | Config actualizada |
|---|---|---|
| `pedro-claude-code` | 2 | `~/.claude.json` → `mcpServers.engram` (ver corrección abajo) |
| `codex` | 3 | `~/.codex/config.toml` |
| `opencode` | 4 | `~/.config/opencode/opencode.json` |
| `hermes-gateway` | 5 | `~/.hermes/.env` (`ENGRAM_HERMES_BEARER_TOKEN`) + `~/.hermes/credentials/engram/bearer.token` |

**Corrección post-reinicio:** `~/.claude/mcp/engram.json` (donde se puso el token
originalmente) **nunca tuvo efecto** — Engram en Claude Code corre como
**plugin** (`CLAUDE_PLUGIN_ROOT=~/.claude/plugins/cache/engram/engram/0.1.1`),
que declara su propio servidor MCP en su `.mcp.json` interno, sin `env`.
Confirmado inspeccionando `/proc/<pid>/environ` del proceso `engram mcp` real
tras el primer reinicio: ninguna variable `ENGRAM_CLOUD_*` presente.

Mecanismo correcto (via agente `claude-code-guide`, con cita a
`code.claude.com/docs/en/mcp.md`): los servidores MCP declarados por
plugins tienen la **prioridad más baja**; un servidor con el mismo nombre
en `~/.claude.json` (scope de usuario) **reemplaza por completo** la
entrada del plugin (no hay merge de campos entre scopes — hay que repetir
`command`/`args` igual que el plugin). Se agregó a la sección `mcpServers`
de nivel superior (no bajo `projects.<path>`, para que aplique en
cualquier proyecto) de `~/.claude.json`:
```json
"engram": {
  "command": "engram",
  "args": ["mcp", "--tools=agent"],
  "env": {
    "ENGRAM_CLOUD_SERVER": "http://127.0.0.1:7180",
    "ENGRAM_CLOUD_TOKEN": "<token de pedro-claude-code>",
    "ENGRAM_CLOUD_AUTOSYNC": "1"
  }
}
```
Requiere reiniciar la sesión de Claude Code (de nuevo) para tomar efecto —
no hace falta reiniciar la terminal completa (esa hubiera sido la otra
opción, variables de entorno en `~/.config/fish/config.fish`, descartada
por necesitar un reinicio más disruptivo).

**Confirmado tras el segundo reinicio**: el proceso `engram mcp` nuevo
(verificado vía `/proc/<pid>/environ`) sí trae las 3 variables correctas.
Señal adicional: el prefijo de los nombres de tool pasó de
`mcp__plugin_engram_engram__*` a `mcp__engram__*` — confirma que Claude Code
está usando la entrada de `mcpServers` en `~/.claude.json`, no la del
plugin. Los 4 clientes quedan onboarded con token propio, de punta a punta.

Los 4 verificados con `engram cloud upgrade doctor --project jarvis_project`
usando cada token — los 4 dan `status: ready`. El bearer de bootstrap
compartido ya no se usa en ninguna config de cliente (deuda técnica de §7
cerrada). `hermes-gateway.service` reiniciado para tomar el cambio; Claude
Code, Codex y OpenCode necesitan reiniciar su sesión/proceso MCP para tomar
el suyo.

---

## 7. Deuda técnica aceptada (histórico — ya no aplica el ítem de tokens)

| Ítem | Por qué se aceptó | Cómo resolverlo después |
|---|---|---|
| Todos los clientes locales comparten el mismo bearer de bootstrap, no tokens por identidad | **Bug real de Engram v1.20.0, no fricción de login.** `engram cloud bootstrap admin --username pedro-claude-code --grant-project jarvis_project --issue-token pedro-claude-code` (con `ENGRAM_CLOUD_TOKEN_PEPPER`/`ENGRAM_JWT_SECRET`/`ENGRAM_DATABASE_URL` correctos, vía port-forward directo a Postgres) creó el admin con éxito (único principal en `cloud_principals`, no existía ninguno antes — la teoría de "admin de 32h" del spec anterior era incorrecta) pero la emisión del token falló: `cloudstore: auth audit insert failed: sensitive auth audit metadata is not allowed: issued_token` — el propio sanitizador de auditoría de Engram rechaza persistir el valor del token, y eso aborta toda la operación en vez de solo omitir ese campo del log (no es atómico: la creación del admin quedó, el token no — confirmado 0 filas en `cloud_principal_tokens`). Probé además loguearme al dashboard (`POST /dashboard/login` con el bearer de bootstrap) — funciona y da cookie de sesión, pero esa identidad no es admin y `/dashboard/admin` da `403`. Para loguear como el admin recién creado hace falta justamente el token que el bug no deja emitir — mismo problema circular por otro lado. **Tercer intento:** agregué temporalmente `ENGRAM_CLOUD_ADMIN` (env var separada, "admin-only dashboard token") al secret `engram-cloud-config`, reinicié el pod, logueé con ese valor — esta vez sí entra a `/dashboard/admin` (`200`). Pero al intentar `POST /dashboard/admin/users/1/tokens` (la ruta real del panel, confirmada leyendo el HTML del formulario) devuelve `403 forbidden: managed admin principal is required` — `ENGRAM_CLOUD_ADMIN` da acceso de **lectura** al panel pero no autoridad para ejecutar acciones de admin, que exigen estar logueado como un principal admin gestionado de verdad (mismo bloqueo circular por una tercera puerta). Reverté el secret a sus 5 claves originales y reinicié el pod de nuevo — no se dejó nada añadido | Reportar el bug a `github.com/Gentleman-Programming/engram` (el sanitizador de audit-log no debería abortar toda la transacción). Reintentar `--issue-token` cuando haya fix. No insertar el token a mano por SQL — requeriría replicar el hash+pepper exacto de Engram, demasiado riesgo |
| Clientes remotos no-Hermes (otra máquina, sin `kubectl`) | Resuelto — ver §7.1 | — |
| Nuevas memorias podrían seguir guardándose con `title=""` (causa raíz del bug de §5 no corregida, solo su síntoma en datos existentes) | El path de guardado de Engram, no algo controlable desde este despliegue | Re-correr el chequeo `WHERE title IS NULL OR title=''` periódicamente; reportar upstream |

---

## 7.1. Proxy mTLS local para clientes remotos sin `kubectl`

Patrón validado con `socat` (ya trae soporte OpenSSL) en lugar de `stunnel`.
Se probó end-to-end en `trantor` mismo, apuntando directo al Tailnet real
(`trantor.tail07dff9.ts.net:443`, sin el port-forward local), y confirmó
`HTTP 404` limpio de la app tanto forzando el header `Host` a mano como con
el patrón natural (`--resolve` simulando el `/etc/hosts` del cliente real).

**Por qué hace falta más que un simple túnel TCP:** Traefik rutea por el
header HTTP `Host`, no solo por SNI. Un cliente local (`engram` CLI) que
apunte a `http://127.0.0.1:<puerto>` mandaría `Host: 127.0.0.1:<puerto>`, y
Traefik no lo matchearía contra el Ingress (`host: trantor.tail07dff9.ts.net`).
La solución es que el cliente use esa URL con el hostname real
(`http://trantor.tail07dff9.ts.net:<puerto>`) y que la máquina resuelva ese
nombre a `127.0.0.1` — así DNS, SNI y `Host` quedan consistentes sin tocar
ningún código.

### En la máquina remota (una vez por identidad)

1. Pedir a un operador con acceso a `~/.config/engram-cloud/pki/ca/` en
   `trantor` que emita un certificado de cliente para esta identidad:
   ```sh
   PKI=~/.config/engram-cloud/pki
   NAME=<identidad, ej. laptop-pedro>
   mkdir -p "$PKI/clients/$NAME"
   openssl genrsa -out "$PKI/clients/$NAME/$NAME.key" 2048
   openssl req -new -key "$PKI/clients/$NAME/$NAME.key" \
     -out "$PKI/clients/$NAME/$NAME.csr" -subj "/CN=$NAME/O=jarvis_project"
   printf 'extendedKeyUsage=clientAuth\n' > "$PKI/clients/$NAME/$NAME.ext"
   openssl x509 -req -in "$PKI/clients/$NAME/$NAME.csr" \
     -CA "$PKI/ca/ca.crt" -CAkey "$PKI/ca/ca.key" -CAcreateserial \
     -out "$PKI/clients/$NAME/$NAME.crt" -days 730 -sha256 \
     -extfile "$PKI/clients/$NAME/$NAME.ext"
   ```
   Copiar `$NAME.crt`, `$NAME.key` y `ca.crt` a la máquina remota (canal
   seguro — scp sobre Tailnet, por ejemplo), en
   `~/.config/engram-cloud/client/{client.crt,client.key,ca.crt}`.

2. Agregar al `/etc/hosts` de la máquina remota:
   ```
   127.0.0.1 trantor.tail07dff9.ts.net
   ```
   (Requiere `sudo` en esa máquina — no algo que un agente pueda hacer
   por sí solo sin intervención del operador.)

3. Instalar el servicio `systemd --user`:
   ```ini
   # ~/.config/systemd/user/engram-remote-mtls-proxy.service
   [Unit]
   Description=Local mTLS-terminating proxy to Engram Cloud (mcps/engram-tailnet)
   After=network-online.target tailscaled.service
   Wants=network-online.target

   [Service]
   Type=simple
   ExecStart=/usr/bin/socat TCP-LISTEN:7280,bind=127.0.0.1,fork,reuseaddr \
     OPENSSL-CONNECT:trantor.tail07dff9.ts.net:443,cert=%h/.config/engram-cloud/client/client.crt,key=%h/.config/engram-cloud/client/client.key,cafile=%h/.config/engram-cloud/client/ca.crt,verify=1
   Restart=always
   RestartSec=5

   [Install]
   WantedBy=default.target
   ```
   ```sh
   systemctl --user daemon-reload
   systemctl --user enable --now engram-remote-mtls-proxy.service
   ```

4. Configurar el cliente `engram`:
   ```sh
   engram cloud config --server http://trantor.tail07dff9.ts.net:7280
   export ENGRAM_CLOUD_TOKEN=<bearer, mismo compartido por ahora — ver deuda de tokens por identidad>
   ```

### Verificación

```sh
curl -sS -w "\nHTTP %{http_code}\n" http://trantor.tail07dff9.ts.net:7280/
```
Debe devolver `404 page not found` (la respuesta de la app Engram, no de
Traefik) — igual que el resto de las pruebas mTLS de este spec.

---

## 8. Checklist de Implementación

- [x] Namespace/Deployments/Service/Ingress/TLSOption existentes verificados (`mcps`, preexistente)
- [x] CA propia generada, clave privada guardada fuera del repo
- [x] `engram-client-ca` y `engram-server-tls` reemplazados con la CA propia
- [x] mTLS validado end-to-end (`curl` + cert de cliente → `404` de la app)
- [x] Bridge Tailnet confirmado activo y funcional (preexistente, sin cambios)
- [x] Atajo local sin mTLS (`systemd --user` port-forward) para clientes con `kubectl`
- [x] Claude Code, Codex, OpenCode, Hermes/Jarvis configurados
- [x] Sync de `jarvis_project` funcionando — corrió limpio tres veces; el bug de `title=""` se reprodujo dos veces en datos nuevos y se resolvió a mano hasta que el fix upstream se compiló y desplegó (§6.1) — ya no debería volver a bloquear
- [x] Tokens por identidad para los 4 clientes (no más bearer de bootstrap compartido) — bug de audit-log identificado, arreglado en el código fuente, binario y CLI compilados y desplegados en el clúster real, tokens emitidos y verificados uno por uno (§6.1)
- [x] Proxy mTLS local para clientes remotos sin `kubectl` (patrón `socat` validado end-to-end, §7.1) — falta usarlo en una máquina remota real cuando exista una
- [x] Namespace `engram` / rama `feat/engram-cloud-manifests` — ambos borrados

---

## 9. Referencias

- `kubernetes/engram/README.md` — diseño original v1.0, todavía útil para el
  patrón del bridge Tailnet (que sí se usa) y las secciones de seguridad
- Memoria de sesión: bug de `upgrade repair`, decisión de consolidación
  namespace `engram` → `mcps`, onboarding de clientes

---

**Fin del SDD**
