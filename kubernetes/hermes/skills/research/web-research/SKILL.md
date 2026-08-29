---
name: web-research
description: Conduct web research using Hermes tools and MCP servers
tags: [research, web, internet, search, mcp]
version: 1.1.0
author: Jarvis
license: MIT
---

# Web Research Skill

Conduct internet research using Hermes Agent's web tools, MCP servers, and fallback strategies.

## Quick Start

```bash
# 1. Check available web tools
hermes tools list | grep -E "web|browser|search"

# 2. Install MCP SDK
pip install mcp --break-system-packages

# 3. Configure MCP server
hermes config edit
# Add to ~/.hermes/config.yaml:
mcp_servers:
  brave_search:
    command: "npx"
    args: ["-y", "brave-search-mcp"]
    timeout: 30
```

## Research Methods

| Method | Best For | Setup |
|--------|----------|-------|
| Direct web tools | Quick queries | None |
| MCP servers | Structured data | MCP configured |
| Fallback (curl) | When MCP unavailable | None |

## MCP Server Catalog

| Server | Purpose | Command |
|--------|---------|---------|
| `brave-search-mcp` | General search | `npx -y brave-search-mcp` |
| `tavily-mcp` | Search with context | `npx -y tavily-mcp` |
| `wikipedia-mcp` | Wikipedia articles | `npx -y wikipedia-mcp` |

## Troubleshooting

### "pip: command not found"
Use full venv path:
```bash
/opt/hermes/.venv/bin/python3 -m pip install mcp
```

### MCP tools not appearing
```bash
pip install mcp
hermes config edit  # Verify mcp_servers section
hermes restart
```

## Fallback Strategies

### 1. Direct URL Access
```bash
curl -sL "https://example.com"
```

### 2. API-based Search
```bash
curl -s "https://api.duckduckgo.com/?q=query&format=json" | jq
```

### 3. Jina AI for Articles
```python
import urllib.request
url = 'https://r.jina.ai/URL_TO_ARTICLE'
data = urllib.request.urlopen(url).read().decode()
```

## Best Practices

1. Start broad, then narrow
2. Cross-reference sources
3. Document findings
4. Use structured data
5. Respect robots.txt

## Deep Research Pattern

For comprehensive software/tool discovery (e.g., finding open-source alternatives):

### 1. Multi-source Discovery
```
web_search(query="open source alternative to notion", limit=10)
web_search(query="open source alternative to jira", limit=10)
```

### 2. Batch Extraction
```
web_extract(urls=[
  "https://source1.com",
  "https://source2.com",
  "https://source3.com"
])
```
- Max 5 URLs per call
- Large pages truncated (full text saved to disk)

### 3. GitHub Verification
```
browser_navigate("https://github.com/org/repo")
browser_snapshot()  # Always after navigate
```
- Check 404 errors for org/repo
- Verify license, stars, contributors

### 4. Synthesis
Combine findings into:
- Feature comparison tables
- Installation commands
- Recommendation with rationale

## Kubernetes MCP Troubleshooting

When verifying MCP services in Kubernetes:

### Quick Check
```bash
kubectl get pods -A -o wide
kubectl get pods -A | grep -i <service-name>
```

### Deployment Inspection
```bash
kubectl get deployment -A -o yaml | grep -A 30 <service-name>
kubectl get configmap -A -o yaml | grep -A 30 <service-name>
kubectl get secrets -A | grep -i <service-name>
```

### API Key Verification
```bash
kubectl get deployment <name> -o yaml | grep -i api_key
```

### Interpretation Matrix

| Finding | Meaning | Action |
|---------|---------|--------|
| No pods | Service not deployed | Deploy service |
| API key found | Config exists | Deploy MCP |
| Pod Running | Service active | Use service |

### Common MCP Service Names
- `brave-search-mcp` / `brave-mcp`
- `mcp-*` (generic)
- `<project>-mcp` (custom)

## Advanced Patterns

### Multi-source Research
```yaml
mcp_servers:
  primary:
    command: "npx"
    args: ["-y", "brave-search-mcp"]
  secondary:
    command: "npx"
    args: ["-y", "wikipedia-mcp"]
```

### Batch Research
```bash
cat > research.sh << 'EOF'
#!/bin/bash
for topic in "${topics[@]}"; do
  echo "=== $topic ==="
  curl -s "https://api.duckduckgo.com/?q=$topic&format=json" | jq
done
EOF
```

## References

- [Hermes Web Tools](https://hermes-agent.nousresearch.com/docs/reference/tools-reference)
- [MCP Servers](https://modelcontextprotocol.io/quick-start)
- [Technical Research Fallback](../technical-research-fallback/SKILL.md) for when browser tools fail

## Use Case Example: Quantum Optics Lab Research

The `technical-research-fallback` skill was demonstrated researching components for a home quantum optics laboratory, extracting information from:

- EduQ V1 (GitHub: yallain/EduQ-V1) - Open-source DIY quantum optics platform
- Thorlabs - Single photon detectors and precision optics
- DuckDuckGo API - Component search and pricing
- Jina AI - Article content extraction from search results

This workflow successfully documented 6 component categories with cost estimates ($1,250-$5,300) and setup recommendations.

---

**Created:** 2026-07-27
**Updated:** 2026-07-27 (v1.1 - Added technical-research-fallback reference and use case documentation)
