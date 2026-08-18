# llama-service

Recursos para servir modelos Qwen GGUF dentro del namespace existente `llms`
con el servidor OpenAI-compatible de llama.cpp. Los modelos grandes usan inferencia
hibrida: llama.cpp
elige automaticamente cuantas capas caben en la RTX 4070 Ti SUPER y mantiene
las restantes, junto con el KV cache inicial, en la RAM del host.

El procedimiento completo, las decisiones de memoria y el rollback estan en
`specs/005_llama_cpp_qwen36_hybrid.md`.

## Ruta recomendada: panel web (`model-panel`)

El handoff de GPU Local↔Cloud (liberar la RTX 4070 Ti SUPER para otro uso y
recuperarla) tiene ahora una ruta primaria sin `kubectl` manual: el panel web
`kubernetes/model-panel/`. Expuesto en la LAN detras de Traefik + mTLS + bearer
(mismo patron que `kubernetes/engram/`), muestra el modo actual (Local/Cloud),
el estado de la sesion Codex y permite:

- Alternar Local ↔ Cloud con una accion (drena, pausa KEDA, escala a cero,
  confirma GPU libre, reescribe el alias `qwen3` de LiteLLM y reinicia
  LiteLLM; la vuelta a Local siempre sube el perfil `daily` por defecto).
- Elegir el perfil local activo (`daily`/`large`) sin salir de Local,
  reutilizando `llama-router` (ver seccion "Router diario/grande" mas abajo).

Detalles completos: `specs/012_gpu_handoff_web_panel.md` (spec numerada de
este cambio) y los artefactos OpenSpec en
`openspec/changes/gpu-handoff-web-panel/` (`proposal.md`, `design.md`,
`specs/`, `tasks.md`).

**El runbook manual de esta seccion ("Secuencia resumida" y "Volver a
vLLM") sigue vigente como fallback** — usarlo si el panel no esta
desplegado, esta en un estado degradado, o para depurar paso a paso.
`switch-model.sh` (router diario/grande) tampoco cambia: sigue siendo la
ruta directa para alternar `daily`/`large` desde la CLI, y el panel la
complementa sin reemplazarla.

### Aprovisionamiento de la sesion Codex del shim (fuera de banda)

El modo Cloud del panel depende de `kubernetes/codex-shim/`, que posee **su
propia** sesion OAuth de Codex/ChatGPT, separada de la de Hermes (D16 del
`design.md`): el refresh token es de un solo uso y rota en el servidor, asi
que compartir un mismo par de tokens entre dos refrescadores independientes
haria que uno cierre la sesion del otro. Por eso el shim necesita su propio
`codex login`, hecho a mano una sola vez, nunca desde el panel (el login
interactivo esta fuera de alcance por diseno).

El procedimiento paso a paso esta en
`kubernetes/codex-shim/scripts/bootstrap_login.md`: login dedicado con una
herramienta distinta de `hermes auth`, verificacion de que la sesion de
Hermes sigue viva, y creacion manual (nunca via manifiesto versionado) de
los Secrets `codex-shim-auth` (par de tokens) y el Secret de bearer interno
que consumen tanto `codex-shim` como la entrada `cloud` de LiteLLM.

## Fuentes fijadas

| Componente | Fuente |
| --- | --- |
| llama.cpp | `server-cuda-b10156`, digest `sha256:4c0ece468721077a8bff8c114f8b0a92a8604ed310c91939fadfe37595a53b9e` |
| GGUF | `unsloth/Qwen3.6-27B-GGUF`, revision `82d411acf4a06cfb8d9b073a5211bf410bfc29bf` |
| Archivo | `Qwen3.6-27B-IQ4_XS.gguf` |
| Tamano | `15,440,005,344` bytes |
| SHA-256 | `8a3365759dc1b33b52c4e7d91d5a67d5ee1418e8408aa54196f04a98da53e5dc` |
| Licencia | Apache-2.0 |

El repositorio oficial `Qwen/Qwen3.6-27B` no contiene este GGUF; contiene los
pesos BF16/Safetensors. El archivo IQ4_XS fijado aqui es la cuantizacion pura de
Unsloth declarada sobre ese modelo base.

## Archivos

| Archivo | Proposito |
| --- | --- |
| `pvc-daily.yaml` | PVC de 12 GiB para Qwen3.5-9B Q6_K. |
| `model-download-daily-job.yaml` | Descarga verificada del modelo diario. |
| `router-config.yaml` | Presets 9B diario y 27B grande. |
| `deployment-router.yaml` | Router con un solo modelo cargado a la vez. |
| `service-router.yaml` | Service interno del router. |
| `switch-model.sh` | Cambio controlado entre los perfiles diario y grande. |
| `pvc.yaml` | PVC local RWO de 30 GiB para el GGUF. |
| `pvc-q3.yaml` | PVC local RWO de 20 GiB para Q3_K_S. |
| `pvc-q6.yaml` | PVC local RWO de 35 GiB para UD-Q6_K_XL. |
| `model-download-job.yaml` | Descarga atomica y verifica tamano + SHA-256. |
| `model-download-q3-job.yaml` | Descarga y verifica Q3_K_S; se aplica aparte. |
| `model-download-q6-job.yaml` | Descarga y verifica UD-Q6_K_XL; se aplica aparte. |
| `deployment.yaml` | `llama-server` CUDA con replicas cero por seguridad. |
| `deployment-q3.yaml` | `llama-server-q3` con replicas cero. |
| `deployment-q6.yaml` | `llama-server-q6` hibrido con replicas cero. |
| `service.yaml` | Service interno `ClusterIP` en 8080. |
| `service-q3.yaml` | Service interno para la variante Q3. |
| `service-q6.yaml` | Service interno para la variante Q6. |
| `networkpolicy.yaml` | Permite ingress desde LiteLLM cuando NetworkPolicy esta habilitado. |
| `kustomization.yaml` | Aplica recursos permanentes; no incluye el Job. |

## Router diario/grande

`llama-router` es la ruta recomendada. Precarga `Qwen3.5-9B-Q6_K` y carga
`Qwen3.6-27B-Q3_K_S` cuando una solicitud selecciona el perfil grande. Usa
`--models-max 1`, por lo que libera completamente un modelo antes de iniciar el
otro. Las rutas LiteLLM son `qwen3.5-9b` y `qwen3.6-27b-q3`; el procedimiento y
las limitaciones de concurrencia estan en `specs/008_qwen35_9b_daily_router.md`.

Hermes genera trafico auxiliar y de background. No seleccionar el 27B mediante
una peticion aislada mientras el gateway usa el 9B: ambos clientes provocarian
desalojos alternados. Usar `./switch-model.sh large` y `./switch-model.sh daily`
para detener el trafico, precargar un unico perfil y actualizar Hermes.

Ambos GGUF provienen de Hugging Face y declaran los tags `base_model:Qwen/...`
y `base_model:quantized:Qwen/...`. Qwen publica los modelos base oficiales, no
estas cuantizaciones GGUF; los labels `base-model-official=true` se refieren al
upstream Qwen y no al publicador de la cuantizacion.

Los Secrets no se versionan. Antes de aplicar los Deployments debe existir:

- `llms/litellm-auth`, keys `master-key` y `llama-api-key`.

`llama-server` monta `llama-api-key` desde ese mismo Secret. `master-key`
autentica Hermes y los demas clientes de LiteLLM.

## Variante Q3_K_S

`Qwen3.6-27B-Q3_K_S.gguf` es la variante oficial cercana a IQ3 elegida para
la prueba; el repositorio fijado no publica un archivo llamado `IQ3_M`.

| Campo | Valor |
| --- | --- |
| Tamano | `12,358,727,904` bytes, `11.510 GiB` |
| SHA-256 | `4afb4abcf0207a484b0d7e92c0421b74e8ce1c7a7250bb9d824b79288da68f20` |
| Deployment | `llama-server-q3`, replicas cero |
| Service | `llama-server-q3.llms.svc.cluster.local:8080` |
| Modelo LiteLLM | `qwen3.6-27b-q3` |

La prueba real offloadeo 65/65 capas, uso 11,254.73 MiB para el buffer CUDA del
modelo y dejo aproximadamente 2,982 MiB de VRAM libres despues de un benchmark
con 31.5K tokens de contexto. El procedimiento y los resultados completos estan
en `specs/007_qwen36_27b_q3_k_s.md`.

## Variante UD-Q6_K_XL

El despliegue alternativo usa `Qwen3.6-27B-UD-Q6_K_XL.gguf`:

| Campo | Valor |
| --- | --- |
| Tamano | `25,636,485,344` bytes, `23.876 GiB` |
| SHA-256 | `8746881d40f280b1b6b858c656a347c754ed3d9cc8d2e1ad46b3635b87f611f8` |
| Deployment | `llama-server-q6`, replicas cero |
| Service | `llama-server-q6.llms.svc.cluster.local:8080` |
| Modelo LiteLLM | `qwen3.6-27b-q6` |

No cabe completo en los 16 GiB de VRAM. llama.cpp conserva 4 GiB de margen,
offloadea automaticamente lo que cabe y ejecuta el resto desde RAM. Ofrece mas
fidelidad que IQ4_XS, pero sera mas lento por el trabajo CPU/PCIe. El spec
completo de descarga, activacion y rollback es
`specs/006_qwen36_27b_ud_q6_xl.md`.

## Secuencia resumida

No escalar llama.cpp antes de detener todos los pods vLLM que solicitan
`nvidia.com/gpu`.

Los comandos usan sintaxis de Bash y deben ejecutarse en una unica sesion. Si
el shell actual es Fish, Zsh u otro, iniciar primero:

```bash
exec bash
```

```bash
set -euo pipefail

# 1. Crear el Secret compartido en llms con valores guardados en un password manager.
read -rsp 'Nuevo LiteLLM master key: ' LITELLM_MASTER_KEY; echo
read -rsp 'Nuevo llama.cpp internal key: ' LLAMA_API_KEY; echo
[[ "$LITELLM_MASTER_KEY" == sk-* && ${#LITELLM_MASTER_KEY} -ge 35 ]]
[[ "$LLAMA_API_KEY" == sk-* && ${#LLAMA_API_KEY} -ge 35 ]]
kubectl -n llms create secret generic litellm-auth \
  --from-literal=master-key="$LITELLM_MASTER_KEY" \
  --from-literal=llama-api-key="$LLAMA_API_KEY" \
  --dry-run=client -o yaml | kubectl apply -f -
unset LITELLM_MASTER_KEY LLAMA_API_KEY

# 2. Crear recursos permanentes. llama-server queda en replicas cero.
kubectl apply -k kubernetes/llama-service

# 3. Descargar y verificar el modelo mientras vLLM sigue atendiendo.
kubectl apply -f kubernetes/llama-service/model-download-job.yaml
kubectl -n llms wait \
  --for=condition=Complete job/download-qwen36-27b-iq4-xs \
  --timeout=4h10m
kubectl -n llms logs job/download-qwen36-27b-iq4-xs

# 4. Pausar KEDA antes de abrir la ventana y liberar la GPU.
VLLM_REPLICAS="$(kubectl -n llms get deployment/vllm -o jsonpath='{.spec.replicas}')"
VLLM_BIG_REPLICAS="$(kubectl -n llms get deployment/vllm-big-model -o jsonpath='{.spec.replicas}')"
VLLM_SMALL_REPLICAS="$(kubectl -n llms get deployment/vllm-small-model -o jsonpath='{.spec.replicas}')"
HERMES_MODEL_DEFAULT="$(hermes config get model.default)"
HERMES_CONTEXT_LENGTH="$(hermes config get model.context_length)"
HERMES_MAX_TOKENS="$(hermes config get model.max_tokens)"
HERMES_COMPRESSION_THRESHOLD="$(hermes config get compression.threshold_tokens)"
HERMES_PROACTIVE_PRUNE="$(hermes config get compression.proactive_prune_tokens)"
[[ "$VLLM_REPLICAS" =~ ^[0-9]+$ ]]
[[ "$VLLM_BIG_REPLICAS" == 0 ]]
[[ "$VLLM_SMALL_REPLICAS" == 0 ]]
test -z "$(kubectl -n llms get scaledobject.keda.sh/vllm-big-model \
  -o jsonpath='{.metadata.annotations.autoscaling\.keda\.sh/paused-replicas}')"
test -z "$(kubectl -n llms get scaledobject.keda.sh/vllm-small-model \
  -o jsonpath='{.metadata.annotations.autoscaling\.keda\.sh/paused-replicas}')"
kubectl -n llms annotate scaledobject.keda.sh/vllm-big-model \
  autoscaling.keda.sh/paused-replicas="0"
kubectl -n llms annotate scaledobject.keda.sh/vllm-small-model \
  autoscaling.keda.sh/paused-replicas="0"
kubectl -n llms wait scaledobject.keda.sh/vllm-big-model \
  --for=jsonpath='{.status.conditions[?(@.type=="Paused")].status}'=True \
  --timeout=2m
kubectl -n llms wait scaledobject.keda.sh/vllm-small-model \
  --for=jsonpath='{.status.conditions[?(@.type=="Paused")].status}'=True \
  --timeout=2m
sudo systemctl stop hermes-gateway.service
kubectl -n llms scale deployment/vllm --replicas=0
kubectl -n llms scale deployment/vllm-big-model --replicas=0
kubectl -n llms scale deployment/vllm-small-model --replicas=0
VLLM_GPU_POD_NAMES="$(kubectl -n llms get pods \
  -l 'app in (vllm,vllm-big-model,vllm-small-model)' \
  --field-selector='status.phase!=Succeeded,status.phase!=Failed' \
  -o name)"
VLLM_GPU_PODS=()
if [[ -n "$VLLM_GPU_POD_NAMES" ]]; then
  mapfile -t VLLM_GPU_PODS <<< "$VLLM_GPU_POD_NAMES"
fi
if (( ${#VLLM_GPU_PODS[@]} > 0 )); then
  kubectl -n llms wait --for=delete "${VLLM_GPU_PODS[@]}" --timeout=5m
fi
VLLM_GPU_POD_NAMES="$(kubectl -n llms get pods \
  -l 'app in (vllm,vllm-big-model,vllm-small-model)' \
  --field-selector='status.phase!=Succeeded,status.phase!=Failed' \
  -o name)"
test -z "$VLLM_GPU_POD_NAMES"
kubectl -n llms get scaledobject.keda.sh vllm-big-model vllm-small-model
kubectl -n llms get pods -o wide
nvidia-smi

# 5. Arrancar llama.cpp solo despues de confirmar que no queda otro pod GPU.
test "$(kubectl -n llms get deployment/llama-server-q3 \
  -o jsonpath='{.spec.replicas}')" = 0
test "$(kubectl -n llms get deployment/llama-server-q6 \
  -o jsonpath='{.spec.replicas}')" = 0
kubectl -n llms scale deployment/llama-server --replicas=1
kubectl -n llms rollout status deployment/llama-server --timeout=35m
kubectl -n llms logs deployment/llama-server --tail=200

# 6. Publicar la ruta qwen3.6-27b en LiteLLM.
kubectl apply -f kubernetes/proxy/litellm-config.yaml
kubectl -n llms rollout restart deployment/litellm
kubectl -n llms rollout status deployment/litellm --timeout=5m
```

Antes de reiniciar Hermes, actualizar su secreto `OPENAI_API_KEY` para que
coincida con `litellm-auth/master-key`. Un bearer token desconocido debe recibir
un rechazo HTTP 4xx; no continuar si LiteLLM vuelve a emitir un fallback
`INTERNAL_USER`. Despues de actualizar el key y los limites del modelo segun el
spec completo, enlazar explicitamente el endpoint LAN con
`hermes config set model.api_key '${env:OPENAI_API_KEY}'`. Hermes no reenvia
automaticamente credenciales OpenAI a una IP privada. Finalmente, arrancar
Hermes con `sudo systemctl start hermes-gateway.service`.

El Service interno es:

```text
http://llama-server.llms.svc.cluster.local:8080/v1
```

No se crea otro `LoadBalancer`; los clientes siguen entrando por LiteLLM.

## Estado seguro del repositorio

`deployment.yaml` conserva `replicas: 0`. El escalado a uno es una decision
operativa y no debe commitearse, porque aplicar llama.cpp y vLLM con una sola
GPU produciria preemption, espera indefinida u OOM si algun proceso usa la GPU
fuera de Kubernetes.

## Volver a vLLM

```bash
set -euo pipefail
test -n "${VLLM_REPLICAS:-}"
test -n "${VLLM_BIG_REPLICAS:-}"
test -n "${VLLM_SMALL_REPLICAS:-}"
test -n "${HERMES_MODEL_DEFAULT:-}"
test -n "${HERMES_CONTEXT_LENGTH:-}"
test -n "${HERMES_MAX_TOKENS:-}"
test -n "${HERMES_COMPRESSION_THRESHOLD:-}"
test -n "${HERMES_PROACTIVE_PRUNE:-}"
test "$(kubectl -n llms get scaledobject.keda.sh/vllm-big-model \
  -o jsonpath='{.metadata.annotations.autoscaling\.keda\.sh/paused-replicas}')" = 0
test "$(kubectl -n llms get scaledobject.keda.sh/vllm-small-model \
  -o jsonpath='{.metadata.annotations.autoscaling\.keda\.sh/paused-replicas}')" = 0
sudo systemctl stop hermes-gateway.service
kubectl -n llms scale deployment/llama-server --replicas=0
kubectl -n llms scale deployment/llama-server-q3 --replicas=0
kubectl -n llms scale deployment/llama-server-q6 --replicas=0
LLAMA_GPU_POD_NAMES="$(kubectl -n llms get pods \
  -l 'app in (llama-server,llama-server-q3,llama-server-q6)' \
  --field-selector='status.phase!=Succeeded,status.phase!=Failed' \
  -o name)"
if [[ -n "$LLAMA_GPU_POD_NAMES" ]]; then
  mapfile -t LLAMA_GPU_PODS <<< "$LLAMA_GPU_POD_NAMES"
  kubectl -n llms wait --for=delete "${LLAMA_GPU_PODS[@]}" --timeout=5m
fi
kubectl -n llms scale deployment/vllm --replicas="$VLLM_REPLICAS"
kubectl -n llms scale deployment/vllm-big-model --replicas="$VLLM_BIG_REPLICAS"
kubectl -n llms scale deployment/vllm-small-model --replicas="$VLLM_SMALL_REPLICAS"
if (( VLLM_REPLICAS > 0 )); then
  kubectl -n llms rollout status deployment/vllm --timeout=20m
fi
hermes config set model.context_length "$HERMES_CONTEXT_LENGTH"
hermes config set model.max_tokens "$HERMES_MAX_TOKENS"
hermes config set compression.threshold_tokens "$HERMES_COMPRESSION_THRESHOLD"
hermes config set compression.proactive_prune_tokens "$HERMES_PROACTIVE_PRUNE"
hermes config set model.default "$HERMES_MODEL_DEFAULT"
sudo systemctl start hermes-gateway.service
hermes -z 'Responde exactamente: OK'
test "$(kubectl -n llms get scaledobject.keda.sh/vllm-big-model \
  -o jsonpath='{.metadata.annotations.autoscaling\.keda\.sh/paused-replicas}')" = 0
test "$(kubectl -n llms get scaledobject.keda.sh/vllm-small-model \
  -o jsonpath='{.metadata.annotations.autoscaling\.keda\.sh/paused-replicas}')" = 0
kubectl -n llms annotate scaledobject.keda.sh/vllm-big-model \
  autoscaling.keda.sh/paused-replicas-
kubectl -n llms annotate scaledobject.keda.sh/vllm-small-model \
  autoscaling.keda.sh/paused-replicas-
```

No borrar el PVC durante un rollback. El archivo de 15.4 GB queda disponible
para el siguiente arranque. KEDA permanece pausado hasta que vLLM este Ready;
quitar antes las anotaciones podria reactivar un modelo cold-start y bloquear
la unica GPU.
