# Design: knowledge-vault Search Deployment

## Technical Approach

Operational-only change, modelled on `specs/022` (hindsight-deployment): deploy the real backend of an already-code-complete adapter. Zero lines change under `hermes-native/memory-router/**` and `hermes-native/knowledge-vault/src/**`, so no image rebuild and no adapter test churn — the deliverable is an enabled unit, an applied Service/EndpointSlice, one mirrored Secret, two env vars, manifest tests, and spec truth-up.

Three halves, ordered host → cluster → router (proposal §Approach), with one insertion: the rebuild-timeout measurement (D-04) runs **before** the persistent enable, because the enable is what makes an unmeasured deadline user-visible.

## Verified Findings

- **F-1** — `install-host.sh:129-138` already creates `/etc/knowledge-vault/search-token` (`root:$GROUP`, `0440`, `openssl rand -hex 32`, never overwritten). The unit consumes it via `LoadCredential=search-token:` only; it is never an env var.
- **F-2** — the unit already pins `KNOWLEDGE_VAULT_SEARCH_TIMEOUT_SECONDS=5` as an `Environment=` line (`knowledge-vault-search.service:16`), so the 5s deadline is a **unit knob**, not only the `DEFAULT_TIMEOUT_SECONDS` constant in `serve.py:63`.
- **F-3** — `03-create-secrets.sh` already has the exact mirror shape needed (block 6, lines 86-100: read source of truth → abort loudly if empty → `create --dry-run=client | apply_secret`). The new block is a fourth instance of an existing pattern, not a new mechanism.
- **F-4** — the endpoints manifest exists and is complete (headless, selector-less, `8088`, `10.42.0.1`, `ready: true`); only its `NOT YET APPLIED` header comment is false.
- **F-5** — `memory-router-deployment.yaml:94-100` is the byte-for-byte template for the router env block; `tests/test_hindsight_manifest.py` is the template for the manifest test.

## Architecture Decisions

| # | Decision | Options / tradeoff | Choice and rationale |
|---|---|---|---|
| D-01 | Host→cluster transport (Decision 1, resolved) | `10.42.0.1` + bearer vs mTLS vs pod-network relay | **Keep `10.42.0.1` + bearer, unchanged.** `specs/018` D-06 already designed and justified this path; it is not redesigned here, only applied. The single-node/flannel coupling is recorded in `specs/024` as an **accepted constraint** with its two invalidating events named (CNI change, second node + router reschedule). mTLS deferred until memory-router can actually land off `trantor`. |
| D-02 | Token source of truth (Decision 2, resolved) | k8s Secret generates → host consumes vs host file generates → Secret mirrors | **Host file is source of truth; the script mirrors, never regenerates.** The unit reads the file at start via `LoadCredential`; a regenerating script would rotate the k8s side without the host side and produce a silent `401`. Identical posture to `hindsight-codex-shim-key` (F-3), and it keeps `install-host.sh` the single writer (F-1). |
| D-03 | Rotation | Automated (reloader/CronJob) vs documented manual runbook | **Documented four-step ordered runbook in `specs/024` + `docs/services/knowledge-vault.md`.** Rotation is rare and cross-boundary (host file + systemd + k8s), so automation would be more moving parts than the failure it prevents. The order is load-bearing and stated as such: (1) regenerate `/etc/knowledge-vault/search-token`, (2) `systemctl restart knowledge-vault-search.service`, (3) re-run `03-create-secrets.sh` (recreates the Secret from the new file), (4) `kubectl rollout restart deploy/memory-router`. Steps 2 and 4 both re-read; between them `/global` degrades to Engram-only, which is the pre-change baseline, not an outage. |
| D-04 | 5s rebuild deadline (Decision 3, resolved) | Ship 5s unverified vs measure after vs **measure before enabling** | **Measure first; the measurement gates the enable.** Time `build_index('/opt/knowledge-vault/tree', <tmp index>)` on `trantor`, in the installed venv, twice: **cold** (no index present — the worst case a restart can hit) and **warm** (index present). Record both, plus published-note count and tree byte size, in `specs/024` so future growth is extrapolable rather than re-measured blind. Threshold rule, stated up front so the result cannot be rationalised: **keep 5s if cold p100 ≤ 1.5s** (>3× headroom); otherwise set the deadline to `ceil(4 × cold p100)`. |
| D-05 | Where a raised deadline lives, if D-04 raises it | `DEFAULT_TIMEOUT_SECONDS` in `serve.py` vs `Environment=` in the unit | **The unit's `Environment=KNOWLEDGE_VAULT_SEARCH_TIMEOUT_SECONDS` (F-2).** Tuning a deployment knob keeps `serve.py` byte-identical, keeps the `knowledge-vault-search-bridge` capability **unmodified** (the proposal's conditional modification collapses to "unchanged"), and keeps the code default honest as a library default. Changing the constant would silently re-tune every future deployment from one host's measurement. |
| D-06 | `KNOWLEDGE_VAULT_BASE_URL` on memory-router | Explicit override vs rely on the adapter default | **No override**, exactly as `specs/022` D-07 concluded for Hindsight. The adapter's default (`knowledge_vault.py:94`) already names `knowledge-vault-search.mcps.svc.cluster.local:8088`, which is the Service this change applies. Only `KNOWLEDGE_VAULT_TOKEN` + `KNOWLEDGE_VAULT_AUTH_MODE=bearer` are added. A redundant override would re-create the "the default is only correct because everyone overrides it" trap. |
| D-07 | First real `systemctl enable --now` (the 3×-repeated risk) | Verification checklist item vs a task with named evidence | **A required task whose done-condition is pasted evidence, not a tick.** This exact failure shape has landed three times (PR #71, PR #75, `specs/022` §8.1). Required evidence, all captured into `specs/024` §"Evidencia de despliegue": `systemctl is-enabled` → `enabled`; `is-active` → `active`; `systemctl show -p NRestarts` → `0` after ≥5 min; `journalctl -u knowledge-vault-search.service -b` clean from unit start with no traceback; `ss -lntp` showing the listener bound to `10.42.0.1:8088` and **not** `0.0.0.0`; the same three after a real host reboot. Any gap found is fixed in `install-host.sh` or the unit and re-proven by a full `disable --now` → re-run installer → `enable --now` cycle — never by a hand-run command. |
| D-08 | `specs/014` §9 correction scope (Decision 4, resolved) | Minimal one-line fix vs full audit | **Full audit** — see §"specs/014 §9 audit" below. The stale line is a symptom; the checklist has been read as a phase-1 snapshot while three backends changed state under it. |

## Data Flow

    memory-router pod (mcps, on trantor)                    trantor host
      KNOWLEDGE_VAULT_TOKEN ─┐ (Secret knowledge-vault-search-token)
      KNOWLEDGE_VAULT_AUTH_MODE=bearer
      base_url default (no override, D-06)
        └─► POST http://knowledge-vault-search.mcps.svc.cluster.local:8088/search
              │  headless Service (no selector) ─► EndpointSlice ─► 10.42.0.1:8088
              │                                                        │  cni0 gw, node-local (D-01)
              │  Authorization: Bearer <token> ──── compare_digest ────┤
              ▼                                                        ▼
                                              knowledge-vault-search.service (systemd)
                                                LoadCredential=search-token
                                                  └─ /etc/knowledge-vault/search-token  ← SOURCE OF TRUTH (D-02)
                                                SingleFlightSearcher(timeout=5s, D-04/D-05)
                                                  └─ read-only: /opt/knowledge-vault/tree/knowledge
                                                                /var/lib/knowledge-vault/index/index.json

    Bootstrap (03-create-secrets.sh, block 7):
      sudo cat /etc/knowledge-vault/search-token ──(mirror, never regenerate, D-02)──►
        Secret mcps/knowledge-vault-search-token[search-token]

## File Changes

| File | Action | Description |
|---|---|---|
| `kubernetes/mcps/bootstrap/03-create-secrets.sh` | Modify | Block 7 (D-02/F-3): read `$KV_SEARCH_TOKEN_FILE` (default `/etc/knowledge-vault/search-token`), abort loudly if unreadable/empty, `create secret generic knowledge-vault-search-token --from-literal=search-token=...`; header + closing `log` 6 → 7. |
| `kubernetes/mcps/bootstrap/05-deploy-manifests.sh` | Modify | Append `knowledge-vault-search-endpoints.yaml` to the ordered apply list, **before** the memory-router apply (loudness ordering). |
| `kubernetes/mcps/knowledge-vault-search-endpoints.yaml` | Modify (comment only) | Replace the `NOT YET APPLIED` header (F-4) with the applied/node-locality note; object bodies byte-identical. |
| `kubernetes/mcps/memory-router-deployment.yaml` | Modify | `+KNOWLEDGE_VAULT_TOKEN` (secretKeyRef `knowledge-vault-search-token`/`search-token`), `+KNOWLEDGE_VAULT_AUTH_MODE: "bearer"`, mirroring lines 94-100 (F-5). No base-URL override (D-06). |
| `kubernetes/mcps/bootstrap/README.md` | Modify | Secret count 6 → 7; manifest list; drop knowledge-vault from "not validated against a deployed instance". |
| `hermes-native/knowledge-vault/scripts/install-host.sh` | Conditional | Only if D-07's real enable exposes a missing prerequisite. |
| `hermes-native/knowledge-vault/systemd/knowledge-vault-search.service` | Conditional | Only if D-04 raises the deadline (D-05) or D-07 exposes a unit gap. |
| `tests/test_knowledge_vault_search_manifest.py` | Create | `unittest` + `yaml.safe_load_all`, following `tests/test_hindsight_manifest.py` (F-5). |
| `specs/024_knowledge_vault_search_deployment.md` | Create | Deployed contract, accepted single-node constraint (D-01), rotation runbook (D-03), measured rebuild numbers (D-04), D-07 evidence block. |
| `specs/018_knowledge_vault_backend.md` | Modify | §8 line 305 `[ ]` → `[x]` pointing at `specs/024`; §4/§7 where the deadline question resolves. |
| `specs/014_memory_router.md` | Modify | §9 audit (D-08). |
| `docs/services/knowledge-vault.md` | Modify | Search unit documented as enabled/operational + rotation runbook. |
| `hermes-native/memory-router/**`, `hermes-native/knowledge-vault/src/**` | **Unchanged** | Stated explicitly: zero code changes, no image rebuild. |

## Interfaces / Contracts

```bash
# 03-create-secrets.sh — block 7 (D-02). Mirror only; this script never
# generates the token. install-host.sh (F-1) is its single writer.
: "${KV_SEARCH_TOKEN_FILE:=/etc/knowledge-vault/search-token}"
KV_SEARCH_TOKEN=$(cat "$KV_SEARCH_TOKEN_FILE" 2>/dev/null || true)
if [ -z "$KV_SEARCH_TOKEN" ]; then
  log "No token at $KV_SEARCH_TOKEN_FILE (run install-host.sh on trantor first) — aborting"
  exit 1
fi
kubectl -n "$MR_NAMESPACE" create secret generic knowledge-vault-search-token \
  --from-literal=search-token="$KV_SEARCH_TOKEN" \
  --dry-run=client -o yaml | apply_secret
```

The file is `0440 root:knowledge-vault`, so the caller must be `root` or in `knowledge-vault` — an unreadable file is an abort, never an empty Secret.

```yaml
# memory-router-deployment.yaml — added env (D-06)
- name: KNOWLEDGE_VAULT_TOKEN
  valueFrom: {secretKeyRef: {name: knowledge-vault-search-token, key: search-token}}
- {name: KNOWLEDGE_VAULT_AUTH_MODE, value: "bearer"}
```

## Testing Strategy

| Layer | What to test | Approach |
|---|---|---|
| Unit — manifest (enforced, RED first) | Service is `clusterIP: None`, has **no** `spec.selector`, port/targetPort `8088`; EndpointSlice `addressType: IPv4`, address exactly `10.42.0.1`, `conditions.ready: true`, `kubernetes.io/service-name` matches the Service name; **no `kind: Ingress`** in the file | `tests/test_knowledge_vault_search_manifest.py`, following `tests/test_hindsight_manifest.py` |
| Unit — cross-manifest (enforced) | `memory-router-deployment.yaml` has `KNOWLEDGE_VAULT_AUTH_MODE == "bearer"`, a `KNOWLEDGE_VAULT_TOKEN` secretKeyRef of exactly `{knowledge-vault-search-token, search-token}`, **no inline `value:`** on it, and **no** `KNOWLEDGE_VAULT_BASE_URL` (D-06) | Same file; the D-01 address assertion is what makes a CNI-driven change deliberate rather than accidental |
| Measurement (gating, D-04) | Cold and warm `build_index` against the real `/opt/knowledge-vault/tree` | Manual on `trantor`; numbers recorded in `specs/024` before the enable task runs |
| Integration (manual, D-07) | `is-enabled`/`is-active`/`NRestarts`/journal/`ss` evidence, pre- and post-reboot; in-pod `curl 10.42.0.1:8088/healthz` → `200 {"status":"ok"}`; same call from the LAN fails to connect; unauthenticated `POST /search` → `401`, bearer → `200` with real hits | Evidence pasted into `specs/024`, not ticked |
| E2E (manual) | memory-router `/global` search returns ≥1 hit with `backend == "knowledge-vault"` **alongside** Engram hits; merged output inspected for ordering/noise (proposal risk 5) | Live cluster |

## Threat Matrix

| Boundary | Applicability | Design response | Planned test |
|---|---|---|---|
| Shell / subprocess | **Applicable** — new `cat` + `kubectl create` block in `03-create-secrets.sh` | Fixed literal namespace/secret/key names; no caller-supplied input reaches a command line; `set -euo pipefail` already in force; every expansion quoted; empty/unreadable token aborts non-zero before any apply | Manual: run with the token file absent → exits non-zero **without** creating an empty Secret |
| Secret handling | Applicable | Token exists as a host file (`0440 root:knowledge-vault`), a `LoadCredential` credential, and a k8s Secret referenced only by `secretKeyRef`. Never an env var on the host, never a literal in a manifest, never logged | Manifest test asserts no inline `value:` under `KNOWLEDGE_VAULT_TOKEN` |
| Auth / unauthenticated access | Applicable | Bearer required from day one (`hmac.compare_digest`, `serve.py:96`); empty token ⇒ every request `401`, no permissive fallback | Manual: in-pod `curl` without the header → `401` |
| Network boundary / egress | Applicable | Listener bound to `10.42.0.1` only, node-local; headless Service, no Ingress, no LAN path; no NetworkPolicy (explicit no-op, same posture as spec 022) | `ss -lntp` evidence (D-07) + LAN `curl` must fail to connect |
| Host filesystem | Applicable | Unit is read-only over vault and index (`ReadOnlyPaths=`, no `ReadWritePaths=`); `ProtectSystem=strict`, `NoNewPrivileges` | Existing unit config; journal evidence (D-07) |
| Availability / DoS | Applicable | `SingleFlightSearcher` coalesces identical in-flight queries; bounded deadline (D-04) turns a slow rebuild into a `503` the adapter degrades over, not a hang | Measurement (D-04) is the sizing evidence |
| Routing / VCS or PR automation / executable-file classification | N/A | No routing change, no VCS automation, no executable classification | None |

## Migration / Rollout

No data migration; this is a read-only path over a corpus that already exists. Ordered, and the order is the failure-loudness design:

1. **Measure** (D-04) — `build_index` cold/warm on `trantor`, numbers into `specs/024`. Gates step 2.
2. **Host** — `systemctl enable --now knowledge-vault-search.service`, then capture D-07's full evidence set, including the reboot cycle.
3. **Cluster** — `kubectl apply -f knowledge-vault-search-endpoints.yaml`; prove `curl` from an `mcps` pod **before** the router knows the token, so a later router `401` can only mean token mismatch and never "nothing is listening".
4. **Secret** — `03-create-secrets.sh` (block 7).
5. **Router** — `kubectl apply -f memory-router-deployment.yaml` + `rollout restart`. Manifest-only, no image rebuild (`specs/022` §8.1 Bug 2's standing lesson).

### Rollback (formalized from the proposal's three-part plan)

| # | Half | Command | Resulting state | Independent of |
|---|---|---|---|---|
| R1 | Router | Remove both env vars → `kubectl apply` + `rollout restart deploy/memory-router` | Adapter falls back to `auth_mode="none"`, bridge answers `401`, `/global` degrades to Engram-only — today's exact behavior | R2, R3 |
| R2 | Cluster | `kubectl delete -f kubernetes/mcps/knowledge-vault-search-endpoints.yaml` (and `kubectl -n mcps delete secret knowledge-vault-search-token`) | Service name stops resolving; adapter raises `BackendUnavailableError`, dispatcher degrades | R1, R3 |
| R3 | Host | `systemctl disable --now knowledge-vault-search.service` | Nothing listens on `10.42.0.1:8088`. Vault, index, and the propose/review/promote pipeline untouched — this unit only ever reads | R1, R2 |

Any subset, in any order, is safe: each lands on a strictly less-available version of the same read-only path, and the floor is the pre-change baseline. Reverting the commit removes manifest wiring, script changes, tests, and specs together. **No writes to undo, no data to migrate, no image to roll back.**

## specs/014 §9 audit (D-08)

`specs/014` line 275 is not just stale, it is stale in three separate ways: it treats six backends as one unit, it names **`Obsidian`** for a backend whose real module and adapter are `knowledge-vault` (`specs/018` §10), and it says "fuera de alcance de esta fase" for two backends that are now deployed. Replace the single line with per-backend lines reflecting real deployment state, and re-verify the rest of §9 (lines 263-274) against the repo — line 272's "6 archivos" manifest count in particular, which predates the Hindsight and knowledge-vault manifests.

| # | Backend | Real state today | §9 line after audit |
|---|---|---|---|
| 1 | Engram | Adapter + deployed (line 269, already true) | unchanged |
| 2 | Hindsight | Adapter + **deployed** (`specs/022`) | `[x]` … desplegado y en vivo, ver `specs/022` |
| 3 | Graphiti | Adapter only, no deployment | `[ ]` … adaptador completo, despliegue pendiente |
| 4 | Honcho | Adapter only, no deployment | `[ ]` … adaptador completo, despliegue pendiente |
| 5 | Cognee | Adapter only, no deployment | `[ ]` … adaptador completo, despliegue pendiente |
| 6 | knowledge-vault (ex-"Obsidian") | Adapter + **deployed by this change** | `[x]` … desplegado y en vivo, ver `specs/024`; nombre real `knowledge-vault` |

## Open Questions

- [ ] **D-04's measurement is unknown until it runs.** If cold p100 exceeds 1.5s the deadline changes per D-05; if it exceeds ~5s outright, that is a design finding (index build is O(vault) on every cold start) that comes back here, not a silent bump.
- [ ] **D-07 may find a prerequisite gap.** Fixed in `install-host.sh`, then re-proven by a full disable/reinstall/enable cycle. A gap requiring a unit change makes `knowledge-vault-search.service` a modified file rather than an unchanged one.
- [ ] Whether the memory-router pod's `automountServiceAccountToken`/egress posture needs anything for a **node-local IP** target — expected no (plain pod egress to the node), unverified until step 3.
- [ ] Cross-backend score comparability and merged-`limit` semantics under real `/global` coexistence (`specs/018` §7) stay unowned; step 5's E2E only *looks* at merged quality to decide whether it needs owning next.
