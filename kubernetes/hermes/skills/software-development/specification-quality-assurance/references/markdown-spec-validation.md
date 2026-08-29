# Markdown specification validation

Use this reference when a repository stores proposals or feature specifications as Markdown rather than a complete OpenSpec directory.

## Pre-write discovery

1. Enumerate every existing specification matching the repository convention.
2. Check the exact candidate path before creating it.
3. Inspect the most recent representative artifact for headings, IDs, status fields, and approval conventions.
4. Capture repository status before writing.

A partial or truncated listing is not sufficient evidence that a sequence number is free.

## Structural checks

The reusable script checks a single Markdown file:

```bash
python scripts/validate_markdown_spec.py path/to/spec.md
```

Optional patterns allow repository-specific identifiers:

```bash
python scripts/validate_markdown_spec.py path/to/spec.md \
  --requirement-pattern 'REQ-[A-Z]+-[0-9]{3}' \
  --acceptance-pattern 'AC-[0-9]{3}'
```

The default conventions expect requirement headings beginning with `### REQ-...`, scenario headings beginning with `#### Scenario:` or `#### Escenario:`, and acceptance checklist entries beginning with `- [ ] AC-...`.

The script verifies:

- at least one requirement exists;
- requirement IDs are unique;
- each requirement block has a scenario;
- acceptance IDs, when present, are unique;
- a traceability section, when present, mentions every requirement;
- the file is valid UTF-8 and non-empty.

A successful script run establishes structural consistency only. It does not prove that the requirements are complete or technically correct.

## Git checks

In a Git repository:

```bash
git diff --check
git status --short -- path/to/spec.md
```

Remember that ordinary `git diff` omits untracked files. Read the new file directly and include it in status inspection.

When the repository was already dirty, compare pre-write and post-write status. Report that unrelated changes were preserved; do not claim the entire tree is clean.

## Semantic review prompts

For each requirement ask:

- Is the behavior externally observable?
- Can a tester decide pass/fail without knowing the implementation?
- Does the scenario include a meaningful precondition, action, and outcome?
- Is the requirement represented in design and tasks?
- Is expected evidence named?
- Are errors, permissions, limits, privacy, and rollback covered when relevant?

For each factual implementation claim ask:

- Was it read from the repository or authoritative documentation?
- Was it measured on the current environment?
- Is it instead a hypothesis, estimate, or preference that needs labeling?

## Approval report

A compact completion report should include:

```text
Artifact: <path>
State: Draft / Approved / Implemented
Requirements: <count>
Scenarios: <count>
Acceptance criteria: <count>
Structural validation: PASS / FAIL
Framework validation: PASS / NOT AVAILABLE / NOT APPLICABLE
Repository impact: <intended paths and pre-existing changes>
Implementation begun: yes / no
```

If a collision was corrected, state both the rejected candidate and final identifier. If implementation has not begun, make that explicit so approval cannot be mistaken for deployment.
