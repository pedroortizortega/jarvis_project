# server — configuración del lado "master" (tiene la CA)

Esto corre en la máquina que tiene la CA privada de Engram Cloud (hoy:
`trantor`, `~/.config/engram-cloud/pki/ca/`). No se copia a ninguna
máquina remota.

## `generate-client-cert.sh`

Versión parametrizada de los comandos `openssl` que ya estaban en
[`client-setup.md`](../../client-setup.md) Escenario 4 (y usados a mano
para la identidad `laptop-trabajo`, spec 011). Emite un certificado de
cliente nuevo para una identidad:

```sh
./generate-client-cert.sh <identidad>
# ej: ./generate-client-cert.sh laptop-casa
```

Después de correrlo, todavía falta (no lo hace el script, porque necesita
una sesión de admin autenticada contra el dashboard):

1. Crear el usuario gestionado (`POST /dashboard/admin/users`, role
   `member`).
2. Darle grant al proyecto (`POST /dashboard/admin/users/<id>/grants`).
3. Emitir su token de aplicación (`POST /dashboard/admin/users/<id>/tokens`
   — se muestra una sola vez, guardarlo ahí mismo).

Ver [`client-setup.md`](../../client-setup.md) sección "Antes que nada"
para el detalle de esos tres pasos.

## Qué más vive en el lado servidor (ya documentado, no repetido acá)

- Los dos units `systemd --user` que hacen de puente Tailnet
  (`engram-tailnet-serve`, `engram-traefik-port-forward`) — ver
  [`installation.md`](../../installation.md).
- La CA misma y su rotación — ver [`architecture.md`](../../architecture.md).
