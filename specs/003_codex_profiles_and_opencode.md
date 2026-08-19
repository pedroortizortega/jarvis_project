# 003 - Perfiles Codex de Hermes y convivencia con OpenCode

## Objetivo

Versionar nueve perfiles Codex de Hermes bajo
`kubernetes/hermes/profiles/`, separando capacidad de modelo y esfuerzo de
razonamiento sin versionar credenciales. La matriz combina Luna, Terra y Sol
con niveles low, medium y high.

## Hechos verificados

- Hermes instalado admite perfiles bajo `/opt/data/profiles/<nombre>/` y los
  puede crear con `hermes profile create --clone-from default`.
- La serie Codex visible en esta version incluye `gpt-5.6-luna`,
  `gpt-5.6-terra`, `gpt-5.6-sol` y las variantes `-pro`.
- Hermes identifica las variantes `-pro` como los modos Codex de alto esfuerzo.
- El proveedor Codex fue autenticado con OAuth. La credencial vive una sola vez
  en `/opt/data/auth.json` y no se versiona en Git.

## Matriz de perfiles

| Perfil | Modelo | Esfuerzo | Caso de uso |
| --- | --- | --- | --- |
| `luna-low` | `gpt-5.6-luna` | low | Clasificar, resumir, consultas cortas y cambios triviales. |
| `luna-medium` | `gpt-5.6-luna` | medium | Cambios acotados, bugs simples y planes cortos. |
| `luna-high` | `gpt-5.6-luna-pro` | high | Investigacion limitada que no justifica Terra. |
| `terra-low` | `gpt-5.6-terra` | low | Desarrollo normal que necesita mejor fiabilidad que Luna. |
| `terra-medium` | `gpt-5.6-terra` | medium | Perfil por defecto para features multiarchivo, pruebas y review. |
| `terra-high` | `gpt-5.6-terra-pro` | high | Debugging dificil, refactors delicados y cambios de seguridad. |
| `sol-low` | `gpt-5.6-sol` | low | Segunda opinion experta, corta y acotada. |
| `sol-medium` | `gpt-5.6-sol` | medium | Arquitectura, trade-offs y revisiones transversales. |
| `sol-high` | `gpt-5.6-sol-pro` | high | Ultimo escalon para decisiones criticas o ambiguas. |

La matriz existe para seleccion explicita, pero nueve perfiles no implica que
los nueve deban usarse por igual. El recorrido recomendado para extraer mejor
valor de la cuota es: `luna-low` para descarte rapido, `terra-medium` para
desarrollo cotidiano y `sol-high` solo cuando una alternativa menor no produjo
evidencia suficiente.

## Provisionamiento reproducible

1. Desplegar Hermes base, PVC y ConfigMap como se describe en el spec 002.
2. Autenticar Codex en el pod cuando se vaya a usar por primera vez:

```bash
kubectl exec -it -n hermes-agents deployment/hermes-agent-master -- hermes auth
```

3. Crear o reconciliar los nueve perfiles desde el checkout del repositorio:

```bash
chmod +x kubernetes/hermes/profiles/bootstrap-profiles.sh
kubernetes/hermes/profiles/bootstrap-profiles.sh
```

4. Confirmar modelos y cambiar el perfil activo para una sesion de CLI:

```bash
kubectl exec -n hermes-agents deployment/hermes-agent-master -- hermes profile list
kubectl exec -n hermes-agents deployment/hermes-agent-master -- hermes profile use terra-medium
```

El bootstrap clona el perfil `default`; por eso preserva la configuracion
versionada de terminal, skills, compresion y limites de contexto. Solo cambia
`model.provider`, `model.base_url` al endpoint OAuth oficial de Codex,
`model.default` y `agent.reasoning_effort`. La credencial OAuth permanece una
sola vez en `/opt/data/auth.json` y los perfiles la reutilizan; no se copia a
cada directorio de perfil.

La imagen versionada incluye una correccion del resolvedor de Codex: los
perfiles nombrados leen el `credential_pool` con fallback al almacen raiz. Sin
esa correccion, `hermes auth status` puede indicar OAuth valido pero la primera
request de un perfil falla por no encontrar credenciales.

### Build y despliegue de la correccion OAuth

La correccion modifica `kubernetes/docker/hermes-agent/hermes_cli/auth.py`.
Debe reconstruirse la imagen e importarse al containerd de k3s antes de
reiniciar Hermes:

```bash
cd kubernetes/docker/hermes-agent
docker build -t hermes-agent:local .
docker save hermes-agent:local | sudo k3s ctr images import -
kubectl -n hermes-agents rollout restart deployment/hermes-agent-master
kubectl -n hermes-agents rollout status deployment/hermes-agent-master
```

Validar la ruta de ejecucion de un perfil sin realizar una inferencia ni gastar
cuota:

```bash
kubectl -n hermes-agents exec deployment/hermes-agent-master -- bash -c \
  'HERMES_HOME=/opt/data/profiles/sol-high /opt/hermes/.venv/bin/python - <<"PY"
from hermes_cli.auth import resolve_codex_runtime_credentials
creds = resolve_codex_runtime_credentials()
assert creds["provider"] == "openai-codex"
assert creds["base_url"] == "https://chatgpt.com/backend-api/codex"
assert creds["source"] == "credential_pool"
assert creds["api_key"]
print("Codex OAuth runtime: valid")
PY'
```

Resultado verificado: los nueve perfiles resuelven OAuth desde el
`credential_pool` raiz y `sol-high` usa `gpt-5.6-sol-pro` con el endpoint
`https://chatgpt.com/backend-api/codex`.

## OpenCode y la misma cuenta Codex

No hay conflicto tecnico por usar Hermes y OpenCode como clientes distintos.
Ambos procesos pueden usar Codex, pero comparten la misma cuenta, cuota y
limites de concurrencia. El inconveniente real es operativo: dos tareas
pesadas simultaneas pueden agotar o ralentizar el presupuesto disponible.

La clasificacion equivalente para OpenCode debe ser mas pequena que la de
Hermes: usar Luna low para exploracion y cambios triviales, Terra medium como
agente de codigo por defecto y Sol high solo para incidentes, diseno complejo
o bloqueos reales. No ejecutar Hermes y OpenCode en Sol/high al mismo tiempo
salvo que la tarea lo justifique.

No se deben compartir ni copiar archivos de autenticacion entre ambos
clientes. Cada herramienta debe iniciar sesion mediante su mecanismo oficial.
Los tokens, `.env` y `auth.json` permanecen fuera del repositorio y deben
inyectarse por Secret o por el flujo de autenticacion correspondiente.
