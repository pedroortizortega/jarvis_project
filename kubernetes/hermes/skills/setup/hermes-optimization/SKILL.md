---
name: hermes-optimization
description: Optimiza configuracion de Hermes para reducir context length
tags: hermes, optimization, setup, configuration, context-length
---

# Hermes Optimization

Optimiza la configuracion de Hermes Agent para reducir consumo de tokens, mejorar rendimiento, y resolver problemas comunes de entorno.

## Cuando Usar

Activa este skill cuando:
- El contexto se satura rapido (context length consumption)
- Necesitas optimizar para Telegram/Discord con muchos mensajes
- Quieres configurar MCP servers para expandir capacidades
- Hermes no funciona como esperado (wrong environment, missing tools)
- Necesitas cambiar web search backend (ddgs, brave, tavily)

## Diagnostico Rapido

### Context Length Saturacion

**Simtomas:**
- Conversaciones cortas a pesar de modelo con 128k context
- Context exceeded errors frecuentes
- Sessions reset unanimemente

**Causas:**
1. Overhead de Telegram: ~100-200 tokens/mensaje (timestamps, metadata, delivery info)
2. Session con >50 mensajes sin limite
3. Tools activas que consumen tokens (browser ~300-2000 tokens/call)
4. Memory con limite bajo (default: 2200 chars)

### Web Search Backend

**Simtomas:**
- Auto-launch failed: Chrome not found
- agent-browser not installed
- Lento o falla con paginas dinamicas

**Solucion:** Usar fallback de curl/wget

### MCP Not Working

**Simtomas:**
- MCP SDK not available
- No MCP servers configured
- Tools MCP no aparecen en hermes tools

## Procedimiento de Optimizacion

### 1. Limitar Context Length para Telegram

hermes config set telegram.context_limit 30

**Explicacion:**
- Default: no limite (infinito historial = saturacion)
- 30 mensajes = 15-25 KB de contexto (vs 100+ KB con 100 mensajes)
- Protege sesiones nuevas del overhead historico

### 2. Configurar Web Search Backend

hermes config set web.backend ddgs

Alternativas:
- brave-free: Sin API key, gratis
- searxng: Motor auto-alojado
- exa: Requiere API key
- tavily: Requiere API key
- firecrawl: Mas completo, requiere API key

### 3. Configurar MCP Servers

python3 -c "import mcp; print('MCP SDK installed')"

cat >> ~/.hermes/config.yaml << EOF

mcp_servers:
  filesystem:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/home/pedro"]
    timeout: 30
EOF

### 4. Optimizar Context Compression

hermes config set compression.threshold 0.35
hermes config set compression.threshold_tokens 50000
hermes config set compression.protect_last_n 8

## Troubleshooting

### hermes config no funciona

**Causa:** Hermes no en PATH

**Solucion:**
export PATH="/home/pedro/Documentos/Projects/jarvis_project/kubernetes/docker/hermes-agent:\$PATH"
hermes --version

### File not found para ~/.hermes/config.yaml

**Causa:** Hermes corre en contenedor con paths montados diferentes

**Solucion:**
cat > ~/.hermes/config.yaml << EOF
...
EOF

### Context Length se consume igual rapido despues de optimizar

**Causas:**
1. Session con >50 mensajes ya creada
2. Memory con >1375 chars
3. Herramientas activas (browser, vision)

**Solucion:**
hermes config set memory.user_char_limit 1000
hermes config set browser.enabled false
hermes config set vision.enabled false

### MCP SDK not available

**Causa:** mcp Python package no instalado

**Solucion:**
pip install mcp
python3 -c "import mcp; print('OK')"

## Procedimiento Avanzado: Dividir Context Files

### Dividir SOUL.md (o archivos contextuales similares)

**Problema:** Archivos contextuales como SOUL.md exceden el limite de 20,000 chars y generan warnings de truncamiento.

**Solucion:** Dividir el archivo en secciones tematicas por debajo del limite.

### Paso 1: Analizar Secciones

python3 << 'EOF'
#!/usr/bin/env python3
import os

path = "/home/pedro/.hermes/SOUL.md"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

# Buscar titulos de secciones
sections = [
    ("01-identidad.md", "Identidad principal"),
    ("02-tratamiento.md", "Tratamiento del usuario"),
    ("03-personalidad.md", "Personalidad"),
    ("04-estilo.md", "Estilo de comunicacion"),
    ("05-extension.md", "Extension de las respuestas"),
    ("06-rigor.md", "Rigor y razonamiento"),
    ("07-comportamiento.md", "Comportamiento tecnico"),
    ("08-herramientas.md", "Herramientas y acciones"),
    ("09-proactividad.md", "Proactividad controlada"),
    ("10-humor.md", "Humor"),
    ("11-seguridad.md", "Seguridad"),
    ("12-memoria.md", "Memoria y aprendizaje"),
    ("13-formato.md", "Formato de respuesta preferido"),
    ("14-regla-final.md", "Regla final"),
    ("15-agent-teams.md", "Agent Teams Lite — Orchestrator Rule for Hermes"),
    ("16-native-bounded.md", "Native Bounded Review Orchestration"),
]

for i, (filename, title) in enumerate(sections):
    start = content.find(f"# {title}")
    if start == -1:
        print(f"⚠️ Seccion no encontrada: {title}")
        continue
    
    if i < len(sections) - 1:
        next_start = content.find(f"# {sections[i+1][1]}")
        if next_start == -1:
            next_start = len(content)
        section = content[start:next_start]
    else:
        section = content[start:]
    
    with open(f"/home/pedro/.hermes/soul/{filename}", "w", encoding="utf-8") as f:
        f.write(section)
    
    print(f"✓ {filename}: {len(section):,} chars, {len(section.splitlines())} lines")

EOF

### Paso 2: Verificar Division

ls -lh /home/pedro/.hermes/soul/

**Resultado esperado:** Archivos individuales < 20,000 chars cada uno.

### Paso 3: Configurar Context File Limits (Opcional)

hermes config set context_file_max_chars 50000

**Nota:** Esto no resuelve archivos > 50,000 chars. Dividir es la solucion definitiva.

## Metricas de Optimizacion

| Configuracion | Default | Optimizado | Impacto |
|---------------|---------|------------|---------|
| telegram.context_limit | infinito | 30 | -70% contexto historico |
| compression.threshold | 35% | 25% | -15% tokens totales |
| memory.user_char_limit | 1375 | 800 | -40% memory overhead |
| browser.enabled | true | false | -200-1000 tokens/call |

## Notas Importantes

1. Reinicio requerido: Cambios en config.yaml requieren reiniciar Hermes
2. Telegram sessions: Sesiones existentes no se resetean automaticamente
3. API keys: web.backend=ddgs requiere DDGS_API_KEY en .env
4. MCP: Requiere npx (Node.js) o uvx (Python uv) en PATH
5. Context compression: Protege ultimos 8 mensajes siempre

## Referencias

- Config file: ~/.hermes/config.yaml
- Secrets: ~/.hermes/.env
- Docs: https://hermes-agent.nousresearch.com/docs/
- MCP spec: https://modelcontextprotocol.io/

---

Nota: Este skill captura lecciones de sesiones reales de optimizacion de Hermes. Actualizalo cuando encuentres nuevas tecnicas o configuraciones optimas.