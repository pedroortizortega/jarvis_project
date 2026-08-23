# memory-router bootstrap scripts

Reproducible record of the deployment sequence that was run and verified
by hand on `trantor` on 2026-08-21 (`specs/014_memory_router.md` §8-9).
Use this to bring memory-router up from zero on a new machine/cluster.

## Order

```
00-config.sh                    # shared env vars — sourced, not run directly
01-build-image.sh                # docker build + k3s ctr images import (needs sudo)
02-generate-pki.sh                # own CA, server cert (SAN = MR_HOST), client certs per identity
03-create-secrets.sh              # the 6 k8s secrets (4 memory-router, wired from 02's PKI output, + 2 hindsight)
04-deploy-traefik-entrypoint.sh   # dedicated Traefik entryPoint (restarts shared Traefik)
05-deploy-manifests.sh            # the 9 memory-router-*.yaml + hindsight-*.yaml manifests
06-verify.sh                      # port-forward healthz + mTLS+bearer per identity + negative control
```

Run them all with `./deploy-all.sh`, or step by step to review each stage.
Every step is idempotent — re-running skips or rotates in place instead of
failing on "already exists".

## Two steps need you at the keyboard

`sudo` needs a real TTY for the password prompt, which this can't supply
non-interactively:

1. **01-build-image.sh** — image import into k3s containerd:
   ```bash
   docker save memory-router:local | sudo k3s ctr images import -
   ```
2. **06-verify.sh** (or before, manually) — `/etc/hosts` entry, since
   Tailscale MagicDNS doesn't resolve subdomains:
   ```bash
   echo "192.168.1.240 memory-router.trantor.tail07dff9.ts.net" | sudo tee -a /etc/hosts
   ```

Both scripts detect the missing step and print the exact command instead
of hanging — run it yourself, then re-run the script.

## `05-deploy-manifests.sh` does NOT rebuild the image

`kubectl apply` only propagates *manifest* changes (env vars, secrets,
resources). A *code* change under `hermes-native/memory-router/` has no
effect in the cluster until you explicitly re-run `01-build-image.sh`
(rebuild + `docker save | sudo k3s ctr images import -`) **and**
`kubectl rollout restart deployment/memory-router` — `05-deploy-manifests.sh`
alone will not detect or trigger this.

Found live (2026-08-22, `specs/022_hindsight_deployment.md` §8.1 Bug 2):
a port-default fix landed and merged in code, `05-deploy-manifests.sh`
ran clean, but the running pod kept using the pre-fix port for hours
because nobody rebuilt the image. `deploy-all.sh` does not chain
`01-build-image.sh` automatically either, precisely because its `sudo`
step cannot run non-interactively — don't assume a clean `deploy-all.sh`
run means the code in the pod matches the code in the repo.

## Overriding for a different machine/cluster

Every `MR_*` variable in `00-config.sh` has a default matching `trantor`.
Override via environment, e.g.:

```bash
MR_HOST=memory-router.newhost.tailxxxx.ts.net \
MR_LB_VIP=192.168.1.50 \
./deploy-all.sh
```

**Caveat:** `memory-router-ingress.yaml` and `memory-router-deployment.yaml`
in the parent directory hardcode the hostname and image tag as plain,
reviewable manifests (not templated on purpose — see spec 014 §8's
rationale for keeping deployment shape reviewable). If you override
`MR_HOST` or `MR_IMAGE_TAG` away from their defaults, edit those two files
to match before running `05-deploy-manifests.sh`.

## Why a dedicated hostname, not just a dedicated port

`04-deploy-traefik-entrypoint.sh` gives memory-router its own Traefik
entryPoint — necessary but **not sufficient** to isolate its mTLS client CA
from Engram's. Traefik resolves the TLS server certificate by SNI
(hostname) against one certificate store shared by the whole instance,
not scoped per entryPoint. Sharing a hostname with `engram-tailnet` meant
the dedicated port still served Engram's certificate and silently skipped
requiring a client certificate — found and fixed by live-testing the
actual TLS handshake, not by inspecting config. `02-generate-pki.sh`'s
server cert and `memory-router-ingress.yaml`'s `Host()` match are what
actually isolate the two. `06-verify.sh`'s negative control
(`certificate required` without a client cert) is what proves it's
actually enforced, not just configured.

## What this does NOT automate

- Distributing `ca.crt` + each identity's cert/key/bearer to a **different**
  physical machine (today all 4 identities run on the same host as the
  bootstrap script). That's the same manual pattern spec 011 already uses
  for remote Engram clients — copy the files over a secure channel, add the
  `/etc/hosts` entry on that machine too.
- Validating any backend adapter (Honcho/Cognee/Graphiti/knowledge-vault)
  against a real instance — still an explicit follow-up in each of their
  specs (016-019), tested so far only against a stubbed HTTP transport.
  Hindsight is validated end-to-end against a real deployed instance —
  see `specs/022_hindsight_deployment.md` §8.1.
