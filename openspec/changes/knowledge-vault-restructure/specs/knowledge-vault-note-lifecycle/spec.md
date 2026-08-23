# Knowledge Vault Note Lifecycle Specification

## Purpose

Define the single-branch, two-folder (`pending/` → `knowledge/`) note lifecycle
of the knowledge vault: the imperative agent write boundary, the human-audited
promotion act, and the retirement of the staging directories and phone-review
branch the old four-stage pipeline required.

## Requirements

### Requirement: Single-Branch, Two-Folder Vault Structure

The vault MUST be one git repository with one branch, containing exactly two
consumed top-level folders: `pending/` and `knowledge/`. No other folder MAY
be treated as a lifecycle stage.

#### Scenario: Vault has exactly one branch

- GIVEN the vault repository is inspected
- WHEN its branches are listed
- THEN exactly one branch exists — no separate `pending` branch

#### Scenario: Vault has exactly two consumed folders

- GIVEN the vault working tree is inspected
- WHEN its top-level folders are enumerated
- THEN only `pending/` and `knowledge/` are treated as lifecycle stages

### Requirement: Agent Write Boundary — JARVIS Writes Only `pending/`

JARVIS's note-writing path (`propose.py`, invoked per `propose-note` SKILL.md)
MUST write new or edited notes only under `pending/<id>.md`. JARVIS MUST NOT
have any code path capable of writing to `knowledge/` or any folder other
than `pending/`.

#### Scenario: Proposing a note writes to pending/

- GIVEN JARVIS proposes a new note
- WHEN the write completes
- THEN the note exists at `pending/<id>.md` and nowhere else

#### Scenario: Agent-facing write path cannot target knowledge/

- GIVEN the agent-facing write path is inspected or invoked with any target
- WHEN it attempts to resolve a destination
- THEN the destination is always under `pending/`, and no argument or
  configuration can redirect it to `knowledge/`

### Requirement: Promotion Requires Reviewer and Rationale

A promotion command MUST refuse to move `pending/<id>.md` to `knowledge/`
unless the note's frontmatter includes `reviewer`, `decision`, and
`rationale` fields. Promotion MUST NOT run to completion without all three.

#### Scenario: Promotion refuses a note missing rationale

- GIVEN `pending/<id>.md` lacks a `rationale` field
- WHEN promotion is run against it
- THEN promotion refuses and no file moves to `knowledge/`

#### Scenario: Promotion succeeds with full audit fields present

- GIVEN `pending/<id>.md` has `reviewer`, `decision`, and `rationale` set
- WHEN promotion is run against it
- THEN the note moves to `knowledge/<id>.md`

### Requirement: Promotion Strips Review Fields Before Publish

Promotion MUST remove `REVIEW_FIELDS` (`reviewer`, `decision`, `rationale`)
from the note's frontmatter before it lands in `knowledge/`. A published note
MUST NOT carry these fields.

#### Scenario: Published note has no review fields

- GIVEN a note was just promoted
- WHEN `knowledge/<id>.md`'s frontmatter is inspected
- THEN `reviewer`, `decision`, and `rationale` are absent

### Requirement: Promotion Records Reviewer and Rationale in Commit History

Promotion MUST record the stripped `reviewer` and `rationale` values in the
commit message of the promoting commit. Git history is the audit trail for
promotion decisions once the frontmatter fields are stripped.

#### Scenario: Promote commit message carries the audit trail

- GIVEN a note is promoted
- WHEN the resulting commit message is inspected
- THEN it includes the `reviewer` and `rationale` values that were stripped
  from the note

### Requirement: Promotion Preserves the Note Id

Promotion MUST move `pending/<id>.md` to `knowledge/<id>.md` using the same
id — never re-minting or renaming it. Intra-vault links referencing the id
MUST continue to resolve after promotion.

#### Scenario: Id is unchanged across promotion

- GIVEN `pending/<id>.md` is promoted
- WHEN `knowledge/<id>.md` is inspected
- THEN its id is byte-for-byte identical to the pending note's id, and any
  intra-vault link to that id still resolves

### Requirement: JARVIS Cannot Trigger Promotion

No agent-facing code path (skill, tool, or command JARVIS can invoke) MUST be
able to execute promotion. Promotion MUST be triggerable only by a human
running the promote command directly.

#### Scenario: No agent-facing promotion path exists

- GIVEN the set of tools/skills exposed to JARVIS is inspected
- WHEN it is searched for a promotion capability
- THEN none exists — only a human-run command can move a note into
  `knowledge/`

### Requirement: Only the Promote Actor Writes `knowledge/`

The "one writer" invariant is re-scoped, not deleted: JARVIS writes only
`pending/`; only the promote actor writes `knowledge/`. This MUST be
enforced both by filesystem ownership/mode and, for the JARVIS-facing
systemd unit, by `ReadWritePaths=`/`InaccessiblePaths=` that exclude
`knowledge/`.

#### Scenario: JARVIS's unit cannot write knowledge/

- GIVEN the systemd unit running JARVIS's propose path is inspected
- WHEN its `ReadWritePaths=`/`InaccessiblePaths=` directives are read
- THEN `knowledge/` is not writable by that unit

#### Scenario: Promote actor is the sole writer of knowledge/

- GIVEN a promotion is executed
- WHEN the resulting write to `knowledge/` is attributed
- THEN it was performed by the promote actor, never by the JARVIS-facing
  process

### Requirement: Manual `git mv` Is an Unaudited but Still-Validated Escape Hatch

A human MAY bypass the promote command with a bare `git mv` from `pending/`
to `knowledge/`. This path MUST NOT be blocked, but a separate validation
check over `knowledge/` MUST detect and report any published note that still
carries `REVIEW_FIELDS`, so unaudited promotion is visible, never silent.

#### Scenario: Hand-promoted note with leaked review fields is reported

- GIVEN a note was moved into `knowledge/` via bare `git mv` without running
  the promote command, and still carries `reviewer`/`decision`/`rationale`
- WHEN the validation check runs over `knowledge/`
- THEN it reports that note as carrying unstripped review fields

### Requirement: Staging Directories and Phone-Review Branch Are Retired

`proposals/`, `decisions/`, `approved/`, `approve_locally.py`, `review-sync`,
and the separate `pending` git branch MUST be removed. `propose.py` MUST
write `pending/<id>.md` directly — no JSON spool intermediary.

#### Scenario: Staging directories are absent

- GIVEN the vault host filesystem is inspected after this change
- WHEN it is searched for `proposals/`, `decisions/`, or `approved/`
- THEN none exist

#### Scenario: No separate pending branch or review-sync unit exists

- GIVEN the vault's git remote and the host's systemd units are inspected
- WHEN searched for a `pending` branch or a `review-sync` unit
- THEN neither exists

### Requirement: Vault Tree Is the Canonical Git Repository; Bare Repo Is Its Remote

The vault working tree MUST be the repository that `pending/`/`knowledge/`
live in. The bare repository at the SSH remote MUST remain only as a git
remote target — a promoting or proposing commit MUST push directly to it,
without any intermediate scratch-worktree copy step.

#### Scenario: A promote commit pushes directly to the remote

- GIVEN a note is promoted
- WHEN the resulting commit is synced
- THEN it is pushed directly from the vault working tree to the bare remote,
  with no scratch-worktree copy step in between
