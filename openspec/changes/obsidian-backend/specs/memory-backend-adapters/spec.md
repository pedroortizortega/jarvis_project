# Delta for Memory Backend Adapters

## ADDED Requirements

### Requirement: Knowledge-Vault Adapter

The system MUST ship a `KnowledgeVaultBackend` adapter satisfying `SearchOnlyBackend` that reaches the knowledge-vault search bridge over HTTP transport, config-driven auth via environment variables (bearer token), mirroring the Honcho/Cognee adapters' shape.

`KnowledgeVaultBackend.capabilities().verbs` MUST equal exactly `frozenset({"search"})`. `KnowledgeVaultBackend.capabilities().namespaces` MUST equal exactly `("/global",)`. The adapter MUST NOT implement or declare `store` or `reflect`.

#### Scenario: Knowledge-vault adapter declares search-only verbs

- GIVEN `KnowledgeVaultBackend().capabilities()` is inspected
- WHEN `verbs` is read
- THEN it equals `frozenset({"search"})` exactly, and `"store" not in verbs` and `"reflect" not in verbs` both hold

#### Scenario: Knowledge-vault adapter declares only /global

- GIVEN `KnowledgeVaultBackend().capabilities()` is inspected
- WHEN `namespaces` is read
- THEN it equals `("/global",)` exactly, so `search` requests on `/projects/*`, `/agents/*`, or `/user/master` select no knowledge-vault backend

#### Scenario: Knowledge-vault adapter reaches the search bridge over HTTP

- GIVEN a `search` request is routed to the knowledge-vault adapter for `/global`
- WHEN the adapter performs the query
- THEN it communicates with the knowledge-vault search bridge via an injectable HTTP transport, sending a bearer-token `Authorization` header sourced from environment configuration, and returns a `SearchResult`

### Requirement: Knowledge-Vault Transport Failure Integrates With Degraded-Backend Handling

When the knowledge-vault adapter's HTTP transport fails (connection error, non-success status, or response decode failure), the adapter MUST raise `BackendUnavailableError("knowledge-vault", ...)`. This MUST integrate, unchanged, with the existing dispatcher degraded-backend behavior: a `search` request returns partial results with an explicit "unavailable" marker for the knowledge-vault backend, and the overall request does not fail outright.

#### Scenario: Knowledge-vault HTTP failure raises BackendUnavailableError

- GIVEN the knowledge-vault search bridge is unreachable, returns a non-success status, or returns an undecodable response
- WHEN the adapter attempts `search`
- THEN it raises `BackendUnavailableError("knowledge-vault", reason)` and does not raise any other exception type

#### Scenario: Partial search results when knowledge-vault is down

- GIVEN a `/global` search where the knowledge-vault backend is unavailable while the Engram backend is healthy
- WHEN the router processes the search
- THEN it returns results from Engram along with an explicit marker identifying knowledge-vault as unavailable, and the request does not fail outright

### Requirement: Knowledge-Vault Empty or Unavailable Index Never Fabricates Hits

When the vault has no notes, or the search bridge's index remains unavailable after its bounded rebuild attempt, the adapter MUST return zero hits. The adapter MUST NOT synthesize, guess, or otherwise fabricate a `SearchHit`.

#### Scenario: Empty vault yields zero hits, not an error

- GIVEN the knowledge-vault search bridge reports zero matching notes
- WHEN the adapter performs `search`
- THEN it returns a `SearchResult` with an empty `hits` tuple and no fabricated content

### Requirement: Knowledge-Vault Hits Are Attributed and Score-Bearing

Each `SearchHit` returned by the knowledge-vault adapter MUST carry `backend == "knowledge-vault"` so a caller can distinguish curated knowledge from session memory. `SearchHit.content` MUST be the note excerpt with the note id and title preserved (embedded in content or via an accompanying field), and `SearchHit.score` MUST be the score surfaced by the search bridge rather than a hardcoded `0.0`.

#### Scenario: Hit carries knowledge-vault attribution

- GIVEN a `/global` search returns a hit sourced from the knowledge-vault adapter
- WHEN the hit is inspected
- THEN `backend == "knowledge-vault"`, `content` includes the excerpt with the note id and title preserved, and `score` reflects the bridge's reported relevance score rather than `0.0`

## MODIFIED Requirements

### Requirement: Degraded Backend — Search Returns Partial Results

When one or more backends required for a `search` are unavailable, the router MUST return available results from healthy backends plus an explicit per-backend "unavailable" marker. The router MUST NOT fail the entire search solely because one backend is down. This applies uniformly whether the unavailable backend is Engram, Hindsight, or knowledge-vault.

(Previously: scoped only to Engram/Hindsight-style backends; now explicitly generalized to include search-only adapters like knowledge-vault.)

#### Scenario: Partial search results with unavailable marker

- GIVEN a search spans a namespace whose backend is unavailable while another in-scope namespace's backend is healthy
- WHEN the router processes the search
- THEN it returns results from the healthy backend along with an explicit marker identifying the unavailable backend, and the request does not fail outright

#### Scenario: /global search fans out to both Engram and knowledge-vault

- GIVEN both the Engram adapter and the `KnowledgeVaultBackend` adapter are registered and healthy
- WHEN `Registry.backends_for(verb="search", namespace="/global")` is evaluated and the router dispatches the search
- THEN both adapters are queried, their hits are merged into a single `SearchResult`, and existing Engram-only `/global` search tests continue to pass unmodified
