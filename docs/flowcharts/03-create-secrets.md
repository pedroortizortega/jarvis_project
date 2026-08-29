Proceeding with diagram generation...

# Generate Mermaid Diagram From Code

This command analyzes the coding files you have selected, understands each component, its flow, connections, and relationships, and then maps everything into a comprehensive Mermaid diagram.

Use this to quickly visualize the structure and logic of your code, making it easier to understand, document, and share.

---

📄 Analyzing: `kubernetes/mcps/bootstrap/03-create-secrets.sh, kubernetes/mcps/bootstrap/05-deploy-manifests.sh, tests/test_knowledge_vault_search_manifest.py`

📊 Generating: **Flowchart**



Fetching Mermaid documentation for flowchart.md
Completed with input: {
  "file": "flowchart.md"
}

Retrieved Mermaid flowchart syntax docs (flowchart.md) and used them as source of truth.

```mermaid
flowchart TD
    A["Start Function"] --> B{Check Condition}
    B -->|Yes| C["Process Data"]
    B -->|No| D["Handle Error"]
    C --> E[(Save to DB)]
    E --> F["Return Success"]
    D --> G["Log Error"]

    %% Bootstrap: create secrets script
    S03("Run: ./03-create-secrets.sh") --> APPLY_FN[["apply_secret() (stdin -> kubectl apply)"]]
    S03 --> ENG_TOKEN_CHECK{"Is MR_ENGRAM_TOKEN set or\nreadable from 'engram-cloud-config'?"}
    ENG_TOKEN_CHECK -->|Yes| CREATE_ENGRAM["Create 'memory-router-engram-credentials' secret"]
    ENG_TOKEN_CHECK -->|No| ABORT_ENGRAM["Log + exit 1"]

    CREATE_ENGRAM --> CREATE_CLIENT_CA["Create 'memory-router-client-ca' (from ca.crt)"]
    CREATE_CLIENT_CA --> CREATE_SERVER_TLS["Create 'memory-router-server-tls' (tls secret)"]

    CREATE_SERVER_TLS --> BEARER_LOOP{"For each NAME in MR_IDENTITIES\n(loop)?"}
    BEARER_LOOP -->|Iterate| CHECK_TOKEN_FILE{"Does '$MR_PKI_DIR/bearers/$NAME' exist?"}
    CHECK_TOKEN_FILE -->|No| GENERATE_TOKEN["Generate token file (openssl rand)"]
    CHECK_TOKEN_FILE -->|Yes| SKIP_GEN["Reuse existing token file"]
    GENERATE_TOKEN --> SKIP_GEN
    SKIP_GEN --> ACCUM_ARG["Accumulate --from-literal for NAME"]
    ACCUM_ARG -->|After loop| CREATE_BEARERS["Create 'memory-router-client-bearers' secret"]

    CREATE_BEARERS --> HINDSIGHT_DIR_CHECK{"Is hindsight tenant key file\npresent in '$MR_PKI_DIR/hindsight'?"}
    HINDSIGHT_DIR_CHECK -->|No| GENERATE_HINDSIGHT["Generate tenant-api-key file"]
    HINDSIGHT_DIR_CHECK -->|Yes| USE_HINDSIGHT["Use existing tenant-api-key file"]
    GENERATE_HINDSIGHT --> USE_HINDSIGHT
    USE_HINDSIGHT --> CREATE_HINDSIGHT_SECRET["Create 'hindsight-tenant-key' secret"]

    CREATE_HINDSIGHT_SECRET --> MIRROR_CODEX_CHECK{"Can read llms/codex-shim-key\nsecret 'internal-key'?"}
    MIRROR_CODEX_CHECK -->|No| ABORT_CODEX["Log + exit 1"]
    MIRROR_CODEX_CHECK -->|Yes| CREATE_CODEX["Create 'hindsight-codex-shim-key' (mirrored)"]

    CREATE_CODEX --> KV_TOKEN_CHECK{"Is host file\n'/etc/knowledge-vault/search-token' readable?"}
    KV_TOKEN_CHECK -->|No| ABORT_KV["Log + exit 1"]
    KV_TOKEN_CHECK -->|Yes| CREATE_KV["Create 'knowledge-vault-search-token' secret"]

    CREATE_KV --> ALL_DONE_SECRETS["All 7 secrets applied in namespace $MR_NAMESPACE"]

    %% Deploy manifests script
    S05("Run: ./05-deploy-manifests.sh") --> IMAGE_MARKER_CHECK{"Does '$MR_IMAGE_HASH_MARKER' exist?"}
    IMAGE_MARKER_CHECK -->|No| ABORT_IMAGE_MISSING["Log + exit 3 (build image first)"]
    IMAGE_MARKER_CHECK -->|Yes| HASH_COMPARE{"Is CURRENT_HASH == BUILT_HASH?"}
    HASH_COMPARE -->|No| ABORT_IMAGE_STALE["Log + exit 3 (stale image)"]
    HASH_COMPARE -->|Yes| APPLY_MANIFESTS_LOOP{"For f in manifests list\n(apply each) (loop)"}
    APPLY_MANIFESTS_LOOP --> APPLY_FILE["kubectl -n $MR_NAMESPACE apply -f $MR_MANIFESTS_DIR/$f"]
    APPLY_FILE -->|After all| WAIT_MEMROUTER["kubectl rollout status deployment/memory-router\n(timeout 90s)"]
    WAIT_MEMROUTER --> WAIT_HINDSIGHT["kubectl rollout status deployment/hindsight\n(timeout 600s)"]
    WAIT_HINDSIGHT --> DEPLOY_DONE["Deploys complete"]

    %% Tests: manifest unit tests
    RUN_TESTS("Run: tests/test_knowledge_vault_search_manifest.py") --> LOAD_DOCS["yaml.safe_load_all on 'knowledge-vault-search-endpoints.yaml' and 'memory-router-deployment.yaml'"]

    LOAD_DOCS --> SERVICE_TESTS["ServiceTests: assert headless, no selector,\nport/targetPort 8088"]
    LOAD_DOCS --> ENDPOINTSLICE_TESTS["EndpointSliceTests: addressType IPv4,\naddresses == ['10.42.0.1'], ready==true,\nlabel matches service name"]
    LOAD_DOCS --> NO_INGRESS_TEST["NoIngressAnywhereTests: no 'Ingress' kind present"]

    LOAD_DOCS --> CROSS_SECRET_TESTS{"CrossManifestSecretWiringTests: secret wiring checks"}
    CROSS_SECRET_TESTS --> SECRET_REF_CHECK["Assert KNOWLEDGE_VAULT_TOKEN is secretKeyRef to\n{'name':'knowledge-vault-search-token','key':'search-token'}"]
    CROSS_SECRET_TESTS --> NO_INLINE_CHECK["Assert KNOWLEDGE_VAULT_TOKEN has no inline 'value'"]
    CROSS_SECRET_TESTS --> AUTH_MODE_CHECK["Assert KNOWLEDGE_VAULT_AUTH_MODE == 'bearer'"]
    CROSS_SECRET_TESTS --> NO_BASE_URL_CHECK["Assert no KNOWLEDGE_VAULT_BASE_URL env override"]

    SECRET_REF_CHECK --> TESTS_DONE["Unit tests pass / fail reported by unittest runner"]

    %% Connect logical start to both flows (user may run separately)
    A_done["Template End"] --> S03
    A_done --> S05
    A_done --> RUN_TESTS
```

Open Diagram in Editor