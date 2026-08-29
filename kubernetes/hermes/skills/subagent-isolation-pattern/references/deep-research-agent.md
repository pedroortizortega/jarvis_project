# Deep Research Agent Configuration

This document describes the configuration for `deep-research-agent`, a subagent with strict isolation.

## Target Configuration

```
master agent
└── deep-research-agent
    ├── profile: terra-medium
    ├── tools: web-search, brave MCP
    └── skill: research-workflow
```

## Implementation Steps

### 1. Create Profile Directory

```bash
mkdir -p ~/hermes/profiles/deep-research
cd ~/hermes/profiles/deep-research
```

### 2. Configure Model (config.yaml)

```yaml
model:
  provider: custom
  model: terra-medium
```

### 3. Restrict Tools (tools.yaml)

```yaml
tools:
  - name: web-search
    enabled: true
  - name: brave-mcp
    enabled: true
  - name: *default
    enabled: false
```

### 4. Load Research Workflow Skill

Create `skills.yaml`:

```yaml
skills:
  - research-workflow
```

### 5. Deploy

```bash
hermes run --profile deep-research
```

## Usage Pattern

### Master Agent Workflow

1. **Small task**: Use `deep-research` skill directly (quick, single query)
   ```
   "Search for recent advances in LLM optimization"
   ```

2. **Autonomous research**: Delegate to `deep-research-agent` (multi-step, long)
   ```
   "Conduct comprehensive research on LLM optimization, including:
   - Survey recent papers from last 6 months
   - Compare methodologies across 3+ approaches
   - Identify open challenges
   - Produce executive summary with citations"
   ```

### Research Workflow Skill

When delegated, the subagent:

1. **Formulates query** - Breaks down research goals into searchable questions
2. **Cross-references sources** - Verifies claims across multiple sources
3. **Cites properly** - Uses `[Source: URL, Date, Authority Level]` format
4. **Structures findings** - Produces organized report with:
   - Executive summary
   - Methodology
   - Key findings
   - Evidence with citations
   - Limitations
   - Recommendations

## Benefits of This Configuration

- **Isolation**: No access to file system, terminal, or deployment tools
- **Predictable**: Fixed model (terra-medium) ensures consistent behavior
- **Focused**: Only web-based research tools, no distractions
- **Secure**: No credential exposure risk

## When to Use

| Scenario | Recommended |
|----------|-------------|
| Quick fact-check (minutes) | Direct skill use |
| Multi-step research (hours) | Subagent delegation |
| Long-term monitoring (days) | Cron job with subagent |
| Multiple concurrent research tasks | Multiple isolated profiles |

## Verification

Test the configuration by:

1. Run subagent with simple query
2. Verify ONLY web-search and Brave MCP were called
3. Confirm no file system or terminal access
4. Check model output matches terra-medium characteristics

---

**Related**: `subagent-isolation-pattern`, `research-workflow`, `deep-research`