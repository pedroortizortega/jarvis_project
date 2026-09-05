# Proposal: Hermes Per-Subagent Model Routing (llama-router presets)

## ⚠️ Code target lives OUTSIDE this repo

The live Hermes (`hermes-gateway.service`, `~/.local/bin/hermes`) runs from a
**separate git repo**: `/home/pedro/.hermes/hermes-agent/` (user's fork of
`NousResearch/hermes-agent`, branch `fix/mcp-client-certificate-context`).

- **Real edit target**: `/home/pedro/.hermes/hermes-agent/tools/delegate_tool.py`.
- `kubernetes/docker/hermes-agent/` in jarvis_project is a **gitignored, untracked
  scratch clone** kept for reading only. `kubernetes/docker/hermes.Dockerfile`
  installs Hermes via `curl .../install.sh`, never copies it. **Do not edit it.**
  Only `tools/delegate_tool.py` was synced there for this planning round.
- jarvis_project hosts SDD artifacts only.
- **Delivery concern for design/tasks**: `sdd-apply` must not commit to the user's
  fork unreviewed. *Recommended*: produce a **patch/diff for the user to apply and
  commit themselves**; tasks phase finalizes (alternative: dedicated branch in the
  fork).

## Intent

`delegate_task()` (line 3597) fans out subagents, but the batch loop
(3828–3861) passes one call-level `creds["model"]` (line 3848; async path 4251,
resolved by `_resolve_delegation_credentials`, 4417) to every child. Per-task
dicts read only `goal`/`context`/`role`. A cheap grep and a hard refactor burn
the same preset. `_build_child_agent()` (line 1578) **already** accepts per-call
`model` + credential overrides — the gap is one unwired field.

## Scope

### In Scope
- Optional per-task `model` on `delegate_task` task dicts; absent = today's behaviour.
- Allowlist validation, rejected **before any child spawns**, error naming valid choices.
- GPU safety: never run two *different* local presets concurrently.
- Tool description/schema update so the parent LLM can use the field.

### Out of Scope
- `specs/009`'s full lock/queue/drain coordinator (`local_large`).
- Any automatic complexity classifier.
- `litellm-config.yaml` (already complete: `cloud` + 3 presets registered) and `router-config.yaml`.
- Model-panel global switch, `model.default`, `switch-model.sh`.
- Pre-existing `litellm_callbacks.py` `qwen3` allowlist gap and the `model.default` alias drift.

## Resolved Product Decisions

1. **Who picks** — the caller. Parent LLM names the model per task at dispatch.
   Matches "dependiendo de lo que se necesite"; no classifier infra. Automatic
   classification: rejected/deferred.
2. **Allowlist** — `qwen3.5-9b`, `qwen3.6-27b-q3`, `qwen3.8-27b-iq2s`, plus
   `cloud` (gpt-5.6-sol, litellm-config.yaml:154). All already registered, zero
   extra cost. Excluded: `qwen3` (vLLM), `qwen3.6-27b-q6`, `qwen3.6-27b` (alias).
3. **GPU concurrency** — one RTX 4070 Ti SUPER, `--models-max 1`
   (`deployment-router.yaml:57`), LRU eviction on load. Group children by
   resolved model: **distinct local presets run sequentially**, same-preset keeps
   existing parallelism, `cloud` is GPU-exempt and runs freely. Mixed batches
   succeed but are slower — rejecting them would defeat the feature.
4. **Coordinator** — grouped serialization is the MVP guard, not a scheduler.
   Spec 009's lock/queue/drain coordinator stays explicit future work.
5. **Config location** — the live config Hermes reads is `~/.hermes/config.yaml`,
   **not** the K8s ConfigMap. jarvis_project's `kubernetes/hermes/config/config.yaml`
   is a checked-in reference copy (currently identical except one env var).
   *Recommended*: if a `delegation.allowed_models` key is added, write **both**
   copies. Drift risk is real — same class of bug as the `model.default` drift.

## Capabilities

### New Capabilities
- `subagent-model-routing`: per-task model selection for delegated subagents, its
  allowlist, and its GPU-serialization rule.

### Modified Capabilities
- None.

## Approach

Read `t.get("model")` in the task loop, validate against the allowlist, pass it
as `_build_child_agent(model=...)` instead of `creds["model"]`. Credential
overrides stay unchanged (all four targets sit behind the same LiteLLM base_url,
`http://192.168.1.241:4000/v1`). Execution groups children by resolved model so
no two local presets are ever in flight together.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `/home/pedro/.hermes/hermes-agent/tools/delegate_tool.py` (external repo) | Modified | Per-task `model`, allowlist, grouped execution, tool schema |
| `~/.hermes/config.yaml` (host, live) | Modified (optional) | `delegation.allowed_models` |
| `kubernetes/hermes/config/config.yaml` (reference copy) | Modified (optional) | Keep in sync with the above |
| `specs/025_*.md` | New | Numbered spec per repo convention |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Edits land in the wrong (vendored/gitignored) copy | High | Callout above; target path stated absolutely |
| Committing to the user's fork without review | Med | Deliver as patch/diff; tasks phase confirms |
| Distinct presets per task → evict/reload storms | High | Grouped serialization |
| Mixed batches much slower (one load per group) | High | Accepted; documented in tool description |
| Two config copies drift | Med | Update both, or declare one authoritative |
| Typo'd model silently 400s at LiteLLM | Med | Pre-dispatch allowlist rejection |

**Pre-existing fact, not to fix here**: live `delegation.max_concurrent_children: 2`
differs from `_DEFAULT_MAX_CONCURRENT_CHILDREN = 10` in `delegate_tool.py`.

## Rollback Plan

Revert `delegate_tool.py` in the fork (or drop the unapplied patch). The field is
additive and optional — omitting it restores today's single-model behaviour. No
manifest, LiteLLM, or router change to undo.

## Dependencies

- llama-router reachable through existing LiteLLM entries (already deployed).
- User approval to modify `/home/pedro/.hermes/hermes-agent/`.

## Success Criteria

- [ ] A `delegate_task` batch with two tasks naming different registered models dispatches each child against its requested model.
- [ ] A task with no `model` behaves exactly as today.
- [ ] An unregistered/misspelled model is rejected **before any child is spawned**, with an error listing valid choices — never silently ignored or defaulted.
- [ ] Two concurrent tasks requesting different local presets in one batch complete correctly and sequentially — no evict/reload thrash, no wrong-model or corrupted response.
- [ ] Same-preset children still run concurrently up to `max_concurrent_children`.
- [ ] A `cloud` child runs concurrently with a local child.
- [ ] Model-panel switching and `model.default` behaviour are unchanged.
- [ ] No file inside `kubernetes/docker/hermes-agent/` is modified.

## Proposal question round

Interactive asking was unavailable in this executor. Decisions above were taken
as stated; correct any before `sdd-spec`:

1. Delivery for the external fork: patch-for-you-to-apply (assumed) vs. `sdd-apply` branching directly in `/home/pedro/.hermes/hermes-agent/`?
2. Allowlist config location: both config copies (assumed), live-only, or hardcoded?
3. Mixed-preset batch: serialize (assumed) vs. reject?
4. Is a multi-second first-token stall on router load acceptable with no budget/warning? *Assumed: acceptable in this slice.*
