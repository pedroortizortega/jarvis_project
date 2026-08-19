# 📖 Documentación de Activación de MCP de Brave Search

## 🎯 Objetivo
Activar el MCP de Brave Search en el cluster Kubernetes para permitir búsquedas web integradas en Hermes Agent.

---

## 🔐 Paso 1: Crear el Secret de Brave fuera de Git

La clave de Brave se conserva localmente y no se versiona. Crea el archivo local a partir de la plantilla:

```bash
cp kubernetes/mcps/.env.example kubernetes/mcps/.env
# Edita kubernetes/mcps/.env y asigna BRAVE_API_KEY a tu clave real.
```

Después crea o actualiza el Secret de Kubernetes de forma idempotente:

```bash
kubectl create secret generic brave-api-key-secret \
  --from-env-file=kubernetes/mcps/.env \
  --dry-run=client -o yaml | kubectl apply -f -
```

`kubernetes/mcps/.env` está ignorado por Git; no lo añadas con `git add -f`.

---

## 🔧 Paso 2: Desplegar el MCP de Brave

**Archivo:** `kubernetes/mcps/brave-search-mcp-deployment.yaml`

**Comando de despliegue:**
```bash
kubectl apply -f kubernetes/mcps/brave-search-mcp-deployment.yaml
```

### Estado Esperado:
```
NAME                          READY   STATUS
brave-search-mcp              1/1     Running
brave-search-mcp-xxxxx        1/1     Running
```

---

## ⚠️ Paso 3: Verificar que el Pod está Operativo

### 2.1. Verificar estado del pod
```bash
kubectl get pods -l app=brave-search-mcp
```

### 2.2. Verificar logs
```bash
kubectl logs <pod-name> --tail=30
```

**Logs esperados:**
```
INF MCP server configured mode=stdio command=/app/node_modules/.bin/mcp-server-brave-search
INF Metrics manager configured listen=:8080 health=/ metrics=/metrics
INF Minibridge frontend configured mcp=/mcp sse=/sse messages=/message agent-token=false mode=http
```

### 2.3. Verificar endpoint de health
```bash
kubectl exec <pod-name> -- curl -s http://localhost:8080/metrics | head -5
```

**Respuesta esperada:**
```
# HELP go_gc_duration_seconds A summary of the wall-time pause
# TYPE go_gc_duration_seconds summary
go_gc_duration_seconds{quantile="0"} 1.9691e-05
```

---

## 🚀 Paso 4: Activar el MCP en Hermes Agent

### 3.1. Mental Model de Integración

**Mental model:**
- Think of MCP as an adapter layer:
  - **Hermes remains the agent** (parent process)
  - **MCP servers contribute tools** (subprocesses)
  - **Hermes discovers those tools at startup or reload time**
  - **The model can use them like normal tools**
  - **You control how much of each server is visible**

### 3.2. Verificar que Hermes está corriendo

```bash
kubectl get pods -A | grep hermes-agent
```

### 3.3. Opción A: Hermes ya está corriendo

Si Hermes ya está activo, solo necesitas **recrear el pod de Hermes** para que cargue el nuevo MCP:

```bash
# Identificar el pod de Hermes actual
kubectl get pods -A | grep hermes-agent

# Eliminar el pod actual (Hermes se recreará automáticamente con el nuevo MCP)
kubectl delete pod <hermes-pod-name>

# O usar rollout restart
kubectl rollout restart deployment/hermes-agent
```

### 3.4. Opción B: Hermes no está corriendo

Si Hermes no está activo, necesitas crearlo primero:

```bash
# Crear deployment de Hermes (reemplaza el command por el correcto de tu Hermes)
kubectl create deployment hermes-agent \
  --image=<hermes-image:tag> \
  --replicas=1 \
  --overrides='
  {
    "spec": {
      "containers": [{
        "name": "hermes-agent",
        "command": ["/app/hermes"],
        "args": ["--mcp-servers", "brave-search-mcp"],
        "env": [...]
      }]
    }
  }'
```

**Nota importante:** Los MCP servers se ejecutan como **subprocesses de Hermes**. El MCP de Brave debe estar disponible en el cluster y Hermes debe estar configurado para usarlo.

### 3.5. Verificar que el MCP está disponible

```bash
# Verificar logs de Hermes para confirmar que cargó el MCP
kubectl logs -f <hermes-pod-name> | grep -i mcp

# O verificar los pods del MCP
kubectl get pods -l mcp=brave-search
```

---

## ✅ Paso 5: Validar que Funciona

### 4.1. Probar una búsqueda

```bash
# Iniciar sesión en Hermes
kubectl exec -it <hermes-pod-name> -- bash

# Verificar herramientas disponibles
python -c "from hermes_tools import web_search; print(web_search)"

# Ejecutar un comando que use el MCP de Brave
# (esto depende de cómo hayas configurado Hermes para usar el MCP)
```

### 4.2. Verificar en la aplicación

- Inicia una conversación con Jarvis
- Pide que busque información actualizada sobre un tema
- Verifica que las respuestas incluyen resultados de Brave Search

---

## 📝 Configuración del YAML

La configuración está en:
```
kubernetes/mcps/brave-search-mcp-deployment.yaml
```

### Variables Ambientales Clave:

| Variable | Valor | Descripción |
|----------|-------|-------------|
| `BRAVE_API_KEY` | `<your-api-key>` | Tu API key de Brave Search |
| `BRAVE_MCP_TRANSPORT` | `stdio` | Método de transporte (stdio o http) |

### Endpoints Disponibles del MCP:

| Endpoint | Descripción |
|----------|-------------|
| `/metrics` | Metrics endpoint (health check) |
| `/mcp` | MCP endpoint principal |
| `/sse` | SSE streaming endpoint |
| `/message` | Messages endpoint |

---

## 🔍 Solución de Problemas Avanzada

### Problema: MCP no aparece en Hermes

**Posibles causas:**
1. Hermes no está reconociendo el nuevo MCP
2. Falta de permisos en el namespace
3. Conflicto de puertos
4. **El MCP no está disponible como subprocess**

**Solución:**
```bash
# Verificar que el MCP está corriendo
kubectl get pods -l mcp=brave-search

# Verificar que Hermes está corriendo
kubectl get pods -A | grep hermes-agent

# Verificar que Hermes tiene permisos para ejecutar subprocesses
kubectl logs <hermes-pod-name> | grep -i error

# Reiniciar completamente Hermes
kubectl delete pod <hermes-pod-name>
```

### Problema: ImagePullBackOff

**Causa:** La imagen Docker no se puede encontrar.

**Solución:**
```bash
# Usar una imagen alternativa que exista
kubectl set image deployment/brave-search-mcp brave-search-mcp=acuvity/mcp-server-brave-search:latest

# O construir la imagen localmente
docker build -t brave-search-mcp:local .
docker push registry.example.com/brave-search-mcp:local
kubectl set image deployment/brave-search-mcp brave-search-mcp=registry.example.com/brave-search-mcp:local
```

### Problema: HealthCheck Failed

**Causa:** El endpoint de health no responde correctamente.

**Solución:**
```bash
# Verificar endpoint correcto (es /metrics, no /health)
kubectl exec <pod-name> -- curl -s http://localhost:8080/metrics

# Verificar logs
kubectl logs <pod-name> --tail=50
```

---

## 📚 Recursos Adicionales

- **GitHub oficial:** https://github.com/brave/brave-search-mcp-server
- **Docker MCP Catalog:** https://hub.docker.com/mcp
- **Documentación Brave Search:** https://brave.com/api
- **Documentación Hermes MCP:** https://hermes-agent.nousresearch.com/docs/guides/use-mcp-with-hermes/

---

## 🎓 Resumen de Integración

### Arquitectura de Integración:

```
┌─────────────────────────────────────┐
│         Hermes Agent (Parent)       │
│  ┌─────────────────────────────┐    │
│  │  MCP Server (Subprocess)    │    │
│  │  ┌─────────────────────┐    │    │
│  │  │  Brave Search MCP   │    │    │
│  │  │  - Búsqueda web     │    │    │
│  │  │  - Imágenes         │    │    │
│  │  │  - Noticias         │    │    │
│  │  │  - Videos           │    │    │
│  │  │  - Contexto LLM     │    │    │
│  │  │  - Resumen AI       │    │    │
│  │  └─────────────────────┘    │    │
│  └─────────────────────────────┘    │
└─────────────────────────────────────┘

- MCP servers contribuyen tools
- Hermes descubre los tools al inicio o reload
- El model puede usarlos como tools normales
```

### Clave de la Integración:

1. ✅ **El MCP de Brave debe estar corriendo** en el cluster (deployment activo)
2. ✅ **Hermes debe estar corriendo** (deployment activo)
3. ✅ **Hermes debe estar configurado** para usar el MCP (subprocess o referencia externa)
4. ✅ **Los endpoints del MCP deben ser accesibles** desde el pod de Hermes

---

## 🎯 Próximos Pasos

1. ✅ Desplegar el MCP de Brave (COMPLETADO)
2. ✅ Configurar el YAML en `kubernetes/mcps/` (COMPLETADO)
3. ✅ Activar y probar el MCP (PENDIENTE)
4. ✅ Documentar integración con Hermes (COMPLETADO)
5. ⏳ Configurar Hermes para usar el MCP (PENDIENTE)

---

## 📁 Archivos Generados

```
kubernetes/mcps/
├── .env.example                       # Plantilla sin secretos
├── brave-search-mcp-deployment.yaml  # YAML de despliegue del MCP
└── brave-mcp-activation-guide.md     # Esta guía de activación
```
