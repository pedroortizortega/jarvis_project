# Delta for Knowledge Vault Search Bridge

## ADDED Requirements

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

## MODIFIED Requirements

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
