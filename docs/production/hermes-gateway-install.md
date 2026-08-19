# Production runbook: installing a persistent Hermes gateway

Stand up Hermes as a persistent, auto-restarting host service (not a
Kubernetes pod — see [Two runtimes, one machine](../architecture/README.md#two-runtimes-one-machine)
if that split isn't clear yet) using
[`hermes-native/scripts/install-hermes.sh`](../../hermes-native/scripts/install-hermes.sh).

## What this script does and doesn't do

This implements the `role=primary`, `mode=bootstrap` path of the installer
contract defined in **specs/004_hermes_native_clone_systemd.md** — a
brand-new host, no existing state to restore. That's the actual scenario
that matters today: this project has one host (`trantor`), one primary, no
standby, no worker nodes.

**Deliberately not implemented** (the script fails closed and tells you why
if you ask for these, rather than faking support):

| Not implemented | Why | What to do instead |
|---|---|---|
| `mode=memory-seed` / `mode=primary-restore` | Restoring real state (SQLite, sessions, OAuth) from a backup archive is high-stakes and untested in script form | Follow spec 004's Fase 4 by hand |
| `role=standby` / `role=worker` | No second host exists yet to install onto | Follow spec 004's Fase 10 by hand when one does |
| Fase 6 (custom skill sync) | `hermes-native/skills/` doesn't exist in this repo yet (spec 004's own pending item) | Nothing to sync until that directory exists |
| Vendoring the official installer | Spec 004 requires a checksum-pinned local copy, not `curl \| bash` against a mutable upstream script; none is vendored yet | See [Before you run it](#before-you-run-it) below |

**What it does implement, faithfully:** preflight (non-root, arch, DNS,
LiteLLM reachability, disk space), system dependency install
(Arch/CachyOS and Debian/Ubuntu), the vendored+checksum-verified installer
invocation, declarative config apply (`SOUL.md`, `model.base_url`,
`terminal.backend`), **reconciling all nine Codex profiles** from
`kubernetes/hermes/profiles/profiles.yaml` (real file, real structure —
`luna-{low,medium,high}`, `terra-{low,medium,high}`, `sol-{low,medium,high}`),
`.env` creation with a `0600` template (never accepts secrets as CLI
arguments — spec 004 rule 7), `hermes doctor` + one-shot validation, and
systemd install that **always leaves the unit disabled and inactive** unless
you pass `--start` *and* type the literal word `yes` at an interactive
fencing checklist.

## Before you run it

The vendored official installer isn't in this repo yet
(`hermes-native/vendor/install-hermes.sh` — spec 004's own "trabajo
pendiente" item 5). The script will tell you exactly this and exit `20` if
it's missing. To provide it:

```bash
# Get the real installer from the Hermes project's own install instructions,
# save it here, then pin its checksum:
sha256sum hermes-native/vendor/install-hermes.sh > hermes-native/vendor/install-hermes.sh.sha256
```

Never `curl | bash` it directly — the whole point of vendoring is that a
pinned `--commit` on an unpinned installer script still lets upstream
change what runs *before* your commit is even checked out.

## Running it

```bash
hermes-native/scripts/install-hermes.sh \
  --litellm-url http://192.168.1.241:4000/v1 \
  --allow-insecure-http
```

(`--allow-insecure-http` is needed today because LiteLLM is plain HTTP on
the LAN — see spec 004's security criteria for why this is flagged, not
silently accepted, and what "properly" looks like: HTTPS, or a
per-host-revocable key instead of the shared master key.)

Add `--enable-browser` only if you actually need Playwright/Chromium (skips
by default — most servers don't need it). Add `--start` only when you're
ready to go through the fencing checklist and actually flip this host live.

## After it finishes

The unit is installed, disabled, and inactive — nothing is running yet.
Confirm:

```bash
systemctl list-unit-files 'hermes*' --no-pager
HERMES_HOME=~/.hermes hermes gateway status
HERMES_HOME=~/.hermes hermes profile list
```

Fill in the real secrets in `~/.hermes/.env` (the script wrote a `0600`
template with every key commented out) — at minimum `OPENAI_API_KEY` and,
for a primary, `TELEGRAM_BOT_TOKEN` + `TELEGRAM_ALLOWED_USERS`.

Then, only once you've confirmed no other gateway anywhere holds the same
Telegram token:

```bash
hermes-native/scripts/install-hermes.sh \
  --litellm-url http://192.168.1.241:4000/v1 \
  --allow-insecure-http \
  --start
# type "yes" at the fencing prompt
```

Full functional validation checklist (Telegram ping, a real Qwen query, a
Codex profile query, a non-destructive terminal command, a service restart,
a host reboot test) is in **specs/004, "Fase 11 - Validacion funcional"** —
run through it before considering the install done.

## See also

- `specs/004_hermes_native_clone_systemd.md` — the full installer contract
  this script implements a scoped slice of, including the parts it doesn't
  (restore modes, standby/worker roles, promotion procedure, rollback to
  Kubernetes)
- [Architecture overview](../architecture/README.md#hermes-gateway-host)
- [Kubernetes cluster bootstrap](kubernetes-bootstrap.md) — the companion
  runbook for the other half of this project; Hermes needs a reachable
  LiteLLM before this script's preflight will pass
