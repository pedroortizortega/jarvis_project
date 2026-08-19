# Voice Output Specification - K3s Distributed Deployment

## Purpose

Define the behavior of voice synthesis services across a distributed k3s cluster, enabling scalable, fault-tolerant audio generation for JARVIS Agent.

## ADDED Requirements

### Requirement: Distributed Voice Synthesis

The system SHALL route voice synthesis requests to any available voice service pod in the k3s cluster.

#### Scenario: Load Balanced Synthesis
- GIVEN voice service is deployed with multiple replicas across k3s nodes
- WHEN Hermes Agent sends a voice synthesis request
- THEN the request is routed to an available pod using round-robin load balancing
- AND the audio is delivered to Hermes Agent within 2 seconds

#### Scenario: Node Addition
- GIVEN voice service is running with N replicas
- WHEN a new RPi node joins the k3s cluster
- THEN the voice service automatically scales to include the new node
- AND requests are distributed across all available nodes

### Requirement: Automatic Failover

The system SHALL automatically reroute requests when a voice service pod becomes unhealthy.

#### Scenario: Pod Failure
- GIVEN voice service pods are running across multiple nodes
- WHEN a pod becomes unhealthy (liveness probe fails)
- THEN Kubernetes removes the pod from service endpoints
- AND subsequent requests are routed to healthy pods
- AND the failed pod is restarted automatically

#### Scenario: Node Failure
- GIVEN voice service is deployed across multiple nodes
- WHEN an entire node fails
- THEN all pods on that node are rescheduled to healthy nodes
- AND voice synthesis continues without manual intervention
- AND the user experiences at most a 60-second delay

### Requirement: Model Caching

Each voice service pod SHALL cache the Piper TTS model locally (~63 MB) to avoid network latency.

#### Scenario: Model Availability
- GIVEN a voice service pod starts
- WHEN the pod receives a voice synthesis request
- THEN it checks if the model is cached locally
- AND if not present, downloads from the configured registry
- AND caches the model for future requests

#### Scenario: Model Persistence
- GIVEN a voice service pod has cached a model
- WHEN the pod is restarted
- THEN the cached model survives the restart
- AND synthesis requests can be processed immediately
- AND no model download is required

### Requirement: Health Monitoring

The system SHALL expose health check endpoints for monitoring and automatic failover.

#### Scenario: Health Check
- GIVEN a voice service pod is running
- WHEN an HTTP GET request is made to /health
- THEN the endpoint returns HTTP 200 with current status
- AND includes model information and uptime
- AND responds within 100ms

#### Scenario: Metrics Endpoint
- GIVEN a voice service pod is running
- WHEN an HTTP GET request is made to /metrics
- THEN the endpoint returns Prometheus-formatted metrics
- AND includes synthesis duration, request count, error rate

## MODIFIED Requirements

### Requirement: Voice Synthesis Endpoint

The system SHALL accept voice synthesis requests via HTTP POST.

#### Scenario: Request Processing
- GIVEN a voice synthesis request with valid text
- WHEN Hermes Agent sends POST to /synthesize endpoint
- THEN the system generates audio from the text
- AND returns audio data as response
- AND logs the request for audit purposes

**CHANGES:**
- Previously: Single-node HTTP endpoint
- Now: Load-balanced cluster endpoint via k3s Service
- New: Automatic failover and retry logic
- New: Distributed model caching per node

### Requirement: Service Availability

The voice synthesis service SHALL be available when requested by Hermes Agent.

#### Scenario: Service Availability
- GIVEN a voice synthesis request
- WHEN the voice service is running
- THEN the request is processed successfully
- AND audio is returned
- AND the service reports healthy status

**CHANGES:**
- Previously: Service could be unavailable if node failed
- Now: Service remains available via other nodes in cluster
- New: Automatic scaling based on load
- New: Health checks with automatic pod replacement

### Requirement: Resource Utilization

The system SHALL use CPU resources efficiently for voice synthesis.

#### Scenario: Resource Limits
- GIVEN a voice service pod starts
- THEN the pod is allocated 500m-1000m CPU
- AND 512MB-1GB memory
- AND ~63 MB storage for model cache
- AND scales based on demand

**CHANGES:**
- Previously: Fixed resource allocation per node
- Now: Kubernetes-managed resources with horizontal scaling
- New: CPU-based autoscaling
- New: Resource limits prevent node overload

## REMOVED Requirements

### Requirement: Single-Node Deployment

The voice service SHALL run on a single node with no redundancy.

**REASON:** Replaced by distributed deployment for reliability and scalability.

### Requirement: Manual Scaling

The number of voice service instances SHALL be set manually and not change automatically.

**REASON:** Replaced by Kubernetes Horizontal Pod Autoscaler for automatic scaling.

### Requirement: Local-Only Synthesis

Voice synthesis SHALL only occur on the primary Hermes node.

**REASON:** Replaced by distributed synthesis across multiple nodes for better performance and reliability.

## Behavioral Changes Summary

| Aspect | Before | After |
|--------|--------|-------|
| Deployment | Single node | Multi-node k3s cluster |
| Scaling | Manual, static | Automatic, horizontal |
| Failover | None | Automatic pod/node replacement |
| Model Storage | Local filesystem | Distributed cache per pod |
| Load Balancing | None | Kubernetes Service |
| Health Checks | None | Kubernetes liveness probes |
| Availability | Single point of failure | High availability |
| Latency | Variable | Consistent (< 2s target) |
| Operations | Manual intervention | Automated recovery |

## Assumptions

1. k3s cluster is properly configured and running
2. All nodes have sufficient resources (4GB+ RAM, 10GB+ storage)
3. Network connectivity between nodes is stable
4. Piper TTS model (~63 MB) is available in all nodes
5. Hermes Agent runs on one node or external system
6. Python 3.11+ is installed on all voice service nodes

## Open Questions

1. Should Hermes Agent run on the same node as voice services or separate?
2. What is the target concurrency level (concurrent audio requests)?
3. Should we use Kubernetes native load balancing or external (nginx ingress)?
4. How long should we retain audio cache per request?

## Security Considerations

- Voice service should only accept authenticated requests from Hermes
- Model files should be read-only within containers
- Cluster should use private networking for internal communication
- External access requires additional authentication

## Performance Targets

| Metric | Target | Measurement |
|--------|--------|-------------|
| Synthesis latency | < 2s | End-to-end time |
| Request throughput | N/A | Based on hardware |
| Availability | 99.9% | Uptime monitoring |
| Failover time | < 60s | Node failure recovery |
| Model load time | < 30s | First request after startup |

## Testing Requirements

- Unit tests for voice synthesis endpoint
- Integration tests for load balancing
- Load tests for concurrency handling
- Chaos tests for failure scenarios
- Performance tests for latency targets

## Rollback Plan

If deployment fails:
1. Rollback voice service to previous version
2. Remove new deployments
3. Restore old configuration
4. Verify single-node functionality

## References

- [OpenSpec Methodology](https://openspec.dev/)
- [k3s Documentation](https://k3s.io/)
- [Piper TTS Documentation](https://github.com/rhasspy/piper)
- [JARVIS Spec 010](../specs/010_jarvis_voice_piper.md)
