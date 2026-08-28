# Proposal: knowledge-vault Search Deployment (persistent host bridge + third live memory-router backend)

## Intent

`KnowledgeVaultBackend` is complete and, uniquely among the five memory-router adapters, was validated end-to-end against a real `serve.py` with **zero wire-format bugs** (`specs/018` §8.1). Yet nothing answers at `knowledge-vault-search.mcps.svc.cluster.local:8088`: that validation ran `serve.py` by hand, in the foreground, with a hand-faked credential directory. `specs/018` §8's last open checklist item — "Despliegue real de `serve.py` como servicio persistente" — is the exact gap.

The consequence today: `/global` searches select both Engram and knowledge-vault, and the knowledge-vault half is permanently unavailable. The curated, human-approved corpus that `/global` was designed to serve is silently absent from every answer, and the router degrades over it correctly enough that nobody notices.

The gap is **purely operational**. No adapter code changes. What is missing is a real deployment flow: an enabled unit with a real credential, an applied Service/EndpointSlice, and memory-router actually told the token.

## Scope

### In Scope
- Enable + start `knowledge-vault-search.service` persistently on `trantor` (`systemctl enable --now`), with `/etc/knowledge-vault/search-token` supplied by `install-host.sh` (PR #75) rather than hand-generated — and confirm the unit's remaining prerequisites (`knowledge-vault-search` account, `KNOWLEDGE_VAULT_DIR=/opt/knowledge-vault/tree` post-restructure per spec 023, index path) are all installer-covered, closing any residual manual step found.
- Apply `kubernetes/mcps/knowledge-vault-search-endpoints.yaml` (selector-less headless `Service` + manually-managed `EndpointSlice` → `10.42.0.1:8088`, D-06) and add it to `bootstrap/05-deploy-manifests.sh`'s ordered apply list.
- Wire memory-router to the bridge: a `knowledge-vault-search-token` k8s Secret in `mcps` plus `KNOWLEDGE_VAULT_TOKEN` + `KNOWLEDGE_VAULT_AUTH_MODE=bearer` env in `memory-router-deployment.yaml`, mirroring the existing `HINDSIGHT_TOKEN` block. No `KNOWLEDGE_VAULT_BASE_URL` override — the adapter's default already names this Service.
- Secret provisioning coverage in `bootstrap/03-create-secrets.sh`, sourcing the token from the host file (single source of truth on `trantor`), not regenerating it — the same "mirror, don't re-generate" posture as `hindsight-codex-shim-key`.
- Manifest tests asserting the Service/EndpointSlice shape (headless, no selector, port 8088, address `10.42.0.1`, `ready: true`) and the memory-router secret refs, per the existing `unittest` manifest-test convention.
- Live end-to-end validation against the real cluster (not just applied): `/global` search returning a real vault hit with `backend == "knowledge-vault"` alongside Engram hits.
- Close `specs/018` §8's deployment checklist item; **correct `specs/014` §9's now-false line** ("Adaptadores de backend #2–6 … fuera de alcance de esta fase") to reflect that Hindsight (spec 022) and knowledge-vault (this change) are deployed and live.
- `specs/024_knowledge_vault_search_deployment.md`.

### Out of Scope
- Any change to `KnowledgeVaultBackend` or to `serve.py`'s wire format — both sides are validated and stay byte-identical unless a decision below forces a timeout constant change.
- Ingress or LAN exposure of the bridge. `10.42.0.1` is deliberately unreachable from the LAN.
- Containerizing the bridge or moving it into the cluster. It reads a host filesystem vault; it stays a host service.
- `store`/`reflect` for knowledge-vault — writes remain behind the propose/review/promote pipeline.
- Cross-backend score normalization and merged-`limit` semantics under `/global` coexistence (`specs/018` §7 open questions, still unowned).
- NetworkPolicy for `mcps` — none exists today; explicit no-op, same posture as spec 022.
- Deploying the remaining adapters (Graphiti, Honcho, Cognee).

## Capabilities

### New Capabilities
- `knowledge-vault-search-service`: the *deployed* bridge contract — in-cluster DNS/port resolution through the selector-less Service, persistence of the unit across host reboot, credential provisioning, and the node-locality boundary. Distinct from `knowledge-vault-search-bridge`, which specifies the process's behavior; this one specifies that it is reachable, always-on, and reachable *only* from this node.

### Modified Capabilities
- `memory-backend-adapters`: the knowledge-vault Adapter requirement's deployed configuration becomes `bearer` auth against a live Service, rather than an unreachable default with `none` auth.
- `knowledge-vault-search-bridge`: **conditional** — only if Decision 3 below changes the `5s` rebuild deadline. Otherwise unchanged.

## Approach

Three independent halves, deliberately ordered host → cluster → router:

1. **Host**: the unit already exists and the token is now installer-provisioned. This half is enable-and-verify, plus fixing whatever the first real `systemctl enable --now` exposes — exactly the class of gap PR #75 and PR #71 each found by actually running the thing.
2. **Cluster**: the manifest already exists and has never been applied. Applying it is additive and reversible; nothing in `mcps` depends on it yet.
3. **Router**: one Secret + two env vars in an existing Deployment. Per `specs/022` §8.1 Bug 2's standing lesson, this is a *manifest* change only, so `kubectl apply` + `rollout restart` suffices — no image rebuild needed, because zero adapter code changes.

Ordering matters for failure loudness: bring up the host bridge and prove `curl` works from a pod *before* the router is told the token, so a router `401` can only mean token mismatch and never "nothing is listening".

## Decisions (resolved by Pedro, 2026-08-27)

| # | Decision | Resolution |
|---|---|---|
| 1 | D-06's `10.42.0.1` binding | **Keep** `10.42.0.1` + bearer. Record the single-node/flannel coupling as an explicit, accepted constraint in `specs/024`. mTLS deferred until memory-router can actually land off `trantor`. |
| 2 | Token transport/rotation | `03-create-secrets.sh` mirrors the host file into the `mcps` Secret; the host file stays source of truth, the script never regenerates. Rotation documented as a four-step ordered runbook in `specs/024`, not automated. |
| 3 | `5s` rebuild timeout | **Measure before enabling.** Time `build_index` against the real `/opt/knowledge-vault/tree`, record the number in `specs/024`, raise the constant only if the measurement justifies it. This changes task ordering — measurement is a task that gates the enable step, not a follow-up. |
| 4 | `specs/014` §9 correction scope | **Broader than the minimal fix.** Audit the entire §9 checklist against all six backends' real deployment states (not just the one stale line), so §9 reads true end-to-end after this change, not just on the one line this change touches directly. |

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `hermes-native/knowledge-vault/systemd/knowledge-vault-search.service` | Unchanged (deployed) | Enabled + started for the first time; edited only if the first real start exposes a gap. |
| `hermes-native/knowledge-vault/scripts/install-host.sh` | Possibly modified | Only if a prerequisite beyond the token (account, dirs) is found missing on real enable. |
| `kubernetes/mcps/knowledge-vault-search-endpoints.yaml` | Applied | Existing file, first `kubectl apply`; the "NOT YET APPLIED" header comment gets corrected. |
| `kubernetes/mcps/memory-router-deployment.yaml` | Modified | `KNOWLEDGE_VAULT_TOKEN` (secretKeyRef) + `KNOWLEDGE_VAULT_AUTH_MODE=bearer`. |
| `kubernetes/mcps/bootstrap/03-create-secrets.sh` | Modified | 7th secret, mirrored from the host token file. |
| `kubernetes/mcps/bootstrap/05-deploy-manifests.sh` | Modified | Add the endpoints manifest to the ordered apply. |
| `kubernetes/mcps/bootstrap/README.md` | Modified | Secret/manifest counts; drop knowledge-vault from the "not validated against a deployed instance" list. |
| `tests/` | New | Manifest tests for the Service/EndpointSlice and the new secret refs. |
| `hermes-native/memory-router/**` | **Unchanged** | Zero adapter code changes — stated explicitly so nobody rebuilds an image needlessly. |
| `specs/018_knowledge_vault_backend.md` | Modified | Close §8's deployment checklist item; update §4/§7 where the open question resolves. |
| `specs/014_memory_router.md` | Modified | §9 stale line correction (Decision 4). |
| `specs/024_knowledge_vault_search_deployment.md` | New | Numbered spec companion. |
| `docs/services/knowledge-vault.md` | Modified | Document the search unit as an enabled, operational unit + the rotation runbook. |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| First real `systemctl enable --now` crash-loops on a prerequisite nobody hit yet (this exact shape has now happened three times: PR #71, PR #75, spec 022 §8.1) | **High** | Treat "run it for real and read the journal" as a required task, not a verification afterthought. Any gap found gets fixed in `install-host.sh`, never by a hand-run command. |
| Host token and k8s Secret drift after a rotation, producing a silent `401` that degrades `/global` back to Engram-only | Med | One source of truth (host file); script mirrors, never regenerates; ordered rotation runbook; validation asserts a real hit, not just a 200. |
| `10.42.0.1` breaks on CNI change, node addition, or memory-router rescheduling | Low today, **high impact** | Decision 1; documented single-node coupling; EndpointSlice address asserted by a manifest test so a change is never accidental. |
| Unmeasured `5s` rebuild deadline turns into `503`s on `/global` as the vault grows | Med | Decision 3 — measure first, record the number. |
| `/global` merged results get noisier or slower once a second backend really answers (score comparability + merged `limit` remain unowned, `specs/018` §7) | Med | Explicitly out of scope, but validation must actually look at merged output quality rather than only asserting a hit exists — so we learn whether it needs owning next. |
| Bridge unavailability becomes user-visible instead of silently degrading | Low | Adapter already raises `BackendUnavailableError` and the dispatcher degrades; a down bridge returns `/global` to today's Engram-only behavior, which is the pre-change baseline, not an outage. |

## Rollback Plan

Three independent, individually reversible halves — none requires an image rebuild.

- **Router**: remove `KNOWLEDGE_VAULT_TOKEN`/`KNOWLEDGE_VAULT_AUTH_MODE` from `memory-router-deployment.yaml`, `kubectl apply` + `rollout restart`. Adapter falls back to `auth_mode="none"`, the bridge rejects it, `/global` degrades to Engram-only — exactly today's behavior.
- **Cluster**: `kubectl delete -f kubernetes/mcps/knowledge-vault-search-endpoints.yaml`. Nothing else in `mcps` references it.
- **Host**: `systemctl disable --now knowledge-vault-search.service`. Vault, index, and the promote/sync pipeline are untouched — the unit only ever reads.

Reverting the commit removes the manifest wiring, secret script changes, tests, and specs together. There is **no data to migrate and no writes to undo**: this is a read-only path over a corpus that already exists.

## Dependencies

- `trantor` reachable with root for `systemctl enable --now` and for reading `/etc/knowledge-vault/search-token`.
- `install-host.sh` (PR #75) already run on `trantor`, or re-run, so the token file exists.
- `/opt/knowledge-vault/tree/knowledge` populated (post spec 023 restructure) and the index present at `/var/lib/knowledge-vault/index/index.json` — an empty vault yields honest-but-useless `200 {"hits": []}` and cannot prove the round trip.
- memory-router pod scheduled on `trantor` (currently the only node) for `10.42.0.1` to resolve at all.

## Success Criteria

- [ ] `systemctl is-enabled knowledge-vault-search.service` → `enabled`, `is-active` → `active`, and it survives a host reboot (proven, not assumed).
- [ ] The unit starts with the installer-provisioned credential — no hand-generated token, no manual `CREDENTIALS_DIRECTORY` fake.
- [ ] `curl http://10.42.0.1:8088/healthz` from inside a `mcps` pod returns `200 {"status": "ok"}`; the same call from the LAN fails to connect.
- [ ] `knowledge-vault-search.mcps.svc.cluster.local:8088` resolves and answers from the `memory-router` pod.
- [ ] An unauthenticated `POST /search` is rejected `401`; the same request with the real bearer returns `200` with real hits.
- [ ] A memory-router `search` on `/global` returns at least one hit with `backend == "knowledge-vault"` **alongside** Engram hits — merged, not replacing.
- [ ] `build_index` timed against the real vault, the number recorded in `specs/024`, and the `5s` deadline either confirmed or adjusted with that number as the justification.
- [ ] Manifest tests assert the Service is headless + selector-less, port 8088, EndpointSlice address `10.42.0.1`, and both memory-router env refs.
- [ ] `specs/018` §8's deployment checklist item is closed; `specs/014` §9's stale line reads true.

## Proposal question round

Resolved directly with Pedro via the four decisions above (2026-08-27). All four confirmed the proposed default except Decision 4, where Pedro chose the broader audit over the minimal one-line fix.
