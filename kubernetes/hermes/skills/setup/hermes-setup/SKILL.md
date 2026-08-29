---
name: hermes-setup
description: Install and configure Hermes Agent tools and MCP servers
version: 1.0.0
author: Curator
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [hermes, setup, configuration, tools, mcp, installation, troubleshooting]
---

# Hermes Setup & Configuration

This skill covers installation, configuration, tool enablement, and troubleshooting for Hermes Agent setup across platforms.

## Scope

- Installation via shell installer or source
- Configuration file structure and key settings
- Toolset enablement and verification
- MCP server configuration for extended capabilities
- Troubleshooting common setup issues

## Quick Start

### Install Hermes

```bash
# Via shell installer (recommended)
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash

# Verify installation
hermes --version
```

### Verify Installation

```bash
hermes --version
hermes doctor  # Check dependencies and config
```

### Check Available Toolsets

```bash
hermes tools list
```

## Configuration

### Main Config File

Location: `~/.hermes/config.yaml`

Key sections to configure:

| Section | Purpose |
|---------|---------|
| `model` | Default LLM and provider |
| `agent` | Agent behavior settings |
| `tools` | Which toolsets to enable |
| `mcp_servers` | MCP server configurations |
| `display` | UI preferences, skin, language |

### Edit Config

```bash
hermes config edit
# or
hermes config set section.key value
```

## Toolsets

### Available Toolsets

| Toolset | Description |
|---------|-------------|
| `web` / `search` | Web search and extraction |
| `browser` | Browser automation (Browserbase, Camofox, Chromium) |
| `terminal` | Shell commands and process management |
| `file` | File read/write/search/patch |
| `code_execution` | Sandboxed Python execution |
| `vision` | Image analysis |
| `skills` | Skill management |
| `memory` | Persistent cross-session memory |

### Enable Toolsets

```bash
hermes tools enable NAME
hermes tools enable web,search  # Enable multiple
```

Tool changes take effect on `/reset` (new session).

### MCP Servers (Model Context Protocol)

### What is MCP

MCP allows Hermes to connect to external servers and use their tools natively. This is the recommended way to extend Hermes with capabilities like web search, GitHub access, database queries, etc.

### MCP Server Configuration Patterns

#### Pattern 1: Local Configuration (Default)

For local development or when Hermes runs on the same machine:

Add to `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  server_name:
    command: "npx"
    args: ["-y", "mcp-server-package-name"]
    env:
      API_KEY: "your-key-here"
    timeout: 120
```

Or for HTTP servers:

```yaml
mcp_servers:
  remote_api:
    url: "https://mcp.example.com/mcp"
    headers:
      Authorization: "Bearer sk-..."
```

#### Pattern 2: Kubernetes MCP Servers (Production/Cluster)

For MCP servers running in Kubernetes clusters, use dedicated deployment YAML files in `kubernetes/mcps/`:

```bash
# Directory structure
kubernetes/mcps/
├── brave-search-mcp-deployment.yaml
├── brave-mcp-activation-guide.md
├── other-mcp-deployment.yaml
└── other-mcp-activation-guide.md
```

**Example deployment structure:**

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
            path: /metrics  # IMPORTANT: Use /metrics, not /health
            port: 8080
          initialDelaySeconds: 30
          periodSeconds: 10
        readinessProbe:
          httpGet:
            path: /metrics  # IMPORTANT: Use /metrics, not /health
            port: 8080
          initialDelaySeconds: 5
          periodSeconds: 5
---
apiVersion: v1
kind: Service
metadata:
  name: brave-search-mcp-service
  labels:
    app: brave-search-mcp
    mcp: brave-search
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

**Apply the deployment:**

```bash
kubectl apply -f kubernetes/mcps/brave-search-mcp-deployment.yaml
kubectl rollout restart deployment/hermes-agent
```

**Verify the MCP is running:**

```bash
kubectl get pods -l app=brave-search-mcp
kubectl logs brave-search-mcp-xxxxx --tail=30
kubectl exec brave-search-mcp-xxxxx -- sh -c "curl -s http://localhost:8080/metrics"
```

**Common Kubernetes MCP Issues:**

| Issue | Cause | Fix |
|-------|-------|-----|
| `ImagePullBackOff` | Image not found or no pull secrets | Check `kubectl describe pod` for image pull errors. Use `imagePullPolicy: Always` for private registries. |
| `CrashLoopBackOff` | Health check endpoint wrong | **Use `/metrics` not `/health`** for MCP servers. Check `kubectl logs` for 404 errors. |
| `404 on health check` | Wrong endpoint path | MCP servers expose `/metrics` for health, not `/health`. Update `livenessProbe` and `readinessProbe` accordingly. |
| `Secret not found` | Secret doesn't exist | Create secret first: `kubectl create secret generic <name> --from-literal=KEY=VALUE` |
| `Container not found` | Wrong container name | Use `kubectl exec <pod-name> -- <command>` not `kubectl exec <container-name>` |

**Available MCP Server Images:**

| Server | Image | Notes |
|--------|-------|-------|
| Brave Search | `acuvity/mcp-server-brave-search:latest` | **Recommended** - STDIO mode |
| Brave Search (official) | `ghcr.io/brave/brave-search-mcp-server:latest` | May not exist, use acuvity alternative |
| Tavily Search | `acuvity/mcp-server-tavily-search:latest` | STDIO mode |
| GitHub | `ghcr.io/modelcontextprotocol/server-github:latest` | STDIO mode |

### Recommended MCP Servers for Web Access

| Server | Use Case | Command (Local) | Kubernetes Image |
|--------|----------|-----------------|------------------|
| `search1-mcp` | General web search | `npx -y search1-mcp` | TBD |
| `brave-search-mcp` | Search via Brave | `npx -y brave-search-mcp` | `acuvity/mcp-server-brave-search:latest` |
| `tavily-mcp` | Search with context | `npx -y tavily-mcp` | TBD |
| `google-mcp` | Google search | `npx -y google-mcp` | TBD |

### Verify MCP Server

After adding to config or deploying in Kubernetes, restart Hermes. Available tools will appear with `mcp_<server>_<tool>` prefix.

Example: `mcp_brave_search_brave_search`, `mcp_brave_search_brave_images`.

---

### Install MCP SDK

```bash
pip install mcp
# or with uv
uv pip install mcp
```

---

### Configure MCP Server

Add to `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  server_name:
    command: "npx"
    args: ["-y", "mcp-server-package-name"]
    env:
      API_KEY: "your-key-here"
    timeout: 120
```

Or for HTTP servers:

```yaml
mcp_servers:
  remote_api:
    url: "https://mcp.example.com/mcp"
    headers:
      Authorization: "Bearer sk-..."
```

---

### Recommended MCP Servers for Web Access

| Server | Use Case | Command |
|--------|----------|---------|
| `search1-mcp` | General web search | `npx -y search1-mcp` |
| `brave-search-mcp` | Search via Brave | `npx -y brave-search-mcp` |
| `tavily-mcp` | Search with context | `npx -y tavily-mcp` |
| `google-mcp` | Google search | `npx -y google-mcp` |

---

### Verify MCP Server

After adding to config, restart Hermes. Available tools will appear with `mcp_<server>_<tool>` prefix.

Example: `mcp_search1_search`, `mcp_search1_extract`.

## Installation Scenarios

### Scenario 1: Hermes Already Running (Telegram/Gateway)

If Hermes is already running via your gateway (Telegram, Discord, etc.), you don't need to install it locally. Configuration changes are stored in `~/.hermes/config.yaml` and apply across all platforms.

To add MCP servers or enable tools:
1. Edit `~/.hermes/config.yaml`
2. Restart Hermes gateway: `hermes gateway restart`
3. Or simply wait for auto-reconnect (usually 1-2 minutes)

### Scenario 2: Fresh Installation

If you need Hermes locally:

```bash
# Install
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash

# Verify
hermes --version

# Run
hermes
```

### Scenario 3: Source Installation

For development or custom modifications:

```bash
git clone https://github.com/NousResearch/hermes-agent.git
cd hermes-agent
pip install -e .
```

## Troubleshooting

### "hermes: command not found"

**Cause**: Installation incomplete or PATH not updated.

**Fix**:
```bash
# Check installation directory
ls -la ~/.local/bin/

# Source PATH if needed
export PATH="$HOME/.local/bin:$PATH"

# Or reinstall
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
```

### Gateway: "No messaging platforms enabled"

**Cause**: The systemd service for `hermes-gateway` does not load `~/.hermes/.env`, so platform credentials (e.g., `TELEGRAM_TOKEN`) are never available to the gateway process.

**Diagnostic steps**:
1. Check gateway status: `hermes gateway status`
2. Check gateway logs: `grep -i "platform\|enabled\|telegram\|connected" ~/.hermes/logs/gateway.log | tail -20`
3. Verify credentials are in `.env`: `grep -i telegram ~/.hermes/.env`
4. Check if the systemd unit loads `.env`: `systemctl cat hermes-gateway` — look for `EnvironmentFile=`

**Fix**: Add `EnvironmentFile` to the systemd unit so credentials from `.env` are loaded at startup:
```bash
# Add EnvironmentFile to the service unit (place it after the existing Environment= lines)
sudo sed -i '/^Environment="HERMES_HOME=/a EnvironmentFile=/home/pedro/.hermes/.env' /etc/systemd/system/hermes-gateway.service

# Reload systemd and restart
sudo systemctl daemon-reload
sudo systemctl restart hermes-gateway
```

**Also required**: Set `TELEGRAM_ALLOWED_USERS` in `.env` to whitelist your Telegram user ID:
```bash
# Find your Telegram chat ID from gateway logs:
grep "inbound message" ~/.hermes/logs/gateway.log | head -5

# Add to .env:
echo 'TELEGRAM_ALLOWED_USERS=6594180235' >> ~/.hermes/.env
```

**Important**: You **cannot restart the gateway from inside the gateway process** — all child processes receive SIGTERM when the gateway restarts. Always run `hermes gateway restart` or `sudo systemctl restart hermes-gateway` from a separate shell outside the running gateway.

### "MCP SDK not available"

**Cause**: `mcp` Python package not installed.

**Fix**:
```bash
pip install mcp
# Restart Hermes
hermes gateway restart
```

### "Failed to connect to MCP server"

**Common causes**:
- Command not found (`npx`, `uvx`)
- Package not found
- Network issues for HTTP servers
- Timeout during startup

**Fix**:
```bash
# Install Node.js/npm for npx
sudo apt install nodejs npm  # or via brew on macOS

# Or use uvx for Python-based servers
pip install uv

# Increase timeout in config.yaml
mcp_servers:
  my_server:
    timeout: 300  # seconds
```

### Tools not appearing after config change

**Cause**: Tool changes only apply on new session.

**Fix**:
```bash
# In Hermes chat, type:
/reset
```

### "MCP server requires HTTP transport but mcp.client.streamable_http is not available"

**Cause**: Outdated `mcp` package.

**Fix**:
```bash
pip install --upgrade mcp
```

## Best Practices

1. **Always backup config before major changes**:
   ```bash
   cp ~/.hermes/config.yaml ~/.hermes/config.yaml.backup
   ```

2. **Test MCP servers one at a time** to identify configuration issues

3. **Use environment variables for secrets** (`.env` file), not in `config.yaml`:
   ```yaml
   mcp_servers:
     github:
       command: "npx"
       args: ["-y", "@modelcontextprotocol/server-github"]
       env:
         GITHUB_PERSONAL_ACCESS_TOKEN: "${GITHUB_TOKEN}"
   ```

4. **Start with minimal toolsets** and enable only what you need

5. **Check logs for errors**:
   ```bash
   hermes logs errors
   ```

## Reference Files

- `references/mcp-servers.md` - Catalog of available MCP servers
- `references/toolsets.md` - Detailed toolsets descriptions
- `templates/config-example.yaml` - Example config.yaml
- `scripts/install-check.sh` - Installation verification script
- `references/container-filesystem.md` - Container filesystem and write permissions

## Container Filesystem Notes

When working in a containerized Hermes environment:

### Working Directory Distinction

| Path | Description | Write Access |
|------|-------------|--------------|
| `/opt/data` | Container-local data directory | ✅ Limited (HERMES_WRITE_SAFE_ROOT) |
| `/home/pedro` | User's actual home directory | ❌ May require SSH/terminal |
| `~/.hermes` | User config (relative path) | ✅ Auto-resolved to `~/.hermes` |

### Tool Limitations

The `write_file` and `patch` tools operate on the **container-local filesystem**. If your target path is not mounted in the container (e.g., `/home/pedro`), these tools will fail with permission errors.

**Solution**: Use `terminal` commands instead for paths outside the container:

```bash
# ❌ Won't work (container-local)
write_file path="/home/pedro/.hermes/config.yaml" ...

# ✅ Use terminal heredoc instead
cat > ~/.hermes/config.yaml << 'EOF'
...
EOF
```

### Environment Detection

Always verify the actual environment:

```bash
hostname  # Check if Docker, K8s, or bare metal
pwd       # Confirm working directory
ls -la ~/.hermes/  # Verify config location
```

## Best Practices

1. **Always backup config before major changes**:
   ```bash
   cp ~/.hermes/config.yaml ~/.hermes/config.yaml.backup
   ```

2. **Test MCP servers one at a time** to identify configuration issues

3. **Use environment variables for secrets** (`.env` file), not in `config.yaml`:
   ```yaml
   mcp_servers:
     github:
       command: "npx"
       args: ["-y", "@modelcontextprotocol/server-github"]
       env:
         GITHUB_PERSONAL_ACCESS_TOKEN: "${GITHUB_TOKEN}"
   ```

4. **Start with minimal toolsets** and enable only what you need

5. **Check logs for errors**:
   ```bash
   hermes logs errors
   ```

6. **Respect container filesystem boundaries**: Use `terminal` for paths outside `/opt/data`