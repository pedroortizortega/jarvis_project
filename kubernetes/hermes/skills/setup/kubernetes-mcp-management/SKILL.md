---
name: kubernetes-mcp-management
description: Deploy and troubleshoot MCP servers in Kubernetes
version: 1.0.0
author: Curator
license: MIT
platforms: [linux, macos, windows]
metadata:
  kubernetes:
    tags: [kubernetes, mcp, deployment, troubleshooting]
---

# Kubernetes MCP Management

Deploy, configure, and troubleshoot MCP servers in Kubernetes clusters.

## Quick Start

### Create Deployment YAML

Create YAML in `kubernetes/mcps/<name>-deployment.yaml`:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: brave-search-mcp
  labels:
    app: brave-search-mcp
    mcp: brave-search
spec:
  replicas: 1
  selector:
    matchLabels:
      app: brave-search-mcp
  template:
    metadata:
      labels:
        app: brave-search-mcp
        mcp: brave-search
    spec:
      containers:
      - name: brave-search-mcp
        image: acuvity/mcp-server-brave-search:latest
        imagePullPolicy: Always
        env:
        - name: BRAVE_API_KEY
          valueFrom:
            secretKeyRef:
              name: brave-api-key-secret
              key: BRAVE_API_KEY
        - name: BRAVE_MCP_TRANSPORT
          value: "stdio"
        ports:
        - containerPort: 8080
          name: mcp-stdio
        resources:
          requests:
            memory: 128Mi
            cpu: 100m
          limits:
            memory: 512Mi
            cpu: 500m
        livenessProbe:
          httpGet:
            path: /metrics  # CRITICAL: Use /metrics NOT /health
            port: 8080
          initialDelaySeconds: 30
          periodSeconds: 10
        readinessProbe:
          httpGet:
            path: /metrics  # CRITICAL: Use /metrics NOT /health
            port: 8080
          initialDelaySeconds: 5
          periodSeconds: 5
---
apiVersion: v1
kind: Service
metadata:
  name: brave-search-mcp-service
spec:
  type: ClusterIP
  ports:
  - port: 8080
    targetPort: 8080
    name: mcp-stdio
  selector:
    app: brave-search-mcp
---
apiVersion: v1
kind: Secret
metadata:
  name: brave-api-key-secret
type: Opaque
stringData:
  BRAVE_API_KEY: "your-api-key"
```

### Apply and Verify

```bash
kubectl apply -f kubernetes/mcps/<name>-deployment.yaml
kubectl rollout restart deployment/hermes-agent
kubectl get pods -l app=<name>
kubectl logs <pod-name> --tail=30
kubectl exec <pod-name> -- curl -s http://localhost:8080/metrics
```

## MCP Server Images

| Server | Image | Notes |
|--------|-------|-------|
| Brave Search | `acuvity/mcp-server-brave-search:latest` | **Recommended** |
| Tavily Search | `acuvity/mcp-server-tavily-search:latest` | STDIO mode |
| GitHub | `ghcr.io/modelcontextprotocol/server-github:latest` | STDIO mode |

## Common Issues

### CrashLoopBackOff - 404 on health check

**Cause:** MCP servers use `/metrics` not `/health` for health checks.

**Fix:**
```yaml
livenessProbe:
  httpGet:
    path: /metrics  # NOT /health
    port: 8080
readinessProbe:
  httpGet:
    path: /metrics  # NOT /health
    port: 8080
```

### ImagePullBackOff

**Cause:** Image not found or no pull secrets.

**Fix:**
```bash
docker pull <image-name>:<tag>
kubectl set image deployment/<mcp> <container>=<image>:<tag>
```

### Secret Not Found

**Fix:**
```bash
kubectl create secret generic <name> --from-literal=API_KEY=value
```

### Container Not Found

**Cause:** Using container name instead of pod name.

**Fix:** Use `kubectl exec <pod-name>` not `kubectl exec <container-name>`

## Directory Structure

```
kubernetes/mcps/
├── <name>-deployment.yaml
├── <name>-activation-guide.md
└── <name>-troubleshooting.md
```

## Verification Checklist

- [ ] Pod: `READY 1/1, STATUS Running`
- [ ] Health endpoint: `curl http://localhost:8080/metrics` returns 200
- [ ] No errors in logs
- [ ] Hermes restarted: `kubectl rollout restart deployment/hermes-agent`
- [ ] Tools appear in Hermes

## Integration with Hermes

Restart Hermes after deploying new MCP servers:

```bash
kubectl rollout restart deployment/hermes-agent
```

Verify tools appear:

```bash
hermes tools list | grep mcp
```