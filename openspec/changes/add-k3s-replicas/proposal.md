# Proposal: K3s Replicas for JARVIS Voice Services

## Intent

Enable scalable deployment of JARVIS voice services (Piper TTS) across multiple Raspberry Pi nodes using k3s distributed Kubernetes, while maintaining local voice synthesis capabilities and low-latency audio delivery for Hermes Agent.

## Context

**Current State:**
- JARVIS Spec 010 implements local Piper TTS for Spanish voice synthesis (es_ES-davefx-medium)
- Hermes Agent runs on a headless server (trantor) with CachyOS
- Server is currently on k3s for potential RPi replicas
- Voice services need to scale across multiple RPi nodes
- Model size: ~63 MB (model.onnx) per instance
- Single-threaded inference, CPU-only optimization
- No GPU acceleration required

**Problem Statement:**
Voice synthesis services need to scale across multiple RPi nodes while maintaining:
- Low-latency audio delivery
- Local voice quality (no streaming)
- Efficient resource utilization
- Simple operational model

## Scope

### In Scope

- k3s cluster deployment across multiple RPi nodes
- Service scaling with replicas
- Piper TTS containerization for distributed deployment
- Audio streaming from multiple nodes to Hermes Agent
- Load balancing for voice synthesis requests
- Health monitoring and automatic failover

### Out of Scope

- GPU acceleration (not available on target hardware)
- Automatic voice cloning
- Wake word detection
- Continuous audio capture
- Speech-to-text integration
- Remote voice model hosting

## Affected Capabilities

- **voice-output**: Distributed voice synthesis across RPi nodes
- **kubernetes**: Service management, scaling, and orchestration
- **hermes-agent**: TTS provider integration (piper)

## Success Criteria

- Voice synthesis request processed by any available RPi node in cluster
- Audio delivery latency < 2 seconds for typical responses
- Automatic node failure detection and request rerouting
- Horizontal scaling from 1 to N replicas based on load
- Zero manual intervention for node addition/removal
- Model caching per node (~63 MB per instance)

## Risks and Dependencies

| Risk | Impact | Mitigation |
|------|--------|------------|
| Network latency between nodes | High audio latency | Co-locate Hermes and voice nodes; use low-latency network |
| Model download on each node | Initial setup time | Pre-deploy models to all nodes during cluster init |
| Single-threaded inference | Limited concurrency | Scale horizontally with more replicas |
| k3s management complexity | Operational overhead | Use k3s single-server mode for small deployments |
| Audio stream synchronization | Out-of-order playback | Sequence audio requests with request IDs |

## Assumptions

- Target hardware: Raspberry Pi 4 or 5 (4+ GB RAM minimum)
- Network: Stable 100 Mbps+ connection between nodes
- Storage: 10 GB+ available per node for model/cache
- Hermes Agent: Runs on one node (primary or separate server)
- Python 3.11+ available on all nodes
- Piper TTS v1.6.0 compatible with k3s environment

## Open Questions

1. Should Hermes Agent run on same node as voice services or separate?
2. What is the target concurrency level (concurrent audio requests)?
3. Do we need persistent storage for voice cache across reboots?
4. Should we use Kubernetes native load balancing or external (nginx ingress)?

## Technical Decisions

- **Container Runtime**: k3s with containerd (default)
- **Voice Service**: Piper TTS in Docker container
- **Model Storage**: Local cache per node (~63 MB per voice)
- **Communication**: HTTP/gRPC between Hermes and voice services
- **Scaling**: Horizontal Pod Autoscaler (HPA) based on CPU/memory
- **Network**: ClusterIP service with NodePort for debugging
