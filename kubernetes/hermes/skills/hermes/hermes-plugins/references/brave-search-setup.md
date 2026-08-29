# Brave Search Plugin Setup

This reference document covers the Brave Search plugin installation and configuration for Hermes Agent.

## Installation

### Step 1: Clone Plugin Repository
```bash
git clone https://github.com/Rabilrbl/hermes-brave-search-plugin.git ~/.hermes/hermes-agent/plugins/hermes-brave-search-plugin
```

### Step 2: Get Brave API Key
1. Visit: https://api-dashboard.search.brave.com/app/keys
2. Create a new API key
3. Copy the key

### Step 3: Configure API Key
```bash
echo "BRAVE_API_KEY=your-api-key" >> ~/.hermes/.env
```

### Step 4: Enable Plugin
```bash
hermes plugins enable brave-search
```

**Expected Output:**
```
✓ Plugin brave-search enabled. Takes effect on next session.
```

### Step 5: Restart Gateway
```bash
hermes gateway restart
```

## Verification

### Check Plugin Status
```bash
hermes plugins list | grep brave-search
```

**Expected Output:**
```
│ brave-search         │ enabled     │ 0.1.0   │ Brave Search API    │ bundled │
```

### Check Tools Available
```bash
hermes tools list | grep brave
```

**Expected Output:**
```
│ brave_web_search │ brave-search │ 🦁 │ Search the web with Brave Search API. │
```

## Usage

### In Telegram/Discord/Other Platforms

Simply ask Hermes to search the web:

```
"Search the web for [your query]"
"Investiga [tema]"
"What are the latest news about [topic]?"
```

Hermes will automatically use the `brave_web_search` tool when web search is needed.

### In Skills

No manual invocation needed. The plugin is automatically available:

```python
def research_trend(skill_ctx):
    # Hermes will automatically use brave_web_search when needed
    ...
```

## Plugin Structure

```
hermes-brave-search-plugin/
├── __init__.py          # Tool registration
│   - TOOLSET_NAME = "brave-search"
│   - register() → ctx.register_tool()
├── plugin.yaml          # Plugin metadata
│   - name: brave-search
│   - version: 0.1.0
│   - requires_env: BRAVE_API_KEY
├── tools.py             # Tool implementations
│   - brave_web_search()
│   - check_brave_api_key()
└── schemas.py           # JSON schemas
    - BRAVE_WEB_SEARCH schema
```

## Available Tools

| Tool Name | Toolset | Emoji | Description |
|-----------|---------|-------|-------------|
| brave_web_search | brave-search | 🦁 | Search the web with Brave Search API |

## Tool Capabilities

The Brave Search API provides:

- ✅ **Web Search** — General web queries
- ✅ **Image Search** — Find images
- ✅ **News Search** — Latest news articles
- ✅ **Video Search** — Video content
- ✅ **Local Business** — Local business information
- ✅ **LLM Context** — Context-aware responses
- ✅ **AI Summary** — Intelligent summaries

## Troubleshooting

### Plugin Not Loading

1. **Check if enabled:**
   ```bash
   hermes plugins list | grep brave-search
   ```

2. **Enable if needed:**
   ```bash
   hermes plugins enable brave-search
   ```

3. **Restart gateway:**
   ```bash
   hermes gateway restart
   ```

### API Key Issues

1. **Check .env file:**
   ```bash
   cat ~/.hermes/.env | grep BRAVE_API_KEY
   ```

2. **Verify key is present:**
   ```bash
   echo $BRAVE_API_KEY | wc -c
   ```
   Should be > 20 characters

3. **Restart gateway:**
   ```bash
   hermes gateway restart
   ```

### Tool Not Appearing in Chat

1. **Check gateway logs:**
   ```bash
   tail -f ~/.hermes/logs/gateway.log
   ```

2. **Look for errors:**
   - "Missing environment variable"
   - "API key validation failed"
   - "Plugin not enabled"

3. **Fix and restart:**
   ```bash
   hermes gateway restart
   ```

## Integration with Kubernetes MCP

**Note:** This plugin uses the Brave API directly, NOT the Kubernetes MCP deployment.

- **Kubernetes MCP** (`brave-search-mcp`): Runs as a container, uses STDIO transport
- **Hermes Plugin** (`brave-search`): Uses Brave API directly via HTTP

Both can coexist, but Hermes uses the plugin for tool calls, not the Kubernetes MCP.

## Best Practices

### 1. API Key Security
- Never commit `.env` to version control
- Use `.gitignore` for `~/.hermes/.env`
- Rotate API keys periodically

### 2. Error Handling
- Always validate API key before making requests
- Handle rate limits gracefully
- Implement retry logic for transient failures

### 3. Rate Limits
- Brave API has rate limits (check dashboard)
- Implement exponential backoff
- Cache results when appropriate

### 4. Monitoring
- Check gateway logs regularly
- Monitor API usage in Brave dashboard
- Set up alerts for API key expiration

## See Also

- [Hermes Plugins Reference](plugins.md) — General plugin documentation
- [Brave API Dashboard](https://api-dashboard.search.brave.com/app/keys) — API key management
- [Brave Search API Docs](https://api.search.brave.com/) — API documentation
- [Hermes Agent Docs](https://hermes-agent.nousresearch.com/docs/) — Complete documentation
