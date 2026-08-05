---
name: propose-note
description: Propose a note for the knowledge vault when something durable is learned — a decision and its reason, a root cause, a convention, a verified fact about the infrastructure. Also use when the user says to remember or write something down.
tags: [knowledge, vault, zettelkasten, okf, obsidian, notes]
version: 2.0.0
author: Jarvis
---

# Propose a note for the vault

You do not write to the vault. You propose, and Pedro decides. A proposal costs
nothing and can be rejected in one word; a bad note that reaches the vault
pollutes what he will trust six months from now.

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

Do **not** write `id`, `title` or `timestamp`: the publisher fills them in. The
title comes from your first heading.

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

Pipe the whole note in, frontmatter included:

```bash
printf '%s' '---
type: infra-fact
tags: [storage, k3s]
---
# Longhorn no esta instalado en trantor

El unico storage class es `local-path`. Verificado el 2026-08-04.' \
  | KNOWLEDGE_VAULT_AGENT=jarvis \
    KNOWLEDGE_VAULT_PROPOSAL_SPOOL=/var/lib/knowledge-vault/proposals \
    /opt/knowledge-vault/.venv/bin/knowledge-vault-propose telegram
```

It prints the proposal id. Proposing the same text twice returns the same
proposal and creates nothing, so a retry is safe. A note without a `type` is
refused: fix it and submit again.

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
saved until he approves it.
