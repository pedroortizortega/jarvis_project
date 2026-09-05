# Verify Report — hermes-subagent-model-routing

**Mode**: full artifacts (proposal, spec, design, tasks all present)
**Date**: 2026-09-01

## Task Completeness
All 40 tasks in tasks.md (Phases 1-9) are checked `[x]`. Independently confirmed by reading the file directly.

## Test Execution (independently re-run, not trusted from apply-progress)
Command: `cd /home/pedro/.hermes/hermes-agent-worktrees/subagent-model-routing && /home/pedro/.hermes/hermes-agent/.venv/bin/python -m pytest tests/tools/test_delegate_model_routing.py tests/tools/test_delegate_output_schema.py -v`
Result: **48 passed in 2.25s**, exit 0. Verbatim output captured; all 24 new + 24 pre-existing tests green.

## Spec Compliance Matrix

| Requirement | Verdict | Evidence |
|---|---|---|
| Optional Per-Task Model Field | PASS | `_resolve_task_model` in diff (delegate_tool.py L43-47); tests `TestResolveTaskModel::test_explicit_model_wins`, `test_absent_model_falls_back_to_default`, `test_none_model_falls_back_to_default`, `test_blank_model_falls_back_to_default` all pass. Single-task `goal=` form untouched (no diff hunk near that path). |
| Pre-Dispatch Allowlist Validation | PASS | `_validate_task_models` (L50-77) wired at delegate_task L3898-3901, before build loop and before `task_models` resolution used for child construction. `TestZeroSpawnOnValidationFailure::test_sync_path_rejects_and_spawns_nothing` genuinely asserts `spy.call_count == 0` on `_build_child_preserving_parent_tools`, not just an error string — confirmed by direct read of test body. Misspelled-model test (`TestValidateTaskModels::test_misspelled_model_rejected_not_silently_defaulted`) passes. |
| Validation runs before spawn in both dispatch paths | PASS | Single call site: `dispatch_async_delegation_batch` (tools/async_delegation.py L1015) takes a `runner` callable built by `delegate_task` — the same closure that already ran `_validate_task_models` before the runner is constructed, so the async path cannot bypass it. `test_background_path_rejects_and_spawns_nothing` and `test_background_path_serializes_distinct_local_presets_too` confirm both statically and behaviorally. |
| GPU-Safe Grouped Execution by Resolved Model | PASS | `_partition_gpu_waves` (L80-113) groups by resolved model, `_GPU_EXEMPT_MODELS = frozenset({"cloud"})` exempts cloud. Wave submit/drain logic at L4~377-458 submits exempt group immediately, then local groups sequentially via `_drain_until(pending, target=grp_futures)` between each. Timing tests use **real threads + `time.monotonic()`** (`_dispatch_wave_batch` helper, confirmed by direct read): `test_two_distinct_local_presets_never_overlap` asserts `not _range_overlaps(group_a, group_b)` on real enter/exit timestamps — genuine non-overlap check, not weaker call-order check. `test_cloud_runs_concurrently_with_local_group` asserts real overlap. `test_same_preset_children_respect_concurrency_cap` spies on `DaemonThreadPoolExecutor(max_workers=...)` construction and asserts `max_active == 3` — confirms concurrency cap preserved. `test_all_same_model_batch_has_no_artificial_delay` asserts wall clock ≈ one sleep interval, not N×. |
| Grouped Execution Covers Both Dispatch Paths | PASS | Same evidence as above; `_execute_and_aggregate` is the single execution site used by both delegate_task's sync branch and the runner handed to `dispatch_async_delegation_batch`. No divergent code path found for async. |
| Allowlist Config Kept in Sync Across Both Copies | PASS | Directly re-read both files: `~/.hermes/config.yaml` L162-166 and `kubernetes/hermes/config/config.yaml` L162-166 — byte-identical `delegation.allowed_models: [qwen3.5-9b, qwen3.6-27b-q3, qwen3.8-27b-iq2s, cloud]`, including the anti-drift comment with the verify command. (Design's `yq` check was substituted with Python yaml equality per documented deviation — acceptable, since yq is unavailable on this host and I independently re-diffed the raw YAML text myself.) |
| Existing Model-Selection Behavior Outside This Slice Is Unchanged | PASS | Diff touches only `tools/delegate_tool.py` and adds `tests/tools/test_delegate_model_routing.py`. No changes to `litellm-config.yaml`, `router-config.yaml`, `switch-model.sh`, or `model.default`. No classifier/heuristic code added — `_resolve_task_model` and `_validate_task_models` are purely explicit-value pass-through/reject, confirmed by reading their bodies. |

## Fail-Fast Guarantee — Verified For Real
Read `TestZeroSpawnOnValidationFailure::test_sync_path_rejects_and_spawns_nothing` directly: it patches `_build_child_preserving_parent_tools` with a `MagicMock(side_effect=AssertionError(...))` and asserts `spy.call_count == 0` after dispatching a 3-task batch with one invalid model at index 2. This is a genuine call-count assertion on a spy, not merely checking that an error was returned. Confirmed strong.

## GPU-Serialization Guarantee — Verified For Real
`_dispatch_wave_batch` test helper spins up real background threads (`_run_single_child` fake sleeps `time.sleep(sleep_seconds)`), records real `time.monotonic()` enter/exit per task, and `_range_overlaps` compares real min/max timestamp ranges between two groups. This proves genuine non-overlap of execution windows, not just submission/call order. Confirmed strong.

## Deepcopy Fix — Verified For Real
Diff hunk L488-501 (`_build_dynamic_schema_overrides`): the pre-existing shallow `{k: dict(v) for k, v in ...}` only copies top-level properties; `overrides_params["properties"]["tasks"]["items"]` is now separately replaced with `copy.deepcopy(DELEGATE_TASK_SCHEMA["parameters"]["properties"]["tasks"]["items"])` before any mutation of the `model` field enum. `TestSchemaDeepcopyRegression::test_static_schema_unmutated_after_two_dynamic_builds` calls `_build_dynamic_schema_overrides()` twice and asserts the static `DELEGATE_TASK_SCHEMA`'s nested `tasks.items.properties` keys are unchanged before vs. after — genuinely proves the static schema is not corrupted process-wide. Confirmed correct.

## Config Sync — Verified For Real
Re-read both files directly (not trusting apply-progress): `~/.hermes/config.yaml` and `jarvis_project/kubernetes/hermes/config/config.yaml` both define identical `delegation.allowed_models` blocks (4 entries, same order, same anti-drift comment). Confirmed identical.

## Scope Discipline — Verified For Real
- `git -C /home/pedro/.hermes/hermes-agent status --porcelain` → empty (clean; fork untouched by this change).
- `git -C jarvis_project status --porcelain -- kubernetes/docker/hermes-agent` → empty (no delta in that path).
- Worktree diff stat: only 2 files changed — `tools/delegate_tool.py` (425 changed lines) and new `tests/tools/test_delegate_model_routing.py` (525 lines added). No stray files.
- jarvis_project working tree shows only expected new/uncommitted artifacts: `specs/025_hermes_subagent_model_routing.md`, `openspec/changes/hermes-subagent-model-routing/`, plus `kubernetes/hermes/config/config.yaml` (M, expected) and pre-existing unrelated dirty state (`kubernetes/llama-service/router-config.yaml`, present before this session started per the initial git status — NOT caused by this change).

## Documented Deviations — Spot-Checked
1. **Concurrency-cap re-scope (4.5/4.6)**: read `test_same_preset_children_respect_concurrency_cap` directly. It spies on `DaemonThreadPoolExecutor` construction, asserts `max_workers == 3` (derived from `_get_max_concurrent_children`) and `max_active == 3` (all 3 ran concurrently, not serialized). This is a reasonable, still-meaningful re-scope — it directly proves the cap wiring survived the wave-scheduling refactor even though the literal "4 tasks / cap 2" batch shape is unreachable due to an unrelated pre-existing gate. Not a coverage gap.
2. **Interrupt-mid-wave fabrication**: confirmed in diff (L402-458) — when interrupted before all waves are submitted, the code walks `children` and fabricates `"interrupted"` entries for any task index not yet in `results`, preserving the one-result-per-task contract. Reasonable and correctly implemented.
3. **Model-field docs moved to tasks-param description**: confirmed in diff — `_build_tasks_param_description()` (L476-484) carries the model-field/omit/mixed-preset note; `_build_top_level_description()` is untouched. Model-visible either way since both feed into the same tool schema surface. Acceptable.
4. **yq substitution**: acceptable — verified independently myself by direct text re-read of both config files (see Config Sync above), not just trusting the apply-progress claim.

None of the 4 deviations amount to skipped or hidden work; all are honest, narrowly-scoped, and still prove what the corresponding task intended.

## Spec Scenario → Test Coverage Gaps
None found. All ~20 spec scenarios across the 6 requirements map to a passing test:
- Task omits/supplies model → `TestResolveTaskModel` (5 tests)
- Single-task call form unaffected → no per-task model path exists in that form; confirmed by diff (no changes near the `goal=` single-call branch)
- All valid / one invalid / misspelled / both dispatch paths → `TestValidateTaskModels` (3) + `TestZeroSpawnOnValidationFailure` (2)
- Two distinct local presets serialize / cloud concurrent / same-preset cap / all-same-model no delay → `TestGpuSafeWaveScheduling` (4 of its 6 tests)
- Background batch parity → `test_background_path_serializes_distinct_local_presets_too`
- Ordering/interrupt → `test_results_sorted_by_task_index_and_interrupt_returns_partial`
- Config sync both copies → directly re-verified by me, not test-covered (correctly — it's a static config file assertion, not code)
- Model-panel/switch-model/classifier unaffected → confirmed by diff scope (zero changes to those files/paths)

## Issues

**CRITICAL**: None.

**WARNING**: None. All fail-fast, serialization, and deepcopy claims independently reproduced with real evidence (spy call counts, real-thread timing, static-schema mutation checks), not just re-reading the apply-progress summary.

**SUGGESTION**:
1. Nothing has been committed to the fork yet (`git apply` not yet run by the user) — this is expected per Phase 9 (documentation-only apply instructions), not a defect, but flagging for the user before they proceed: run `git -C ~/.hermes/hermes-agent apply --check patches/0001-subagent-model-routing.patch` first.
2. The worktree at `/home/pedro/.hermes/hermes-agent-worktrees/subagent-model-routing` should be removed after the patch is applied and reviewed (`git -C ~/.hermes/hermes-agent worktree remove <path>`) to avoid confusion with two copies of delegate_tool.py existing side by side.
3. Consider running the broader 237-test collateral sweep (mentioned in task 6.1) one more time by the user post-`git apply`, since it was only run inside the worktree, not against the actual committed fork state.

## Overall Verdict: **PASS**

All 6 spec requirements PASS with real runtime evidence (not static-analysis-only). All 40 tasks complete and match the code state in the worktree diff/patch. Test suite genuinely green (48/48, independently re-run). Fail-fast and GPU-serialization guarantees hold up under direct inspection of their actual assertions (call-count spies, real-thread timing), not just trusting weaker checks. Config sync confirmed byte-identical. Scope discipline confirmed — fork untouched, only 2 files changed in the worktree. The 4 documented deviations are real, reasonable, and still prove their corresponding task's intent.

**Ready for the user to apply the patch to their real fork.**
