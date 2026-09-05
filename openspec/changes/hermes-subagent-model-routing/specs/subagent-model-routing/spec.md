# subagent-model-routing Specification

## Purpose

Lets the parent LLM pick a specific llama-router preset (or `cloud`) per
delegated subagent task, instead of every child in a `delegate_task` call
inheriting one call-level model. Adds a pre-dispatch allowlist so a
misspelled or unregistered model is rejected before any child spawns, and a
grouped/serialized execution rule so GPU-bound local presets are never
loaded concurrently on the single-GPU `llama-router` (`--models-max 1`,
LRU eviction). Applies to both the synchronous batch loop and the
background/async dispatch path in `delegate_tool.py`.

## Requirements

### Requirement: Optional Per-Task Model Field
Each task dict passed via `delegate_task(tasks=[...])` MUST accept an
optional `model` string field. When `model` is absent, `null`, or an empty
string on a task, that task MUST resolve to exactly today's behavior: the
call-level `creds["model"]` produced by `_resolve_delegation_credentials`.
This applies identically to the single-task `goal=` call form, where no
per-task `model` exists and call-level resolution is unaffected.

#### Scenario: Task omits model
- GIVEN a `delegate_task` batch where task 0 has no `model` key
- WHEN the batch is dispatched
- THEN task 0's child is built with `model=creds["model"]`, identical to
  pre-change behavior

#### Scenario: Task supplies a valid model
- GIVEN a `delegate_task` batch where task 1 has `model: "qwen3.6-27b-q3"`
- WHEN the batch is dispatched
- THEN task 1's child is built with `model="qwen3.6-27b-q3"` instead of
  `creds["model"]`

#### Scenario: Single-task call form is unaffected
- GIVEN a `delegate_task(goal="...")` single-task call with no `tasks` array
- WHEN the call is dispatched
- THEN model resolution is unchanged from pre-change behavior (call-level
  `creds["model"]` only; no per-task `model` field exists in this call form)

### Requirement: Pre-Dispatch Allowlist Validation
The system MUST validate every task's `model` value (when present) against
a fixed allowlist before building or spawning any child agent in the batch:
exactly `qwen3.5-9b`, `qwen3.6-27b-q3`, `qwen3.8-27b-iq2s`, `cloud`. If any
task in the batch names a value outside this set, the system MUST reject
the entire call with a `tool_error` naming the offending task index, the
invalid value, and the full list of valid choices, and MUST NOT construct
or spawn any child for any task in that batch (fail fast, no partial
dispatch).

#### Scenario: All models valid
- GIVEN a batch of 3 tasks each naming a model from the allowlist (or
  omitting `model`)
- WHEN the batch is dispatched
- THEN validation passes and all 3 children are built and spawned

#### Scenario: One invalid model blocks the whole batch
- GIVEN a batch of 3 tasks where task 2 has `model: "qwen3.6-27b-q6"`
  (a real router entry not on this allowlist)
- WHEN the batch is dispatched
- THEN the system returns `tool_error` naming task index 2, the value
  `"qwen3.6-27b-q6"`, and the four valid choices, AND no child for task 0,
  1, or 2 is built or spawned

#### Scenario: Misspelled model rejected, not silently defaulted
- GIVEN a task has `model: "qwen3.5-9"` (typo)
- WHEN the batch is dispatched
- THEN the system rejects the call with an error listing the valid choices;
  it MUST NOT silently ignore the field, fall back to `creds["model"]`, or
  forward the typo'd value to LiteLLM

#### Scenario: Validation runs before any spawn, in both dispatch paths
- GIVEN a batch destined for the synchronous dispatch loop or one destined
  for `dispatch_async_delegation_batch` (background/async path), each
  containing one invalid `model`
- WHEN either call is dispatched
- THEN validation happens before child construction begins in that path,
  and zero children are spawned in either case

### Requirement: GPU-Safe Grouped Execution by Resolved Model
Within one batch, the system MUST group children by their resolved model
(the per-task `model` if present, else the call-level `creds["model"]`)
before execution. The `cloud` group MUST always run concurrently with
whatever local-preset groups are executing, and MUST NOT be blocked by, or
itself block, local-preset serialization. Distinct local-preset groups (any
two groups whose resolved model differs and both differ from `cloud`) MUST
run sequentially relative to each other: one local-preset group's children
MUST fully complete before the next distinct local-preset group's children
begin. Children within the same resolved-model group MUST retain today's
existing intra-group concurrency, bounded by
`delegation.max_concurrent_children` (via `_get_max_concurrent_children`).

#### Scenario: Two distinct local presets serialize
- GIVEN a batch with 2 tasks on `qwen3.5-9b` and 2 tasks on
  `qwen3.6-27b-q3`
- WHEN the batch executes
- THEN the `qwen3.5-9b` group's children run and fully complete before any
  `qwen3.6-27b-q3` child starts (or vice versa, but never interleaved),
  preventing router evict/reload thrash

#### Scenario: Cloud runs alongside a local group
- GIVEN a batch with 2 tasks on `qwen3.5-9b` and 1 task on `cloud`
- WHEN the batch executes
- THEN the `cloud` child runs concurrently with the `qwen3.5-9b` group and
  is never made to wait for local-group serialization to finish

#### Scenario: Same-preset children stay concurrent up to the cap
- GIVEN a batch with 4 tasks all naming `qwen3.6-27b-q3` and
  `delegation.max_concurrent_children` = 2
- WHEN that group executes
- THEN at most 2 of those 4 children run at once, matching pre-change
  concurrency behavior for a single-model batch

#### Scenario: All-cloud or all-same-model batch has no serialization overhead
- GIVEN a batch where every task resolves to the same model (including the
  case where all resolve to `cloud`, or all omit `model` and share one
  `creds["model"]`)
- WHEN the batch executes
- THEN there is exactly one group, and execution proceeds with today's
  existing concurrency — no artificial sequential delay is introduced

### Requirement: Grouped Execution Covers Both Dispatch Paths
Grouping, serialization of distinct local-preset groups, and cloud's
GPU-exempt concurrency MUST be enforced identically in the synchronous
batch loop (`delegate_task`'s inline `_execute_and_aggregate` path) and in
the background/async path (`dispatch_async_delegation_batch`). A batch
dispatched in the background MUST NOT bypass grouping just because it runs
off the main thread.

#### Scenario: Background batch still serializes distinct local presets
- GIVEN a batch with `background=true` (or otherwise routed through
  `dispatch_async_delegation_batch`) containing 2 distinct local presets
- WHEN the batch runs on the daemon executor
- THEN the same sequential-group guarantee applies as in the synchronous
  path: the two local-preset groups never have children in flight at the
  same time

#### Scenario: Foreground and background paths produce equivalent grouping for the same batch shape
- GIVEN the same task list (same models, same task count) dispatched once
  synchronously and once with `background=true`
- WHEN both runs complete
- THEN the grouping and serialization behavior observed (which models ran
  concurrently vs. sequentially) is equivalent between the two paths

### Requirement: Allowlist Config Kept in Sync Across Both Copies
When the allowlist is externalized to config (e.g.
`delegation.allowed_models`) rather than hardcoded, the same list of valid
model values MUST be present, in the same set, in both `~/.hermes/config.yaml`
(the live host config Hermes actually reads) and jarvis_project's
`kubernetes/hermes/config/config.yaml` (the checked-in reference copy).
These two copies MUST NOT diverge on this key: a value accepted by one but
rejected by the other constitutes a defect. If the allowlist is hardcoded
in `delegate_tool.py` instead of externalized, this requirement does not
apply, but that choice MUST be recorded so a future config-driven change
does not silently reintroduce copy drift without also updating this spec.

#### Scenario: Both copies define the same allowed_models set
- GIVEN `delegation.allowed_models` is defined in both
  `~/.hermes/config.yaml` and `kubernetes/hermes/config/config.yaml`
- WHEN the two lists are compared
- THEN they contain exactly the same set of values (`qwen3.5-9b`,
  `qwen3.6-27b-q3`, `qwen3.8-27b-iq2s`, `cloud`)

#### Scenario: Drift between copies is a defect, not an accepted state
- GIVEN the reference copy (`kubernetes/hermes/config/config.yaml`) is
  updated to add or remove a value from `delegation.allowed_models`
- WHEN the live copy (`~/.hermes/config.yaml`) is not updated to match
- THEN the system is in a defective state (the guarantee "both copies
  agree" is broken) even though nothing enforces this automatically today;
  any future drift-check tooling MUST treat this mismatch as a failure

### Requirement: Existing Model-Selection Behavior Outside This Slice Is Unchanged
This capability MUST NOT alter `model.default`, the model-panel's Local/Cloud
switch, `switch-model.sh`, `litellm-config.yaml`'s existing 4 entries
(`cloud`, `qwen3.5-9b`, `qwen3.6-27b-q3`, `qwen3.8-27b-iq2s`), or
`router-config.yaml`. It MUST NOT implement `specs/009`'s full
lock/queue/drain coordinator for `local_large`, and MUST NOT introduce any
automatic complexity classifier that picks a model on the caller's behalf —
model selection stays an explicit, caller-supplied per-task choice.

#### Scenario: Model-panel switching unaffected
- GIVEN a user switches Local/Cloud mode via the model-panel while
  `delegate_task` batches are also in use
- WHEN either action occurs
- THEN neither affects the other: model-panel mode switching does not
  change how per-task `model` routing resolves, and per-task routing does
  not change `model.default` or panel state

#### Scenario: No automatic classifier is introduced
- GIVEN a `delegate_task` batch where some tasks omit `model`
- WHEN those tasks are dispatched
- THEN the system MUST NOT infer or auto-select a model based on task
  content; omitted `model` resolves only to `creds["model"]` as specified
  above, never to a heuristically "better" preset

#### Scenario: Pre-existing gaps are out of scope
- GIVEN the pre-existing `litellm_callbacks.py` `qwen3` allowlist gap and
  the `model.default` alias drift noted in the proposal
- WHEN this capability is implemented
- THEN neither pre-existing issue is fixed or required to be fixed as part
  of this change
