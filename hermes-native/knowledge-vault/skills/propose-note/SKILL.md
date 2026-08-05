---
name: propose-note
description: Propose a note for the knowledge vault when something durable is learned — a decision and its reason, a root cause, a convention, a verified fact about the infrastructure. Also use when the user says to remember or write something down.
tags: [knowledge, vault, memory, obsidian, notes]
version: 1.0.0
author: Jarvis
---

# Propose a note for the vault

You do not write to the vault. You propose, and Pedro decides. A proposal costs
nothing and can be rejected in one word; a bad note that reaches the vault
pollutes what he will trust six months from now.

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
- Something already in the vault. Propose a revision of the existing note
  instead of a second note about the same thing.

## How

Write the note, then pipe it in. The note is Markdown and its **first heading
becomes the file name**, so the heading must read like a title someone would
search for, not like a sentence.

```bash
printf '%s' '# Longhorn no esta instalado en trantor

El unico storage class del cluster es `local-path`, el provisioner local de
k3s, atado al nodo. Verificado con `kubectl get storageclass` el 2026-08-04.

Consecuencia: cualquier PVC queda ligado a trantor y no sobrevive a mover el
pod a otro nodo.' | KNOWLEDGE_VAULT_AGENT=jarvis \
  KNOWLEDGE_VAULT_PROPOSAL_SPOOL=/var/lib/knowledge-vault/proposals \
  /opt/knowledge-vault/.venv/bin/knowledge-vault-propose telegram
```

It prints the proposal id. Proposing the same text twice returns the same
proposal and creates nothing, so a retry is safe.

## Write the note for the reader Pedro will be

He will read it without this conversation in his head. So:

- State the fact, not the story of how you found it.
- Say **why**, not only what. A note that says what without why is a note he
  cannot act on.
- Write dates as absolute (`2026-08-04`), never "yesterday" or "today".
- Include the evidence: the command, the value, the path you actually saw.
- Keep it to one idea. Two ideas are two notes.

## After proposing

Tell Pedro in one line that you proposed it and what it says. Do not describe
the mechanism, and do not claim anything was saved to the vault: nothing is
saved until he approves it.
