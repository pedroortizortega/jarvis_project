---
name: mcp-kubernetes-deployment
description: Deploy Model Context Protocol servers on Kubernetes.
author: Jarvis
version: 1.0
tags: [mcp, kubernetes, devops, deployment, troubleshooting]
---

# MCP Kubernetes Deployment

## 🎯 Trigger

Use this skill when deploying, configuring, or troubleshooting Model Context Protocol (MCP) servers on Kubernetes clusters.

**Common scenarios:**
- Deploy a new MCP server (Brave Search, GitHub, Web Search, etc.)
- Create deployment YAML configurations for MCP servers
- Troubleshoot MCP deployment issues (ImagePullBackOff, SecretNotFound, health checks)
- Document MCP activation procedures
- Manage MCP API keys and secrets in Kubernetes

## 📋 Workflow

### 1. Discovery & Research
```bash
# Search for MCP server
web_search("mcp server <service-name> kubernetes deployment")

# Find official GitHub repo
browser_navigate("https://github.com/<mcp-repo>")

# Check Docker images available
web_search("mcp server <name> dockerhub")
```

### 2. YAML Configuration
Create deployment YAML in `kubernetes/mcps/<name>-deployment.yaml`:

**Standard structure:**
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: <mcp-server-name>
  labels:
    app: <mcp-server-name>
    mcp: <mcp-type>
spec:
  replicas: 1
  selector:
    matchLabels:
      app: <mcp-server-name>
  template:
    metadata:
      labels:
        app: <mcp-server-name>
        mcp: <mcp-type>
    spec:
      containers:
      - name: <mcp-server-name>
        image: <registry>/<image>:<tag>
        env:
        - name: <API_KEY>
          valueFrom:
            secretKeyRef:
              name: <secret-name>
              key: <key-name>
        resources:
          requests:
            memory: 128Mi
            cpu: 100m
          limits:
            memory: 512Mi
            cpu: 500m
        livenessProbe:
          httpGet:
            path: /health
            port: 8080
          initialDelaySeconds: 30
          periodSeconds: 10
        readinessProbe:
          httpGet:
            path: /health
            port: 8080
          initialDelaySeconds: 5
          periodSeconds: 5
---
apiVersion: v1
kind: Service
metadata:
  name: <mcp-server-name>-service
spec:
  type: ClusterIP
  ports:
  - port: 8080
    targetPort: 8080
    name: mcp-stdio
  selector:
    app: <mcp-server-name>
---
apiVersion: v1
kind: Secret
metadata:
  name: <secret-name>
type: Opaque
stringData:
  <API_KEY>: "<your-api-key>"
```

### 3. Deployment
```bash
kubectl apply -f kubernetes/mcps/<name>-deployment.yaml
```

### 4. Verification
```bash
# Check deployment status
kubectl get deployment <name>
kubectl get pods -l app=<name>

# Verify health
kubectl logs <pod-name>

# Test endpoint
kubectl exec -it <pod-name> -- curl http://localhost:8080/health
```

### 5. Integration with Hermes

#### Option A: Kubernetes Hermes Deployment
```bash
# Restart Hermes to load new MCP
kubectl rollout restart deployment/hermes-agent
kubectl get pods -l app=hermes-agent

# Verify MCP is loaded
kubectl logs -f <hermes-pod> | grep -i mcp
```

#### Option B: Local Hermes (systemd)
**Scenario:** Hermes runs locally via systemd, not in Kubernetes

**Step 1: Install MCP Server Locally**
```bash
# Download or build MCP server
git clone https://github.com/brave/brave-search-mcp-server
cd brave-search-mcp-server

# Or use Docker
docker pull acuvity/mcp-server-brave-search:latest
```

**Step 2: Configure Hermes**
```bash
# Create .env file with API key
echo "BRAVE_API_KEY=your-key" > ~/.hermes/.env

# Install plugin (if not already installed)
pip install acuvity/mcp-server-brave-search  # if available
# Or clone plugin
git clone https://github.com/rabilrbl/hermes-brave-search-plugin.git ~/.hermes/hermes-agent/plugins/
```

**Step 3: Restart Hermes**
```bash
# Stop current processes
pkill -f "hermes_cli.main"

# Restart with new config
/home/pedro/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main gateway run &

# Verify plugin loaded
pgrep -f "hermes_cli.main"
```

**Alternative: Use Plugin (Recommended for Local Hermes)**
The plugin approach is simpler than running MCP server as subprocess:
- Plugin communicates directly with Brave API
- No need to run MCP server separately
- Easier to manage and debug

## ⚠️ Critical Distinction

| Scenario | MCP Server Location | Hermes Location | Integration Method |
|----------|---------------------|-----------------|-------------------|
| **Kubernetes Hermes** | Kubernetes namespace | Kubernetes namespace | `kubectl rollout restart` |
| **Local Hermes** | Kubernetes (separate) | Local systemd | Use plugin OR connect via stdio |
| **Local Hermes** | Local Docker/standalone | Local systemd | Direct subprocess connection |

**Key Insight:** MCP servers don't need to be in the same namespace/process as Hermes. They can be:
- Remote services (HTTP/SSE)
- Separate Kubernetes deployments (connected via plugin)
- Local subprocesses (stdio mode)
- External APIs (via plugin like hermes-brave-search-plugin)

## ⚠️ Pitfalls

### Common Issues & Solutions

| Issue | Cause | Solution |
|-------|-------|----------|
| `ImagePullBackOff` | Image not found or no pull credentials | Use alternative image (e.g., `acuvity/mcp-server-brave-search`) or create imagePullSecret |
| `SecretNotFound` | Secret doesn't exist or wrong key name | Run `kubectl create secret generic <name> --from-literal=KEY=VALUE` |
| `HealthCheck Failed` | Endpoint not responding | Check logs: `kubectl logs <pod>`, verify `/health` endpoint exists |
| `PermissionDenied` | Namespace permissions | Check: `kubectl auth can-i use <resource> --namespace=<ns>` |
| `Port conflict` | Another service using same port | Change port in YAML or use different service name |

### MCP Transport Modes
- **STDIO** (default): Best for Hermes integration
- **HTTP**: Set `BRAVE_MCP_TRANSPORT=http` for HTTP transport

### Image Alternatives
When official images aren't available:
1. Search Docker Hub for community images
2. Use alternative maintainers (e.g., `acuvity/mcp-server-brave-search`)
3. Build custom image from GitHub repo
4. Use `docker pull` and `kubectl set image` to update deployment

## 📚 References

- **MCP Servers Directory:** https://mcpservers.org/
- **Docker MCP Catalog:** https://hub.docker.com/mcp
- **Brave Search MCP:** https://github.com/brave/brave-search-mcp-server
- **Model Context Protocol Spec:** https://modelcontextprotocol.io/

## 🔗 Related Files

- `templates/mcp-deployment-template.yaml` - Boilerplate YAML for MCP deployments
- `references/mcp-deployment-patterns.md` - Detailed patterns and examples
- `references/local-hermes-mcp-pattern.md` - Pattern for local Hermes + Kubernetes MCP integration
- `scripts/verify-mcp-deployment.sh` - Automated verification script

## 🎓 Pro Tips

1. **Always store YAML in `kubernetes/mcps/`** - Creates consistent, discoverable structure
2. **Use resource limits** - MCP servers are lightweight, don't over-provision
3. **Include health checks** - Essential for production deployments
4. **Document activation steps** - Create markdown guides alongside YAML
5. **Test images before deployment** - `docker pull <image>` locally first
6. **Use secrets for API keys** - Never hardcode in YAML files

## 🛠️ Example Commands

```bash
# Create directory structure
mkdir -p kubernetes/mcps/

# Apply deployment
kubectl apply -f kubernetes/mcps/brave-search-mcp-deployment.yaml

# Check status
kubectl get pods -l app=brave-search-mcp

# Update image
kubectl set image deployment/brave-search-mcp brave-search-mcp=acuvity/mcp-server-brave-search:latest

# Rollout Hermes
kubectl rollout restart deployment/hermes-agent

# Verify MCP loaded
kubectl logs -f hermes-agent -n default | grep -i "mcp\|brave"
```

---

**Version:** 1.0  
**Last Updated:** 2026-07-28  
**Author:** Jarvis (via user direction)