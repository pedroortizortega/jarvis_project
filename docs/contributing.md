# Contributing

How to make a change in this repo, from "I have an idea" to "it's merged" —
grounded in the conventions this project's own history actually follows,
not an idealized process nobody uses.

## Quick path

1. Is this a real feature/architecture decision, or a small fix? See
   [Do you need a spec?](#do-you-need-a-spec) to decide.
2. One branch per **independent** change — never bundle unrelated fixes
   into one PR. See [Branching](#branching).
3. Conventional commits, no AI attribution trailers. See
   [Commit messages](#commit-messages).
4. Test the way the specific service you touched is tested — see
   [Testing](#testing-by-service), it's different per subsystem.
5. Push, open a PR against `main`, one logical unit of work. See
   [Pull requests](#pull-requests).

## Do you need a spec?

This repo follows **spec-driven development (SDD)** via OpenSpec for
anything that's a real capability or architecture decision — not for every
change.

| Change size | Process |
|---|---|
| New capability, new service, architecture decision | OpenSpec change proposal first (`openspec/changes/<name>/`), then a numbered `specs/NNN_*.md` once implemented | 
| Bug fix, drift correction, doc fix, small refactor | Just do it — branch, fix, PR. No proposal needed. |

The `sdd-*` Claude Code skills (`sdd-new`, `sdd-explore`, `sdd-apply`,
`sdd-verify`, `sdd-archive`, ...) operate the OpenSpec workflow end to end.
`openspec/config.yaml` declares the process rules (RFC 2119 keywords in
specs, Given/When/Then scenarios, `strict_tdd: true`).

The lifecycle, concretely (see `openspec/changes/archive/2026-08-19-memory-router/`
for a real, complete example to copy the shape of):

1. `openspec/changes/<name>/proposal.md` + `design.md` + delta `specs/`
2. Implementation, following the design
3. A numbered `specs/NNN_<name>.md` documenting what actually shipped
4. Archive: the change moves to `openspec/changes/archive/<date>-<name>/`,
   its durable capability specs get promoted to `openspec/specs/`

## Spec numbering — the one rule that's bitten this repo before

`specs/NNN_<name>.md` numbers **must be unique and sequential**. This isn't
theoretical: two files both claimed `010` in this repo's actual history
(`010_jarvis_voice_piper.md` and `010_jarvis_k8s_ingress_sdd.md`) before
being caught and fixed by renumbering the later one to the next free slot.

**Before creating a new numbered spec:**

```bash
git ls-tree -r --name-only main -- specs/ | sort
```

Use the number one past the highest one shown — including numbers that only
exist on an open PR's branch, not yet on `main`. If you're not sure whether
another PR already claimed the next number, ask before you pick one; two
PRs racing for the same number is exactly how the `010` collision happened.

## Branching

**One branch, one PR, one independent logical change.** This repo's own
history is the model to follow: a single session that touched five
unrelated things (a feature, a `.gitignore` fix, a namespace-drift bug fix,
a spec renumbering, and an unrelated runbook fix sitting uncommitted) became
**five separate PRs**, not one. If you catch yourself about to commit two
unrelated fixes together because "I'm already in there," stop and split
them — a reviewer (or you, in six months) shouldn't have to untangle
unrelated diffs to understand or revert one of them.

Branch names follow `type/short-description` (matches the commit type
below): `feat/gpu-handoff-web-panel`, `fix/engram-namespace-drift`,
`docs/glossary`, `chore/openspec-config`.

Branch from `main`, not from another feature branch — even if your change
is related, branching from an in-flight branch means you inherit its
history and its eventual merge conflicts. If two changes are genuinely
related but reviewable independently, still give them separate branches
and let PRs land in order.

## Commit messages

**Conventional commits, always. No AI attribution trailers, ever** — no
`Co-Authored-By`, no "Generated with Claude Code" line, regardless of who
or what wrote the change.

```
type(scope): short description

Longer body if the "why" isn't obvious from the diff alone — what
was broken, what was found, what the tradeoff was. Not required for
small, self-explanatory changes.
```

Types actually used in this repo's history: `feat`, `fix`, `docs`, `chore`,
`refactor`, `merge`. Scope is the service/area touched
(`feat(model-panel): ...`, `fix(engram): ...`, `docs(specs): ...`) — omit it
only for changes that don't belong to one specific area
(`chore: gitignore local .atl/ skill-registry cache`).

## Pull requests

Every PR in this repo targets `main` directly (no intermediate integration
branch). Write a body with:

- **Summary** — what changed and why, 2-4 bullets
- **Verification** — what you actually ran and what it showed (test counts,
  commands, output) — not "should work," show that it does
- Anything the change deliberately does **not** cover, if that's not obvious
  from the summary alone (see `hermes-native/scripts/install-hermes.sh`'s
  PR for an example: explicitly listing what was scoped out and why)

There's no CI in this repo (no `.github/workflows/`) — you are the CI.
Run the real test suite for whatever you touched (see below) and paste real
output into the PR body, not a claim that it passes.

## Testing by service

There's no single root test command — each subsystem has its own runner and
sometimes its own venv. Don't assume; check the specific service's own doc
in [`docs/services/`](services/) for the exact, verified command. Summary:

| Path | Runner | Needs its own venv? |
|---|---|---|
| `kubernetes/model-panel/` | `pytest` | Yes (`requirements.txt`) |
| `kubernetes/codex-shim/` | `pytest` (`asyncio_mode = auto`) | Yes (`requirements.txt`) |
| repo root `tests/` (memory-router + shared-mcp-services) | `python -m unittest discover -s tests` | No — runs against system Python directly |
| `hermes-native/orchestration/` | `python -m unittest discover -s tests` | Yes — `pip install -e .` first |
| `hermes-native/knowledge-vault/` | `python -m unittest discover -s tests` | Yes — `pip install -e .` first |
| `kubernetes/llama-service/`, `kubernetes/proxy/` | none | — manifests + shell only, verify with `kubectl apply --dry-run` |

If you add a new Python package under `hermes-native/`, give it its own
`pyproject.toml` in its own subdirectory — `hermes-native/knowledge-vault/`,
`hermes-native/memory-router/`, and `hermes-native/orchestration/` each
already do this. The real reason it matters, not just style:
`hermes-intent-orchestration` gets `pip install -e`'d directly into the
*live* Hermes gateway's venv, so bundling unrelated dependencies into that
install is an operational risk. This already caused a real path collision
once — `hermes-native/orchestration/` briefly held two unrelated packages'
`pyproject.toml` at the same path — fixed by giving memory-router its own
directory. See [docs/services/memory-router.md](services/memory-router.md)
and [docs/services/hermes-intent-orchestration.md](services/hermes-intent-orchestration.md).

## Before you touch production docs or scripts

This repo has already caught (and documented) real gaps between what's
committed and what's actually running — see
[Known drift and gotchas](architecture/README.md#known-drift-and-gotchas).
If you're writing or updating anything under `docs/production/` or a script
meant to touch the live cluster/host:

- **Verify against the real system**, don't trust an older doc or your own
  assumption. `kubectl get ns`, `kubectl get svc -A`, `systemctl status
  <unit>` are cheap; a doc that confidently states the wrong namespace isn't.
- If you find a real gap (like the `engram` vs `mcps` namespace drift), fix
  the drift **and** document what you found — both, not just one.
- Never write a script that assumes it's safe to run destructively against
  `trantor`'s live state. `hermes-native/scripts/install-hermes.sh` is the
  model here: it fails closed on anything it can't safely automate, and
  never starts a service without an explicit, informed confirmation.

## Secrets

Never commit a real credential, token, or key — not even "temporarily" or
in a `.example` file with a placeholder that looks real. Every Secret this
repo's manifests reference (`litellm-auth`, `engram-*`, `memory-router-*`,
`brave-api-key-secret`, `codex-shim-auth`) is created out-of-band, by hand,
documented in the relevant service's own doc under `docs/services/` or
`docs/production/`.

## See also

- [Architecture overview](architecture/README.md)
- [Glossary](glossary.md)
- [Per-service deep dives](services/) — each has its own verified test
  commands and "Common modifications" section
- [Production runbooks](production/) — cluster bootstrap and Hermes install
