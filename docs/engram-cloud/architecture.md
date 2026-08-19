# Arquitectura

## Componentes

```
┌──────────────────────────────────────────────────────────────────┐
│  Kubernetes (namespace dedicado, ej. "engram" o "mcps")          │
│                                                                    │
│   Deployment engram-cloud (1 réplica)                             │
│     imagen: ghcr.io/gentleman-programming/engram:vX.Y.Z           │
│     comando: engram cloud serve                                   │
│     lee: Secret engram-cloud-config (DB URL, JWT secret, bearer,  │
│           proyectos permitidos)                                   │
│         │                                                          │
│         ▼ postgres://...                                           │
│   Deployment engram-postgres (1 réplica, strategy: Recreate)      │
│     PVC (StorageClass del clúster, ej. local-path en K3s)         │
│                                                                    │
│   Service engram-cloud (ClusterIP:8080)  ◄────────────┐            │
└─────────────────────────────────────────────────────────┼────────┘
                                                            │
                          ┌──────────────────────────────────┴───┐
                          │   Traefik Ingress + TLSOption          │
                          │   RequireAndVerifyClientCert (mTLS)    │
                          └───────┬─────────────────────────┬─────┘
                                  │                          │
                    ┌─────────────┴──────┐      ┌────────────┴──────────────┐
                    │  Camino LAN         │      │  Camino Tailnet            │
                    │  Ingress host:      │      │  bridge systemd:           │
                    │  <nombre>.lan       │      │  kubectl port-forward      │
                    │  vía LoadBalancer   │      │  → tailscale serve --tcp   │
                    │  (MetalLB, etc.)    │      │  (preserva el handshake    │
                    └─────────────┬──────┘      │  TLS hasta Traefik)         │
                                  │              └────────────┬──────────────┘
                            Cliente LAN                  Cliente Tailnet
```

## Por qué está diseñado así

**ClusterIP, nunca `LoadBalancer`/`NodePort` directo.** El único punto de
entrada externo es Traefik, que ya está en el clúster para otras cosas.
Esto evita duplicar superficie de exposición y mantiene un solo lugar
donde se aplica mTLS.

**mTLS + bearer, dos factores de transporte independientes.** El
certificado de cliente lo verifica Traefik (`TLSOption` con
`RequireAndVerifyClientCert`) antes de que el tráfico llegue siquiera a la
aplicación. El bearer token lo verifica la aplicación (`engram cloud
serve`) después. Ninguno sustituye al otro — un atacante necesitaría
comprometer ambos.

**Dos caminos de red, mismo servicio.** Tanto el Ingress LAN como el
bridge Tailnet terminan en el mismo `Service`. No son mecanismos de auth
distintos, son solo formas distintas de que el tráfico *llegue* a Traefik.
La autenticación real (mTLS + bearer) es idéntica por cualquiera de los
dos caminos.

**El bridge Tailnet reenvía TCP crudo, no HTTPS.** `tailscale serve --tcp`
en vez de `tailscale serve --https` es deliberado: si Tailscale terminara
el TLS, Traefik nunca vería el certificado de cliente original y el mTLS
dejaría de funcionar. El flujo real es:

```
cliente Tailnet
  → tailscale serve --tcp=443 (en el nodo servidor, reenvía TCP crudo)
  → kubectl port-forward local (service/traefik 8443:443)
  → Traefik (termina TLS, valida el cert de cliente, rutea por Host header)
  → Service engram-cloud
```

**Traefik rutea por el header HTTP `Host`, no solo por SNI.** Esto importa
para cualquier proxy o túnel local que arme un cliente: si el cliente
manda `Host: 127.0.0.1:<puerto>` en vez del hostname real configurado en
el `Ingress`, Traefik no encuentra la ruta y devuelve 404 aunque el mTLS
haya pasado. Ver [client-setup.md](client-setup.md) para el patrón que
resuelve esto.

**Identidad por cliente, no un token compartido.** Cada agente/máquina
tiene su propio certificado de cliente (`CN=<identidad>`) y su propio
token de aplicación, emitidos desde un usuario administrador gestionado
por Engram Cloud (no el bearer de arranque del servidor, que es solo un
credential de recuperación de operador). Esto permite revocar el acceso
de un cliente sin afectar a los demás.

## Modelo de datos y sync

Cada máquina que corre `engram` (CLI o MCP) mantiene su propia base
SQLite local (`~/.engram/engram.db`) — es **local-first**: todo se guarda
ahí primero, sin depender de la red. La sincronización a la nube
(`engram sync --cloud --project <p>`, o `ENGRAM_CLOUD_AUTOSYNC=1` para que
pase en segundo plano) empuja los cambios locales al servidor central por
proyecto, y los trae de vuelta para que todos los clientes converjan en
el mismo estado.

Un único proceso `engram` local (por ejemplo el daemon `engram serve` en
`:7437`) puede atender a *todos* los agentes de esa misma máquina a la
vez — no hace falta una base SQLite por herramienta, es una por usuario
del sistema operativo.

## Aislamiento de red — límites reales

Si el clúster tiene `NetworkPolicy` deshabilitado (común en instalaciones
K3s de un solo nodo sin CNI que lo soporte), la única garantía de
aislamiento real es el mTLS + bearer — no hay una capa de red adicional
separando "quién puede intentar conectarse" de "quién puede autenticarse
con éxito". Es aceptable siempre que el mTLS esté correctamente exigido
(`RequireAndVerifyClientCert`, no `RequestClientCert`), pero es importante
no asumir que el Ingress LAN y el Ingress Tailnet son fronteras de
seguridad distintas: son la misma frontera (mTLS) alcanzable por dos
rutas de red diferentes.
