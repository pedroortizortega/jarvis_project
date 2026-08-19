# Engram Cloud: private shared deployment

This is the first deployment slice for one persistent Engram Cloud backend. It
has two independently controllable paths to the same `engram-cloud` ClusterIP
service:

| Path | Entry point | Control boundary | Public exposure |
| --- | --- | --- | --- |
| LAN | `engram.lan` through Traefik | `lan-ingress.yaml` | Never |
| Tailnet | raw TCP `tailscale serve` to a localhost port-forward | host systemd units | Never |

Both paths terminate at the same Traefik router and require a client
certificate signed by `engram-client-ca`. Engram then requires its own bearer
other.

## Deliberate boundaries

- `engram-cloud` and `engram-postgres` are `ClusterIP` services only. There is
  no NodePort, LoadBalancer, Tailscale Kubernetes operator, or public Ingress.
- The LAN path is only the `Ingress` object. Deleting it removes LAN routing
  without deleting data or the Tailnet path.
- The Tailnet path forwards raw TCP, not HTTPS terminated by Tailscale. The
  original TLS handshake reaches Traefik, so Traefik can verify each client
  certificate. Do not replace this with `tailscale serve --https`: that would
  terminate TLS before mTLS reaches Traefik.
- K3s NetworkPolicy enforcement is disabled in this environment. No isolation
  claim in this deployment depends on a `NetworkPolicy`.
- `local-path` storage is node-bound. PostgreSQL is intentionally one replica
  with `Recreate`; this is not a highly available database design.

## Version and endpoint parameters

The deployment pins Engram to `ghcr.io/gentleman-programming/engram:v1.20.0`.
Before any upgrade, verify the release and replace the tag deliberately; do not
use `latest`.

There is no assumed local DNS zone. Every client needs a host mapping for the
same name used by the server certificate:

- `ENGRAM_HOSTNAME=engram.lan`
- `TRAEFIK_LAN_IP=<the already allocated Traefik LoadBalancer address>`
- `TRANTOR_TAILNET_IP=<trantor Tailnet IPv4 or IPv6 address>`

`TRAEFIK_LAN_IP` is an explicit operator parameter. The MetalLB pool is
`192.168.1.240-192.168.1.250`, but no free address is presumed or reserved by
these manifests. The known Traefik address is `.240`; verify it before use.
The TLS server certificate must contain `DNS:engram.lan`. The client CA secret
must contain a `ca.crt` key. Do not commit certificate authority, server key,
client keys, bearer tokens, database URLs, or generated secret YAML.

## Prerequisites

1. Traefik is installed and owns the expected `traefik` IngressClass.
2. The Traefik `TLSOption` CRD exists under `traefik.io/v1alpha1`.
3. An operator has a private CA and can issue one client certificate per
   identity plus the `engram.lan` server certificate.
4. `TRAEFIK_LAN_IP` is confirmed to be Traefik's existing allocation, not an
   assumed unused MetalLB address.
5. The selected Engram image tag can be pulled by every K3s node.
6. PostgreSQL storage capacity and backup/recovery ownership are established.

Check the cluster facts without changing it:

```sh
kubectl get ingressclass traefik
kubectl api-resources --api-group=traefik.io | grep TLSOption
kubectl -n kube-system get service traefik
kubectl get storageclass local-path
```

## Secret bootstrap

Run these commands from a secure operator shell. Substitute values from a
secret manager or protected files; the placeholders below are intentionally not
usable secrets. `ENGRAM_JWT_SECRET` must be at least 32 random bytes. The
server's `ENGRAM_CLOUD_TOKEN` is an application bearer requirement in addition
to mTLS.

```sh
kubectl apply -f kubernetes/engram/namespace.yaml

kubectl -n engram create secret generic engram-postgres-auth \
  --from-literal=POSTGRES_PASSWORD='<database-password-from-secret-manager>'

kubectl -n engram create secret generic engram-cloud-config \
  --from-literal=ENGRAM_DATABASE_URL='postgres://engram:<url-encoded-database-password>@engram-postgres.engram.svc.cluster.local:5432/engram_cloud?sslmode=disable' \
  --from-literal=ENGRAM_JWT_SECRET='<32-or-more-random-bytes>' \
  --from-literal=ENGRAM_CLOUD_TOKEN='<bootstrap-bearer-token>' \
  --from-literal=ENGRAM_CLOUD_ALLOWED_PROJECTS='jarvis_project'

kubectl -n engram create secret generic engram-client-ca \
  --from-file=ca.crt=/secure/path/engram-client-ca.pem

kubectl -n engram create secret tls engram-server-tls \
  --cert=/secure/path/engram-server-cert.pem \
  --key=/secure/path/engram-server-key.pem
```

The bootstrap bearer is injected only into the server by
`engram-cloud-config`; the manifest contains no secret value. Treat it as an
operator recovery credential. Enroll a distinct mTLS client identity for each
machine or agent and use Engram's authenticated managed-token enrollment flow
to issue a distinct client bearer token. Store each issued bearer only in that
client's local secret store or deployment secret. Do not distribute the server
bootstrap token to normal clients and do not put client bearer values in Git.

This slice does not invent an unverified token-issuance CLI or dashboard path.
After the server is available, use the version-pinned Engram Cloud management
flow to create, revoke, and rotate a token for each named identity. Record the
identity, certificate serial, allowed project, token creation time, and
rotation/revocation outcome in the operator's protected inventory.

## Apply and verify

Render the base before applying. The base deliberately excludes the LAN
Ingress, so a first apply does not make LAN reachable:

```sh
kubectl kustomize kubernetes/engram
kubectl apply --dry-run=client -k kubernetes/engram
kubectl apply --dry-run=client -f kubernetes/engram/lan-ingress.yaml
```

After all four secrets exist, apply and observe startup:

```sh
kubectl apply -k kubernetes/engram
kubectl -n engram rollout status deployment/engram-postgres
kubectl -n engram rollout status deployment/engram-cloud
kubectl -n engram get pods,service,ingress,tlsoption
kubectl -n engram logs deployment/engram-cloud
```

No HTTP health path is configured because this slice does not assume one. A
successful rollout only proves process startup, not successful client
authentication or sync. Verify those with one enrolled mTLS client using
`engram cloud status` and the project's normal sync workflow.

## LAN client procedure

On the LAN workstation, add a local hosts entry rather than assuming DNS:

```text
<TRAEFIK_LAN_IP> engram.lan
```

Install that machine's private key/certificate and CA certificate with file
permissions readable only by its owning account. Configure its HTTPS client or
system TLS stack to present that certificate for `engram.lan`. Then set the
following in the agent process environment, sourcing the token from its local
secret store:

```sh
export ENGRAM_CLOUD_AUTOSYNC=1
export ENGRAM_CLOUD_SERVER=https://engram.lan
export ENGRAM_CLOUD_TOKEN='<this-client-managed-bearer-token>'
```

## Tailnet bridge on trantor

The bridge is deliberately host-level. It does not create a Kubernetes
LoadBalancer, route a subnet, or use Funnel. It keeps a port-forward bound to
`127.0.0.1`, then uses a raw TCP `tailscale serve` listener to preserve the
client-to-Traefik TLS handshake.

Create `/etc/systemd/system/engram-traefik-port-forward.service` on trantor:

```ini
[Unit]
Description=Local Traefik TLS port-forward for Engram Tailnet access
After=k3s.service
Requires=k3s.service

[Service]
Type=simple
ExecStart=/usr/local/bin/kubectl -n kube-system port-forward --address=127.0.0.1 service/traefik 8443:443
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Create `/etc/systemd/system/engram-tailnet-serve.service` on trantor:

```ini
[Unit]
Description=Tailnet-only raw TCP access to Engram mTLS router
After=tailscaled.service engram-traefik-port-forward.service
Requires=tailscaled.service engram-traefik-port-forward.service

[Service]
Type=simple
ExecStart=/usr/bin/tailscale serve --tcp=443 tcp://127.0.0.1:8443
ExecStop=/usr/bin/tailscale serve --tcp=443 off
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable and inspect it:

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now engram-traefik-port-forward.service engram-tailnet-serve.service
sudo systemctl status engram-traefik-port-forward.service engram-tailnet-serve.service
tailscale serve status
```

The `--tcp=443` form is intentional. Never use `tailscale funnel`, and do not
substitute `tailscale serve --https` because it terminates TLS at Tailscale and
would prevent Traefik from validating the individual client certificate.

On a Tailnet client, map the same certificate name to trantor's Tailnet IP:

```text
<TRANTOR_TAILNET_IP> engram.lan
```

Then use the same three environment variables and its own mTLS identity and
managed bearer. Tailnet ACLs remain an additional outer control and should
allow only intended client identities to reach trantor on TCP 443.

## Agent onboarding limits

There are no existing Engram configuration locations for Hermes/Jarvis,
OpenCode, Claude Code, or Codex in this repository. This deployment does not
edit those tools or claim that setting an environment variable alone makes a
given launcher inherit it. For each target, identify the actual service,
shell, launcher, or MCP process that runs Engram and set the three variables
there. Also make its mTLS certificate and private key available to the HTTP
transport it uses.

| Target | Required operator action |
| --- | --- |
| Hermes/Jarvis | Add the variables, its client certificate configuration, and a unique managed bearer to the authoritative systemd environment or secret mechanism after locating it. |
| OpenCode | Add them to the OpenCode process environment or its verified local configuration, with a unique client certificate and bearer. |
| Claude Code | Add them to the process environment or verified project/user configuration used to launch Claude Code, with a unique client certificate and bearer. |
| Codex | Add them to the Codex process environment or verified launcher configuration, with a unique client certificate and bearer. |

For each client: first run `engram cloud config --server https://engram.lan`,
then `engram cloud enroll jarvis_project`, and confirm `engram cloud status`.
Use the version-pinned client documentation to verify its mTLS support before
either path safely and must not be enrolled.

## Enable and disable LAN safely

Enable LAN only after mTLS has been tested through the Tailnet bridge or an
equivalent controlled client:

```sh
kubectl apply -f kubernetes/engram/lan-ingress.yaml
kubectl -n engram get ingress engram-lan
```

Disable LAN without touching PostgreSQL, Engram, secrets, or Tailnet:

```sh
kubectl -n engram delete ingress engram-lan
```

Re-enable by applying the same tracked file. Confirm the Tailnet systemd units
remain active after either operation. To disable Tailnet separately, stop and
it is still needed for diagnostics.

## Backup and removal

Back up PostgreSQL before upgrades or removal. Removing the manifests does not
guarantee deletion of the local-path volume; inspect the PVC/PV and back up or
delete storage deliberately. To remove only this slice, delete the Ingress
first, disable the Tailnet unit, then delete the remaining resources only after
a verified database backup.
