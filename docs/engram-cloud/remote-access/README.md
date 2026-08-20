# Acceso remoto — configuración reusable

Plantillas versionadas para sumar una máquina remota nueva a Engram Cloud
cuando esa máquina **no tiene `kubectl`** y, opcionalmente, **tampoco tiene
permisos de administrador del sistema operativo** (ej. una laptop de
trabajo gestionada por IT). Es la versión "archivo, no solo prosa" del
patrón documentado en [`client-setup.md`](../client-setup.md), Escenario 5.

Dos carpetas, según de qué lado del mTLS estás parado:

- [`server/`](server/) — corre en la máquina que tiene la CA (hoy,
  `trantor`). Emite el certificado de cliente y el token de aplicación
  para cada identidad nueva. **Nadie fuera de esa máquina necesita esto.**
- [`client/`](client/) — plantilla genérica para la máquina remota. No
  incluye ningún secreto: el certificado, la clave privada y el token de
  cada identidad se generan con `server/` y se copian a mano por un canal
  seguro, nunca se commitean.

## Por qué no hay secretos acá

Un certificado de cliente y un token de aplicación son credenciales reales
— igual que una contraseña. Si un archivo así entra al historial de git,
queda ahí para siempre aunque después se borre el archivo. Por eso:

- `client/` solo tiene la plantilla (`docker-compose.yml`, `Dockerfile`,
  instrucciones) con placeholders — nunca un cert/key/token real.
- `.gitignore` de esta carpeta bloquea cualquier `client/secrets/` o
  archivo `*.key`/`*.crt`/`*.token` que alguien deje ahí por error.
- Cada identidad nueva revoca por separado desde el dashboard admin de
  Engram Cloud (`/dashboard/admin/users/<id>`) si se pierde la máquina o
  se compromete el secreto — no hay credencial compartida entre clientes.

Ver [spec 011](../../../specs/011_engram_cloud_centralized.md) §7.1 para el
historial de por qué existe este patrón y su primera validación end-to-end.
