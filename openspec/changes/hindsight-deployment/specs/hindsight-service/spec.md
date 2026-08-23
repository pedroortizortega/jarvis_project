# Hindsight Service Specification

## Purpose

Define the deployed contract for the in-cluster Hindsight instance in namespace `mcps`: in-cluster DNS/port, bearer-auth enforcement, persistence across restarts, self-contained LLM/embeddings dependencies, resource sizing, and the no-Ingress network boundary.

## Requirements

### Requirement: In-Cluster DNS and Port

The Hindsight Service MUST be reachable inside the cluster at `hindsight.mcps.svc.cluster.local` on port `8888`, matching Hindsight's real `HINDSIGHT_API_PORT` default.

#### Scenario: Service resolves on the documented port

- GIVEN a pod inside the cluster
- WHEN it sends a request to `http://hindsight.mcps.svc.cluster.local:8888/health`
- THEN the request reaches the Hindsight container and receives a response

### Requirement: Bearer Authentication Enforced

The deployed Hindsight instance MUST require a bearer token (`HINDSIGHT_API_TENANT_API_KEY`) for API access. A request without a token, or with an incorrect token, MUST be rejected. The equivalent request carrying the correct tenant bearer token MUST succeed.

#### Scenario: Unauthenticated request is rejected

- GIVEN the Hindsight pod is running with bearer auth configured
- WHEN an in-cluster request is sent to a data-plane endpoint with no `Authorization` header
- THEN the request is rejected with an authentication error

#### Scenario: Correctly authenticated request succeeds

- GIVEN the Hindsight pod is running with bearer auth configured
- WHEN an in-cluster request is sent with `Authorization: Bearer <tenant-key>` matching the configured key
- THEN the request succeeds

### Requirement: Persistence Across Pod Restarts

Hindsight's embedded Postgres data MUST persist on a PersistentVolumeClaim mounted at `/home/hindsight/.pg0` (`storageClassName: local-path`). Deleting and rescheduling the pod MUST NOT lose previously stored memories.

#### Scenario: Memories survive pod deletion and reschedule

- GIVEN a memory has been stored in a Hindsight bank
- WHEN the pod is deleted and Kubernetes reschedules a replacement pod
- THEN the same memory is retrievable from the new pod once it becomes ready

### Requirement: Single-Writer Deployment Strategy

The Hindsight Deployment MUST run with `replicas: 1` and strategy `Recreate`, never `RollingUpdate`, because the RWO PVC and embedded Postgres cannot tolerate two concurrent writers.

#### Scenario: Deployment strategy is Recreate

- GIVEN the Hindsight Deployment manifest
- WHEN its `spec.strategy.type` is inspected
- THEN it equals `Recreate` and `spec.replicas` equals `1`

### Requirement: LLM Calls Route Through codex-shim

Hindsight's LLM calls MUST resolve through `codex-shim.llms.svc.cluster.local:8080/v1`, authenticated with a bearer secret duplicated into the `mcps` namespace from codex-shim's internal-bearer secret in `llms`. Hindsight MUST have no other external LLM dependency.

#### Scenario: LLM extraction call reaches codex-shim

- GIVEN a `store` operation that triggers LLM-based fact extraction
- WHEN Hindsight performs the extraction call
- THEN the call is observed reaching `codex-shim` (e.g. in codex-shim's logs), not any external LLM provider

### Requirement: Self-Contained Embeddings, No External Egress for Vectors

Embeddings and reranking MUST be served entirely by Hindsight's bundled `onnx` provider (`intfloat/multilingual-e5-small` and its reranker) running inside the pod. No external embeddings service call is required to store or search memories.

#### Scenario: Store and search succeed with embeddings blocked externally

- GIVEN the pod's network egress to any external embeddings endpoint is blocked
- WHEN a `store` followed by a `search` is performed
- THEN both operations succeed using the in-pod embedding provider

### Requirement: Multilingual Embeddings With Native Query/Passage Prefixing

Embeddings MUST be served by Hindsight's bundled `onnx` provider configured with `intfloat/multilingual-e5-small` (multilingual, 384 dimensions), not the SentenceTransformers-based `local` provider's English-only default (`bge-small-en-v1.5`) and not the external `local-embeddings` service. This choice is deliberate: the `onnx` provider prepends `query:`/`passage:` prefixes natively inside Hindsight before each embedding call, a correctness property `local-embeddings`'s OpenAI-compatible endpoint cannot provide (no `input_type` signal reaches it, since memory-router never calls embeddings directly — only Hindsight does, for both `retain` and `recall`).

#### Scenario: Day-one deployment uses the multilingual onnx-provider model

- GIVEN the Hindsight deployment configuration
- WHEN the embeddings provider is inspected
- THEN `HINDSIGHT_API_EMBEDDINGS_PROVIDER` is `onnx` and `HINDSIGHT_API_EMBEDDINGS_ONNX_MODEL_ID` is `intfloat/multilingual-e5-small`, not `local`/`bge-small-en-v1.5` and not `local-embeddings`

#### Scenario: Spanish memories retrieve correctly

- GIVEN a memory stored in `/projects/*` with Spanish-language content
- WHEN a `search` is performed with a Spanish-language query
- THEN the stored memory is returned, evidencing multilingual recall

### Requirement: No Ingress, ClusterIP Only

The Hindsight Service MUST be exposed only via a `ClusterIP` Service and MUST NOT have an Ingress object. It is an in-cluster consumer-only dependency.

#### Scenario: No Ingress object exists

- GIVEN the manifests for the Hindsight Service
- WHEN they are inspected
- THEN the Service type is `ClusterIP` and no Ingress object exists for it

### Requirement: No Dedicated NetworkPolicy

No `NetworkPolicy` is added for the Hindsight Service by this change. There is no default-deny policy in `mcps`; the `ClusterIP` + no-Ingress boundary is the intended network boundary, and broad in-namespace traffic between memory-router and Hindsight is expected.

#### Scenario: In-namespace traffic from memory-router is not blocked

- GIVEN the memory-router pod in namespace `mcps`
- WHEN it sends a request to `hindsight.mcps.svc.cluster.local:8888`
- THEN the request is not blocked by any NetworkPolicy

### Requirement: Resource Sizing

The Hindsight container MUST declare resource requests of `1` CPU / `2Gi` memory and limits of `4` CPU / `6Gi` memory.

#### Scenario: Manifest declares the sized requests and limits

- GIVEN the Hindsight Deployment manifest
- WHEN its container `resources` block is inspected
- THEN `requests` equals `1` CPU / `2Gi` and `limits` equals `4` CPU / `6Gi`

### Requirement: Image Pinning Policy

The Deployment MUST use image `ghcr.io/vectorize-io/hindsight:latest` with `imagePullPolicy: Always` for day one. Pinning a specific digest is an accepted future follow-up, not a day-one requirement.

#### Scenario: Manifest asserts the day-one image policy

- GIVEN the Hindsight Deployment manifest
- WHEN its container image and pull policy are inspected
- THEN the image is `ghcr.io/vectorize-io/hindsight:latest` and `imagePullPolicy` is `Always`
