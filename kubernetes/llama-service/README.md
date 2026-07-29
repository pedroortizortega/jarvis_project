# llama-service

Recursos para servir `Qwen3.6-27B-IQ4_XS.gguf` dentro del namespace existente
`llms` con el servidor OpenAI-compatible de llama.cpp. El modelo usa inferencia
hibrida: llama.cpp
elige automaticamente cuantas capas caben en la RTX 4070 Ti SUPER y mantiene
las restantes, junto con el KV cache inicial, en la RAM del host.

El procedimiento completo, las decisiones de memoria y el rollback estan en
`specs/005_llama_cpp_qwen36_hybrid.md`.

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
| `pvc.yaml` | PVC local RWO de 30 GiB para el GGUF. |
| `pvc-q6.yaml` | PVC local RWO de 35 GiB para UD-Q6_K_XL. |
| `model-download-job.yaml` | Descarga atomica y verifica tamano + SHA-256. |
| `model-download-q6-job.yaml` | Descarga y verifica UD-Q6_K_XL; se aplica aparte. |
| `deployment.yaml` | `llama-server` CUDA con replicas cero por seguridad. |
| `deployment-q6.yaml` | `llama-server-q6` hibrido con replicas cero. |
| `service.yaml` | Service interno `ClusterIP` en 8080. |
| `service-q6.yaml` | Service interno para la variante Q6. |
| `networkpolicy.yaml` | Permite ingress desde LiteLLM cuando NetworkPolicy esta habilitado. |
| `kustomization.yaml` | Aplica recursos permanentes; no incluye el Job. |

Los Secrets no se versionan. Antes de aplicar los Deployments debe existir:

- `llms/litellm-auth`, keys `master-key` y `llama-api-key`.

`llama-server` monta `llama-api-key` desde ese mismo Secret. `master-key`
autentica Hermes y los demas clientes de LiteLLM.

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
spec completo, arrancar Hermes con `sudo systemctl start hermes-gateway.service`.

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
test "$(kubectl -n llms get scaledobject.keda.sh/vllm-big-model \
  -o jsonpath='{.metadata.annotations.autoscaling\.keda\.sh/paused-replicas}')" = 0
test "$(kubectl -n llms get scaledobject.keda.sh/vllm-small-model \
  -o jsonpath='{.metadata.annotations.autoscaling\.keda\.sh/paused-replicas}')" = 0
kubectl -n llms scale deployment/llama-server --replicas=0
kubectl -n llms wait --for=delete pod -l app=llama-server --timeout=5m
kubectl -n llms scale deployment/vllm --replicas="$VLLM_REPLICAS"
kubectl -n llms scale deployment/vllm-big-model --replicas="$VLLM_BIG_REPLICAS"
kubectl -n llms scale deployment/vllm-small-model --replicas="$VLLM_SMALL_REPLICAS"
if (( VLLM_REPLICAS > 0 )); then
  kubectl -n llms rollout status deployment/vllm --timeout=20m
fi
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
