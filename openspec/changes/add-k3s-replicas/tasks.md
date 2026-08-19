# Tasks: K3s Distributed Voice Synthesis

## 1. Infrastructure Setup

### 1.1 k3s Cluster Initialization
- [ ] 1.1.1 Install k3s on primary RPi node
  - **Requirements**: REQ-001 (k3s cluster)
  - **Verification**: `kubectl get nodes` shows 1 master node
  - **Command**: `curl -sfL https://get.k3s.io | sh -`
  - **Note**: Use `--write-kubeconfig-mode 644` for security

- [ ] 1.1.2 Install k3s on additional RPi nodes
  - **Requirements**: REQ-001 (k3s cluster)
  - **Verification**: `kubectl get nodes` shows 1 master + N worker nodes
  - **Command**: `sudo k3s agent --server https://<MASTER_IP>:6443`
  - **Note**: Copy kubeconfig to all nodes

- [ ] 1.1.3 Configure cluster network
  - **Requirements**: REQ-001 (cluster connectivity)
  - **Verification**: All nodes can reach each other on pod network
  - **Command**: `kubectl cluster-info`
  - **Note**: Verify CNI plugin (flannel/default)

### 1.2 Voice Service Configuration
- [ ] 1.2.1 Create voice service YAML manifest
  - **Requirements**: REQ-002 (voice service deployment)
  - **Verification**: `kubectl apply -f voice-service.yaml` succeeds
  - **File**: `/home/pedro/Documentos/Projects/jarvis_project/k3s/voice-service.yaml`
  - **Note**: Include resource limits, health checks

- [ ] 1.2.2 Create Dockerfile for Piper TTS
  - **Requirements**: REQ-002 (containerized voice service)
  - **Verification**: `docker build -t voice-service .` succeeds
  - **File**: `/home/pedro/Documentos/Projects/jarvis_project/k3s/Dockerfile`
  - **Note**: Python 3.11, pip packages, model baked in

- [ ] 1.2.3 Build and tag voice service image
  - **Requirements**: REQ-002 (containerized voice service)
  - **Verification**: `kubectl create secret docker-registry` succeeds
  - **Command**: `docker build -t voice-service:latest .`
  - **Note**: Push to registry or load on nodes

### 1.3 Model Distribution
- [ ] 1.3.1 Download Piper model to primary node
  - **Requirements**: REQ-003 (model availability)
  - **Verification**: Model exists at `/opt/piper/cache/model.onnx`
  - **Command**: Copy from `/home/pedro/.hermes/cache/piper-voices/model.onnx`
  - **Note**: ~63 MB per voice

- [ ] 1.3.2 Distribute models to all nodes
  - **Requirements**: REQ-003 (model availability)
  - **Verification**: All nodes have model in cache
  - **Command**: `rsync` or `kubectl cp` to all nodes
  - **Note**: Manual sync for initial deployment

- [ ] 1.3.3 Verify model integrity
  - **Requirements**: REQ-003 (model integrity)
  - **Verification**: Model loads successfully in container
  - **Command**: `python -c "import piper_tts; print('OK')"`
  - **Note**: Test on each node

## 2. Deployment

### 2.1 Deploy Voice Service
- [ ] 2.1.1 Apply voice service manifest
  - **Requirements**: REQ-001 (k3s cluster), REQ-002 (containerized voice service)
  - **Verification**: `kubectl get pods` shows voice pods running
  - **Command**: `kubectl apply -f voice-service.yaml`
  - **Note**: Start with 1 replica, scale up later

- [ ] 2.1.2 Verify service endpoints
  - **Requirements**: REQ-001 (k3s cluster)
  - **Verification**: `kubectl get endpoints` shows healthy pods
  - **Command**: `kubectl get svc voice-service`
  - **Note**: Check ClusterIP and NodePort

- [ ] 2.1.3 Test voice synthesis
  - **Requirements**: REQ-001 (k3s cluster), REQ-002 (containerized voice service)
  - **Verification**: Audio file generated successfully
  - **Command**: `kubectl exec -it <voice-pod> -- curl http://localhost:8000/synthesize`
  - **Note**: Test with sample text

### 2.2 Scale Voice Service
- [ ] 2.2.1 Scale to 2 replicas
  - **Requirements**: REQ-001 (k3s cluster)
  - **Verification**: 2 pods running on different nodes
  - **Command**: `kubectl scale deployment voice-service --replicas=2`
  - **Note**: Verify load balancing

- [ ] 2.2.2 Test load balancing
  - **Requirements**: REQ-001 (k3s cluster)
  - **Verification**: Requests routed to different pods
  - **Command**: Send 10 requests, check pod logs
  - **Note**: Round-robin distribution

- [ ] 2.2.3 Configure Horizontal Pod Autoscaler
  - **Requirements**: REQ-001 (k3s cluster)
  - **Verification**: HPA created and scaling works
  - **Command**: `kubectl autoscale deployment voice-service --cpu-percent=70 --min=1 --max=5`
  - **Note**: Monitor CPU usage

## 3. Hermes Integration

### 3.1 Update Hermes Configuration
- [ ] 3.1.1 Configure Hermes TTS provider
  - **Requirements**: REQ-004 (Hermes integration)
  - **Verification**: `hermes config get tts` shows k3s endpoint
  - **Command**: `hermes config set tts.endpoint http://voice-service:8000`
  - **Note**: Internal k8s DNS for Hermes

- [ ] 3.1.2 Test Hermes voice synthesis
  - **Requirements**: REQ-004 (Hermes integration)
  - **Verification**: Audio generated through Hermes
  - **Command**: `hermes tts --text "Hola mundo"`
  - **Note**: End-to-end test

### 3.2 Hermes Fallback Logic
- [ ] 3.1.3 Implement text fallback
  - **Requirements**: REQ-004 (Hermes integration)
  - **Verification**: Text response when voice fails
  - **File**: Update Hermes TTS module
  - **Note**: Graceful degradation

## 4. Monitoring and Observability

### 4.1 Configure Prometheus
- [ ] 4.1.1 Deploy Prometheus
  - **Requirements**: REQ-005 (monitoring)
  - **Verification**: Prometheus pod running
  - **Command**: `kubectl apply -f prometheus.yaml`
  - **Note**: Use k3s metrics-server

- [ ] 4.1.2 Create voice service metrics
  - **Requirements**: REQ-005 (monitoring)
  - **Verification**: Metrics exposed at `/metrics`
  - **File**: Add metrics to voice service
  - **Note**: Request rate, duration, errors

### 4.2 Configure Alerting
- [ ] 4.2.1 Set up alerting rules
  - **Requirements**: REQ-005 (monitoring)
  - **Verification**: Alerts fire on test failures
  - **File**: `/home/pedro/Documentos/Projects/jarvis_project/k3s/alerts.yaml`
  - **Note**: Service down, high latency

### 4.3 Configure Logging
- [ ] 4.3.1 Deploy fluentd
  - **Requirements**: REQ-005 (monitoring)
  - **Verification**: Fluentd pod running
  - **Command**: `kubectl apply -f fluentd.yaml`
  - **Note**: Forward to Elasticsearch or file

## 5. Testing

### 5.1 Unit Tests
- [ ] 5.1.1 Create voice service tests
  - **Requirements**: REQ-006 (testing)
  - **Verification**: All tests pass
  - **File**: `/home/pedro/Documentos/Projects/jarvis_project/k3s/tests/test_voice.py`
  - **Note**: Test synthesis endpoint

- [ ] 5.1.2 Create health check tests
  - **Requirements**: REQ-006 (testing)
  - **Verification**: Health endpoint responds correctly
  - **File**: `/home/pedro/Documentos/Projects/jarvis_project/k3s/tests/test_health.py`
  - **Note**: Test /health endpoint

### 5.2 Integration Tests
- [ ] 5.2.1 End-to-end synthesis test
  - **Requirements**: REQ-006 (testing)
  - **Verification**: Full flow works
  - **File**: `/home/pedro/Documentos/Projects/jarvis_project/k3s/tests/test_e2e.py`
  - **Note**: Hermes → Voice → Audio

- [ ] 5.2.2 Failover test
  - **Requirements**: REQ-006 (testing)
  - **Verification**: Requests reroute on failure
  - **File**: `/home/pedro/Documentos/Projects/jarvis_project/k3s/tests/test_failover.py`
  - **Note**: Kill pod, verify rerouting

### 5.3 Performance Tests
- [ ] 5.3.1 Load test
  - **Requirements**: REQ-006 (testing)
  - **Verification**: Handles expected load
  - **File**: `/home/pedro/Documentos/Projects/jarvis_project/k3s/tests/load_test.py`
  - **Note**: 10 concurrent users, 1000 requests

- [ ] 5.3.2 Latency test
  - **Requirements**: REQ-006 (testing)
  - **Verification**: Latency < 2s
  - **File**: `/home/pedro/Documentos/Projects/jarvis_project/k3s/tests/latency_test.py`
  - **Note**: Measure end-to-end time

### 5.4 Chaos Tests
- [ ] 5.4.1 Node failure test
  - **Requirements**: REQ-006 (testing)
  - **Verification**: Service survives node loss
  - **Tool**: k3s chaos or manual node shutdown
  - **Note**: Test automatic rescheduling

## 6. Documentation

### 6.1 User Documentation
- [ ] 6.1.1 Create deployment guide
  - **Requirements**: REQ-007 (documentation)
  - **Verification**: Guide covers all steps
  - **File**: `/home/pedro/Documentos/Projects/jarvis_project/docs/k3s-deploy.md`
  - **Note**: Step-by-step instructions

- [ ] 6.1.2 Create troubleshooting guide
  - **Requirements**: REQ-007 (documentation)
  - **Verification**: Common issues covered
  - **File**: `/home/pedro/Documentos/Projects/jarvis_project/docs/k3s-troubleshoot.md`
  - **Note**: Error codes, solutions

### 6.2 API Documentation
- [ ] 6.2.1 Document voice API
  - **Requirements**: REQ-007 (documentation)
  - **Verification**: API specs complete
  - **File**: `/home/pedro/Documentos/Projects/jarvis_project/docs/api-voice.md`
  - **Note**: Endpoints, parameters, examples

## 7. Rollback Preparation

### 7.1 Backup Current Setup
- [ ] 7.1.1 Backup voice service manifest
  - **Requirements**: REQ-008 (rollback)
  - **Verification**: Backup file exists
  - **Command**: `kubectl get deployment voice-service -o yaml > voice-service-backup.yaml`
  - **Note**: Store in version control

- [ ] 7.1.2 Backup model files
  - **Requirements**: REQ-008 (rollback)
  - **Verification**: Models backed up
  - **Command**: `rsync -av ~/.hermes/cache/piper-voices/ backup/`
  - **Note**: Store off-cluster

### 7.2 Rollback Procedure
- [ ] 7.2.1 Document rollback steps
  - **Requirements**: REQ-008 (rollback)
  - **Verification**: Steps clear and testable
  - **File**: `/home/pedro/Documentos/Projects/jarvis_project/docs/k3s-rollback.md`
  - **Note**: Step-by-step recovery

## 8. Validation

### 8.1 Acceptance Testing
- [ ] 8.1.1 Verify all requirements met
  - **Requirements**: REQ-001 to REQ-008
  - **Verification**: Checklist complete
  - **Tool**: Manual review against requirements
  - **Note**: Sign-off from stakeholder

- [ ] 8.1.2 Performance validation
  - **Requirements**: REQ-001 (k3s cluster)
  - **Verification**: Meets performance targets
  - **Tool**: Load test results
  - **Note**: Latency < 2s, throughput > target

### 8.2 Security Validation
- [ ] 8.2.1 Security review
  - **Requirements**: REQ-009 (security)
  - **Verification**: No critical vulnerabilities
  - **Tool**: SAST/DAST scan
  - **Note**: Container image scan

- [ ] 8.2.2 Network security
  - **Requirements**: REQ-009 (security)
  - **Verification**: Network policies in place
  - **Command**: `kubectl get networkpolicies`
  - **Note**: Internal traffic only

## Verification Checklist

- [ ] All tasks completed
- [ ] All tests passing
- [ ] Performance targets met
- [ ] Documentation complete
- [ ] Security review passed
- [ ] Stakeholder sign-off
- [ ] No code written during design phase
- [ ] Artifacts ready for implementation
