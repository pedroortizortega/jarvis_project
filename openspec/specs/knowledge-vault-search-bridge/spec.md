# Knowledge Vault Search Bridge Specification

## Purpose

Define the read-only HTTP surface on `trantor` that exposes `search_vault()` to memory-router, so curated knowledge-vault content becomes reachable over the network without granting any write access to the vault.

## Requirements

### Requirement: Search Scope Is Allowlisted to `knowledge/` by Construction

The search bridge MUST index and search only the `knowledge/` root of the
vault. This MUST be an allowlist (the service is only ever given the
`knowledge/` root as its scan/index root), not a denylist that excludes
`pending/` or other folders by name. A note in `pending/` or in any future
third folder added to the vault MUST NOT be searchable and MUST NOT affect
the index revision, with no code change required to exclude a new folder.

#### Scenario: A note in pending/ is never returned by search

- GIVEN a note exists only at `pending/<id>.md`
- WHEN `POST /search` is called with a query that matches its content
- THEN the note is never returned as a hit

#### Scenario: A note in pending/ never changes the index revision

- GIVEN the index revision is computed before and after a note is added to
  `pending/`
- WHEN the two revisions are compared
- THEN they are identical — writing to `pending/` does not trigger a rebuild

#### Scenario: An arbitrary third folder is invisible without a code change

- GIVEN a new folder (e.g. `drafts/`) is added to the vault working tree
  alongside `pending/` and `knowledge/`, containing a note that would match
  a search query
- WHEN `POST /search` is called with that query, with no code change made to
  the search bridge
- THEN the note in the third folder is never returned as a hit

### Requirement: Search-Only HTTP Surface

The service MUST expose exactly two routes: `POST /search` and `GET /healthz`. No route MUST allow creating, modifying, or deleting vault content; the service MUST NOT expose any write verb reachable by any method or path.

#### Scenario: Only the declared routes are routable

- GIVEN the service's route table is inspected
- WHEN every registered method+path pair is enumerated
- THEN only `POST /search` and `GET /healthz` exist, and no path accepts a request that would mutate the vault

#### Scenario: Mutating request is not routable

- GIVEN a client sends a `POST`, `PUT`, `PATCH`, or `DELETE` request to any path other than `/search`
- WHEN the service receives it
- THEN it returns a routing/method-not-found error and no vault file is written

### Requirement: Bearer-Token Authentication

`POST /search` MUST require a valid `Authorization: Bearer <token>` header, sourced from a systemd credential. `GET /healthz` MAY be reachable without authentication for liveness probing.

#### Scenario: Unauthenticated search request rejected

- GIVEN a client sends `POST /search` with no `Authorization` header or an invalid token
- WHEN the service processes the request
- THEN it rejects the request with an explicit authentication error and does not perform a search

#### Scenario: Authenticated search request proceeds

- GIVEN a client sends `POST /search` with a valid bearer token matching the configured credential
- WHEN the service processes the request
- THEN it performs the search and returns results

### Requirement: Local-Interface Binding

The service MUST bind to a local-only network interface (not a public or all-interfaces bind), consistent with the local-reachable in-cluster→host access pattern.

#### Scenario: Service unreachable from outside the host

- GIVEN the service is running with its configured bind address
- WHEN a request originates from outside the host's local network path
- THEN the request does not reach the service

### Requirement: Read-Only Vault and Index Mount

The service's systemd unit MUST declare `ReadOnlyPaths=` covering the
`knowledge/` root and the index path, and MUST NOT declare `ReadWritePaths=`
for either. `ReadOnlyPaths=`/`InaccessiblePaths=` MUST NOT grant read access
to `pending/`. The promote actor remains the sole writer of `knowledge/`; the
search bridge remains read-only.

(Previously: `ReadOnlyPaths=` covered the whole vault directory, since search
scanned the vault root; now it covers only `knowledge/`, and `pending/` MUST
be inaccessible to the search bridge's unit.)

#### Scenario: Unit file grants no write access

- GIVEN the systemd unit file for the search service is inspected
- WHEN its `ReadOnlyPaths=` and `ReadWritePaths=` directives are read
- THEN `knowledge/` and the index path appear only under `ReadOnlyPaths=`,
  and neither appears under any `ReadWritePaths=` directive

#### Scenario: The unit cannot read pending/

- GIVEN the systemd unit file for the search service is inspected
- WHEN its path directives are read
- THEN `pending/` is not covered by `ReadOnlyPaths=` and is either absent or
  covered by `InaccessiblePaths=`

### Requirement: Bounded Inline Index Rebuild

When `search_vault()` detects a stale index and rebuilds it inline, the service MUST bound that rebuild with a request timeout. If the rebuild does not complete within the timeout, the service MUST return an explicit unavailable response rather than blocking indefinitely.

#### Scenario: Rebuild completes within timeout

- GIVEN the index is stale and the rebuild finishes before the configured timeout
- WHEN `POST /search` is handled
- THEN the service returns fresh search results from the rebuilt index

#### Scenario: Rebuild exceeds timeout

- GIVEN the index is stale and the rebuild does not finish before the configured timeout
- WHEN `POST /search` is handled
- THEN the service returns an explicit unavailable/error response instead of hanging, and does not fabricate results

### Requirement: Search Response Shape

`POST /search` MUST return, for each hit, the note id, title, excerpt, and
score, sourced only from notes under `knowledge/`. `GET /healthz` MUST return
a liveness/availability indicator.

(Previously: hits were sourced from the flat vault root; now every hit is
guaranteed to originate from `knowledge/` because that is the only root the
service ever scans.)

#### Scenario: Search returns note, title, excerpt, and score per hit

- GIVEN a query matches one or more notes under `knowledge/` above the
  relevance threshold
- WHEN `POST /search` returns a 200 response
- THEN each item in the response includes `note`, `title`, `excerpt`, and a
  numeric `score`

#### Scenario: Empty vault or unavailable index yields zero hits

- GIVEN `knowledge/` has no notes, or the index remains unavailable after a
  bounded rebuild attempt
- WHEN `POST /search` is called
- THEN the service returns an empty hit list, never a fabricated hit
