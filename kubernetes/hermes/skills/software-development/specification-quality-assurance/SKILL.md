---
name: specification-quality-assurance
description: Validate specification artifacts before approval.
version: 1.0.0
author: Hermes Curator
license: MIT
metadata:
  hermes:
    tags: [specifications, quality, traceability, validation]
    related_skills: [spec-designer, plan, requesting-code-review]
---

# Specification Quality Assurance

## Purpose

Review proposal, requirements, design, task, and acceptance artifacts before they are presented for approval or implementation. This skill is independent of any one project, numbering scheme, or specification framework.

Use it after a specification has been drafted and before declaring it complete. It complements design skills; it does not replace domain review or implementation tests.

## Core principles

1. **Inspect before naming.** Enumerate existing artifacts before selecting a sequence number, change ID, capability, or destination path.
2. **Validate claims mechanically.** Counts, uniqueness, traceability, file state, and checksums must come from actual tools or scripts.
3. **Review semantics separately.** Passing structural checks does not prove that requirements are correct, safe, or useful.
4. **Protect unrelated work.** Capture repository status before writing and compare it afterward; never attribute pre-existing changes to the current task.
5. **One approval gate.** A draft remains unapproved until the user or designated reviewer explicitly accepts it.
6. **Use proportional ceremony.** Apply all relevant checks, but do not force heavyweight OpenSpec layout onto a repository that has an established lightweight convention.

## Workflow

### 1. Establish repository conventions

Before choosing a filename or artifact structure:

- inspect repository guidance (`AGENTS.md`, `CLAUDE.md`, README, contributing documents);
- enumerate existing specifications and proposed changes;
- identify naming and numbering conventions;
- inspect at least one recent representative artifact;
- record repository status so pre-existing changes are distinguishable;
- determine whether OpenSpec CLI, another validator, or only manual validation applies.

If a candidate filename or sequence number already exists, select the next unambiguous identifier before writing. Do not infer the next number from an incomplete listing.

### 2. Validate structure

Confirm that the artifact contains the sections required by its framework and risk profile. Typical sections include:

- intent and motivation;
- scope and out of scope;
- facts, assumptions, decisions, and open questions;
- normative requirements;
- concrete scenarios;
- design and alternatives;
- risks, security, privacy, and failure modes;
- implementation tasks;
- traceability;
- verification and acceptance criteria;
- rollback;
- approval state.

Do not demand irrelevant sections merely to make a document longer.

### 3. Validate identifiers and scenarios

Mechanically check that:

- requirement identifiers are unique;
- task and acceptance identifiers are unique when present;
- every normative requirement has at least one scenario;
- scenario names describe behavior rather than restate headings;
- obligation keywords are intentional (`MUST`/`SHALL`, `SHOULD`, `MAY`);
- no requirement contains multiple unrelated behaviors joined into an untestable bundle.

Use `scripts/validate_markdown_spec.py` as a baseline structural check, adapting patterns when the repository uses another convention.

### 4. Validate traceability

Build or inspect links across:

```text
intent → scope → requirement → scenario → design → task → evidence
```

Every requirement must map to:

- at least one scenario;
- a design component or explicit configuration decision;
- one or more implementation tasks;
- an expected verification artifact.

Flag orphan requirements, tasks without a requirement, and acceptance criteria that cannot be tied to observable evidence.

### 5. Validate factual grounding

For every factual claim likely to affect implementation:

- cite the repository, installed version, official documentation, or measured output;
- label estimates and subjective decisions explicitly;
- avoid freezing transient setup failures into permanent constraints;
- verify model names, configuration keys, file paths, licenses, sizes, and runtime behavior from authoritative sources when relevant;
- distinguish “supported by the product” from “installed and operational in this environment.”

### 6. Validate safety and reversibility

Confirm that the plan includes, when applicable:

- backup before configuration or data changes;
- least-privilege installation and execution;
- secret redaction;
- privacy treatment for logs, caches, and temporary files;
- failure behavior that preserves the primary service;
- verification before declaring success;
- a rollback procedure that restores the prior known-good state.

### 7. Validate repository impact

After writing:

1. run the structural validator;
2. run the framework validator when available;
3. run `git diff --check` in Git repositories;
4. inspect status limited to the intended paths;
5. compare with the pre-write status;
6. compute a checksum only when it is useful for delivery or audit;
7. read back the final header and critical decisions from the actual file.

Do not claim “only this file changed” unless the before/after comparison supports it. If unrelated modifications already existed, say that they were preserved rather than pretending the tree was clean.

### 8. Deliver the review result

Report concisely:

- artifact path and approval state;
- major decisions;
- structural validation counts;
- validator results;
- collisions or corrections made;
- known limitations and unresolved questions;
- explicit statement that implementation has or has not begun.

Stop at the approval gate when the governing design process requires explicit acceptance.

## Common pitfalls

1. **Selecting the next sequence number from a truncated file listing.** Enumerate all matching artifacts or query the exact candidate path.
2. **Detecting a collision only after drafting.** The correction is harmless but avoidable; reserve the final name first.
3. **Counting by eye.** Use deterministic parsing for requirement, scenario, and acceptance counts.
4. **Treating structural validity as design correctness.** Perform a semantic review after the script passes.
5. **Using `git diff` alone for untracked artifacts.** Combine status inspection with direct file validation; untracked files do not appear in ordinary diffs.
6. **Claiming a clean repository when unrelated work exists.** Preserve and explicitly distinguish prior changes.
7. **Inventing performance targets as facts.** Mark targets as acceptance goals and measure them during implementation.
8. **Overwriting established project conventions.** Adapt OpenSpec concepts to the repository unless initialization was authorized.
9. **Skipping read-back.** Verify the persisted file, not merely the write tool's success response.

## Verification checklist

- [ ] Existing artifact names were fully enumerated before choosing the path.
- [ ] Repository guidance and a representative recent spec were inspected.
- [ ] Pre-existing repository changes were recorded.
- [ ] Required sections exist and are proportional to risk.
- [ ] Requirement, task, and acceptance IDs are unique.
- [ ] Every requirement has at least one concrete scenario.
- [ ] Every requirement has design, task, and evidence coverage.
- [ ] Factual claims are sourced, measured, or labeled as assumptions.
- [ ] Safety, privacy, failure modes, and rollback are addressed where relevant.
- [ ] Structural and framework validators were executed or their absence declared.
- [ ] Whitespace/diff checks pass.
- [ ] The final artifact was read back from disk.
- [ ] The report distinguishes new changes from pre-existing work.
- [ ] Approval state and implementation state are explicit.

## Supporting files

- `references/markdown-spec-validation.md` — condensed review method, command patterns, and interpretation guidance.
- `scripts/validate_markdown_spec.py` — reusable structural validator for Markdown specifications.
