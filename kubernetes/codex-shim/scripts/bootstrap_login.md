# codex-shim credential bootstrap (D16, task 1.4 — manual, one-time)

`codex-shim` needs its **own** dedicated Codex OAuth session, separate from
the Hermes CLI's `~/.hermes/auth.json` credential (design.md D16). OAuth
refresh tokens are single-use and rotate server-side on every refresh —
sharing one token pair between two independent refreshers means whichever
client refreshes second gets `refresh_token_reused` and silently logs the
other one out. This is a mechanic, not a policy choice.

**This step is inherently interactive and cannot be automated by an agent.**
The proposal's explicit non-goal is "Performing the OAuth login itself from
the panel" — login stays a user-driven, out-of-band flow.

## Prerequisites

- A terminal with network access to `https://auth.openai.com` and a browser
  available for the OAuth redirect (same requirement as any `codex login` /
  `hermes auth` flow).
- `kubectl` access to the `llms` namespace with permission to create Secrets
  (this is a one-time bootstrap step; the running `codex-shim` Pod itself
  only has `get`/`patch`/`update` on the named Secret, per D13 — it cannot
  create it).

## Steps

1. Run a Codex OAuth login **using tooling separate from any existing Hermes
   session** — e.g. the standalone `codex` CLI's `codex login` (do **not**
   reuse `hermes auth`, which would touch the Hermes-owned credential this
   design explicitly keeps separate). Complete the interactive browser
   consent flow.

2. **D-OQ3 validation (task 1.4):** immediately after completing step 1,
   confirm Hermes's own existing Codex session is still valid (e.g.
   `hermes model` or any Hermes-side Codex-backed call still succeeds). If
   Hermes's session was evicted, this account tolerates only one live Codex
   OAuth session — **stop here** and flag the cloud-shim design for
   re-scoping (a second dedicated login is not viable) rather than
   proceeding to step 3.

3. Extract the resulting `access_token` and `refresh_token` from the fresh
   login's local credential file (path depends on which tool was used for
   step 1 — inspect its documented auth-storage location; do not print the
   values to a shared terminal/log).

4. Create the `codex-shim-auth` Secret directly (never through a checked-in
   manifest — this Secret holds live credentials):

   ```sh
   kubectl create secret generic codex-shim-auth \
     --namespace llms \
     --from-literal=access_token="$ACCESS_TOKEN" \
     --from-literal=refresh_token="$REFRESH_TOKEN"
   ```

   `codex-shim` derives and caches `expires_at` itself from the access
   token's JWT `exp` claim on first read (D12) — no need to set it here.

5. Create the static internal bearer Secret consumed by LiteLLM's `cloud`
   entry (`api_key: os.environ/CODEX_SHIM_KEY`) and by `codex-shim`'s own
   `/v1/*` internal-bearer check (`CODEX_SHIM_INTERNAL_KEY` env, wired via
   `deployment.yaml`):

   ```sh
   kubectl create secret generic codex-shim-key \
     --namespace llms \
     --from-literal=internal-key="$(openssl rand -hex 32)"
   ```

6. Unset `$ACCESS_TOKEN`/`$REFRESH_TOKEN` from the shell and clear scrollback
   containing them.

7. Deploy `codex-shim` (`kubectl apply -k kubernetes/codex-shim/`) and
   confirm `GET /internal/session` (cluster-internal — `kubectl port-forward`
   or exec a probe from another Pod in `llms`) reports `state: "valid"`.
   This is also the point where design.md's D-OQ1/D-OQ4 migration-step-2
   validation happens (task 4.4/4.5) — do not force an extra refresh to test
   this; use the token as freshly minted by step 1.
