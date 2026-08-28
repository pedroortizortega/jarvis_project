# Knowledge Vault Search Service Specification

## Purpose

Define the *deployed* contract for the knowledge-vault search bridge: a
persistent host unit on `trantor`, in-cluster reachability through a
selector-less Service/EndpointSlice, credential provisioning and rotation,
the accepted node-locality boundary, and a live-validated `/global` fan-out
alongside Engram. Distinct from `knowledge-vault-search-bridge` (the
process's own request/response/auth behavior), which this spec does not
redefine.

## Requirements

### Requirement: Persistent Host Unit, Reboot-Survivable

`knowledge-vault-search.service` MUST be enabled and active on `trantor`
(`systemctl enable --now`) and MUST remain active after a host reboot, not
only after a manual start.

#### Scenario: Unit is enabled and active

- GIVEN `trantor` after this change is applied
- WHEN `systemctl is-enabled knowledge-vault-search.service` and `systemctl
  is-active knowledge-vault-search.service` are run
- THEN both report `enabled` and `active`

#### Scenario: Unit survives host reboot

- GIVEN the unit is enabled
- WHEN `trantor` reboots
- THEN the unit is `active` again without manual intervention

### Requirement: Installer-Provisioned Credential Only

The unit MUST start using the credential written by `install-host.sh` to
`/etc/knowledge-vault/search-token`, sourced via systemd `LoadCredential=`.
No hand-generated token or manually faked `CREDENTIALS_DIRECTORY` MUST be
required or used for the unit to start successfully.

#### Scenario: Unit starts from the installer-provisioned token

- GIVEN `install-host.sh` has run and written the token file
- WHEN the unit starts via `systemctl start`
- THEN it authenticates requests using that token, with no manual
  credential step performed

### Requirement: Headless Service and Fixed EndpointSlice

`kubernetes/mcps/knowledge-vault-search-endpoints.yaml` MUST be applied,
defining a selector-less headless `Service` and a manually-managed
`EndpointSlice` pointing at `10.42.0.1:8088` with `ready: true`.

#### Scenario: In-cluster DNS resolves and answers

- GIVEN the manifest is applied
- WHEN a pod in `mcps` resolves
  `knowledge-vault-search.mcps.svc.cluster.local:8088`
- THEN it resolves and a request reaches the host bridge

#### Scenario: Bridge unreachable from the LAN

- GIVEN the Service is `10.42.0.1`-only
- WHEN a request to `10.42.0.1:8088` originates from the LAN, outside the
  cluster's pod network
- THEN it fails to connect

### Requirement: Node-Locality Coupling Is an Accepted, Documented Constraint

The `10.42.0.1` binding couples the bridge to the current single-node
flannel CNI. This coupling MUST be recorded as an explicit, accepted
constraint, not silently assumed. A manifest test MUST assert the
EndpointSlice address so a change to it is never accidental.

#### Scenario: EndpointSlice address is asserted by a test

- GIVEN the manifest test suite
- WHEN the knowledge-vault-search-endpoints manifest is parsed
- THEN a test fails if the EndpointSlice address is anything other than
  `10.42.0.1`

### Requirement: Secret Mirrors the Host Token, Never Regenerates

`bootstrap/03-create-secrets.sh` MUST create the `mcps` Secret
(`knowledge-vault-search-token`) by reading `/etc/knowledge-vault/search-token`
on the host. The script MUST NOT generate a new token.

#### Scenario: Secret content matches the host file byte-for-byte

- GIVEN the host token file exists
- WHEN `03-create-secrets.sh` runs
- THEN the resulting Secret's token value equals the host file's content,
  and the script performs no token generation

### Requirement: Token Rotation Is a Documented Runbook, Not Automated

Rotating the token MUST follow a documented, ordered four-step runbook
(regenerate on host → re-run the secret script → roll memory-router →
verify a live hit), not an automated process.

#### Scenario: Rotation runbook exists and is ordered

- GIVEN `docs/services/knowledge-vault.md`
- WHEN the rotation section is read
- THEN it lists exactly the four ordered steps, and no automation performs
  rotation on its own

### Requirement: Rebuild Timeout Measured Against the Real Vault

Before the unit is enabled persistently, `build_index`'s wall-clock time
against the real `/opt/knowledge-vault/tree` MUST be measured and recorded
in this spec. `serve.py`'s inline-rebuild timeout MUST only be raised above
`5s` if this measurement justifies it.

#### Scenario: Measurement gates enabling

- GIVEN the real vault at `/opt/knowledge-vault/tree`
- WHEN `build_index` is timed against it before the unit is enabled
- THEN the measured duration is recorded, and the timeout constant changes
  only if that number exceeds the current deadline

### Requirement: Host-Before-Router Deployment Ordering

The host unit MUST be verified reachable from an in-cluster pod (`curl
.../healthz` returns `200`) before memory-router is configured with the
bearer token. This ordering MUST be followed so that a router-side `401`
can only mean token mismatch, never "nothing is listening".

#### Scenario: curl proof precedes router wiring

- GIVEN the deployment sequence for this change
- WHEN memory-router's Secret/env are applied
- THEN an in-cluster `curl` proof against the bridge already succeeded
  beforehand

### Requirement: Live /global Fan-Out Includes Knowledge-Vault

Once deployed, a `search` on `/global` MUST return at least one hit with
`backend == "knowledge-vault"` alongside Engram hits — merged, not
replacing — against the real cluster, not a mock.

#### Scenario: Merged live hit confirms the deployed round trip

- GIVEN the host unit, cluster manifest, and router wiring are all applied
- WHEN a client issues `search` on `/global` with a query matching curated
  vault content
- THEN the response includes a hit with `backend == "knowledge-vault"` and
  at least one Engram-backed hit in the same response
