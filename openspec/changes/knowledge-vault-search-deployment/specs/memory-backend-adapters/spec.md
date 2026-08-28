# Delta for Memory Backend Adapters

## MODIFIED Requirements

### Requirement: Knowledge-Vault Adapter

The system MUST ship a `KnowledgeVaultBackend` adapter satisfying
`SearchOnlyBackend` that reaches the knowledge-vault search bridge over HTTP
transport, config-driven auth via environment variables (bearer token),
mirroring the Honcho/Cognee adapters' shape.

`KnowledgeVaultBackend.capabilities().verbs` MUST equal exactly
`frozenset({"search"})`. `KnowledgeVaultBackend.capabilities().namespaces`
MUST equal exactly `("/global",)`. The adapter MUST NOT implement or declare
`store` or `reflect`.

The deployed memory-router instance MUST be configured with
`KNOWLEDGE_VAULT_TOKEN` (sourced from the `knowledge-vault-search-token`
Secret) and `KNOWLEDGE_VAULT_AUTH_MODE=bearer`, reaching the real
`knowledge-vault-search.mcps.svc.cluster.local:8088` Service — not an
unreachable default with `auth_mode="none"`. No `KNOWLEDGE_VAULT_BASE_URL`
override is set; the adapter's existing default already names this Service.

(Previously: the adapter was validated end-to-end only by hand, against a
foreground `serve.py` process with a hand-faked credential directory and
`auth_mode="none"`; no live Service existed for the default `base_url` to
resolve against, and memory-router carried no `KNOWLEDGE_VAULT_TOKEN`/
`KNOWLEDGE_VAULT_AUTH_MODE` configuration.)

#### Scenario: Knowledge-vault adapter declares search-only verbs

- GIVEN `KnowledgeVaultBackend().capabilities()` is inspected
- WHEN `verbs` is read
- THEN it equals `frozenset({"search"})` exactly, and `"store" not in verbs`
  and `"reflect" not in verbs` both hold

#### Scenario: Knowledge-vault adapter declares only /global

- GIVEN `KnowledgeVaultBackend().capabilities()` is inspected
- WHEN `namespaces` is read
- THEN it equals `("/global",)` exactly, so `search` requests on
  `/projects/*`, `/agents/*`, or `/user/master` select no knowledge-vault
  backend

#### Scenario: Knowledge-vault adapter reaches the search bridge over HTTP

- GIVEN a `search` request is routed to the knowledge-vault adapter for
  `/global`
- WHEN the adapter performs the query
- THEN it communicates with the knowledge-vault search bridge via an
  injectable HTTP transport, sending a bearer-token `Authorization` header
  sourced from environment configuration, and returns a `SearchResult`

#### Scenario: Deployed instance requires bearer auth against the real Service

- GIVEN the memory-router Deployment configured with `KNOWLEDGE_VAULT_TOKEN`
  and `KNOWLEDGE_VAULT_AUTH_MODE=bearer` against the live
  `knowledge-vault-search.mcps.svc.cluster.local:8088` Service
- WHEN a `search` request on `/global` is issued
- THEN the adapter sends `Authorization: Bearer <token>`, and the deployed
  bridge accepts it and returns real hits; an unauthenticated request to the
  same bridge is rejected `401`
