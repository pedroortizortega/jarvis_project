---
name: hermes-plugins
description: "Manage Hermes Agent plugins and custom tools."
version: 1.0.0
author: Hermes Agent + User
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [plugins, tools, extension, development]
    related_skills: [hermes-agent]
---

# Hermes Plugins

This skill covers installing, enabling, and using third-party plugins with Hermes Agent.

## Plugin Architecture

Plugins extend Hermes with custom tools and capabilities. They are:

- **Installed** in `~/.hermes/hermes-agent/plugins/`
- **Enabled** via `hermes plugins enable <name>`
- **Automatically loaded** by the gateway when enabled
- **Toolset-aware** — each plugin registers one or more toolsets

### Example Plugin Structure

```
hermes-brave-search-plugin/
├── __init__.py          # Tool registration
├── plugin.yaml          # Metadata (name, version, requires_env)
├── tools.py             # Tool implementations
└── schemas.py           # JSON schemas for tool calls
```

## Plugin Lifecycle

### 1. Installation

#### Option A: Git Clone (Recommended)
```bash
git clone <repo-url> ~/.hermes/hermes-agent/plugins/<plugin-name>
```

#### Option B: ZIP Download
```bash
unzip -d ~/.hermes/hermes-agent/plugins/<plugin-name> <zip-file>
```

#### Option C: Local Directory
```bash
cp -r <local-dir> ~/.hermes/hermes-agent/plugins/
```

### 2. Enable Plugin
```bash
hermes plugins enable <plugin-name>
```

**Output:**
```
✓ Plugin <plugin-name> enabled. Takes effect on next session.
```

### 3. Disable Plugin
```bash
hermes plugins disable <plugin-name>
```

### 4. List Plugins
```bash
hermes plugins list
```

**Output:**
```
│ <plugin-name>    │ <enabled/disabled> │ <version> │ <description> │ <source> │
└──────────────────┴────────────────────┴───────────┴───────────────┴──────────┘
```

## Plugin Types

### 1. Hermes Plugins (`.py` + `.yaml`)
- Installed in `~/.hermes/hermes-agent/plugins/`
- Register tools via `ctx.register_tool()`
- Example: `hermes-brave-search-plugin`

### 2. Desktop Plugins (`.js`)
- Installed in `~/.hermes/desktop-plugins/`
- Extend the desktop app UI
- Example: Custom widgets, sidebars

### 3. TUI Widgets (`.mjs`)
- Installed in `~/.hermes/tui-widgets/`
- Extend the Ink TUI
- Example: Clock, dashboard panels

### 4. Pets (`.json`)
- Installed in `~/.hermes/pets/`
- Mascots that appear in chat
- Example: Custom avatars, reactions

## Plugin Requirements

Plugins may declare required environment variables:

```yaml
# plugin.yaml
requires_env:
  - name: BRAVE_API_KEY
    description: Brave Search API subscription token
    url: https://api-dashboard.search.brave.com/app/keys
    secret: true
```

### Setup Required

1. Set the environment variable in `~/.hermes/.env`:
   ```
   BRAVE_API_KEY=your-api-key
   ```

2. Restart the gateway:
   ```bash
   hermes gateway restart
   ```

3. The plugin will now be available in the toolset.

## Automatic Tool Loading

**Important:** Enabled plugins are loaded **automatically** by the gateway. You do NOT need to:

- ❌ Call the plugin manually from skills
- ❌ Import plugin code in skill code
- ❌ Register tools in skill prompts

The plugin's tools are available to all skills and prompts that need them.

### Example: Using an Enabled Plugin

**Scenario:** You have `hermes-brave-search-plugin` enabled.

**In a skill:**
```python
def research_trend(skill_ctx):
    # No explicit call to brave_web_search needed
    # Hermes automatically uses it when web search is needed
    ...
```

**In a prompt:**
```
"Investiga las últimas noticias sobre X"
# → Hermes detects need for web search → uses brave_web_search automatically
```

## Troubleshooting

### Plugin Not Loading

1. **Check if enabled:**
   ```bash
   hermes plugins list | grep <plugin-name>
   ```
   Expected: `│ <plugin-name> │ enabled │ ... │`

2. **Enable if needed:**
   ```bash
   hermes plugins enable <plugin-name>
   ```

3. **Restart gateway:**
   ```bash
   hermes gateway restart
   ```

### Missing Environment Variables

1. **Check required env:**
   ```bash
   cat ~/.hermes/hermes-agent/plugins/<plugin-name>/plugin.yaml
   ```

2. **Add to .env:**
   ```bash
   echo "VAR_NAME=value" >> ~/.hermes/.env
   ```

3. **Restart gateway:**
   ```bash
   hermes gateway restart
   ```

### Plugin Conflicts

If two plugins provide the same tool name:

1. **List conflicts:**
   ```bash
   hermes plugins list | grep <tool-name>
   ```

2. **Disable conflicting plugin:**
   ```bash
   hermes plugins disable <conflicting-plugin>
   ```

3. **Restart gateway:**
   ```bash
   hermes gateway restart
   ```

## Custom Plugin Development

### 1. Create Plugin Directory
```bash
mkdir -p ~/.hermes/hermes-agent/plugins/my-custom-plugin
```

### 2. Create `plugin.yaml`
```yaml
name: my-custom-plugin
version: 0.1.0
description: Custom plugin description
author: Your Name
provides_tools:
  - my_tool
requires_env: []
```

### 3. Create `__init__.py`
```python
"""My Custom Plugin."""

TOOLSET_NAME = "my-custom"

def register(ctx):
    ctx.register_tool(
        name="my_tool",
        toolset=TOOLSET_NAME,
        schema=SCHEMA,
        handler=handler_fn,
        description="Tool description",
        emoji="🔧",
    )
```

### 4. Create `tools.py`
```python
from . import schemas

def my_tool(ctx, args):
    # Your implementation here
    return {"result": "success"}

def check_api_key(ctx):
    return True  # or validate BRAVE_API_KEY
```

### 5. Create `schemas.py`
```python
import json

MY_TOOL_SCHEMA = {
    "name": "my_tool",
    "description": "Tool description",
    "inputSchema": {
        "type": "object",
        "properties": {
            "param1": {"type": "string"}
        }
    }
}

BRAVE_WEB_SEARCH_SCHEMA = {
    "name": "brave_web_search",
    "description": "Search the web with Brave Search API.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {"type": "string"}
        },
        "required": ["query"]
    }
}
```

### 6. Enable and Test
```bash
hermes plugins enable my-custom-plugin
hermes gateway restart
```

Then test in the chat:
```
"Use my_tool with param1=value"
```

## Best Practices

### 1. Security
- Never expose secrets in plugin code
- Always require API keys via `requires_env`
- Validate all inputs

### 2. Error Handling
- Wrap tool calls in try/except
- Return clear error messages
- Log errors to `~/.hermes/logs/`

### 3. Documentation
- Include `README.md` in plugin directory
- Document required environment variables
- Provide usage examples

### 4. Versioning
- Use semantic versioning in `plugin.yaml`
- Track changes in `CHANGELOG.md`
- Pin compatible Hermes versions

## See Also

- [Hermes Configuration](configuration.md) — config.yaml structure
- [Background Systems](background-systems.md) — cron, delegation
- [Troubleshooting](troubleshooting.md) — common issues
