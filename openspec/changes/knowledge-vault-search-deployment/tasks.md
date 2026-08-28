# Tasks: knowledge-vault Search Deployment

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | PR1 ~120 (manifests/bootstrap/tests), PR2 ~150 (specs/docs prose) |
| 400-line budget risk | Low |
| Chained PRs recommended | Optional — single PR is fine given low line count, but the manual host/cluster/router evidence in Phase 3-6 gates merge regardless of PR split |
| Suggested split | PR 1 (manifests + bootstrap + tests) → PR 2 (specs/018, specs/014, specs/024, docs) — or one PR if evidence is captured before opening it |
| Delivery strategy | ask-on-risk (D-07's "gap found → fix in installer, never hand-run" risk is High-likelihood per proposal) |
| Chain strategy | N/A unless split |

Decision needed before apply: No — all four decisions (D-01..D-08) are resolved in design.md.
400-line budget risk: Low.

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Manifest edits + bootstrap wiring + manifest tests | PR 1 | `python -m unittest tests.test_knowledge_vault_search_manifest` | `kubectl apply -f kubernetes/mcps/knowledge-vault-search-endpoints.yaml` then `kubectl rollout status deploy/memory-router -n mcps` | `kubectl delete -f knowledge-vault-search-endpoints.yaml`; revert `memory-router-deployment.yaml`/`03-create-secrets.sh`/`05-deploy-manifests.sh` |
| 2 | Host measurement + real enable + evidence capture | Manual, gates PR merge/close per D-07 | N/A — `systemctl`/`journalctl`/`ss` on `trantor` | Real host, real reboot | `systemctl disable --now knowledge-vault-search.service` |
| 3 | Live E2E validation | Manual, post cluster+router apply | N/A — `curl`/router `search` call | Real cluster | Router rollback (R1) degrades to Engram-only |
| 4 | Spec/doc truth-up | PR 2 (or folded into PR 1) | N/A — prose | N/A | Revert the spec/doc diffs |

Note: `specs/024_knowledge_vault_search_deployment.md` and both OpenSpec delta specs (`openspec/changes/knowledge-vault-search-deployment/specs/{knowledge-vault-search-service,memory-backend-adapters}/spec.md`) are already written and consistent with design — no task recreates them. Spec 024 §6's measurement line and §"Evidencia de despliegue" are currently *pending*; Phase 2 tasks below are what fills them in.

## Phase 1: Rebuild-Timeout Measurement (blocks Phase 3 — D-04, D-07 ordering)

- [ ] 1.1 On `trantor`, in the installed venv, time `build_index('/opt/knowledge-vault/tree', <tmp index>)` **cold** (no index present) — the worst case a restart can hit. Record wall-clock p100.
  - Satisfies: `knowledge-vault-search-service` Requirement "Rebuild Timeout Measured Against the Real Vault"; design.md D-04.
- [ ] 1.2 Time the same call **warm** (index already present). Record wall-clock p100, plus published-note count and tree byte size for future extrapolation.
  - Satisfies: same requirement; design.md D-04.
- [ ] 1.3 Apply the threshold rule stated up front in D-04: keep `5s` if cold p100 ≤ 1.5s; otherwise set the deadline to `ceil(4 × cold p100)`. Record the decision and both numbers in `specs/024_knowledge_vault_search_deployment.md` §6, replacing the "pendiente" placeholder.
  - Satisfies: design.md D-04, D-05.
- [ ] 1.4 If (and only if) 1.3 raises the deadline: edit `Environment=KNOWLEDGE_VAULT_SEARCH_TIMEOUT_SECONDS=5` in `hermes-native/knowledge-vault/systemd/knowledge-vault-search.service` to the new value — **not** `serve.py`'s `DEFAULT_TIMEOUT_SECONDS` constant (design.md D-05 keeps that byte-identical). Add a `MODIFIED` delta on the `knowledge-vault-search-bridge` capability's "Bounded Inline Index Rebuild" requirement documenting the new number and its justification.
  - Satisfies: design.md D-05; proposal.md Capabilities "conditional" modification.
  - Note: if 1.3 does not raise the deadline, this task is a no-op and the `knowledge-vault-search-bridge` capability stays unmodified, exactly as proposal.md states.

## Phase 2: Cluster Manifests + Bootstrap Wiring (PR 1)

- [x] 2.1 Modify `kubernetes/mcps/knowledge-vault-search-endpoints.yaml`: replace the `NOT YET APPLIED` header comment with an applied/node-locality note (design.md F-4). Object bodies (`Service`, `EndpointSlice`) stay byte-identical — no field changes.
  - Satisfies: `knowledge-vault-search-service` Requirement "Headless Service and Fixed EndpointSlice"; design.md File Changes table.
- [x] 2.2 Modify `kubernetes/mcps/memory-router-deployment.yaml`: add `KNOWLEDGE_VAULT_TOKEN` (`secretKeyRef: {name: knowledge-vault-search-token, key: search-token}`) and `KNOWLEDGE_VAULT_AUTH_MODE: "bearer"`, mirroring lines 94-100's `HINDSIGHT_TOKEN` block (design.md F-5). No `KNOWLEDGE_VAULT_BASE_URL` (D-06).
  - Satisfies: `memory-backend-adapters` MODIFIED Requirement "Knowledge-Vault Adapter" (deployed config: bearer, not `none`); design.md D-06, Interfaces/Contracts block.
- [x] 2.3 Modify `kubernetes/mcps/bootstrap/03-create-secrets.sh`: add block 7 (design.md Interfaces/Contracts) — read `$KV_SEARCH_TOKEN_FILE` (default `/etc/knowledge-vault/search-token`), abort loudly with a non-zero exit if unreadable/empty and **do not** create an empty Secret, else `kubectl create secret generic knowledge-vault-search-token --from-literal=search-token=... --dry-run=client | apply_secret`. Update the closing `log` line from "6 secrets" to "7 secrets".
  - Satisfies: `knowledge-vault-search-service` Requirement "Secret Mirrors the Host Token, Never Regenerates"; design.md D-02, F-3.
- [x] 2.4 Modify `kubernetes/mcps/bootstrap/05-deploy-manifests.sh`: append `knowledge-vault-search-endpoints.yaml` to the ordered apply list, **before** the memory-router apply (loudness ordering per design.md Migration/Rollout step 3 vs step 5).
  - Satisfies: proposal.md Scope "add it to bootstrap/05-deploy-manifests.sh's ordered apply list"; design.md File Changes table.
- [x] 2.5 Modify `kubernetes/mcps/bootstrap/README.md`: secret count 6 → 7, add the new manifest to its list, drop knowledge-vault from the "not validated against a deployed instance" list.
  - Satisfies: design.md File Changes table.
  - Note: kept knowledge-vault noted as "manifest/secret wiring applied by bootstrap, live E2E validation still pending" rather than fully "validated" — live validation has not happened yet (Phase 4-7 unchecked below).

## Phase 3: Manifest Enforcement Tests (PR 1, strict TDD)

- [x] 3.1 RED: create `tests/test_knowledge_vault_search_manifest.py` (`unittest.TestCase` + `yaml.safe_load_all`, following `tests/test_hindsight_manifest.py`, design.md F-5) asserting: `Service` is `clusterIP: None` with **no** `spec.selector`, port/targetPort `8088`; `EndpointSlice` `addressType: IPv4`, address exactly `10.42.0.1`, `conditions.ready: true`, `kubernetes.io/service-name` matches the Service name; **no `kind: Ingress`** anywhere in the file. Confirmed RED via `git stash` of Phase 2's manifest edits (3 failures: headless `clusterIP`, no-selector, `KNOWLEDGE_VAULT_TOKEN` secretKeyRef), then restored.
  - Satisfies: `knowledge-vault-search-service` Requirement "Node-Locality Coupling Is an Accepted, Documented Constraint" (EndpointSlice address test); design.md Testing Strategy row 1.
- [x] 3.2 In the same file, add the cross-manifest test: `memory-router-deployment.yaml` has `KNOWLEDGE_VAULT_AUTH_MODE == "bearer"`, a `KNOWLEDGE_VAULT_TOKEN` secretKeyRef of exactly `{knowledge-vault-search-token, search-token}` with **no inline `value:`**, and **no** `KNOWLEDGE_VAULT_BASE_URL` key present anywhere in the file.
  - Satisfies: `memory-backend-adapters` MODIFIED Requirement "Knowledge-Vault Adapter"; design.md Testing Strategy row 2, Threat Matrix "Secret handling" row.
- [x] 3.3 GREEN: run `python -m unittest tests.test_knowledge_vault_search_manifest` after Phase 2's edits land; confirm it passes. 12/12 passed.
- [x] 3.4 Run `python -m unittest discover -s tests` and confirm the full suite still passes (no regression in other manifest tests). 398/398 passed.

## Phase 4: Real Host Enable, With Named Evidence (D-07 — gates Phase 5)

**Every sub-task's done-condition is pasted evidence into `specs/024` §"Evidencia de despliegue" — a checkbox alone does not satisfy this phase.** Any gap found here is fixed in `install-host.sh` (or the unit), never by a hand-run command, and re-proven by a full `disable --now` → re-run installer → `enable --now` cycle before the evidence is considered final.

- [ ] 4.1 Confirm Phase 1's measurement is recorded in `specs/024` before proceeding (D-04 gates this phase — do not enable on an unmeasured deadline).
  - Satisfies: design.md Migration/Rollout step ordering ("Measure... Gates step 2").
- [ ] 4.2 On `trantor`, confirm/re-run `install-host.sh` (PR #75) so `/etc/knowledge-vault/search-token` exists (`root:$GROUP`, `0440`, `openssl rand -hex 32`, never overwritten if present). Verify the `knowledge-vault-search` account, `KNOWLEDGE_VAULT_DIR=/opt/knowledge-vault/tree` (post spec-023 restructure), and the index path are all installer-covered — not manual steps. Note any gap found.
  - Satisfies: `knowledge-vault-search-service` Requirement "Installer-Provisioned Credential Only"; proposal.md Scope bullet 1; design.md F-1.
- [ ] 4.3 Run `systemctl enable --now knowledge-vault-search.service`. Read `journalctl -u knowledge-vault-search.service -b` in full. If a prerequisite gap surfaces (crash-loop, missing dir, permission error — the exact shape that has landed three times per proposal.md Risks), fix it in `install-host.sh` (or the unit file if the gap is a unit config issue, per design.md File Changes "Conditional" rows) — **never** by a hand-run `mkdir`/`chown`/etc. Then re-run this task from a clean `disable --now` state.
  - Satisfies: `knowledge-vault-search-service` Requirement "Persistent Host Unit, Reboot-Survivable"; design.md D-07.
- [ ] 4.4 Capture and paste into `specs/024` §"Evidencia de despliegue" (create this section if absent): `systemctl is-enabled knowledge-vault-search.service` → `enabled`; `systemctl is-active knowledge-vault-search.service` → `active`; `systemctl show -p NRestarts knowledge-vault-search.service` → `0`, checked **after waiting ≥5 minutes** post-start; `journalctl -u knowledge-vault-search.service -b` output showing a clean start with no traceback; `ss -lntp` output showing the listener bound to `10.42.0.1:8088` and explicitly **not** `0.0.0.0:8088`.
  - Satisfies: design.md D-07 required evidence list.
- [ ] 4.5 Reboot `trantor` for real. After boot, repeat the exact same three checks (`is-enabled`/`is-active`/`NRestarts==0` after ≥5 min) plus a fresh `journalctl -u knowledge-vault-search.service -b` and `ss -lntp` capture. Paste all of it into `specs/024` alongside the pre-reboot evidence, clearly labeled "post-reboot".
  - Satisfies: `knowledge-vault-search-service` Requirement "Unit survives host reboot"; design.md D-07 ("the same three after a real host reboot").
- [ ] 4.6 If 4.3 or 4.5 found and fixed any gap in `install-host.sh`/the unit, re-run the full cycle (`disable --now` → re-run installer → `enable --now` → reboot) one more time and replace the evidence in `specs/024` with the clean re-proof, not an amendment appended to the failed run.
  - Satisfies: design.md D-07, Open Questions "D-07 may find a prerequisite gap".

## Phase 5: Cluster Apply + Curl Proof Before Router Wiring (D-01, D-07 ordering)

- [ ] 5.1 `kubectl apply -f kubernetes/mcps/knowledge-vault-search-endpoints.yaml`. Confirm `Service` and `EndpointSlice` are created as expected (`kubectl get svc,endpointslice -n mcps`).
  - Satisfies: `knowledge-vault-search-service` Requirement "Headless Service and Fixed EndpointSlice"; proposal.md In Scope bullet 2.
- [ ] 5.2 From inside an `mcps` pod, `curl http://10.42.0.1:8088/healthz` → confirm `200 {"status": "ok"}`. From outside the cluster (LAN), attempt the same call against `10.42.0.1:8088` and confirm it fails to connect. **This proof must complete before Phase 6 wires the router token** (design.md D-07/D-01 ordering: a later router `401` must only ever mean token mismatch, never "nothing is listening").
  - Satisfies: `knowledge-vault-search-service` Requirement "In-cluster DNS resolves and answers" / "Bridge unreachable from the LAN"; proposal.md Success Criteria.
- [ ] 5.3 From inside an `mcps` pod, confirm `knowledge-vault-search.mcps.svc.cluster.local:8088` resolves via DNS and answers.
  - Satisfies: `knowledge-vault-search-service` Requirement "In-cluster DNS resolves and answers".
- [ ] 5.4 From inside an `mcps` pod, send an unauthenticated `POST /search` and confirm `401`.
  - Satisfies: `memory-backend-adapters` Scenario "Deployed instance requires bearer auth against the real Service" (unauthenticated half).

## Phase 6: Secret + Router Wiring (D-02, D-06 — after Phase 5's curl proof)

- [ ] 6.1 Run `kubernetes/mcps/bootstrap/03-create-secrets.sh` (block 7 from Phase 2.3). Confirm the `mcps/knowledge-vault-search-token` Secret's value equals the host file's content byte-for-byte, and confirm the script performed no token generation (rerun is idempotent — same value both times).
  - Satisfies: `knowledge-vault-search-service` Requirement "Secret Mirrors the Host Token, Never Regenerates".
- [ ] 6.2 `kubectl apply -f kubernetes/mcps/memory-router-deployment.yaml` then `kubectl rollout restart deploy/memory-router -n mcps` and `kubectl rollout status`. Manifest-only change — confirm no image rebuild occurred (design.md Migration/Rollout step 5, `specs/022` §8.1 Bug 2's standing lesson).
  - Satisfies: `memory-backend-adapters` MODIFIED Requirement "Knowledge-Vault Adapter" (deployed bearer config).
- [ ] 6.3 From inside an `mcps` pod (or via the router itself), repeat an authenticated `POST /search` against the bridge with the real bearer token and confirm `200` with real hits (not an empty result).
  - Satisfies: `memory-backend-adapters` Scenario "Deployed instance requires bearer auth against the real Service" (authenticated half); `knowledge-vault-search-service` proposal.md Success Criteria.

## Phase 7: Live E2E — `/global` Merged Fan-Out (proposal.md Risks row 5)

- [ ] 7.1 Issue a `search` request on `/global` with a query matching curated vault content. Confirm the response includes at least one hit with `backend == "knowledge-vault"` **and** at least one Engram-backed hit in the same response (merged, not replacing).
  - Satisfies: `knowledge-vault-search-service` Requirement "Live /global Fan-Out Includes Knowledge-Vault"; proposal.md Success Criteria.
- [ ] 7.2 Inspect the merged output for ordering/noise quality (score comparability, result diversity) — not to fix it (cross-backend score normalization stays explicitly out of scope per proposal.md and design.md Open Questions), but to record a finding on whether it needs owning next. Add the observation to `specs/024` or a follow-up note.
  - Satisfies: proposal.md Risks row 5; design.md Testing Strategy row "E2E (manual)"; design.md Open Questions last bullet.

## Phase 8: Spec and Doc Truth-Up (PR 2, D-08)

- [x] 8.1 In `specs/018_knowledge_vault_backend.md` §8 — **deviation from the literal task text**: kept line 305's checklist item `[ ]` (unchecked) rather than flipping to `[x]`, because the real host enable (Phase 4) has not happened yet and this agent has no `trantor` root access to perform it. Added a note pointing at `specs/024` and stating the block reason instead. Updated the rebuild-deadline open question (§7) to point at `specs/024` §6 as pending-not-resolved (Phase 1's measurement also not done, same reason).
  - Satisfies: proposal.md Scope "Close specs/018 §8's deployment checklist item" — **partially**: prerequisite work done, item itself stays open honestly until Phase 4 runs; design.md File Changes table.
- [x] 8.2 In `specs/014_memory_router.md` §9, performed the **full audit** per D-08: replaced the single stale line with six per-backend lines. Deviation from `specs/024` §7's table: since knowledge-vault is **not yet actually deployed live** (Phase 4-7 blocked on host/cluster access), its line is `[ ]` "adapter complete, manifests/secret/tests ready, live deploy pending — see specs/024", not `[x]` deployed — kept honest to real state rather than the pre-written table's anticipated end-state. Hindsight `[x]` deployed (spec 022); Graphiti/Honcho/Cognee `[ ]` adapter-only. "Obsidian" naming corrected to "knowledge-vault" throughout.
  - Satisfies: design.md D-08; proposal.md Decision 4.
- [x] 8.3 In the same `specs/014` §9 audit, re-verified lines 263-274 against the repo — corrected line 272's "6 archivos" manifest count to 10 (6 memory-router + 3 Hindsight + 1 knowledge-vault-search-endpoints, per `bootstrap/05-deploy-manifests.sh`'s current apply list).
  - Satisfies: design.md D-08, `specs/014 §9 audit (D-08)` section ("re-verify the rest of §9 ... 6 archivos").
- [x] 8.4 Updated `docs/services/knowledge-vault.md` with a new "Search deployment (in-cluster bridge)" section documenting `knowledge-vault-search.service`'s intended enabled/operational posture, the Service/EndpointSlice/Secret wiring, and the four-step ordered rotation runbook from design.md D-03/`specs/024` §5.
  - Satisfies: `knowledge-vault-search-service` Requirement "Token Rotation Is a Documented Runbook, Not Automated"; proposal.md Affected Areas table.
- [ ] 8.5 Filled in `specs/024_knowledge_vault_search_deployment.md`'s checklist (§10) **partially**: checked "Diseño" and "Tareas" (artifacts exist and were used), left "Implementación" unchecked with a "parcial" note (manifests/bootstrap/tests done, live host/cluster/router/E2E phases blocked on `trantor` root + `kubectl apply` this agent cannot run) and "Validado en vivo" unchecked. §6's measurement placeholder is still "pendiente" — not filled in, since Phase 1's live measurement did not run. Task not marked complete because the measurement and evidence sections remain genuinely pending, not because of an oversight.
  - Satisfies: `specs/024` §10 self-checklist; design.md D-04, D-07.

## Phase 9: Final Suite + Rollback Sanity

- [x] 9.1 Run `python -m unittest discover -s tests` once more after all spec/doc edits, to confirm nothing broke. 398/398 passed. Also ran the knowledge-vault package's own suite in a fresh venv: 124/124 passed.
- [x] 9.2 Dry-review the three rollback commands from design.md's Rollback table (R1 router, R2 cluster, R3 host) against the final state of the manifests — confirmed each command's object/field names (`KNOWLEDGE_VAULT_TOKEN`/`KNOWLEDGE_VAULT_AUTH_MODE` env names, `knowledge-vault-search` Service/EndpointSlice names, `knowledge-vault-search-token` Secret name, `knowledge-vault-search.service` unit name) match the actual repo state exactly — no drift.
  - Satisfies: proposal.md Rollback Plan; design.md Migration/Rollout Rollback table.
