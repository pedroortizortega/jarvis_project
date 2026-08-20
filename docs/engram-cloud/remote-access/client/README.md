# client — plantilla para la máquina remota

Para conectar una máquina que **no tiene `kubectl`** y **no tiene permisos
de administrador del sistema** (Tailscale corre adentro de Docker en vez
de instalarse en el host — así el único requisito real es que Docker esté
disponible). Ver [`../README.md`](../README.md) para por qué esta carpeta
no tiene ningún secreto.

## 0. Prerrequisito — identidad emitida

Alguien con acceso a `trantor` ya tiene que haber corrido
[`../server/generate-client-cert.sh <identidad>`](../server/README.md) y
creado el usuario + token en el dashboard admin. Necesitás que te pasen,
por un canal seguro:

- `client.crt`, `client.key`, `ca.crt`
- el token de aplicación (`engram.token`)

## 1. Copiar los archivos

```sh
cp <lo que te pasaron>/client.crt identity/client.crt
cp <lo que te pasaron>/client.key identity/client.key
cp <lo que te pasaron>/ca.crt     identity/ca.crt
cp <lo que te pasaron>/engram.token identity/engram.token
```

## 2. Configurar

```sh
cp env.example .env
$EDITOR .env   # TS_HOSTNAME=<tu identidad>, ENGRAM_INGRESS_HOST=<host real del ingress>
```

## 3. Levantar

```sh
docker compose up -d --build
docker compose logs -f tailscale
```

Va a imprimir una URL de login de Tailscale
(`https://login.tailscale.com/a/...`) — abrila en el browser una sola vez.
Confirmá que el dispositivo aparece en tu tailnet con el nombre de
`TS_HOSTNAME`.

## 4. Verificar el proxy (con la VPN corporativa activa, si aplica)

```sh
source .env  # o exportá las mismas variables a mano
curl -sS -w "\nHTTP %{http_code}\n" \
  --resolve "${ENGRAM_INGRESS_HOST}:${ENGRAM_PROXY_PORT}:127.0.0.1" \
  "http://${ENGRAM_INGRESS_HOST}:${ENGRAM_PROXY_PORT}/"
```

`404 page not found` = la petición pasó el mTLS y llegó a la app. Si no
responde nada, revisar primero si el problema es la VPN bloqueando
Tailscale (ver `client-setup.md`, Escenario 5) antes de tocar esta config.

## 5. El único paso que sigue necesitando sudo en el host

Traefik rutea por el header HTTP `Host`, no solo por SNI — así que el
cliente `engram` tiene que resolver `ENGRAM_INGRESS_HOST` a `127.0.0.1`
de verdad, no solo en el `curl` de prueba. Agregar a `/etc/hosts`:

```
127.0.0.1 <ENGRAM_INGRESS_HOST>
```

Si ni siquiera eso está permitido en esa máquina, no hay forma de saltear
este punto con Docker solo — hace falta otra estrategia (proxy DNS de
usuario, por ejemplo) antes de seguir.

## 6. Configurar el cliente `engram`

```sh
engram cloud config --server "http://${ENGRAM_INGRESS_HOST}:${ENGRAM_PROXY_PORT}"
export ENGRAM_CLOUD_TOKEN="$(cat identity/engram.token)"
export ENGRAM_CLOUD_AUTOSYNC=1
engram cloud upgrade doctor --project jarvis_project
```

Para Claude Code específicamente, replicar el patrón de `~/.claude.json`
de [`client-setup.md`](../../client-setup.md) — entrada `mcpServers.engram`
de nivel superior, no la del plugin (el plugin no toma `env`).
