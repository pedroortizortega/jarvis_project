# Delta for Memory Backend Adapters

## MODIFIED Requirements

### Requirement: Hindsight Adapter

The system MUST ship a `HindsightBackend` adapter implementing the `MemoryBackend` Protocol (`capabilities()`, `health()`, `store()`, `search()`) that reaches Hindsight over HTTP transport, not the stdio-subprocess transport used by the Engram adapter. When no explicit `base_url` and no `HINDSIGHT_BASE_URL` environment override are provided, the adapter's default `base_url` MUST resolve to `http://hindsight.mcps.svc.cluster.local:8888`, matching Hindsight's real `HINDSIGHT_API_PORT` default. The deployed cluster instance is configured for bearer auth (`HINDSIGHT_AUTH_MODE=bearer` with `HINDSIGHT_TOKEN` set), not the unauthenticated default.

(Previously: default `base_url` resolved to port `8080`, which did not match any real Hindsight instance since none was deployed; no deployed auth configuration existed to reference.)

#### Scenario: Hindsight adapter handles store and search over HTTP

- GIVEN a request is routed to the Hindsight adapter
- WHEN the adapter performs store or search
- THEN it communicates with Hindsight via an HTTP client (not a subprocess) and returns results/status through the adapter contract

#### Scenario: Default base_url resolves to port 8888

- GIVEN `HindsightBackend()` is constructed with no explicit `base_url` argument and no `HINDSIGHT_BASE_URL` environment variable set
- WHEN the adapter's resolved `base_url` is inspected
- THEN it equals `http://hindsight.mcps.svc.cluster.local:8888`

#### Scenario: Explicit override still wins over the default

- GIVEN the `HINDSIGHT_BASE_URL` environment variable is set to a custom value
- WHEN `HindsightBackend()` is constructed with no explicit `base_url` argument
- THEN the adapter's resolved `base_url` equals the environment override, not the port-8888 default

#### Scenario: Deployed instance requires bearer auth

- GIVEN the memory-router Deployment configured against the in-cluster Hindsight instance
- WHEN a store or search request is issued
- THEN the adapter sends `Authorization: Bearer <token>` sourced from `HINDSIGHT_TOKEN`, and a request without a valid token is rejected by the deployed Hindsight instance
