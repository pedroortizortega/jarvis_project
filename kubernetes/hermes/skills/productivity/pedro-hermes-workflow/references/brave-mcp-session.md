# Brave Search MCP Configuration Session (2026-07-28)

## Context

User requested: "Puedes decirme que mcps tienes configurados y que se pueden utilizar actualmente?"

Discovery revealed:
- No MCP servers configured in `~/.hermes/config.yaml`
- MCP SDK installed (`python3 -c "import mcp"` succeeded)
- DuckDuckGo backend (`ddgs`) not working (no API key, DNS resolution failed)
- Brave Search MCP available via `@modelcontextprotocol/server-brave-search`

## Session Flow

### 1. Initial Check
```bash
hermes tools  # No MCP tools found
find ~/.hermes -name "*.json" -o -name "*mcp*"  # No MCP config files
```

### 2. MCP Discovery
- **Brave Search MCP**: ✅ Available (npm package)
- **DuckDuckGo MCP**: ❌ Does not exist
- **DuckDuckGo backend**: ❌ Not working (no API key)

### 3. Configuration Attempt
Attempted to create `/opt/data/configure_brave.py` but failed due to:
```
Permission denied on /opt/data
```

**Root cause**: Containerized environment without write access to `/opt/data`.

### 4. Manual Configuration Required

User needs to:
1. Visit https://api.search.brave.com/app
2. Create free account
3. Copy API key
4. Run manual commands:
```bash
# Save API key
echo "BRAVE_API_KEY=***" > ~/.hermes/.env

# Add to config.yaml
cat >> ~/.hermes/config.yaml << 'EOF'

mcp_servers:
  brave:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-brave-search"]
    env:
      BRAVE_API_KEY: "[REDACTED]"
EOF

# Install and restart
npx -y @modelcontextprotocol/server-brave-search --help
hermes --daemon
```

## Key Learnings

### Permission Constraints
- `write_file` and `patch` only work on `/opt/data`
- Docker container environment: no write access to `/opt/data`
- Use `terminal` with heredoc for file operations in this environment

### Environment Detection
```bash
# Check if Docker or Kubernetes
docker ps -a  # Docker
kubectl get pods  # Kubernetes
# This session: Docker (hostname: trantar)
```

### MCP Configuration Pattern
```yaml
mcp_servers:
  <name>:
    command: "npx"  # or uvx, docker, etc.
    args: ["-y", "@modelcontextprotocol/server-<name>"]
    env:
      <API_KEY>: "[REDACTED]"
```

## Tools Used

- `web_search`: Discovered Brave Search MCP availability
- `terminal`: Environment checks, permission tests
- `execute_code`: Attempted script creation (failed due to permissions)
- `skill_manage`: Created `pedro-hermes-workflow` skill

## Related Sessions

See `pedro-hermes-workflow` skill for general Hermes operations guidance.

## Status

**INCOMPLETE**: Manual configuration required from user due to permission constraints.

Next steps:
1. User obtains Brave API key
2. User runs manual configuration commands
3. Verify MCP server loads: `hermes tools | grep brave`
4. Test search: `hermes web_search "test query"`