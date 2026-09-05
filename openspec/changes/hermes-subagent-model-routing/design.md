# Design: Hermes Per-Subagent Model Routing

Capability: `subagent-model-routing`. Target file lives OUTSIDE this repo:
`/home/pedro/.hermes/hermes-agent/tools/delegate_tool.py` (line numbers below verified in that file).

## Technical Approach

Four additive pieces in one file: (1) resolve a per-task model, (2) fail-fast allowlist
validation before the build loop, (3) GPU-safe wave scheduling inside the *existing*
`DaemonThreadPoolExecutor`, (4) schema/description exposure. Both sync and async paths are
covered by construction: children are built once (3828–3888) *before* the `background` branch,
and the async runner `_batch_runner` (4197) simply calls the same `_execute_and_aggregate`
(3890). So there is exactly one execution site to change and no sync/async divergence.

## Architecture Decisions

| # | Decision | Choice | Rejected | Rationale |
|---|---|---|---|---|
| 1 | Validation style | `_validate_task_models(task_list, allowlist) -> Optional[str]`, wrapped by `tool_error()` | Custom exception | Mirrors `_validate_batch_tasks` (3551) exactly; the file returns error strings, never raises, from `delegate_task` |
| 2 | Validation site | Immediately after the `output_schema` coercion loop (~3777), before `overall_start`/build loop | Inside the build loop | Fail-fast requirement: task 3's typo must abort before task 0 spawns |
| 3 | Allowlist source | `cfg.get("allowed_models")` — `_load_config()` (4559) already returns the `delegation` subsection — falling back to module constant `_DEFAULT_ALLOWED_MODELS` | Hard-fail on missing key | Feature is additive; hard-fail would break every existing install that has no such key. Validation only runs for tasks that actually set `model`, so absent key + absent field = byte-identical behaviour |
| 4 | GPU-bound set | `set(allowlist) - _GPU_EXEMPT_MODELS` where `_GPU_EXEMPT_MODELS = frozenset({"cloud"})` (module constant, no extra config key) | New `gpu_exempt_models` config key | Cheapest surface. Risk: a second cloud entry added later would be needlessly serialized — perf bug, not correctness |
| 5 | Concurrency | Extend the existing `DaemonThreadPoolExecutor(max_workers=max_children)` (3921) with wave submission | New lock/queue/semaphore layer | Reuses the interrupt-aware `_cf_wait` poll loop, spinner lines, and result ordering already there |
| 6 | Schema `model` field | Add to `tasks.items.properties`; `enum` injected **dynamically** from the allowlist in `_build_dynamic_schema_overrides` (4714) | Hardcode 4 values in the static `DELEGATE_TASK_SCHEMA` | Static enum drifts from config. **Gotcha**: that function shallow-copies only top-level properties (`{k: dict(v)}`), so `tasks["items"]` must be `copy.deepcopy`'d before mutation or the static schema is corrupted process-wide |
| 7 | Delivery | Patch generated from a detached git worktree of the fork | `sdd-apply` editing/committing the live fork | Confirmed decision 1: user applies and commits |

## Data Flow

    task dict {goal, model?} ──┐
    creds["model"] (default) ──┴─→ _resolve_task_model() ─→ task_models[i]
                                          │
                       _validate_task_models()  ── invalid ─→ tool_error(), 0 children spawned
                                          │ valid
                       build loop 3828 → _build_child_preserving_parent_tools(model=task_models[i])
                                          │
                       _execute_and_aggregate → _partition_gpu_waves()
                                          │
              cloud/exempt ─ submitted immediately, never blocked ─┐
              local group A ─ submitted, drained ─→ local group B ─┴→ results.sort(task_index)

## Interfaces / Contracts

```python
_DEFAULT_ALLOWED_MODELS = ("qwen3.5-9b", "qwen3.6-27b-q3", "qwen3.8-27b-iq2s", "cloud")
_GPU_EXEMPT_MODELS = frozenset({"cloud"})

def _get_allowed_models() -> list[str]:
    val = _load_config().get("allowed_models")
    return [str(m) for m in val] if isinstance(val, list) and val else list(_DEFAULT_ALLOWED_MODELS)

def _resolve_task_model(t: dict, default_model: Optional[str]) -> Optional[str]:
    raw = t.get("model")
    return raw.strip() if isinstance(raw, str) and raw.strip() else default_model

def _validate_task_models(task_list, allowed) -> Optional[str]:
    """None when every explicit per-task model is allowed; error string otherwise."""
    for i, t in enumerate(task_list):
        raw = t.get("model")
        if raw is None:
            continue
        if not isinstance(raw, str) or not raw.strip():
            return (f"Task {i} has an invalid 'model' ({raw!r}): expected a non-empty "
                    f"string. Valid choices: {', '.join(allowed)}.")
        if raw.strip() not in allowed:
            return (f"Task {i} requested model {raw.strip()!r}, which is not in the "
                    f"delegation allowlist. Valid choices: {', '.join(allowed)}. "
                    f"Set delegation.allowed_models in config.yaml to change this. "
                    f"No subagents were spawned.")
    return None
```

Wave scheduling replaces the single submit loop at 3924–3937:

```python
exempt, local_groups = _partition_gpu_waves(children, task_models, gpu_bound)
pending: set = set()
pending |= _submit(executor, exempt)                 # cloud: immediate, never waits
for group in local_groups:                            # distinct presets: sequential
    grp = _submit(executor, group)
    pending |= grp
    pending = _drain_until(pending, target=grp)       # existing _cf_wait poll loop, extracted
    if interrupted: break
pending = _drain_until(pending, target=pending)
```

`_drain_until` is the current `while pending:` block (3948–4039) verbatim — interrupt check,
`_cf_wait(timeout=0.5, FIRST_COMPLETED)`, per-task completion line, spinner update — with the
loop condition changed from `while pending` to `while target & pending`. When all tasks share
one model (today's case) there is exactly one group and the behaviour is identical.
`max_concurrent_children` and `max_spawn_depth` semantics are untouched.

Two metadata call sites must stop reporting a single model: `create_live_transcripts(model=...)`
(3798) and `dispatch_async_delegation_batch(model=...)` (4251 — display metadata for the
completion block only, not routing). Both take `_models_label(task_models)` → the single model
when uniform, else `"mixed: a+b"`.

Tool description (`_build_top_level_description`, ~4650) gains: the `model` field, that omitting
it inherits the configured delegation model, and that mixing distinct local presets in one batch
is correct but **runs sequentially and is slower**.

## Config Schema

Identical block in `~/.hermes/config.yaml` (live, authoritative) and
`kubernetes/hermes/config/config.yaml` (reference copy):

```yaml
delegation:
  # Per-task delegate_task(tasks=[{model: ...}]) allowlist.
  # KEEP IN SYNC with the other copy (~/.hermes/config.yaml <-> jarvis_project
  # kubernetes/hermes/config/config.yaml). Verify:
  #   diff <(yq '.delegation.allowed_models' ~/.hermes/config.yaml) \
  #        <(yq '.delegation.allowed_models' kubernetes/hermes/config/config.yaml)
  allowed_models:
    - qwen3.5-9b
    - qwen3.6-27b-q3
    - qwen3.8-27b-iq2s
    - cloud
```

Anti-drift = reciprocal comment + the one-line `diff` command, run as an explicit task step.
jarvis_project has no test suite or CI to host an automated check; an unrunnable test would be
theatre.

## Patch Delivery

`sdd-apply` never touches the fork's checkout, index, or branches:

1. `git -C /home/pedro/.hermes/hermes-agent worktree add --detach /home/pedro/.hermes/hermes-agent-worktrees/subagent-model-routing HEAD`
2. Edit + write RED tests + run pytest **inside that worktree**.
3. `git -C <worktree> add -A && git -C <worktree> diff --cached > openspec/changes/hermes-subagent-model-routing/patches/0001-subagent-model-routing.patch`
   (staged diff so the new test file is included; nothing is committed).
4. Worktree is left in place for user inspection; removal command documented.

User applies: `git -C ~/.hermes/hermes-agent apply --check <patch>` then `apply`, reviews, commits.
Restart `hermes-gateway.service` to load the change.

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `~/.hermes/hermes-agent/tools/delegate_tool.py` | Modify (via patch) | Constants, `_get_allowed_models`, `_resolve_task_model`, `_validate_task_models`, `_partition_gpu_waves`, `_drain_until` extraction, build-loop `model=`, schema + descriptions |
| `~/.hermes/hermes-agent/tests/tools/test_delegate_model_routing.py` | Create (via patch) | RED tests below |
| `openspec/changes/.../patches/0001-*.patch` | Create | Deliverable |
| `~/.hermes/config.yaml` | Modify | `delegation.allowed_models` |
| `kubernetes/hermes/config/config.yaml` | Modify | Same block, in sync |
| `specs/025_*.md` | Create | Repo spec convention |
| `kubernetes/docker/hermes-agent/**` | **Untouched** | Gitignored scratch clone |

## Testing Strategy

The fork has a real suite: pytest 9.1.1 (`[tool.pytest.ini_options] testpaths = ["tests"]`,
pyproject:460), `tests/conftest.py` redirects `HERMES_HOME`, and existing tests already patch
`tools.delegate_tool._load_config`, `_resolve_delegation_credentials`, and
`_build_child_preserving_parent_tools` (see `tests/tools/test_delegate_output_schema.py:279–350`).
So Strict TDD is fully executable inside the worktree before the user applies anything.

| Layer | What to test | Approach |
|-------|--------------|----------|
| Unit | Per-task `model` reaches the child | Patch `_build_child_preserving_parent_tools`, assert recorded `model` kwarg per task index |
| Unit | Absent `model` = today's behaviour | Assert every child gets `creds["model"]` |
| Unit | Fail-fast rejection | Invalid model on task 3 of 5 → `tool_error` naming valid choices; build spy call count == 0 |
| Unit | Allowlist from config | Patch `_load_config` to return a custom `allowed_models`; assert acceptance/rejection follows it; missing key falls back to the 4 defaults |
| Unit | Schema | `get_definitions()` exposes `tasks.items.properties.model` with enum == allowlist; static `DELEGATE_TASK_SCHEMA` unmutated after two calls (deep-copy regression) |
| Integration | GPU serialization | Fake `_run_single_child` recording (model, enter_ts, exit_ts) with a sleep; assert no time overlap between distinct local presets, overlap allowed within one preset and between `cloud` and any local |
| Integration | Ordering/interrupt | `results` still sorted by `task_index`; `_interrupt_requested` mid-wave still returns partial results |

**Cannot be verified before the user applies**: real llama-router load/evict behaviour, actual
LiteLLM routing to the three presets, first-token latency, and gateway restart integration. Those
map to the proposal's success criteria and stay manual/user-side.

## Threat Matrix

| Boundary | Applicability | Design response | Planned RED test |
|---|---|---|---|
| Documentation-like paths | N/A — no file classification or execution of authored content | — | — |
| Git repository selection | Applicable — patch generation targets an external repo | Every git call uses explicit absolute `-C <fork>` / `-C <worktree>`; never relies on cwd | Task-level assertion that no git command in the apply plan is cwd-relative |
| Commit state | Applicable — step 3 stages in the worktree | Staging happens only in the detached worktree; the fork's own index is never written; no `commit`/`commit -a` anywhere | Verify `git -C <fork> status --porcelain` unchanged before/after patch generation |
| Push state | N/A — nothing is pushed | — | — |
| PR commands | N/A — no PR automation for the external fork | — | — |

## Migration / Rollout

No migration. Additive and optional: omit `model` → today's behaviour. Rollback = revert the
patch in the fork (or never apply it) and drop the `allowed_models` key.

## Open Questions

None — the four product decisions are confirmed. Accepted trade-offs: multi-second preset-swap
stall is acceptable (no budget/timeout in this slice); mixed local batches are slower by design.

## Rejected Alternatives (cross-reference, proposal §"Resolved Product Decisions")

- **Automatic complexity classifier** — deferred; caller-chosen model needs zero new inference infra.
- **Spec 009 lock/queue/drain coordinator** — deferred; grouped serialization is the MVP guard, a
  full scheduler is out of scope for a one-file additive change.
- **Rejecting mixed-preset batches** — rejected; would defeat the feature's purpose.
