# 006 - Qwen3.6-27B UD-Q6_K_XL con llama.cpp

## Objetivo

Agregar una segunda variante de Qwen3.6-27B en `llms` sin reemplazar ni borrar
el IQ4_XS. Ambos Deployments permanecen en cero por defecto y comparten la unica
GPU de forma excluyente.

## Artefacto verificado

Fuente: `https://huggingface.co/unsloth/Qwen3.6-27B-GGUF`.

| Campo | Valor |
| --- | --- |
| Archivo | `Qwen3.6-27B-UD-Q6_K_XL.gguf` |
| Revision fijada | `82d411acf4a06cfb8d9b073a5211bf410bfc29bf` |
| Tamano | `25,636,485,344` bytes, `23.876 GiB` |
| SHA-256 | `8746881d40f280b1b6b858c656a347c754ed3d9cc8d2e1ad46b3635b87f611f8` |
| Arquitectura | `qwen35`, 64 capas, 27B parametros |
| Contexto nativo | 262,144 tokens |
| Licencia | Apache-2.0 |

Unsloth clasifica `UD-Q6_K_XL` como un Dynamic 2.0 GGUF. `UD` no introduce un
nuevo tipo de tensor: combina cuantizaciones soportadas y conserva mayor
precision en tensores sensibles.

## Compatibilidad con la RTX 4070 Ti SUPER

La GPU tiene 16,376 MiB de VRAM y compute capability 8.9. La imagen fijada de
llama.cpp b10156 incluye CUDA para Ada, arquitectura `qwen35`, Gated DeltaNet y
los tipos usados por Q6_K_XL. La compatibilidad de formato es alta.

El archivo pesa 23.876 GiB, por lo que no puede cargarse completo en la GPU. El
Deployment usa:

```text
--n-gpu-layers auto
--fit on
--fit-target 4096
--ctx-size 65536
--no-kv-offload
--cache-type-k f16
--cache-type-v f16
--ctx-checkpoints 4
```

Esto reserva aproximadamente 4 GiB de margen VRAM y deja en RAM los pesos que
no entren y el KV cache. Para 65,536 tokens, los 16 bloques de full attention
requieren aproximadamente 4 GiB de KV F16 por slot, mas estado recurrente y
buffers. El pod solicita 32 GiB de RAM y limita en 52 GiB. Con 62 GiB totales y
aproximadamente 45 GiB disponibles al verificar, debe caber, pero el pico RSS y
el numero efectivo de capas CUDA solo se conocen al cargar el archivo.

Comparacion esperada:

| Variante | Archivo | Comportamiento esperado |
| --- | ---: | --- |
| IQ4_XS | 14.380 GiB | Mas capas en GPU, menor latencia, menor fidelidad. |
| UD-Q6_K_XL | 23.876 GiB | Mejor fidelidad, mas trabajo CPU/RAM, menor velocidad. |

Q6 es adecuado si la calidad importa mas que la respuesta interactiva. IQ4_XS
sigue siendo la opcion recomendada para Hermes si se prioriza velocidad. No se
deben prometer tokens/s ni capas offloaded sin medir los logs del arranque.

## Recursos

```text
kubernetes/llama-service/pvc-q6.yaml
kubernetes/llama-service/model-download-q6-job.yaml
kubernetes/llama-service/deployment-q6.yaml
kubernetes/llama-service/service-q6.yaml
```

- PVC independiente `llama-models-q6` de 35 GiB.
- Deployment `llama-server-q6`, versionado con `replicas: 0`.
- Service `llama-server-q6.llms.svc.cluster.local:8080`.
- Alias OpenAI `qwen3.6-27b-q6`.
- Ruta LiteLLM `qwen3.6-27b-q6`.
- El mismo `Secret/llms/litellm-auth` protege ambos servidores.

## Descargar sin interrumpir el modelo activo

El Job no solicita GPU:

```bash
exec bash
set -euo pipefail

kubectl apply -k kubernetes/llama-service
kubectl -n llms delete job download-qwen36-27b-ud-q6-k-xl \
  --ignore-not-found
kubectl apply -f kubernetes/llama-service/model-download-q6-job.yaml
kubectl -n llms wait \
  --for=condition=Complete job/download-qwen36-27b-ud-q6-k-xl \
  --timeout=6h10m
kubectl -n llms logs job/download-qwen36-27b-ud-q6-k-xl
```

Los logs deben incluir:

```text
/models/Qwen3.6-27B-UD-Q6_K_XL.gguf: OK
Model downloaded and verified
```

## Activar Q6

Antes de empezar, vLLM debe estar en cero y sus dos ScaledObjects KEDA deben
mostrar `PAUSED=True`, siguiendo la Fase 4 del spec 005. El IQ4_XS tambien debe
quedar detenido:

```bash
set -euo pipefail

kubectl -n llms scale deployment/llama-server --replicas=0

IQ4_PODS="$(kubectl -n llms get pods -l app=llama-server \
  --field-selector='status.phase!=Succeeded,status.phase!=Failed' -o name)"
if [[ -n "$IQ4_PODS" ]]; then
  kubectl -n llms wait --for=delete $IQ4_PODS --timeout=5m
fi

test "$(kubectl -n llms get deployment/vllm -o jsonpath='{.spec.replicas}')" = 0
test "$(kubectl -n llms get deployment/llama-server -o jsonpath='{.spec.replicas}')" = 0

kubectl -n llms scale deployment/llama-server-q6 --replicas=1
kubectl -n llms rollout status deployment/llama-server-q6 --timeout=45m
kubectl -n llms logs deployment/llama-server-q6 --tail=300
```

Validar en logs:

- Build b10156 y arquitectura `qwen35`.
- Contexto efectivo 65,536.
- Numero efectivo de capas CUDA y memoria por backend.
- Sin CUDA OOM, `unknown model architecture` ni OOMKilled.
- RSS del pod por debajo del limite de 52 GiB.

## Publicar y probar

```bash
kubectl apply -f kubernetes/proxy/litellm-config.yaml
kubectl -n llms rollout restart deployment/litellm
kubectl -n llms rollout status deployment/litellm --timeout=5m

read -rsp 'LiteLLM API key: ' LITELLM_API_KEY
echo
LITELLM_IP="$(kubectl -n llms get service/litellm \
  -o jsonpath='{.status.loadBalancer.ingress[0].ip}')"
curl --fail --silent --show-error \
  -H "Authorization: Bearer $LITELLM_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen3.6-27b-q6","messages":[{"role":"user","content":"Responde exactamente OK"}],"max_tokens":32}' \
  "http://$LITELLM_IP:4000/v1/chat/completions"
unset LITELLM_API_KEY LITELLM_IP
```

Para Hermes, cambiar `model.default` a `qwen3.6-27b-q6`; el contexto y output
siguen en 65,536 y 16,384 respectivamente.

## Volver a IQ4_XS

```bash
kubectl -n llms scale deployment/llama-server-q6 --replicas=0
kubectl -n llms wait --for=delete pod -l app=llama-server-q6 --timeout=5m
kubectl -n llms scale deployment/llama-server --replicas=1
kubectl -n llms rollout status deployment/llama-server --timeout=35m
```

No borrar ninguno de los PVC durante el cambio. Para volver a vLLM, usar el
rollback del spec 005 y reanudar KEDA solo despues de que vLLM este Ready.

## Limitaciones

- Este Deployment es text-only; no descarga ni carga `mmproj`.
- Con mas capas en CPU, el bus PCIe y el ancho de banda de RAM limitan decode.
- El contexto largo aumenta la latencia aunque el estado de Gated DeltaNet sea
  parcialmente recurrente.
- La recomendacion oficial de 128K prioriza capacidad de thinking, pero 65K es
  el baseline seguro para esta maquina y debe aumentarse solo con mediciones.
