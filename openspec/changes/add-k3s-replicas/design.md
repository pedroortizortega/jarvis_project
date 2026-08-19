# Design: K3s Distributed Voice Synthesis for JARVIS

## Context and Constraints

- **Target Platform**: k3s Kubernetes on Raspberry Pi cluster
- **Voice Engine**: Piper TTS v1.6.0 (CPU-only, no GPU)
- **Model Size**: ~63 MB per voice model (es_ES-davefx-medium)
- **Inference**: Single-threaded, CPU-bound
- **Network**: Local cluster with potential external access
- **Operational Model**: Minimal human intervention, automated scaling

## Goals and Non-Goals

### Goals

- Enable horizontal scaling of voice synthesis across multiple RPi nodes
- Maintain low-latency audio delivery (< 2s target)
- Support dynamic node addition/removal without service disruption
- Provide health monitoring and automatic failover
- Minimize operational complexity

### Non-Goals

- GPU acceleration (hardware not available)
- Real-time streaming synthesis
- Wake word detection
- Automatic voice cloning

## Proposed Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    k3s Cluster (RPi Nodes)                      │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐             │
│  │  Node 1     │  │  Node 2     │  │  Node N     │             │
│  │ (Hermes)    │  │ (Voice)     │  │ (Voice)     │             │
│  │             │  │             │  │             │             │
│  │ Hermes      │  │ Piper TTS   │  │ Piper TTS   │             │
│  │ Agent       │  │ Pod         │  │ Pod         │             │
│  │             │  │             │  │             │             │
│  │ Voice       │◄─┤ Load        │◄─┤ Health      │             │
│  │ Service     │  │ Balancer    │  │ Check       │             │
│  └─────────────┘  └─────────────┘  └─────────────┘             │
│         │                  │                  │                 │
│         └──────────────────┼──────────────────┘                 │
│                           │                                     │
│                   ┌───────▼────────┐                            │
│                   │  Model Cache   │                            │
│                   │  (63 MB/node)  │                            │
│                   └────────────────┘                            │
└─────────────────────────────────────────────────────────────────┘
```

## Components and Responsibilities

### 1. Voice Service Pod

- **Container**: Piper TTS Python application
- **Model**: Pre-loaded `model.onnx` (~63 MB)
- **Endpoints**:
  - `/synthesize` - Generate audio from text
  - `/health` - Health check endpoint
  - `/metrics` - Prometheus metrics (optional)
- **Resources**:
  - CPU: 500m - 1000m (scalable)
  - Memory: 512MB - 1GB
  - Storage: Local volume for model/cache

### 2. Voice Load Balancer

- **Service Type**: ClusterIP (internal) + NodePort (external)
- **Load Balancing**: Round-robin across voice pods
- **Health Checks**: Kubernetes liveness probe (HTTP /health)
- **Failover**: Automatic routing to healthy nodes

### 3. Model Cache Service

- **Storage**: Local volume per node (~63 MB per voice)
- **Sync**: Manual or automated model distribution
- **Persistence**: Survives pod restarts

### 4. Hermes Agent Integration

- **Client**: HTTP/gRPC to Voice Load Balancer
- **Fallback**: Text response if voice unavailable
- **Retry Logic**: Exponential backoff on failures

## Data and Control Flows

### Voice Synthesis Request Flow

```
1. User Request → Hermes Agent
2. Hermes → Voice Load Balancer (HTTP POST /synthesize)
3. Load Balancer → Available Voice Pod (round-robin)
4. Voice Pod:
   - Check model in cache
   - If missing: Download from registry (one-time)
   - Synthesize audio (text → phonemes → audio)
5. Voice Pod → Load Balancer (audio file)
6. Load Balancer → Hermes Agent
7. Hermes → Text-to-Speech Tool → Audio Delivery
```

### Health Check Flow

```
1. k8s Controller → Voice Pod (/health endpoint)
2. Pod responds 200 OK
3. Controller marks pod healthy
4. Load Balancer routes requests to healthy pods
5. If unhealthy: Remove from endpoints, trigger restart
```

## Interfaces and Contracts

### Voice API Contract

```python
# Request
POST /synthesize
Content-Type: application/json

{
    "text": "Hola, ¿cómo puedo ayudarte hoy?",
    "voice": "es_ES-davefx-medium",
    "sample_rate": 22050,
    "speed": 1.0
}

# Response
200 OK
Content-Type: audio/wav

<binary audio data>

# Error Response
400 Bad Request
{
    "error": "Invalid text",
    "detail": "Text cannot be empty"
}
```

### Health Check Contract

```python
GET /health
Content-Type: application/json

{
    "status": "healthy",
    "model": "es_ES-davefx-medium",
    "uptime": 3600,
    "samples_generated": 150,
    "cache_hit_rate": 0.95
}
```

## Key Decisions

| ID | Decision | Rationale | Trade-offs |
|----|----------|-----------|------------|
| K-001 | Horizontal scaling (multiple replicas) | Distribute load, increase reliability | Higher resource usage, network overhead |
| K-002 | Local model cache per node | Fast synthesis, no network dependency | ~63 MB per node, sync complexity |
| K-003 | Kubernetes native load balancing | Automatic health checks, easy scaling | k3s learning curve, resource overhead |
| K-004 | HTTP/gRPC communication | Simple, language-agnostic | HTTP more verbose, gRPC more efficient |
| K-005 | CPU-only inference | No GPU hardware available | Slower than GPU, but acceptable for voice |

## Alternatives Considered

### Alternative 1: Single Node (No Scaling)

- **Pros**: Simpler, no cluster management
- **Cons**: Single point of failure, no scalability
- **Decision**: Rejected for production deployment

### Alternative 2: External Load Balancer (nginx)

- **Pros**: More control, lower latency
- **Cons**: Additional complexity, maintenance overhead
- **Decision**: Rejected for simplicity; k3s native is sufficient

### Alternative 3: GPU Acceleration

- **Pros**: 5-10x faster synthesis
- **Cons**: Not available on target hardware, high cost
- **Decision**: Deferred to future iteration

### Alternative 4: Persistent Volume Storage

- **Pros**: Centralized model storage
- **Cons**: Network I/O bottleneck, single point of failure
- **Decision**: Rejected; local cache is faster and more reliable

## Security, Privacy and Safety

- **Data Protection**: Audio synthesis only, no text logging
- **Authentication**: Kubernetes service account for cluster access
- **Network Isolation**: Private cluster network, optional external access
- **Model Security**: Read-only model files, no modification at runtime

## Reliability and Failure Modes

### Single Node Failure

- **Detection**: Kubernetes liveness probe (30s interval)
- **Recovery**: Automatic pod rescheduling to healthy node
- **Latency Impact**: ~30-60s for new pod startup

### Network Partition

- **Detection**: Node health checks
- **Recovery**: Kubernetes automatic rescheduling
- **Mitigation**: Co-locate Hermes and voice nodes when possible

### Model Corruption

- **Detection**: Synthesis error on startup
- **Recovery**: Automatic model re-download
- **Prevention**: Model integrity checksums

## Performance and Capacity

### Resource Requirements

| Component | CPU | Memory | Storage |
|-----------|-----|--------|---------|
| Piper Pod (per instance) | 500m-1000m | 512MB-1GB | 63 MB (model) |
| Hermes Agent | 250m | 256MB | - |
| Load Balancer | 100m | 128MB | - |

### Scaling Guidelines

| Concurrent Users | Replicas | Notes |
|------------------|----------|-------|
| 1-5 | 1 | Single node, development |
| 5-20 | 2-3 | Small cluster |
| 20-50 | 4-6 | Medium cluster |
| 50+ | 8+ | Large cluster, consider GPU |

### Latency Budget

| Stage | Target | Buffer |
|-------|--------|--------|
| Network (Hermes → Voice) | 50ms | 50ms |
| Model Load | 0ms (cached) | 100ms |
| Synthesis | 1000ms | 500ms |
| Audio Delivery | 50ms | 50ms |
| **Total** | **~1.2s** | **~650ms** |

## Compatibility and Migration

### From Current Setup

1. **Backup**: Current Piper configuration
2. **Deploy**: k3s cluster on RPi nodes
3. **Initialize**: Pull voice models to all nodes
4. **Update**: Change Hermes config to use k3s voice endpoint
5. **Verify**: Test synthesis across nodes
6. **Monitor**: Observe for 24h before rollback

### Rollback Strategy

```yaml
# Kubernetes Rollback
kubectl rollout undo voice-service

# Or revert to previous version
kubectl rollout undo voice-service --to-revision=1
```

## Observability and Operations

### Monitoring

- **Metrics**: Prometheus + Grafana
  - Synthesis duration
  - Request rate
  - Error rate
  - Model cache hit rate
- **Logs**: Fluentd/EFK stack
- **Tracing**: OpenTelemetry (optional)

### Alerting

- Voice service down (> 3 failures)
- High error rate (> 5%)
- High latency (> 5s average)
- Model download failures

### Operations

```bash
# Check cluster status
k3s server --list-nodes

# Scale voice service
kubectl scale deployment voice-service --replicas=3

# View logs
kubectl logs -f deployment/voice-service

# Check health
kubectl get pods --show-labels
kubectl exec -it <voice-pod> -- curl http://localhost:8000/health
```

## Testing Strategy

### Unit Tests

- Voice synthesis endpoint
- Health check response
- Error handling

### Integration Tests

- End-to-end synthesis flow
- Load balancer routing
- Failover behavior

### Performance Tests

- Concurrent request handling
- Latency measurements
- Resource utilization

### Load Tests

- Scale test (1 → 10 replicas)
- Stress test (sustained load)
- Chaos test (node failures)

## Rollback Strategy

### Immediate Rollback

```bash
# Revert to previous deployment
kubectl rollout undo voice-service

# Or delete new deployment
kubectl delete deployment voice-service

# Restore old configuration
kubectl apply -f voice-service-v1.yaml
```

### Data Recovery

- Model cache: Re-download from registry
- Configuration: Git repository backup
- Logs: Retained for analysis

### Known Limitations

- Model re-download time: ~30s per node
- Pod startup time: ~15-30s
- No automatic model sync across nodes

## Unresolved Decisions

1. **Hermes Placement**: Should Hermes run on same node or separate?
   - **Impact**: Network latency vs resource utilization
   - **Status**: Awaiting user input

2. **External Access**: Should voice service be accessible externally?
   - **Impact**: Security, network configuration
   - **Status**: Internal cluster only for now

3. **Model Distribution**: Manual vs automated model sync?
   - **Impact**: Operational complexity
   - **Status**: Manual for initial deployment
