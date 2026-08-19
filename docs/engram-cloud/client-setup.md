# Conectar clientes

Cuatro escenarios, de más simple a más general. Elegí el que corresponda
a dónde corre el agente que querés conectar.

## Antes que nada: identidad y token, para cualquier escenario

Cada cliente necesita **su propio** certificado (mTLS, salvo el escenario
1) y **su propio** token de aplicación — nunca reuses el bearer de
arranque del operador (`ENGRAM_CLOUD_TOKEN` del secret) para clientes
reales; es un credential de recuperación, no de uso normal.

**Emitir un token por identidad** (requiere que el servidor tenga
`ENGRAM_CLOUD_TOKEN_PEPPER` configurado — ver instalación, paso 3):

Primero, un admin gestionado (una sola vez, hace falta acceso directo a
Postgres vía `kubectl port-forward` — ver `installation.md` para el
patrón completo de extraer `ENGRAM_DATABASE_URL`/`ENGRAM_JWT_SECRET`/
`ENGRAM_CLOUD_TOKEN_PEPPER` del secret y reescribir el host a
`127.0.0.1:<puerto forwardeado>`):
```sh
engram cloud bootstrap admin --username <tu-usuario> \
  --grant-project <proyecto> --issue-token <tu-usuario>
```
Guardá el token que imprime — se muestra **una sola vez**.

Con ese primer admin ya podés loguearte al dashboard web
(`https://<host-del-ingress>/dashboard/login`, o vía `POST` con el token
como campo `token`) y desde ahí, `/dashboard/admin/users`, crear un
usuario por cada identidad nueva (`POST .../users`), otorgarle el proyecto
(`POST .../users/{id}/grants`) y emitirle su token
(`POST .../users/{id}/tokens`) — sin volver a tocar Postgres directamente.

Guardá cada token fuera del repo, con permisos restrictivos:
```sh
mkdir -p ~/.config/engram-cloud/tokens && chmod 700 ~/.config/engram-cloud/tokens
echo -n "<token>" > ~/.config/engram-cloud/tokens/<identidad>.token
chmod 600 ~/.config/engram-cloud/tokens/<identidad>.token
```

---

## Escenario 1 — el cliente corre en el mismo nodo del clúster

Si el agente corre en la misma máquina que ya tiene `kubectl` contra el
clúster (por ejemplo, en un clúster K3s de un solo nodo donde vos mismo
sos ese nodo), no hace falta pasar por Traefik/mTLS: hay un atajo directo
al `Service`, autenticado solo con el bearer/token.

```ini
# ~/.config/systemd/user/engram-port-forward.service
[Unit]
Description=Local plain-HTTP port-forward to engram-cloud (bypasses Traefik/mTLS)
After=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/kubectl -n mcps port-forward --address=127.0.0.1 service/engram-cloud 7180:8080
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```
```sh
systemctl --user daemon-reload
systemctl --user enable --now engram-port-forward.service
```

Configurar el cliente:
```sh
engram cloud config --server http://127.0.0.1:7180
export ENGRAM_CLOUD_TOKEN="$(cat ~/.config/engram-cloud/tokens/<identidad>.token)"
export ENGRAM_CLOUD_AUTOSYNC=1
```

Es aceptable saltarse mTLS acá porque el proceso ya corre como el mismo
usuario del sistema operativo que tiene `kubectl` — no agrega superficie
de ataque nueva, solo evita reconstruir manualmente lo que `kubectl` ya
garantiza.

**Nota para MCP servers instalados como plugin** (Claude Code, y
potencialmente otros): el archivo de config del plugin no siempre acepta
un bloque `env` — puede ignorarse silenciosamente. Verificá que el
proceso lanzado realmente heredó las variables:
```sh
ps aux | grep "engram mcp"
tr '\0' '\n' < /proc/<pid>/environ | grep ENGRAM_CLOUD
```
Si no aparecen, la config vive en otro lado con más prioridad (por
ejemplo, para Claude Code: un `mcpServers` a nivel de usuario en
`~/.claude.json` reemplaza por completo — no mergea campo por campo — la
entrada que declara el plugin).

## Escenario 2 — otra máquina en la misma LAN

Necesita: el certificado de cliente propio, la CA pública del servidor, y
que el `Ingress` LAN (`ingress.yaml` de `installation.md`) esté aplicado.

```sh
# en la máquina servidor, confirmar la IP real de Traefik antes de asumir nada:
kubectl -n kube-system get service traefik
```
En la máquina cliente:
```sh
echo "<IP de Traefik>  engram.lan" | sudo tee -a /etc/hosts

engram cloud config --server https://engram.lan
export ENGRAM_CLOUD_TOKEN="$(cat ~/.config/engram-cloud/tokens/<identidad>.token)"
```
El certificado de cliente (mTLS) hay que configurarlo a nivel del
transporte HTTP que use tu herramienta — el binario `engram` no acepta un
certificado de cliente por variable de entorno (verificado inspeccionando
el binario: no existe ningún `ENGRAM_CLOUD_CLIENT_CERT` ni equivalente).
Ver el escenario 4 para el patrón de proxy local que resuelve esto de
forma genérica — aplica igual acá que a una máquina remota por Tailnet.

## Escenario 3 — otra máquina por Tailscale (Tailnet)

Igual que el escenario 2, pero el host es el nombre MagicDNS de Tailscale
del servidor (`<nodo>.<tailnet>.ts.net`) en vez de un hostname `.lan`, y
no hace falta que las dos máquinas compartan LAN — alcanza con estar en
el mismo Tailnet. El bridge del lado servidor (`tailscale serve --tcp`)
ya lo cubre `architecture.md` — no hay nada distinto que configurar del
lado cliente respecto al escenario 2, salvo el hostname.

## Escenario 4 — máquina remota sin `kubectl`, incluida Raspberry Pi

Este es el caso general: cualquier máquina con Tailscale (o en la LAN)
que **no** tiene credenciales de `kubectl` contra el clúster, así que
necesita el camino mTLS real de punta a punta.

### El problema a resolver

El binario `engram` no sabe presentar un certificado de cliente. La
solución es un **proxy local que termina el mTLS**: escucha en
`127.0.0.1:<puerto>` en texto plano, y hacia afuera abre la conexión TLS
con el certificado de cliente correcto. `engram` apunta al puerto local
sin saber que hay mTLS de por medio.

Complicación extra: Traefik rutea por el header HTTP `Host`, no solo por
SNI (ver `architecture.md`). Si el cliente apunta a
`http://127.0.0.1:<puerto>`, el `Host` que manda es `127.0.0.1:<puerto>`
y Traefik no encuentra la ruta. La solución es que el cliente use la URL
con el **hostname real** (`http://<host-del-ingress>:<puerto>`) y que
`/etc/hosts` de esa máquina resuelva ese hostname a `127.0.0.1` — así
DNS, SNI y `Host` quedan consistentes sin tocar ningún código.

### Instalación (Linux — Raspberry Pi OS incluido)

`socat` con soporte OpenSSL hace de proxy; viene en los repos de
cualquier distro Debian-based (Raspberry Pi OS incluida):
```sh
sudo apt install socat   # Raspberry Pi OS / Debian / Ubuntu
```

**El binario `engram` en una Raspberry Pi**: el proyecto publica builds
oficiales `linux/arm64` (Raspberry Pi 4/5 de 64 bits) además de
`linux/amd64` — no hace falta compilar desde fuente. Instalalo con el
método que uses normalmente (`brew`, descarga directa del release de
GitHub para `arm64`, o `go install
github.com/Gentleman-Programming/engram/cmd/engram@latest` si tenés Go
instalado). Una Raspberry Pi de 32 bits necesitaría compilar desde
fuente para `arm` (no publicado oficialmente) — no recomendado, usá un
SO de 64 bits.

1. Pedile a un operador con acceso a la CA (`~/.config/engram-cloud/pki/ca/`
   en el servidor) que emita tu certificado de cliente:
   ```sh
   PKI=~/.config/engram-cloud/pki
   NAME=raspberry-pi-jarvis   # tu identidad
   mkdir -p "$PKI/clients/$NAME"
   openssl genrsa -out "$PKI/clients/$NAME/$NAME.key" 2048
   openssl req -new -key "$PKI/clients/$NAME/$NAME.key" \
     -out "$PKI/clients/$NAME/$NAME.csr" -subj "/CN=$NAME"
   printf 'extendedKeyUsage=clientAuth\n' > "$PKI/clients/$NAME/$NAME.ext"
   openssl x509 -req -in "$PKI/clients/$NAME/$NAME.csr" \
     -CA "$PKI/ca/ca.crt" -CAkey "$PKI/ca/ca.key" -CAcreateserial \
     -out "$PKI/clients/$NAME/$NAME.crt" -days 730 -sha256 -extfile "$PKI/clients/$NAME/$NAME.ext"
   ```
   Copiá `$NAME.crt`, `$NAME.key` y `ca.crt` a la Raspberry Pi por un
   canal seguro (`scp` sobre Tailnet, por ejemplo) a
   `~/.config/engram-cloud/client/{client.crt,client.key,ca.crt}`.

2. En la Raspberry Pi, agregar a `/etc/hosts`:
   ```sh
   echo "127.0.0.1  <host-del-ingress>" | sudo tee -a /etc/hosts
   ```

3. Servicio `systemd --user` (o `systemd` de sistema si la Pi corre el
   agente como servicio, no como usuario interactivo):
   ```ini
   # ~/.config/systemd/user/engram-remote-mtls-proxy.service
   [Unit]
   Description=Local mTLS-terminating proxy to Engram Cloud
   After=network-online.target tailscaled.service
   Wants=network-online.target

   [Service]
   Type=simple
   ExecStart=/usr/bin/socat TCP-LISTEN:7280,bind=127.0.0.1,fork,reuseaddr \
     OPENSSL-CONNECT:<host-del-ingress>:443,cert=%h/.config/engram-cloud/client/client.crt,key=%h/.config/engram-cloud/client/client.key,cafile=%h/.config/engram-cloud/client/ca.crt,verify=1
   Restart=always
   RestartSec=5

   [Install]
   WantedBy=default.target
   ```
   ```sh
   systemctl --user daemon-reload
   systemctl --user enable --now engram-remote-mtls-proxy.service
   ```

4. Configurar `engram`:
   ```sh
   engram cloud config --server http://<host-del-ingress>:7280
   export ENGRAM_CLOUD_TOKEN="$(cat ~/.config/engram-cloud/tokens/<identidad>.token)"
   export ENGRAM_CLOUD_AUTOSYNC=1
   ```

### Verificación

```sh
curl -sS -w "\nHTTP %{http_code}\n" http://<host-del-ingress>:7280/
```
`404 page not found` con `HTTP 404` es éxito — la petición pasó el mTLS y
llegó a la aplicación.

Si el proxy no arranca en una Raspberry Pi con recursos muy limitados
(RAM/CPU compartidos con otras cargas), `socat` es liviano (unos pocos
MB), pero si igual hace falta ahorrar recursos, el mismo patrón funciona
con `stunnel` en modo cliente — la configuración es equivalente
(certificado + clave + CA + destino), solo cambia la sintaxis del
archivo de configuración.
