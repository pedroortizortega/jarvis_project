# Local Embeddings Specification

## Purpose

Define the OpenAI-compatible `/v1/embeddings` contract for the cluster-internal, CPU-only, zero-egress `local-embeddings` service in namespace `llms`: pinned model/dimension guarantee, request/response shape, batching limits, error semantics, and network/runtime invariants.

**Corrected 2026-08-22**: the pinned model is `intfloat/multilingual-e5-large` (1024 dims), not `multilingual-e5-small` (384 dims) as originally specified — `-small` does not exist in `fastembed`, only discovered when actually building the image. See `specs/021_local_embeddings_service.md`'s correction note for the full story and the revised resource sizing.

## Requirements

### Requirement: OpenAI-Compatible Embeddings Endpoint

The service MUST expose `POST /v1/embeddings` accepting `input` as either a single string or a list of strings, and MUST return an OpenAI-shaped response: `object: "list"`, one `data[]` entry per input preserving order via `index`, each entry's `embedding` as a float vector, plus populated `model` and `usage` fields.

#### Scenario: Single string input

- GIVEN a request body `{"input": "hola mundo", "model": "text-embedding-3-small"}`
- WHEN `POST /v1/embeddings` is called
- THEN the response has exactly one `data[0]` entry with `index: 0` and a 1024-length `embedding`

#### Scenario: List input preserves order

- GIVEN a request with `input` as a list of 5 strings
- WHEN `POST /v1/embeddings` is called
- THEN the response `data` array has 5 entries whose `index` values are `0..4` in the same order as the input list

### Requirement: Opt-In Query/Passage Prefixing

The service MUST accept an optional request field `input_type` with allowed values `"query"` or `"passage"`. When omitted, the service MUST embed the input verbatim (no prefix). When present, the service MUST prepend the matching `intfloat/multilingual-e5-large` convention string (`"query: "` for `"query"`, `"passage: "` for `"passage"`) to each input before embedding. This MUST be additive on the wire: a client that never sends `input_type` (any stock OpenAI-SDK client) MUST observe no behavior change and MUST continue to receive verbatim embeddings.

#### Scenario: Omitted input_type embeds verbatim

- GIVEN a request body `{"input": "hola mundo"}` with no `input_type` field
- WHEN `POST /v1/embeddings` is called
- THEN the embedded text is exactly `"hola mundo"`, with no prefix added

#### Scenario: input_type "query" prepends the query prefix

- GIVEN a request body `{"input": "hola mundo", "input_type": "query"}`
- WHEN `POST /v1/embeddings` is called
- THEN the text embedded is `"query: hola mundo"`

#### Scenario: input_type "passage" prepends the passage prefix

- GIVEN a request body `{"input": "hola mundo", "input_type": "passage"}`
- WHEN `POST /v1/embeddings` is called
- THEN the text embedded is `"passage: hola mundo"`

#### Scenario: Unknown input_type value is rejected, never silently ignored

- GIVEN a request body `{"input": "hola mundo", "input_type": "doc"}`
- WHEN `POST /v1/embeddings` is called
- THEN the service returns a 4xx error naming the invalid value, and does not fall back to embedding verbatim or guessing an intent

### Requirement: Pinned Model and Dimension Guarantee

The service MUST always serve `intfloat/multilingual-e5-large` producing 1024-dimensional vectors, baked into the container image at build time. The service MUST NOT pad, truncate, or otherwise resize a vector to match a dimension other than 1024, even if a client requests one.

#### Scenario: Every response uses the pinned dimension

- GIVEN any valid embeddings request
- WHEN the response is inspected
- THEN every `embedding` array has exactly 1024 elements

#### Scenario: A client-requested different dimension is rejected, never faked

- GIVEN a request includes `"dimensions": 1536`
- WHEN `POST /v1/embeddings` is called
- THEN the service returns a 4xx error and does not return a padded or truncated vector

### Requirement: Model-Name Compatibility

The service MUST accept any string in the request's `model` field without validating it against a known list, and MUST echo the client-requested string back in the response's `model` field while always performing inference with the pinned model.

#### Scenario: Unknown model name is accepted and echoed

- GIVEN a request with `"model": "text-embedding-3-small"`
- WHEN `POST /v1/embeddings` is called
- THEN the request succeeds using the pinned model, and the response `model` field is `"text-embedding-3-small"`

### Requirement: Batch Ceiling

The service MUST accept at most 256 items in `input` per request. Above that ceiling, it MUST reject the request with a clear 4xx error and MUST NOT silently truncate the batch or process only a subset.

#### Scenario: Batch at the ceiling succeeds

- GIVEN `input` is a list of exactly 256 strings
- WHEN `POST /v1/embeddings` is called
- THEN the response contains 256 `data` entries

#### Scenario: Batch above the ceiling is rejected

- GIVEN `input` is a list of 257 strings
- WHEN `POST /v1/embeddings` is called
- THEN the service returns a 4xx error and no partial `data` is returned

### Requirement: Unsupported Encoding Format Rejection

The service MUST honor `encoding_format: "float"` (default) and MUST reject `encoding_format: "base64"` with a clear 4xx error rather than returning a wrongly-encoded or silently-ignored value.

#### Scenario: base64 encoding format is rejected

- GIVEN a request includes `"encoding_format": "base64"`
- WHEN `POST /v1/embeddings` is called
- THEN the service returns a 4xx error identifying the unsupported parameter

### Requirement: Authorization Header Passthrough

The service MUST accept requests carrying an `Authorization` header and MUST ignore its value entirely — no credential is validated, since the service requires no authentication.

#### Scenario: Dummy API key is accepted

- GIVEN a request includes `"Authorization: Bearer sk-dummy"`
- WHEN `POST /v1/embeddings` is called with an otherwise valid body
- THEN the request succeeds identically to a request with no `Authorization` header

### Requirement: Readiness Reflects Model Load State

`GET /healthz` MUST report ready only after the pinned embedding model has finished loading into memory; it MUST NOT report ready before the model is available to serve requests.

#### Scenario: Health check before model load

- GIVEN the process has started but the model has not finished loading
- WHEN `GET /healthz` is called
- THEN it does not report ready

#### Scenario: Health check after model load

- GIVEN the model has finished loading
- WHEN `GET /healthz` is called
- THEN it reports ready

### Requirement: No External Egress, No GPU

The pinned model MUST be resolved entirely from the container image at runtime, with zero network egress required to serve requests. The deployment MUST NOT request `nvidia.com/gpu` and MUST run correctly on CPU-only nodes.

#### Scenario: Serving requires no outbound network call

- GIVEN the pod is running with network egress blocked except for cluster-internal traffic
- WHEN `POST /v1/embeddings` is called
- THEN the request succeeds without any external network dependency

#### Scenario: No GPU resource is requested

- GIVEN the deployment manifest
- WHEN its container resource requests are inspected
- THEN no `nvidia.com/gpu` entry is present

### Requirement: Cluster-Internal Network Boundary

The service MUST be reachable only via a `ClusterIP` Service inside the cluster and MUST NOT have an Ingress. No `NetworkPolicy` is added for this service by this change; the ClusterIP-only, no-Ingress boundary is the sole intended network boundary, and broad in-namespace (`llms`) consumption is expected.

#### Scenario: Service is ClusterIP with no Ingress

- GIVEN the service's manifests
- WHEN they are inspected
- THEN the Service type is `ClusterIP` and no Ingress object exists for it

#### Scenario: Any workload in the `llms` namespace can reach it

- GIVEN another pod in namespace `llms`
- WHEN it sends `POST /v1/embeddings` to `local-embeddings.llms.svc.cluster.local:8080`
- THEN the request is not blocked by any NetworkPolicy
