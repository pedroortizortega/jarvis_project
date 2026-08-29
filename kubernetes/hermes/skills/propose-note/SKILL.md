---
name: propose-note
description: Propose a permanent note for the Obsidian knowledge vault (the "cerebro digital", the Zettelkasten). Use whenever knowledge should outlive this conversation and reach the vault Pedro reads in Obsidian - a decision and its reason, a root cause, a convention, a verified fact. Use it for any request naming the vault, the cerebro digital, Obsidian, a nota, or a Zettelkasten. This is NOT the agent's own memory tool: `memory` keeps context for you, this proposes a document for Pedro to review and publish.
tags: [knowledge, vault, zettelkasten, okf, obsidian, notes]
version: 3.0.0
author: Jarvis
---

# Propose a note for the vault

**Not the `memory` tool.** `memory` is your own working context and nobody
reviews it. This produces a document that lands in Pedro's Obsidian vault after
he approves it. If a request is about the vault, the cerebro digital, Obsidian
or a nota, it belongs here — even when it also sounds like "remember this".

**You write only to `pending/`, through the `knowledge-vault-propose` CLI —
never a raw file write, never any other tool.** You have no code path that
reaches `knowledge/`: only a human-recorded decision followed by the unattended
`knowledge-vault-promote` timer moves a note there, and nothing you run can
trigger that promotion directly. A proposal costs nothing and can be rejected
in one word; a bad note that reaches the vault pollutes what Pedro will trust
six months from now.

**Never write the note file yourself.** Always pipe the rendered Markdown into
`knowledge-vault-propose` on stdin (see [How to submit](#how-to-submit)). Do
not construct or edit a file under `pending/`, `knowledge/`, or anywhere else
in the vault tree directly — the CLI mints the id, checks for a duplicate, and
writes the file atomically with the right owner/mode; a hand-written file skips
all three.

The vault is a Zettelkasten wrapped in the Open Knowledge Format: **one idea per
note**, notes linked to each other, and a YAML envelope so agents can query it.

## Propose when

- A decision is made **and there is a reason worth keeping**. The reason is the
  note; the decision alone is not.
- A root cause is found, after debugging something that was not obvious.
- A convention or rule of this system is established.
- A fact about the infrastructure is **verified**, not assumed: a real value, a
  real path, an output actually observed.
- Pedro says to remember it, write it down, or save it. Then propose without
  hesitating.

## Do not propose

- Anything you did not verify. A wrong note is worse than no note, because it
  will be read as true later.
- What the repository already records: code structure, git history, what a file
  plainly says. Link to it instead of copying it.
- Conversation, status updates, or what you did in this session.
- **Two ideas in one note.** That is the one rule Zettelkasten does not bend. If
  you are about to write "and also", stop: it is a second note, and the two
  should link to each other.

## The shape of a note

```markdown
---
type: infra-fact
tags: [storage, k3s, trantor]
description: El cluster no tiene Longhorn; el unico storage class es local-path.
---
# Longhorn no esta instalado en trantor

El unico storage class del cluster es `local-path`, el provisioner local de
k3s, atado al nodo. Verificado con `kubectl get storageclass` el 2026-08-04.

Por eso [los PVC quedan atados al nodo](20260804224512.md), y por eso el
control plane de propuestas [usa SQLite](20260805090133.md) en vez de una base
de datos del cluster.
```

`type` is the only required field and it is what makes a note queryable. Choose
a short, reusable one and prefer an existing one over inventing a near-synonym:
`decision`, `infra-fact`, `root-cause`, `convention`, `concept`.

`tags` and `description` are optional and worth the seconds they cost.

Do **not** write `id`, `title` or `timestamp`: `knowledge-vault-propose` fills
them in when it renders the note into `pending/`. The title comes from your
first heading.

## Links are the point

A note nobody links to is a note nobody will find again. Before proposing, ask
what the vault already knows about this, and link to it.

Links are markdown links to the note's **id file name**, `[texto](20260804224512.md)`,
never to a title — titles change, ids never do. To find the id of an existing
note, look at the vault directory or its frontmatter.

If you do not know the id of a note you want to link, say so in the body in
plain words rather than inventing a link. A broken link is worse than a
sentence.

## How to submit

Pipe the whole note in, frontmatter included, to `knowledge-vault-propose`.
The only required environment variable is `KNOWLEDGE_VAULT_DIR` (the vault
tree root); an optional single argument names the source channel:

```bash
printf '%s' '---
type: infra-fact
tags: [storage, k3s]
---
# Longhorn no esta instalado en trantor

El unico storage class es `local-path`. Verificado el 2026-08-04.' \
  | KNOWLEDGE_VAULT_AGENT=jarvis \
    KNOWLEDGE_VAULT_DIR=/opt/knowledge-vault/tree \
    /opt/knowledge-vault/.venv/bin/knowledge-vault-propose telegram
```

It writes `pending/<id>.md` directly — no JSON spool, no intermediate stage —
and prints the new note's id on stdout. Proposing the same text twice returns
the path of the already-pending note and writes nothing new (deduped on a
`sha256` of the note text, kept as `idempotency_key` in the pending note's own
frontmatter), so a retry is safe. A note without a `type` is refused before
anything is written: fix it and submit again.

## Write for the reader Pedro will be

He will read it without this conversation in his head. So:

- State the fact, not the story of how you found it.
- Say **why**, not only what. A note that says what without why cannot be acted
  on.
- Write dates as absolute (`2026-08-04`), never "yesterday" or "today".
- Include the evidence: the command, the value, the path you actually saw.

## After proposing

Tell Pedro in one line that you proposed it and what it says. Do not describe
the mechanism, and do not claim anything was saved to the vault: nothing is
in `knowledge/` — and therefore nothing is searchable or real — until he
records a decision and an unattended promotion run picks it up. Never say a
note "was published", "is in the vault", or "is now searchable" on the basis
of having proposed it.

## What you must never do

- Never write a file into `pending/`, `knowledge/`, or anywhere in the vault
  tree except through `knowledge-vault-propose`'s stdin pipe.
- Never touch `knowledge/` in any way — you have no tool that can, and no
  workaround (a raw file write, a shell command, editing an existing note)
  is acceptable even if the filesystem happens to allow it.
- Never run, script, or ask a human to run promotion on your behalf as a way
  to skip review. Promotion moving a note into `knowledge/` runs on its own
  unattended schedule (`knowledge-vault-promote.timer`) once — and only once
  — a human has filled in `reviewer`, `decision: approved`, and `rationale`
  on the pending note; nothing you do should look like, or be timed to
  simulate, that human step.
- Never pre-fill or suggest values for `reviewer`, `decision`, or `rationale`
  on a pending note. Those three fields exist so a human decision is
  auditable later; a note where you supplied any of them is not reviewed,
  even if the timer would technically promote it. This is a trust boundary
  the system does not enforce at the filesystem level — you are the only
  thing standing between "proposed" and "silently self-approved", so treat
  this rule as absolute.
