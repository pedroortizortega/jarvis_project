---
name: deep-research-subagent
description: "Create autonomous subagents for restricted deep research."
version: 1.0.0
author: Jarvis
license: MIT
platforms: [linux, macos]
tags: [Subagent, Deep Research, Autonomous, Restricted Tools, Investigation, Report Generation]

---

# Deep Research Subagent

## Trigger

Use when creating autonomous subagents for deep research with multi-source verification, structured reports, and restricted tool access.

Do NOT use for simple tasks (<5 min), direct skill execution, or research without source comparison.

## Architecture

Master Agent └── Deep Research Subagent
    ├── Model: terra-medium (fixed, unchangeable)
    ├── Tools: web-search, brave MCP ONLY
    ├── Skill: research-workflow
    └── Blocked: terminal, file, browser, memory, delegate_task

## Purpose

The subagent enables **autonomous deep research** with:
- **Security**: No access to terminal, files, or credentials
- **Focus**: Only web-based research tools
- **Structure**: Consistent report format with citations
- **Autonomy**: Completes task without user intervention

## When to Delegate vs Direct Skill Use

### Delegate to Subagent (when):
- Research requires **multi-phase investigation** (>15 min)
- Need to **contrast multiple sources** for verification
- Generate **comprehensive reports** with citations
- **Security** is priority (no file/terminal access needed)
- Task is **complex enough** to justify subagent overhead

### Use Direct Skill (when):
- Quick research (<5 min)
- Simple queries without source comparison
- Tasks that don't need structured reporting
- Main agent has all necessary tools available

## Creating the Subagent

### Step 1: Create Profile Directory
```bash
mkdir -p /path/to/profiles/deep-research/{skills,tools,config}
```

### Step 2: Create Required Files

**README.md** - Profile description
**USAGE.md** - Usage guide with examples  
**SKILL.md** - Subagent definition
**config.yaml** - Model and tool configuration
**tools.yaml** - Enabled/disabled tools list

### Step 3: Create Research Workflow Skill
```bash
mkdir -p /path/to/skills/research-workflow
```
Create SKILL.md with the 5-phase research workflow

### Step 4: Configure Model
Set model to `terra-medium` in config.yaml

### Step 5: Restrict Tools
Enable ONLY:
- ✅ web-search (Google Search)
- ✅ brave-mcp (Brave Search)

Disable ALL others:
- ❌ terminal, file, browser, delegate_task, memory, vision, tts, cronjob

## Usage Examples

### Simple Delegation (Direct Skill)
```python
from hermes_tools import delegate_task

# Quick research - use skill directly
result = delegate_task(
    goal="Investiga el impacto de LLMs en el mercado de traducción",
    context="Usa web-search y brave MCP",
    skills=["research-workflow"],
    background=True
)
```

### Complex Delegation (Subagent)
```python
from hermes_tools import delegate_task

# Deep research - use subagent profile
tasks = [{
    "goal": "Investiga el impacto de LLMs en el mercado de traducción",
    "context": """
    1. Formula preguntas investigables
    2. Ejecuta 4-6 búsquedas con web-search y brave MCP
    3. Contrasta fuentes para identificar consistencias y discrepancias
    4. Genera informe estructurado:
       - Resumen ejecutivo
       - Hallazgos principales con nivel de confianza
       - Citas con URLs
       - Limitaciones identificadas
    
    Restricciones:
    - Modelo fijo: terra-medium
    - Solo herramientas: web-search, brave MCP
    - Sin acceso a otras herramientas
    - Máximo 30 minutos
    """
}]

result = delegate_task(
    tasks=tasks,
    skills=["deep-research-subagent", "research-workflow"],
    background=True
)
```

## Research Workflow (5 Phases)

### Phase 1: Query Analysis
**Goal:** Transform user question into investigable questions

**Steps:**
1. Identify central topic
2. Decompose into sub-questions
3. Define verification criteria

**Example:**
```
User: "Impacto de LLMs en traducción"
→ Sub-questions:
   - ¿Cómo automatizan LLMs el flujo de trabajo?
   - ¿Qué métricas de eficiencia mejoran?
   - ¿Qué industrias adoptan más rápido?
```

### Phase 2: Search Execution
**Goal:** Collect information from multiple sources

**Strategy:**
- **Initial search (web-search):** "[topic] + [year] + [context]"
- **Complementary search (Brave MCP):** "[topic] + [synonym] + [year]"
- **Contrast search:** "[opinion] vs [opinion] + [year]"

### Phase 3: Source Comparison
**Goal:** Identify consistencies and discrepancies

**Verification Matrix:**
| Source | Finding | Confidence Level | Contradicts |
|--------|---------|-----------------|-------------|
| [URL] | [data] | [High/Med/Low] | [Yes/No] |
| [URL] | [data] | [High/Med/Low] | [Yes/No] |

**Confidence Levels:**
- **High:** 2+ consistent sources
- **Medium:** 1 reliable source + context
- **Low:** 1 source with limitations

### Phase 4: Citation
**Format:**
```markdown
[Fuente] Autor/Origen (Año). [Brief finding]. [URL]

Example:
Smith, J. (2024). LLMs reduce translation costs by 40%.
https://example.com/article
```

**Rules:**
- Always include URL
- Include author/organization
- Include year if available
- Brief description of finding

### Phase 5: Report Generation
**Structure:**
```markdown
# [Report Title]

## Executive Summary
[1-2 paragraphs with main findings]

## Main Findings

### [Category 1]
- [Finding 1]
  - Confidence Level: [High/Medium/Low]
  - [Source] Author (Year). [URL]
- [Finding 2]
  - Confidence Level: [High/Medium/Low]
  - [Source] Author (Year). [URL]

### [Category 2]
[... similar ...]

## Identified Limitations
- [Limitation 1]
- [Limitation 2]

## Conclusions
[Implications and recommendations]

## References
[Complete list of all sources]
```

## Output Format

The subagent returns a markdown report with:
- Clear structure with headings
- Citations with URLs and context
- Confidence levels for each finding
- Identified limitations
- Complete references

## Example Output

```markdown
# Impacto de LLMs en el Mercado de Traducción

## Resumen Ejecutivo
Los LLMs están transformando el mercado de traducción, automatizando el 30-40% de las tareas de traducción humana según fuentes de 2024.

## Hallazgos Principales

### Automatización de Tareas
- Los LLMs automatizan aproximadamente el 30-40% del flujo de trabajo de traducción
- Tareas más automatizables: investigación, formatting, revisión
- Nivel de confianza: Alto
- [Fuente] European Commission (2024). [URL]

### Eficiencia de Costos
- Reducción del 25-35% en costos operativos
- Tiempos de entrega 2x más rápidos
- Nivel de confianza: Alto
- [Fuente] Translation World (2024). [URL]

## Limitaciones
- Lenguajes minoritarios con menos datos
- Contextos culturales complejos
- Terminología técnica especializada

## Conclusiones
La adopción de LLMs es inevitable, pero la traducción humana se desplazará hacia tareas de mayor valor: revisión, localización cultural, y manejo de contextos complejos.
```

## Security & Isolation

The subagent operates with **intentional limitations**:

| Tool | Status | Reason |
|------|--------|--------|
| web-search | ✅ Allowed | Necessary for research |
| brave-mcp | ✅ Allowed | Alternative search engine |
| terminal | ❌ Blocked | Security |
| file | ❌ Blocked | Security |
| browser | ❌ Blocked | Focus on web tools |
| memory | ❌ Blocked | Isolation |
| delegate_task | ❌ Blocked | Single hierarchy |

## Monitoring

To verify subagent progress:

```bash
# Check running processes
ps aux | grep hermes

# Check subagent output
tail -f /tmp/hermes_output/deep-research-*.log
```

## Troubleshooting

### Problem: Subagent not responding
**Cause:** Possible block on a search
**Solution:**
1. Verify tools are available
2. Retry with extended timeout
3. Divide task into smaller parts

### Problem: Incomplete report
**Cause:** Insufficient execution time
**Solution:**
1. Increase timeout in delegation
2. Simplify the objective
3. Provide additional context

### Problem: Insufficient sources
**Solution:** Expand search year or use synonyms

### Problem: Contradictions between sources
**Solution:** Search for more authoritative source

## Best Practices

1. **Always specify time limit** in delegation context
2. **Be specific about expected output** format
3. **Provide background context** when relevant
4. **Set clear boundaries** on what to include/exclude
5. **Monitor progress** for long tasks
6. **Split complex tasks** if subagent takes too long

## Version History

- **v1.0.0** (Jul 2024): Initial version with terra-medium and basic restrictions

## Notes

This skill captures the **pattern** of creating restricted subagents for deep research. Each time you create such a subagent, follow the structure defined here to ensure consistency and security.

## References

- See `USAGE.md` in profile directory for detailed usage guide
- See `research-workflow/SKILL.md` for the 5-phase research workflow
- See `config.yaml` for model and tool configuration
- See `tools.yaml` for enabled/disabled tools list
'''