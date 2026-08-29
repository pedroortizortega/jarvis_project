---
name: local-git-repository-operations
description: Use for safe local Git repository operations.
---

# Local Git Repository Operations

Use when the user asks whether local Git capabilities are available, or asks to inspect, modify, commit, branch, synchronize, recover, or otherwise manage a repository on disk.

## Capability discovery before claiming availability

1. Distinguish **catalog availability**, **installation**, and **enabled state**. A matching catalog search result is not proof that a skill is installed or usable.
2. Query the live Hermes inventory with:
   ```bash
   hermes skills list
   ```
   Verify the skill name, source, trust level, and `enabled` status.
3. If the request is to find an installable skill, use:
   ```bash
   hermes skills search "local git repository" --limit 15 --json
   hermes skills search "git workflow" --limit 15 --json
   ```
4. Inspect an external skill before installation. With the current CLI resolver, use the catalog display name if its bare identifier is ambiguous:
   ```bash
   hermes skills inspect "Git cli"
   ```
   Read the preview for destructive commands, credential handling, and unexpected network or filesystem actions.
5. Never install a community skill without the user's confirmation. Report source and trust level first.

## Repository procedure

1. Discover context safely: run `git status --short --branch` and `git rev-parse --show-toplevel` from the intended directory.
2. Start with read-only commands: `git status`, `git diff`, `git diff --staged`, `git log`, and `git remote -v`.
3. Before any write, name the exact repository, branch, files, and intended side effect. Ask for confirmation when staging, committing, changing refs, fetching/pulling, pushing, stashing, rebasing, merging, or discarding changes.
4. Treat `git reset --hard`, `git clean -fdx`, force pushes, destructive checkout/restore, history rewrites, and reflog expiry as high-risk. Explain impact and provide a reversible alternative first.
5. Before staging untracked infrastructure or configuration, inspect applicable repository instructions, `git check-ignore`, and nested Git checkouts. Stage explicit reviewed paths rather than `git add .` when credentials, generated files, or third-party checkouts may be present.
6. Handle secrets as data, not encoding: Base64 in a Kubernetes `Secret` is not protection. Keep real values in an ignored local `.env` (prefer mode `600`) or a pre-existing external secret store; commit only an `.env.example` and workload references to the Secret. For Kubernetes, use an idempotent provisioning pattern such as `kubectl create secret generic <name> --from-env-file=<ignored-env> --dry-run=client -o yaml | kubectl apply -f -`.
7. Before committing, run `git diff --cached --check`, scan staged content for high-confidence credential forms without printing their values, and validate changed Kubernetes manifests with `kubectl apply --dry-run=client -f <manifest>` when available. After committing, confirm `git status --short` is empty and prove ignored credential paths are absent from `HEAD`.
8. Verify the requested result with follow-up read-only commands, then report evidence rather than merely declaring success.

## Reporting

- Say **installed and enabled** only after live inventory confirms both.
- Say **available in the catalog** for a search result that has not been installed.
- State whether a skill is builtin, local, or community-sourced; do not describe all listed GitHub skills as a generic local-Git skill.

## Notes

The hub-installed `git-cli` skill is appropriate for general local repository operations, but it is community-sourced. Its installed status must always be checked from the live inventory, not inferred from a previous session.
