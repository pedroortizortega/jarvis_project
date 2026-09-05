# Tasks: Hermes Per-Subagent Model Routing

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~430-480 (fork patch ~230-260: constants/resolve/validate/waves/schema/desc ~130 + new test file ~150-180; config x2 ~12 each; specs/025 ~150-180) |
| 400-line budget risk | Medium |
| Chained PRs recommended | No (fork patch isn't a jarvis_project PR; see asymmetry note) |
| Suggested split | Single patch artifact + single jarvis_project commit |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending |

Decision needed before apply: Yes
Chained PRs recommended: No
Chain strategy: pending
400-line budget risk: Medium

**Asymmetry note**: The `delegate_tool.py` + test-file changes (~380-440 lines) live entirely
inside the external fork worktree and ship as one `.patch` file — this is not a jarvis_project PR
and standard PR-chaining does not apply to it; the user reviews/applies/commits it themselves in
their fork. Only the jarvis_project-side changes (2 config edits + `specs/025_*.md` + openspec
artifacts, ~180-200 lines) are normal repo changes subject to this repo's own review flow. Because
the two sides deliver through different mechanisms, they are not combinable into one chainable PR
set; risk is assessed Medium because the fork-side patch, while sizeable, is reviewed by the user
directly against `git apply --check` and is not going through this repo's PR budget at all.

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Fork patch (delegate_tool.py + tests) inside detached worktree | N/A (external patch file) | `pytest tests/tools/test_delegate_model_routing.py -v` inside worktree | User applies patch, restarts `hermes-gateway.service` | Revert/discard the patch file; never applied to fork = zero-touch |
| 2 | jarvis_project config sync (`~/.hermes/config.yaml` + `kubernetes/hermes/config/config.yaml`) | jarvis_project commit | `diff <(yq '.delegation.allowed_models' ~/.hermes/config.yaml) <(yq '.delegation.allowed_models' kubernetes/hermes/config/config.yaml)` | N/A — config-only, no runnable harness in this repo | Revert both files together (single commit) |
| 3 | `specs/025_*.md` + openspec artifacts | jarvis_project commit | N/A — documentation | N/A | Revert file |

## Phase 1: Setup

- [x] 1.1 Create detached worktree: `git -C /home/pedro/.hermes/hermes-agent worktree add --detach /home/pedro/.hermes/hermes-agent-worktrees/subagent-model-routing HEAD`
- [x] 1.2 Verify `git -C /home/pedro/.hermes/hermes-agent status --porcelain` is empty before and confirm all further edits happen only inside the worktree path

## Phase 2: Allowlist Resolution (TDD)

- [x] 2.1 RED: in `<worktree>/tests/tools/test_delegate_model_routing.py`, write test asserting `_resolve_task_model({"model": "qwen3.6-27b-q3"}, "cloud")` returns `"qwen3.6-27b-q3"`, and `_resolve_task_model({}, "cloud")` returns `"cloud"`; run pytest, confirm failure (function missing)
- [x] 2.2 GREEN: add `_DEFAULT_ALLOWED_MODELS`, `_GPU_EXEMPT_MODELS`, `_get_allowed_models()`, `_resolve_task_model()` to `delegate_tool.py`; run pytest, confirm pass
- [x] 2.3 RED: add test patching `_load_config` to return custom `allowed_models`; assert `_get_allowed_models()` follows it; add test for missing key falling back to the 4 defaults; run, confirm failure
- [x] 2.4 GREEN: confirm `_get_allowed_models()` config-precedence logic satisfies 2.3 (adjust if needed); run pytest, confirm pass

## Phase 3: Fail-Fast Validation (TDD)

- [x] 3.1 RED: write test — task 3 of 5 has `model: "qwen3.6-27b-q6"` (invalid); assert `_validate_task_models` returns an error string naming task index 3, the value, and the 4 valid choices; run, confirm failure (function missing)
- [x] 3.2 GREEN: add `_validate_task_models()` to `delegate_tool.py`; run pytest, confirm pass
- [x] 3.3 RED: write zero-spawn-on-failure test — patch `_build_child_preserving_parent_tools` as a spy, dispatch a batch with one invalid model, assert `tool_error` is returned AND spy call count == 0; run, confirm failure
- [x] 3.4 GREEN: wire `_validate_task_models()` call immediately after the `output_schema` coercion loop (~line 3777), before the build loop, in `delegate_task`; run pytest, confirm pass
- [x] 3.5 RED: write test covering misspelled model (`"qwen3.5-9"`) — assert rejection, not silent fallback to `creds["model"]`; run, confirm failure or already-pass (verify behavior explicitly)
- [x] 3.6 GREEN: adjust `_validate_task_models` if 3.5 exposed a gap; run pytest, confirm pass
- [x] 3.7 RED: write test asserting validation runs before spawn in both dispatch paths — one case through the sync `delegate_task` path, one through `dispatch_async_delegation_batch`; both assert zero children built; run, confirm failure
- [x] 3.8 GREEN: confirm the single validation call site (shared by both paths per design's construction-order note) satisfies 3.7; adjust call site if async path bypasses it; run pytest, confirm pass

## Phase 4: GPU-Safe Wave Scheduling (TDD)

- [x] 4.1 RED: write test — fake `_run_single_child` recording `(model, enter_ts, exit_ts)` with a sleep; batch has 2 tasks on `qwen3.5-9b` and 2 on `qwen3.6-27b-q3`; assert no time overlap between the two local groups; run, confirm failure (no grouping exists yet)
- [x] 4.2 GREEN: add `_partition_gpu_waves()` and extract `_drain_until()` from the existing 3948-4039 `while pending:` block (loop condition changed to `while target & pending`); replace submit loop at 3924-3937 with wave submission per design; run pytest, confirm pass
- [x] 4.3 RED: write test — batch has 2 tasks on `qwen3.5-9b` and 1 on `cloud`; assert `cloud` child's timestamps overlap the local group's timestamps (cloud never waits); run, confirm failure if not yet satisfied
- [x] 4.4 GREEN: confirm `_GPU_EXEMPT_MODELS` exclusion in `_partition_gpu_waves` satisfies 4.3; adjust if needed; run pytest, confirm pass
- [x] 4.5 RED: write test — 4 tasks all `qwen3.6-27b-q3`, `max_concurrent_children = 2`; assert at most 2 run concurrently (existing cap preserved); run, confirm failure if regressed
- [x] 4.6 GREEN: verify `_get_max_concurrent_children` still bounds intra-group concurrency post-refactor; fix if broken; run pytest, confirm pass — **deviation**: the literal "4 tasks, cap=2" scenario is unreachable through `delegate_task` because a pre-existing, out-of-scope gate (`len(tasks) > max_children` → `tool_error`, line ~3708) rejects any batch larger than the SAME `max_concurrent_children` value before dispatch. Re-scoped the test to 3 tasks/cap=3 (all reachable) and additionally spied on `DaemonThreadPoolExecutor(max_workers=...)` construction to directly prove the cap is still derived from `_get_max_concurrent_children()` post-refactor. See apply-progress "Deviations" for detail.
- [x] 4.7 RED: write test — all-same-model batch (including all-`cloud`) produces exactly one group and no artificial sequential delay (assert wall-clock roughly equals pre-change baseline, or assert single-group code path taken); run, confirm failure if not yet true
- [x] 4.8 GREEN: adjust `_partition_gpu_waves` grouping to short-circuit the single-group case; run pytest, confirm pass — no special-casing needed; a single-model batch naturally partitions into exactly one group/one exempt-group, so the existing wave loop degrades to one submit+drain automatically
- [x] 4.9 RED: write ordering/interrupt regression test — `results` still sorted by `task_index`; simulate `_interrupt_requested` mid-wave and assert partial results returned; run, confirm failure if `_drain_until` extraction broke it
- [x] 4.10 GREEN: fix `_drain_until`/result-assembly to restore sort-by-index and interrupt behavior; run pytest, confirm pass — additionally had to fabricate "interrupted" entries for local groups that were never even submitted yet at interrupt time (not just in-flight ones), to preserve the "one result entry per task" contract; see Deviations
- [x] 4.11 RED: write background-path parity test — same task list dispatched once via sync `delegate_task` and once via `dispatch_async_delegation_batch(background=true)`; assert equivalent grouping/serialization observed in both; run, confirm failure if async path diverges
- [x] 4.12 GREEN: confirm `_batch_runner`/`dispatch_async_delegation_batch` route through the same `_execute_and_aggregate` (per design's single-execution-site claim); patch only if divergence found; run pytest, confirm pass — confirmed, no divergence, zero extra code needed

## Phase 5: Schema Deepcopy Fix + Model Field Exposure (TDD)

- [x] 5.1 RED: write regression test calling `get_definitions()` (or equivalent) twice; assert the static `DELEGATE_TASK_SCHEMA`'s `tasks["items"]["properties"]` is unmutated after both calls (proves the shallow-copy bug at ~4714-4720 exists); run, confirm failure — implemented via two direct calls to `_build_dynamic_schema_overrides()` (schema-builder unit, no registry needed)
- [x] 5.2 GREEN: in `_build_dynamic_schema_overrides`, replace the shallow `{k: dict(v)}` copy of `tasks["items"]` with `copy.deepcopy`; run pytest, confirm pass
- [x] 5.3 RED: write test asserting `tasks.items.properties.model` exists in the dynamic schema with `enum == _get_allowed_models()` (patch `_load_config` for a custom allowlist to prove it's dynamic, not hardcoded); run, confirm failure
- [x] 5.4 GREEN: add `model` property with dynamic `enum` to `tasks.items.properties` in `_build_dynamic_schema_overrides` (~4714); run pytest, confirm pass
- [x] 5.5 GREEN (no test — descriptive text): update the `model` field documentation — **deviation**: the pre-existing `test_top_level_description_compact_and_complete` regression test caps `_build_top_level_description()` at 2200 chars and the baseline was already at 2199 (1-char budget). Moved the model-field / omit-inherits / mixed-preset-slowdown note into `_build_tasks_param_description()` instead (dynamically rebuilt per `get_definitions()` call, no length ceiling test), which is model-visible in the same schema pass. Left the top-level description text unchanged.
- [x] 5.6 GREEN: update the two per-task metadata call sites — `create_live_transcripts(model=...)` (3798) and `dispatch_async_delegation_batch(model=...)` (4251, display-only) — to use `_models_label(task_models)` instead of a single model string; add/extend a unit test asserting `_models_label` returns the single model when uniform and `"mixed: a+b"` when not

## Phase 6: Patch Generation

- [x] 6.1 Run full worktree suite: `pytest tests/tools/test_delegate_model_routing.py tests/tools/test_delegate_output_schema.py -v`; confirm all green, including pre-existing tests unaffected by the deepcopy fix — 48/48 green; also ran the full `tests/tools/test_delegate*.py` + `test_async_delegation*.py` sweep (16 files, 237 tests) as an extra collateral-damage check given how much of `_execute_and_aggregate`'s batch loop was rewritten — all 237 green
- [x] 6.2 Stage and generate patch: `git -C <worktree> add -A && git -C <worktree> diff --cached > /home/pedro/Documentos/Projects/jarvis_project/openspec/changes/hermes-subagent-model-routing/patches/0001-subagent-model-routing.patch`
- [x] 6.3 Verify `git -C /home/pedro/.hermes/hermes-agent status --porcelain` is still unchanged (fork's own checkout untouched); leave worktree in place for user inspection

## Phase 7: Config Edits (not unit-testable — plain file edits)

- [x] 7.1 Add `delegation.allowed_models` block (with anti-drift comment + `diff`/`yq` verify command) to `~/.hermes/config.yaml`
- [x] 7.2 Add the identical `delegation.allowed_models` block to `kubernetes/hermes/config/config.yaml`
- [x] 7.3 Run the verify command from design: `diff <(yq '.delegation.allowed_models' ~/.hermes/config.yaml) <(yq '.delegation.allowed_models' kubernetes/hermes/config/config.yaml)`; confirm empty diff

## Phase 8: Spec Documentation

- [x] 8.1 Create `specs/025_hermes_subagent_model_routing.md` documenting the capability per repo spec-numbering convention (next free number confirmed as 025)

## Phase 9: User Apply Instructions (documentation only — not executed by sdd-apply)

- [x] 9.1 Write final instructions block (in tasks output / handoff) telling the user exactly how to apply: `git -C ~/.hermes/hermes-agent apply --check patches/0001-subagent-model-routing.patch`, then `git -C ~/.hermes/hermes-agent apply <patch>`, review the diff, `git -C ~/.hermes/hermes-agent add -A && git -C ~/.hermes/hermes-agent commit`, then restart `hermes-gateway.service`; note worktree removal command: `git -C ~/.hermes/hermes-agent worktree remove /home/pedro/.hermes/hermes-agent-worktrees/subagent-model-routing`
