---
name: subagent-isolation-pattern
description: Isolate subagents with restricted tools for research.
version: 1.0.0
author: Jarvis
license: MIT
tags: [subagents, isolation, autonomous, research, tools]
---

# Subagent Isolation Pattern

Create autonomous subagents with **strict tool and model constraints** for specialized, long-running tasks.

## Use Cases

- **Deep research** with only web search + MCP tools
- **API-specific agents** that shouldn't touch file system or terminal
- **Sandboxed experiments** with controlled capabilities
- **Long-running background work** with predictable resource usage

## Architecture

```
Master Agent
└── Subagent (isolated)
    ├── Profile: <custom> (fixed model)
    ├── Tools: [restricted list]
    └── Skills: [domain-specific]
```

## Implementation Options

### Option A: Profile-based isolation (recommended)

Create a dedicated Hermes profile with restricted tools.

**Steps:**

1. **Create profile directory**
```bash
mkdir -p ~/hermes/profiles/deep-research
cd ~/hermes/profiles/deep-research
```

2. **Configure model (config.yaml)**
```yaml
model:
  provider: custom
  model: terra-medium
```

3. **Restrict tools (tools.yaml)**
```yaml
tools:
  - name: web-search
    enabled: true
  - name: brave-mcp
    enabled: true
  - name: *default
    enabled: false
```

4. **Load research workflow skill**
Create `SKILL.md` or reference existing skill in `skills.yaml`

5. **Deploy**
```bash
hermes run --profile deep-research
```

### Option B: Script-based isolation (no_agent=True)

For deterministic, script-driven research.

**Steps:**

1. **Create script**
```python
#!/usr/bin/env python3
"""Research script with only web_search and brave_mcp."""
import requests
from brave_mcp import BraveSearch

def research(query):
    results = brave_mcp.search(query)
    return summary

if __name__ == "__main__":
    print(research("query"))
```

2. **Run with cron**
```bash
hermes cron create --schedule "0 9 * * *" --script research.py --no_agent=True
```

### Option C: DelegateTask with prompt restrictions

Quick subagents (minutes only) with tool avoidance.

**Prompt template:**
```
You are a deep research agent with ONLY web-search and Brave MCP tools.
DO NOT attempt to:
- Edit files
- Deploy code
- Access credentials
- Use terminal commands

If a task requires these tools, report it as a limitation and ask for guidance.
```

**Limitations:**
- Cannot truly prevent tool usage (agent may attempt restricted tools)
- Only suitable for short, bounded tasks
- No isolation - inherits all parent tools

## Skill Integration

Create a `research-workflow` skill that defines:

1. **Query formulation**
   - Start with broad questions
   - Refine based on initial findings
   - Identify knowledge gaps

2. **Source cross-referencing**
   - Verify claims across 3+ sources
   - Note conflicting information
   - Prefer authoritative sources

3. **Citation format**
   ```markdown
   [Source: URL, Date, Authority Level]
   ```

4. **Report structure**
   - Executive summary
   - Methodology
   - Key findings
   - Evidence with citations
   - Limitations
   - Recommendations

## Pitfalls

- **Tool leakage**: Agent may attempt restricted tools even when not allowed
- **Context drift**: Long research sessions lose focus
- **Source bias**: Research agent may over-rely on first sources
- **Credential exposure**: Never include API keys in prompts or scripts

## Verification

Test isolation by:
1. Run agent with test query
2. Verify ONLY allowed tools were called
3. Check no unintended side effects (file changes, network calls)

## When to Use Each Option

| Scenario | Recommended |
|----------|-------------|
| Long research (hours+) | Option A (Profile) |
| Scripted, deterministic | Option B (Script) |
| Quick check (minutes) | Option C (DelegateTask) |
| Multiple concurrent agents | Option A (multiple profiles) |

---

**Related**: `research-workflow`, `autonomous-ai-agents`, `deep-research`