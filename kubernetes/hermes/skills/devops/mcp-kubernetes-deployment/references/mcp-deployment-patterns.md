# MCP Deployment Patterns & Best Practices

## Overview

This document covers proven patterns for deploying Model Context Protocol (MCP) servers on Kubernetes, based on real-world experience with Brave Search, GitHub, and other MCP servers.

---

## Deployment Patterns

### 1. Standard Deployment (Recommended)

**Use case:** Most MCP servers that need persistent availability

**Key characteristics:**
- Single replica with auto-recovery
- Resource limits to prevent runaway containers
- Health checks for automatic restart
- Secret management for API keys

**Example:**
```yaml
# See: kubernetes/mcps/brave-search-mcp-deployment.yaml
```

### 2. Multi-Replica Deployment

**Use case:** High-traffic MCP servers requiring redundancy

**When to use:**
- MCP servers with high request volume
- Mission-critical integrations
- Long-running operations

**Configuration:**
```yaml
spec:
  replicas: 3  # Increase from 1 to 3
  # Add podDisruptionBudget for high availability
```

### 3. Namespace-Isolated Deployment

**Use case:** Multiple MCP servers that shouldn't interfere

**Benefit:**
- Easier debugging and troubleshooting
- Resource quota enforcement
- Security isolation

**Implementation:**
```bash
kubectl create namespace mcp-servers
kubectl apply -f kubernetes/mcps/brave-search-mcp-deployment.yaml -n mcp-servers
```

---

## Troubleshooting Patterns

### Pattern 1: ImagePullBackOff

**Symptoms:**
```
STATUS: ImagePullBackOff
ERROR: failed to pull image
```

**Root causes:**
- Image doesn't exist in registry
- No pull credentials configured
- Network connectivity issues

**Solutions:**
```bash
# 1. Check image exists
docker pull <image>:<tag>

# 2. Use alternative image (community maintained)
kubectl set image deployment/<name> <container>=acuvity/mcp-server-brave-search:latest

# 3. Create image pull secret
kubectl create secret docker-registry <secret> \
  --docker-server=<registry> \
  --docker-username=<user> \
  --docker-password=<pass>

# 4. Add to deployment
imagePullSecrets:
- name: <secret>
```

### Pattern 2: SecretNotFound

**Symptoms:**
```
ERROR: secret "<name>" not found
```

**Solutions:**
```bash
# 1. Create the secret
kubectl create secret generic <secret-name> \
  --from-literal=API_KEY="your-api-key" \
  --from-literal=API_SECRET="your-api-secret"

# 2. Verify it exists
kubectl get secret <secret-name> -o yaml

# 3. Update deployment to reference correct secret name
```

### Pattern 3: HealthCheck Failed

**Symptoms:**
```
STATUS: CrashLoopBackOff
ERROR: Liveness probe failed
```

**Root causes:**
- `/health` endpoint doesn't exist
- Endpoint returns wrong status code
- Port not accessible

**Solutions:**
```bash
# 1. Check what endpoints the MCP server exposes
kubectl exec -it <pod> -- curl -v http://localhost:8080/

# 2. If no /health endpoint, modify deployment:
livenessProbe:
  exec:
    command: ["curl", "-f", "http://localhost:8080/api/v1/status"]
  # or use a simple check
  command: ["curl", "-f", "http://localhost:8080"]

# 3. Adjust probe timing for slow-starting servers
initialDelaySeconds: 60  # Increase from 30
periodSeconds: 30        # Decrease from 10
```

### Pattern 4: CrashLoopBackOff

**Symptoms:**
```
STATUS: CrashLoopBackOff
RESTARTS: 10+
```

**Root causes:**
- Application crashes on startup
- Missing dependencies
- Environment variable issues
- Port conflicts

**Solutions:**
```bash
# 1. Check logs
kubectl logs <pod> --previous

# 2. Check for missing env vars
kubectl exec <pod> -- env | grep -i mcp

# 3. Verify port is not in use
kubectl get svc -A | grep 8080

# 4. Add more resources
resources:
  requests:
    memory: 256Mi  # Increase from 128Mi
    cpu: 250m      # Increase from 100m
  limits:
    memory: 1Gi
    cpu: 1000m
```

---

## Integration Patterns with Hermes Agent

### Pattern 1: Standard MCP Integration

**Steps:**
1. Deploy MCP server
2. Restart Hermes Agent
3. Verify MCP is loaded
4. Test through conversation

**Commands:**
```bash
# Deploy
kubectl apply -f kubernetes/mcps/brave-search-mcp-deployment.yaml

# Wait for deployment
kubectl rollout status deployment/brave-search-mcp

# Restart Hermes
kubectl rollout restart deployment/hermes-agent

# Wait for Hermes to restart
kubectl rollout status deployment/hermes-agent

# Verify MCP loaded
kubectl logs -f <hermes-pod> | grep -i "mcp\|brave"
```

### Pattern 2: Incremental Testing

**For safe testing without disrupting production:**

1. **Deploy to test namespace first:**
```bash
kubectl create namespace mcp-test
kubectl apply -f kubernetes/mcps/brave-search-mcp-deployment.yaml -n mcp-test
```

2. **Test independently:**
```bash
kubectl run -it --rm --namespace mcp-test test-cli --image=alpine \
  --command -- sleep 3600
```

3. **Verify MCP functionality:**
```bash
kubectl exec -it <test-pod> -- \
  curl http://brave-search-mcp-service:8080/mcp/list
```

4. **If successful, promote to production:**
```bash
kubectl apply -f kubernetes/mcps/brave-search-mcp-deployment.yaml
kubectl delete namespace mcp-test
```

---

## Performance Optimization Patterns

### Pattern 1: Resource Right-Sizing

**Start conservative, scale up if needed:**

```yaml
resources:
  requests:
    memory: 128Mi    # Start small
    cpu: 100m
  limits:
    memory: 512Mi    # Cap to prevent runaway
    cpu: 500m
```

**Scale up based on monitoring:**
```bash
kubectl top pods -l app=brave-search-mcp
# If consistently using 400Mi memory, increase to 512Mi
```

### Pattern 2: Startup Optimization

**For slow-starting MCP servers:**

```yaml
livenessProbe:
  initialDelaySeconds: 60  # Wait 60s before first check
  periodSeconds: 15        # Check every 15s

readinessProbe:
  initialDelaySeconds: 30  # Wait 30s before traffic
  periodSeconds: 10        # Check every 10s
```

### Pattern 3: Auto-Scaling (Advanced)

**For variable workloads:**

```yaml
spec:
  replicas: 1
  minReadySeconds: 30
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 0
```

---

## Security Patterns

### Pattern 1: Secret Management

**Best practice: Never hardcode secrets in YAML**

```yaml
# ❌ BAD - Hardcoded secret
env:
- name: BRAVE_API_KEY
  value: "secret-key-12345"

# ✅ GOOD - Reference Kubernetes Secret
env:
- name: BRAVE_API_KEY
  valueFrom:
    secretKeyRef:
      name: brave-api-key-secret
      key: BRAVE_API_KEY
```

### Pattern 2: Network Policies

**Restrict MCP server access:**

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: restrict-brave-mcp
spec:
  podSelector:
    matchLabels:
      app: brave-search-mcp
  policyTypes:
  - Ingress
  ingress:
  - from:
    - namespaceSelector:
        matchLabels:
          name: hermes-agent
    ports:
    - protocol: TCP
      port: 8080
```

### Pattern 3: RBAC for MCP Access

**Restrict who can manage MCP deployments:**

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: mcp-manager
rules:
- apiGroups: ["apps"]
  resources: ["deployments"]
  verbs: ["get", "list", "watch", "create", "update", "patch"]
```

---

## Monitoring & Observability

### Pattern 1: Log Aggregation

**Centralize MCP server logs:**

```yaml
apiVersion: logging.k8s.io/v1
kind: ElasticsearchOutput
metadata:
  name: elasticsearch-output
spec:
  hosts:
  - https://elasticsearch.example.com:9200
```

### Pattern 2: Metrics Collection

**Enable Prometheus metrics:**

```yaml
spec:
  containers:
  - name: brave-search-mcp
    args:
    - "--metrics"
    - "--metrics.port=9090"
    - "--metrics.path=/metrics"
```

### Pattern 3: Health Dashboard

**Create a monitoring dashboard:**

```bash
# Quick health check script
kubectl get pods -l app=brave-search-mcp -o jsonpath='{.items[*].status.conditions[?(@.type=="Ready")].status}'
# Output: True (healthy) or False (unhealthy)
```

---

## Migration Patterns

### Pattern 1: Blue-Green Deployment

**Zero-downtime updates:**

1. Deploy new version to separate namespace
2. Test thoroughly
3. Switch traffic to new version
4. Clean up old version

### Pattern 2: Rolling Update

**Standard Kubernetes update:**

```yaml
spec:
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 0
```

---

## Quick Reference

| Task | Command |
|------|---------|
| Deploy MCP | `kubectl apply -f kubernetes/mcps/<name>-deployment.yaml` |
| Check status | `kubectl get pods -l app=<name>` |
| View logs | `kubectl logs <pod>` |
| Restart MCP | `kubectl rollout restart deployment/<name>` |
| Update image | `kubectl set image deployment/<name> <container>=<image>:<tag>` |
| Verify Hermes | `kubectl logs -f <hermes-pod> \| grep -i mcp` |
| Create secret | `kubectl create secret generic <name> --from-literal=KEY=VALUE` |
| Delete MCP | `kubectl delete -f kubernetes/mcps/<name>-deployment.yaml` |

---

**Document Version:** 1.0  
**Last Updated:** 2026-07-28  
**Author:** Jarvis (via user direction)