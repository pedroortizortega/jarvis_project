# 005 - llama.cpp hibrido para Qwen3.6-27B en Kubernetes

## Relacion con los specs anteriores

Este spec extiende 001, 002, 003 y 004. Mantiene LiteLLM como endpoint estable
de la LAN y agrega un motor de inferencia alternativo en el namespace
existente `llms`.

El cambio no elimina vLLM. Define un procedimiento manual y reversible para
liberar la unica GPU, arrancar llama.cpp y seleccionar `qwen3.6-27b` desde
Hermes. vLLM permanece como rollback del modelo `qwen3`.

## Objetivo

1. Desplegar llama.cpp junto a LiteLLM y vLLM en el namespace `llms`.
2. Descargar y verificar `Qwen3.6-27B-IQ4_XS.gguf` en almacenamiento persistente.
3. Ejecutar llama.cpp con capas distribuidas entre VRAM y RAM.
4. Exponer una API OpenAI-compatible autenticada solo dentro del cluster.
5. Registrar `qwen3.6-27b` en LiteLLM.
6. Documentar paso a paso el cambio desde vLLM y el rollback.
7. Evitar dos workloads compitiendo por la RTX 4070 Ti SUPER.

## Autoridad de documentacion

`https://llama-cpp.com/` se consulto como referencia, pero su propio footer
indica que es un sitio no oficial. Las decisiones de flags, imagenes y endpoints
se validaron contra el upstream oficial:

- `https://github.com/ggml-org/llama.cpp`
- `https://github.com/ggml-org/llama.cpp/blob/master/docs/docker.md`
- `https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md`

## Hechos verificados

### Hardware de `trantor`

| Recurso | Valor |
| --- | --- |
| CPU | Intel Core i9-14900F, 24 cores / 32 threads |
| RAM | 62 GiB visibles, aproximadamente 47 GiB disponibles durante el diseno |
| GPU | NVIDIA RTX 4070 Ti SUPER |
| VRAM | 16,376 MiB |
| Driver | 610.43.03 |
| Disco | Aproximadamente 1.6 TiB libre en el filesystem de `local-path` |
| RuntimeClass | `nvidia` presente y operativo |

### Modelo

El enlace original `Qwen/Qwen3.6-27B` es el modelo base oficial, pero no
contiene `Qwen3.6-27B-IQ4_XS.gguf`. El archivo solicitado se verifico en una
cuantizacion que declara ese modelo base sin abliteracion ni fine-tune:

| Campo | Valor |
| --- | --- |
| Modelo base | `Qwen/Qwen3.6-27B` |
| Repo GGUF | `unsloth/Qwen3.6-27B-GGUF` |
| Revision fijada | `82d411acf4a06cfb8d9b073a5211bf410bfc29bf` |
| Archivo | `Qwen3.6-27B-IQ4_XS.gguf` |
| Tamano | `15,440,005,344` bytes, aproximadamente 15.4 GB decimales |
| SHA-256 | `8a3365759dc1b33b52c4e7d91d5a67d5ee1418e8408aa54196f04a98da53e5dc` |
| Arquitectura GGUF | `qwen35` |
| Contexto nativo | 262,144 tokens |
| Licencia | Apache-2.0 |

Qwen3.6-27B tiene 64 capas y arquitectura hibrida con Gated DeltaNet y bloques
de atencion. La compatibilidad debe confirmarse en los logs del build de
llama.cpp fijado; que el GGUF exista no sustituye una prueba real de carga,
chat template, reasoning y tool calling.

### Imagen de llama.cpp

```text
ghcr.io/ggml-org/llama.cpp:server-cuda-b10156
sha256:4c0ece468721077a8bff8c114f8b0a92a8604ed310c91939fadfe37595a53b9e
```

La imagen oficial contiene `llama-server` como entrypoint. Kubernetes debe usar
`args:` y no repetir el nombre del binario. El digest corresponde al indice
multi-arquitectura; el manifest `amd64` esta incluido.

## Arquitectura

```text
Hermes
  |
  | OpenAI-compatible, model=qwen3.6-27b
  v
LiteLLM (llms, LoadBalancer 192.168.1.241:4000)
  |
  | Bearer key interno, desde Secret
  | http://llama-server.llms.svc.cluster.local:8080/v1
  v
llama-server (llms)
  |-- capas que caben -> RTX 4070 Ti SUPER
  |-- capas restantes -> RAM
  |-- KV cache inicial -> RAM
  `-- GGUF -> PVC local-path de 30 GiB
```

No se crea un `LoadBalancer` para llama.cpp. LiteLLM sigue siendo el unico
endpoint de la LAN y la politica de red permite ingress al pod desde LiteLLM.

## Decisiones de memoria

Configuracion inicial conservadora:

```text
--n-gpu-layers auto
--fit on
--fit-target 2048
--ctx-size 65536
--parallel 1
--cache-type-k f16
--cache-type-v f16
--no-kv-offload
--cache-ram 0
```

Motivos:

- `auto` selecciona cuantas capas caben en VRAM.
- `--fit-target 2048` intenta conservar 2 GiB de margen en la GPU.
- El archivo pesa casi toda la VRAM fisica; full offload no es realista junto a
  buffers, CUDA y el escritorio.
- `--no-kv-offload` mantiene el KV en RAM y prioriza VRAM para pesos.
- `--cache-ram 0` deshabilita inicialmente el cache adicional de checkpoints,
  que en este build reservaria hasta 8 GiB de RAM aparte del KV.
- F16/F16 es el baseline compatible. Q8/Q8 se evalua despues, no antes.
- Un solo slot evita multiplicar presion de memoria durante la primera prueba.
- 65,536 tokens es menor que el contexto nativo, pero reduce memoria y tiempo de
  prefill. El fabricante recomienda 128K para conservar mejor las capacidades
  de thinking; eso queda como tuning posterior sujeto a medicion.

El pod solicita 24 GiB y tiene limite de 48 GiB. La solicitud es un punto de
partida, no una medicion: registrar el pico RSS de una sesion larga y ajustarla
antes de considerar estable el servicio. No se debe desplegar en una maquina de
32 GiB sin reducir contexto y recalcular el presupuesto.

## Archivos implementados

```text
kubernetes/llama-service/
├── README.md
├── deployment.yaml
├── kustomization.yaml
├── model-download-job.yaml
├── networkpolicy.yaml
├── pvc.yaml
└── service.yaml
```

`kustomization.yaml` no incluye el Job para evitar que un cambio futuro a su
template inmutable bloquee una reconciliacion normal. El Job se aplica de forma
explicita.

## Procedimiento

Todos los bloques operativos usan sintaxis de Bash (`set -euo pipefail`,
`[[ ... ]]` y `mapfile`) y comparten variables para el rollback. Ejecutarlos en
una unica sesion Bash. Si el shell actual es otro, iniciar primero:

```bash
exec bash
```

### Fase 0 - Preflight

Ejecutar desde la raiz del repositorio:

```bash
kubectl get node trantor
kubectl get runtimeclass nvidia
kubectl get node trantor \
  -o jsonpath='{.status.allocatable.nvidia\.com/gpu}{" GPU\n"}'
nvidia-smi
free -h
df -h /var/lib/rancher/k3s/storage
```

Confirmar:

- Nodo `Ready` y label `workload=llm`.
- `nvidia.com/gpu: 1` allocatable.
- RuntimeClass `nvidia` presente.
- Al menos 30 GiB libres para el PVC.
- Al menos 48 GiB de RAM razonablemente disponibles para el limite elegido.
- Solo una GPU fisica.

Listar todos los pods que solicitan GPU:

```bash
kubectl get pods --all-namespaces -o json | jq -r '
  .items[]
  | select(any(((.spec.initContainers // []) + (.spec.containers // []))[];
      ([.resources.requests["nvidia.com/gpu"] // "0",
        .resources.limits["nvidia.com/gpu"] // "0"]
       | map(tonumber) | max) > 0))
  | [.metadata.namespace, .metadata.name, .status.phase]
  | @tsv'
```

No continuar si aparece un consumidor desconocido.

### Fase 1 - Crear Secrets y recursos permanentes

Los valores deben generarse y guardarse en un password manager. No crear un
manifest de Secret en el repositorio. El master key sera el bearer token de
Hermes hacia LiteLLM; el segundo key solo autentica LiteLLM hacia llama.cpp.

```bash
exec bash
set -euo pipefail

read -rsp 'Nuevo LiteLLM master key: ' LITELLM_MASTER_KEY; echo
read -rsp 'Nuevo llama.cpp internal key: ' LLAMA_API_KEY; echo
[[ "$LITELLM_MASTER_KEY" == sk-* && ${#LITELLM_MASTER_KEY} -ge 35 ]]
[[ "$LLAMA_API_KEY" == sk-* && ${#LLAMA_API_KEY} -ge 35 ]]
kubectl -n llms create secret generic litellm-auth \
  --from-literal=master-key="$LITELLM_MASTER_KEY" \
  --from-literal=llama-api-key="$LLAMA_API_KEY" \
  --dry-run=client -o yaml | kubectl apply -f -
unset LITELLM_MASTER_KEY LLAMA_API_KEY
kubectl apply -k kubernetes/llama-service
kubectl -n llms get pvc llama-models
kubectl -n llms get deployment llama-server
kubectl -n llms get service llama-server
kubectl -n llms get networkpolicy allow-litellm-ingress
kubectl -n llms get secret litellm-auth
```

Resultado esperado:

- Recursos creados dentro del namespace existente `llms`.
- PVC creado o esperando al primer consumidor.
- Deployment con `0/0` replicas.
- Service `ClusterIP` en 8080.
- Secrets presentes sin mostrar su contenido.
- Ningun pod llama.cpp y ningun uso adicional de GPU.

### Fase 2 - Descargar el modelo sin interrumpir vLLM

El Job no solicita GPU, por lo que puede ejecutarse mientras vLLM sigue
atendiendo. Si existe un Job anterior con ese nombre y se necesita repetir:

```bash
kubectl -n llms delete job download-qwen36-27b-iq4-xs \
  --ignore-not-found
kubectl apply -f kubernetes/llama-service/model-download-job.yaml
kubectl -n llms wait \
  --for=condition=Complete job/download-qwen36-27b-iq4-xs \
  --timeout=4h10m
kubectl -n llms logs job/download-qwen36-27b-iq4-xs
```

En una descarga nueva, los logs deben contener la verificacion del archivo
final y el mensaje de descarga. En una repeticion idempotente deben mostrar la
verificacion y `Model already present and verified.`:

```text
/models/Qwen3.6-27B-IQ4_XS.gguf: OK
Model downloaded and verified
```

El Job usa descarga atomica `.partial`, comprueba el tamano y SHA-256 y solo
entonces renombra el archivo final. No borrar el PVC despues de descargar.

### Fase 3 - Preparar mantenimiento

Guardar el estado actual en archivos privados. Mantener `LLAMA_SWITCH_STATE`
en la misma shell para poder restaurar los valores exactos:

```bash
exec bash
set -euo pipefail

export LLAMA_SWITCH_STATE="$(mktemp -d /tmp/llama-switch.XXXXXX)"
chmod 700 "$LLAMA_SWITCH_STATE"
for deployment in vllm vllm-big-model vllm-small-model; do
  kubectl -n llms get deployment "$deployment" \
    -o jsonpath='{.spec.replicas}' > "$LLAMA_SWITCH_STATE/$deployment.replicas"
done
hermes config get model.default > "$LLAMA_SWITCH_STATE/model.default"
hermes config get model.context_length > "$LLAMA_SWITCH_STATE/model.context_length"
hermes config get model.max_tokens > "$LLAMA_SWITCH_STATE/model.max_tokens"
hermes config get compression.threshold_tokens \
  > "$LLAMA_SWITCH_STATE/compression.threshold_tokens"
hermes config get compression.proactive_prune_tokens \
  > "$LLAMA_SWITCH_STATE/compression.proactive_prune_tokens"
kubectl -n llms get configmap litellm-config -o yaml \
  > "$LLAMA_SWITCH_STATE/litellm-configmap.yaml"
for state_file in "$LLAMA_SWITCH_STATE"/*; do
  test -s "$state_file"
done
VLLM_REPLICAS="$(< "$LLAMA_SWITCH_STATE/vllm.replicas")"
VLLM_BIG_REPLICAS="$(< "$LLAMA_SWITCH_STATE/vllm-big-model.replicas")"
VLLM_SMALL_REPLICAS="$(< "$LLAMA_SWITCH_STATE/vllm-small-model.replicas")"
[[ "$VLLM_REPLICAS" =~ ^[0-9]+$ ]]
[[ "$VLLM_BIG_REPLICAS" =~ ^[0-9]+$ ]]
[[ "$VLLM_SMALL_REPLICAS" =~ ^[0-9]+$ ]]
(( VLLM_REPLICAS + VLLM_BIG_REPLICAS + VLLM_SMALL_REPLICAS <= 1 ))
(( VLLM_BIG_REPLICAS == 0 && VLLM_SMALL_REPLICAS == 0 ))
unset VLLM_REPLICAS VLLM_BIG_REPLICAS VLLM_SMALL_REPLICAS
for scaledobject in vllm-big-model vllm-small-model; do
  test -z "$(kubectl -n llms get scaledobject.keda.sh/$scaledobject \
    -o jsonpath='{.metadata.annotations.autoscaling\.keda\.sh/paused-replicas}')"
  test "$(kubectl -n llms get scaledobject.keda.sh/$scaledobject \
    -o jsonpath='{.status.conditions[?(@.type=="Paused")].status}')" = False
done
kubectl -n llms get deployment vllm vllm-big-model vllm-small-model
kubectl -n llms get pods -o wide
kubectl -n llms get deployment llama-server
hermes backup --quick --label before-llama-service
sudo systemctl stop hermes-gateway.service
```

No aplicar todavia `litellm-config.yaml`: la ruta versionada de
`qwen3.6-27b` ya apunta a llama.cpp y fallaria hasta que el nuevo pod este
Ready. La ruta `qwen3` sigue apuntando a vLLM durante esta fase.

Las validaciones abortan si los targets KEDA no estan ya en cero o si los
replica counts capturados podrian iniciar mas de un consumidor GPU. Confirmar
tambien que los dos ScaledObjects muestran `PAUSED=False`; si ya estaban
pausados, detenerse para no sobrescribir estado de otro operador.
Hermes queda detenido para drenar nuevas tareas y evitar una recarga parcial de
su configuracion. El cambio tiene downtime mientras llama.cpp carga.

### Fase 4 - Deshabilitar vLLM y liberar la GPU

Pausar primero los ScaledObjects generados por KEDA HTTP Add-on. Anotar solo los
HTTPScaledObjects no basta: en este cluster el controlador genera
`ScaledObject/vllm-big-model` y `ScaledObject/vllm-small-model`, que son los
recursos observados por KEDA.

```bash
set -euo pipefail

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
kubectl -n llms scale deployment/vllm --replicas=0
kubectl -n llms scale deployment/vllm-big-model --replicas=0
kubectl -n llms scale deployment/vllm-small-model --replicas=0
```

Esperar todos los pods no terminales de los tres Deployments. El selector de
fase ignora pods historicos `Succeeded`/`Failed`, que no consumen GPU:

```bash
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
kubectl -n llms get pods -o wide
nvidia-smi
```

Los HTTPScaledObjects permanecen creados, pero sus ScaledObjects no pueden
reactivar los Deployments mientras tengan `paused-replicas: "0"`. Mantener esa
anotacion durante todo el switch y cualquier rollback, hasta que el motor que
debe conservar la GPU este Ready.

Volver a ejecutar el inventario GPU del preflight. No escalar llama.cpp si
queda un pod GPU Running/Terminating o si un proceso externo consume la VRAM que
se pretende reservar.

### Fase 5 - Arrancar llama.cpp

```bash
test "$(kubectl -n llms get deployment/llama-server-q3 \
  -o jsonpath='{.spec.replicas}')" = 0
test "$(kubectl -n llms get deployment/llama-server-q6 \
  -o jsonpath='{.spec.replicas}')" = 0
kubectl -n llms scale deployment/llama-server --replicas=1
kubectl -n llms rollout status deployment/llama-server --timeout=35m
kubectl -n llms get pods -l app=llama-server -o wide
kubectl -n llms logs deployment/llama-server --tail=300
```

Validar en logs:

- Build `b10156`.
- Arquitectura `qwen35` reconocida.
- Chat template Jinja cargado desde GGUF.
- Numero efectivo de capas offloaded a CUDA.
- Memoria VRAM estimada y margen de fit.
- Contexto efectivo 65,536.
- Servidor escuchando en `0.0.0.0:8080`.
- Sin `unknown model architecture`, CUDA OOM ni error de KV cache.

Si falla, ejecutar el rollback inmediato de la Fase 10. No aumentar memoria o
desactivar controles de seguridad sin identificar primero el error real.

### Fase 6 - Smoke test directo

Usar un port-forward temporal y leer el key desde el Secret sin imprimirlo.
`/health` es publicamente legible por diseno; los endpoints de inferencia deben
exigir bearer token:

```bash
set -euo pipefail

kubectl -n llms port-forward service/llama-server 18080:8080 \
  > /tmp/llama-port-forward.log 2>&1 &
LLAMA_PORT_FORWARD_PID=$!
trap 'kill "$LLAMA_PORT_FORWARD_PID" 2>/dev/null || true' EXIT
sleep 2
LLAMA_API_KEY="$(kubectl -n llms get secret litellm-auth \
  -o jsonpath='{.data.llama-api-key}' | base64 --decode)"
curl --fail --silent --show-error http://127.0.0.1:18080/health
INVALID_STATUS="$(curl --silent --output /dev/null --write-out '%{http_code}' \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer definitely-invalid' \
  -d '{"model":"qwen3.6-27b","messages":[{"role":"user","content":"test"}],"max_tokens":1}' \
  http://127.0.0.1:18080/v1/chat/completions)"
case "$INVALID_STATUS" in
  401|403) ;;
  *) printf 'ERROR: llama.cpp invalid-key status=%s\n' "$INVALID_STATUS" >&2; exit 1 ;;
esac
curl --fail --silent --show-error \
  -H "Authorization: Bearer $LLAMA_API_KEY" \
  http://127.0.0.1:18080/v1/models
curl --fail --silent --show-error \
  -H "Authorization: Bearer $LLAMA_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen3.6-27b","messages":[{"role":"user","content":"Responde exactamente OK"}],"max_tokens":32}' \
  http://127.0.0.1:18080/v1/chat/completions
unset LLAMA_API_KEY INVALID_STATUS
kill "$LLAMA_PORT_FORWARD_PID"
trap - EXIT
```

Debe devolver:

```json
{"status":"ok"}
```

El test debe anunciar `qwen3.6-27b` y completar el chat. `/v1/models` puede ser
publico en este build, por lo que la prueba negativa usa el endpoint protegido
de chat. Cualquier respuesta distinta de 401/403 bloquea el despliegue.

### Fase 7 - Conectar LiteLLM

La entrada versionada es:

```yaml
- model_name: qwen3.6-27b
  litellm_params:
    model: openai/qwen3.6-27b
    api_base: http://llama-server.llms.svc.cluster.local:8080/v1
    api_key: os.environ/LLAMA_API_KEY
  model_info:
    id: llama-cpp-qwen36-27b
    mode: chat
    max_tokens: 65536
    max_input_tokens: 49152
    max_output_tokens: 16384
```

Aplicar y reiniciar LiteLLM:

```bash
set -euo pipefail

kubectl apply -f kubernetes/proxy/litellm-config.yaml
kubectl -n llms rollout restart deployment/litellm
kubectl -n llms rollout status deployment/litellm --timeout=5m
kubectl -n llms logs deployment/litellm --since=5m
```

LiteLLM queda fijado a `v1.93.0`, usa `Recreate`, tiene readiness probe y carga
`LITELLM_MASTER_KEY`/`LLAMA_API_KEY` desde `Secret/llms/litellm-auth`. No usa
Postgres ni virtual keys; `allow_requests_on_db_unavailable` es `false` para
que una falla de DB nunca cree identidades `INTERNAL_USER`. El callback
`MaxOutputTokensCap` limita `max_tokens`, `max_completion_tokens` y Responses
API a 16,384 para esta ruta, incluso si el cliente usa el nombre publico, el
nombre upstream o el deployment ID y pide un valor mayor. Tambien sanea
`n_predict` y los overrides dentro de `extra_body`, y cubre Chat Completions,
Text Completions, Responses y Anthropic Messages.

Probar desde el host sin escribir la key en el historial:

```bash
read -rsp 'LiteLLM API key: ' LITELLM_API_KEY
echo
INVALID_STATUS="$(curl --silent --output /dev/null --write-out '%{http_code}' \
  -H 'Authorization: Bearer definitely-invalid' \
  http://192.168.1.241:4000/v1/models)"
case "$INVALID_STATUS" in
  400|401|403) ;;
  *) printf 'ERROR: LiteLLM invalid-key status=%s\n' "$INVALID_STATUS" >&2; exit 1 ;;
esac
curl --fail --silent --show-error \
  -H "Authorization: Bearer $LITELLM_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen3.6-27b","messages":[{"role":"user","content":"Responde exactamente OK"}],"max_tokens":32}' \
  http://192.168.1.241:4000/v1/chat/completions
unset LITELLM_API_KEY
```

### Fase 8 - Cambiar Hermes

El servidor tiene 65,536 tokens de contexto. Hermes no debe seguir anunciando
98,304 para este modelo ni reservar 32,768 de salida sin margen. Actualizar
primero el secreto `OPENAI_API_KEY` de Hermes para que coincida con
`litellm-auth/master-key`; usar el backend de secretos configurado o el archivo
devuelto por `hermes config env-path`, sin imprimir el valor. No guardarlo en
`config.yaml`. Como el endpoint es una IP privada, Hermes bloquea el fallback
automatico de `OPENAI_API_KEY`; guardar solo la referencia de entorno explicita.

```bash
hermes config set model.api_key '${env:OPENAI_API_KEY}'
hermes config set model.context_length 65536
hermes config set model.max_tokens 16384
hermes config set compression.threshold_tokens 40000
hermes config set compression.proactive_prune_tokens 32000
hermes config set auxiliary.compression.context_length 65536
hermes config set auxiliary.compression.timeout 1800
hermes config set auxiliary.title_generation.timeout 300
hermes config set model.default qwen3.6-27b
hermes config get model
hermes config get compression
sudo systemctl start hermes-gateway.service
```

Verificar:

```bash
hermes -z 'Responde exactamente: OK'
hermes gateway status
```

Despues probar desde Telegram. Vigilar en paralelo:

```bash
kubectl -n llms logs -f deployment/llama-server
watch -n 1 nvidia-smi
```

### Fase 9 - Validar tool calling

Un chat simple no demuestra que Hermes pueda operar como agente. Crear una
conversacion nueva y pedir una accion no destructiva que fuerce una herramienta,
por ejemplo consultar `pwd` y listar un directorio de prueba.

Confirmar:

- llama.cpp devuelve `tool_calls` o el formato que Hermes transforma en tool use.
- Los argumentos anidados sobreviven al chat template.
- El reasoning no se mezcla de forma que rompa el parser.
- Hermes ejecuta la herramienta una sola vez y continua la conversacion.
- No aparecen loops de tool call ni errores de contexto.

Si tool calling falla, mantener llama.cpp disponible para pruebas pero volver
Hermes a `qwen3` hasta validar un build/chat template compatible.

### Fase 10 - Rollback inmediato a vLLM

KEDA debe seguir pausado. Detener Hermes para que no observe una combinacion
parcial de los valores restaurados:

```bash
set -euo pipefail

test -n "${LLAMA_SWITCH_STATE:-}"
test -d "$LLAMA_SWITCH_STATE"
for state_file in \
  vllm.replicas vllm-big-model.replicas vllm-small-model.replicas \
  model.default model.context_length model.max_tokens \
  compression.threshold_tokens compression.proactive_prune_tokens; do
  test -s "$LLAMA_SWITCH_STATE/$state_file"
done
[[ "$(< "$LLAMA_SWITCH_STATE/vllm.replicas")" =~ ^[0-9]+$ ]]
[[ "$(< "$LLAMA_SWITCH_STATE/vllm-big-model.replicas")" == 0 ]]
[[ "$(< "$LLAMA_SWITCH_STATE/vllm-small-model.replicas")" == 0 ]]
sudo systemctl stop hermes-gateway.service
```

Detener ambas variantes de llama.cpp y esperar que liberen la GPU:

```bash
kubectl -n llms scale deployment/llama-server --replicas=0
kubectl -n llms scale deployment/llama-server-q3 --replicas=0
kubectl -n llms scale deployment/llama-server-q6 --replicas=0
LLAMA_GPU_POD_NAMES="$(kubectl -n llms get pods \
  -l 'app in (llama-server,llama-server-q3,llama-server-q6)' \
  --field-selector='status.phase!=Succeeded,status.phase!=Failed' \
  -o name)"
LLAMA_GPU_PODS=()
if [[ -n "$LLAMA_GPU_POD_NAMES" ]]; then
  mapfile -t LLAMA_GPU_PODS <<< "$LLAMA_GPU_POD_NAMES"
  kubectl -n llms wait --for=delete "${LLAMA_GPU_PODS[@]}" --timeout=5m
fi
nvidia-smi
```

Restaurar las replicas capturadas. En el estado normal de este cluster los dos
targets KEDA eran cero; no continuar si los tres archivos intentan arrancar mas
de un consumidor GPU:

```bash
kubectl -n llms scale deployment/vllm \
  --replicas="$(< "$LLAMA_SWITCH_STATE/vllm.replicas")"
kubectl -n llms scale deployment/vllm-big-model \
  --replicas="$(< "$LLAMA_SWITCH_STATE/vllm-big-model.replicas")"
kubectl -n llms scale deployment/vllm-small-model \
  --replicas="$(< "$LLAMA_SWITCH_STATE/vllm-small-model.replicas")"
kubectl -n llms rollout status deployment/vllm --timeout=20m
kubectl -n llms logs deployment/vllm --tail=200
```

Restaurar la configuracion anterior de Hermes:

```bash
hermes config set model.context_length \
  "$(< "$LLAMA_SWITCH_STATE/model.context_length")"
hermes config set model.max_tokens "$(< "$LLAMA_SWITCH_STATE/model.max_tokens")"
hermes config set compression.threshold_tokens \
  "$(< "$LLAMA_SWITCH_STATE/compression.threshold_tokens")"
hermes config set compression.proactive_prune_tokens \
  "$(< "$LLAMA_SWITCH_STATE/compression.proactive_prune_tokens")"
hermes config set model.default "$(< "$LLAMA_SWITCH_STATE/model.default")"
sudo systemctl start hermes-gateway.service
hermes -z 'Responde exactamente: OK'
```

Solo despues de que el motor restaurado este Ready y la prueba de Hermes pase,
reanudar KEDA:

```bash
test "$(kubectl -n llms get scaledobject.keda.sh/vllm-big-model \
  -o jsonpath='{.metadata.annotations.autoscaling\.keda\.sh/paused-replicas}')" = 0
test "$(kubectl -n llms get scaledobject.keda.sh/vllm-small-model \
  -o jsonpath='{.metadata.annotations.autoscaling\.keda\.sh/paused-replicas}')" = 0
kubectl -n llms annotate scaledobject.keda.sh/vllm-big-model \
  autoscaling.keda.sh/paused-replicas-
kubectl -n llms annotate scaledobject.keda.sh/vllm-small-model \
  autoscaling.keda.sh/paused-replicas-
kubectl -n llms get scaledobject.keda.sh vllm-big-model vllm-small-model
```

La entrada LiteLLM `qwen3` nunca cambio y vuelve a funcionar al quedar vLLM
Ready. La entrada `qwen3.6-27b` permanece apuntando a llama.cpp y estara no
disponible mientras su Deployment tenga cero replicas. Esto es un rollback del
motor, no del catalogo. El ConfigMap capturado es solo referencia de estado: no
reaplicarlo porque puede contener la configuracion fail-open anterior. Un
rollback completo del catalogo debe retirar la ruta de llama.cpp manteniendo el
master key desde Secret y `allow_requests_on_db_unavailable: false`.

## Tuning posterior

Cambiar una variable por vez y medir prompt processing, generacion, RAM y VRAM.

### Resultado de `--fit-target 2048`

El 29 de julio de 2026 se compararon `4096` y `2048` con el mismo build,
modelo, contexto, threads y KV cache. El benchmark uso el endpoint nativo
`/completion`, `cache_prompt=false`, semilla fija y 256 tokens con
`ignore_eos=true`.

| Target | Prompt | Capas CUDA | Buffer CUDA | Buffer CPU | Prompt tok/s | Generacion tok/s |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 4096 MiB | 3,744 | 44/65 | 9,749.51 MiB | 4,964.75 MiB | 743.63 | 6.53 |
| 2048 MiB | 3,744 | 54/65 | 11,777.10 MiB | 2,937.16 MiB | 897.22 | 7.78 |
| 4096 MiB | 31,454 | 44/65 | 9,749.51 MiB | 4,964.75 MiB | 697.27 | 2.90 |
| 2048 MiB | 31,454 | 54/65 | 11,777.10 MiB | 2,937.16 MiB | 828.33 | 3.46 |

El cambio offloadea diez capas adicionales y mejora la generacion cerca de
19% tanto con contexto corto como con 31.5K tokens. Durante la medicion larga
quedaron aproximadamente 1,945 MiB de VRAM libres, incluyendo el consumo del
escritorio. No hubo OOM, reinicios ni errores CUDA, por lo que `2048` queda
como valor operativo. El KV F16 permanece en CPU y ocupa 4,096 MiB; Flash
Attention y los kernels fusionados de Gated DeltaNet quedaron habilitados.

### Mayor rendimiento

1. Probar quitar `--no-kv-offload` para mover KV a GPU.
2. Mantener `--fit-target 2048` durante la primera prueba.
3. Comparar numero efectivo de capas CUDA antes y despues.
4. Probar KV `q8_0/q8_0` solo si Flash Attention se activa y el servidor carga
   sin errores.
5. Ajustar `--threads` y `--threads-batch` segun tokens/s, no solo CPU usage.

### Mayor contexto

Escalones sugeridos:

```text
65536 -> 98304 -> 131072
```

Por cada cambio:

1. Escalar llama.cpp a cero.
2. Editar `--ctx-size`.
3. Arrancar y revisar memoria.
4. Actualizar `model.context_length`, `max_tokens` y thresholds de Hermes.
5. Probar una sesion larga real.

No configurar 262,144 solo porque sea el maximo nativo. El contexto consume
memoria y tiempo de prefill, y Hermes necesita margen para output y herramientas.

### Vision

Este despliegue es text-only. Para imagen se requiere descargar un `mmproj`
compatible, validar su SHA y agregar `--mmproj`. Eso consume aproximadamente
0.9 GiB adicionales para F16 y cambia el presupuesto VRAM/RAM. Debe tratarse en
un cambio separado.

## Fallos esperables

| Sintoma | Causa probable | Accion |
| --- | --- | --- |
| Pod `Pending` por GPU | Todavia existe otro pod GPU | Mantener llama en cero, encontrar y detener el consumidor. |
| `unknown model architecture` | Build llama.cpp sin soporte `qwen35` | Actualizar a otro build fijado y volver a validar flags/digest. |
| CUDA OOM al cargar | Demasiadas capas o VRAM externa ocupada | Reducir offload/margen, liberar procesos externos; no usar `all`. |
| OOMKilled | Limite RAM insuficiente | Revisar uso real, reducir contexto o aumentar limite con margen para el nodo. |
| `/health` devuelve 503 | Modelo todavia cargando | Esperar startup probe y seguir logs. |
| Chat funciona pero tools no | Template/parser incompatible | Volver Hermes a qwen3 y validar otro build/template. |
| Respuesta corta o thinking truncado | Contexto/output insuficiente | Medir tokens y ajustar juntos servidor y Hermes. |
| vLLM reaparece | KEDA o un operador lo escalo | Identificar el trigger; no permitir requests a rutas cold-start durante el switch. |

## Seguridad

- `llama-server` no se expone por LoadBalancer.
- `llama-server` exige un bearer key montado desde Secret; LiteLLM recibe la
  copia correspondiente mediante `secretKeyRef`.
- El pod corre UID/GID 1000, sin capabilities y con root filesystem read-only.
- El volumen del modelo se monta read-only en el servidor.
- Los hashes e imagenes estan fijados.
- No se habilita la Web UI ni los built-in tools de llama.cpp.
- NetworkPolicy es defensa adicional. En el estado verificado del cluster K3s
  arranca con `--disable-network-policy`, por lo que actualmente no se aplica;
  la autenticacion interna sigue protegiendo la API frente a otros pods. La
  policy debe habilitarse y probarse en una ventana separada de mantenimiento.
- LiteLLM falla cerrado, usa un master key desde Secret y no acepta bearer
  tokens desconocidos cuando no hay Postgres.

## Criterios de aceptacion

1. El Job termina y valida SHA-256.
2. Ningun vLLM GPU pod esta activo al arrancar llama.cpp.
3. `llama-server` queda Ready sin OOM ni arquitectura desconocida.
4. `/v1/models` anuncia `qwen3.6-27b`.
5. llama.cpp devuelve 401/403 para un key invalido en chat y el chat autenticado responde.
6. LiteLLM devuelve 400/401/403 para un bearer token desconocido y responde para `qwen3.6-27b` con el master key.
7. Hermes usa contexto 65,536 y responde por CLI/Telegram.
8. Una herramienta no destructiva completa el ciclo de tool calling.
9. El rollback restaura `qwen3` sin borrar el PVC de llama.cpp.
10. Los manifiestos versionados conservan `replicas: 0` para llama.cpp.
11. KEDA permanece pausado durante todo el uso de llama.cpp y solo se reanuda despues del rollback validado.


-----
Importante: los objetos Service no tienen réplicas. Escalar los Deployment a cero deshabilita vLLM sin eliminar Services, PVC ni configuración.
1. Preparar llama.cpp
Esto no interrumpe vLLM y deja llama-server en cero réplicas.
exec bash
set -euo pipefail

read -rsp 'Nuevo LiteLLM master key: ' LITELLM_MASTER_KEY
echo
read -rsp 'Nuevo llama.cpp internal key: ' LLAMA_API_KEY
echo

[[ "$LITELLM_MASTER_KEY" == sk-* && ${#LITELLM_MASTER_KEY} -ge 35 ]]
[[ "$LLAMA_API_KEY" == sk-* && ${#LLAMA_API_KEY} -ge 35 ]]

kubectl -n llms create secret generic litellm-auth \
  --from-literal=master-key="$LITELLM_MASTER_KEY" \
  --from-literal=llama-api-key="$LLAMA_API_KEY" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl apply -k kubernetes/llama-service

kubectl -n llms get deployment llama-server
Debe mostrar 0/0 para llama-server.
2. Descargar el modelo
Puede ejecutarse mientras vLLM sigue funcionando:
kubectl -n llms delete job download-qwen36-27b-iq4-xs \
  --ignore-not-found

kubectl apply \
  -f kubernetes/llama-service/model-download-job.yaml

kubectl -n llms wait \
  --for=condition=Complete \
  job/download-qwen36-27b-iq4-xs \
  --timeout=4h10m

kubectl -n llms logs job/download-qwen36-27b-iq4-xs
Los logs deben confirmar el SHA-256 del archivo final.
3. Guardar las réplicas actuales
Ejecutar todo el cambio en la misma sesión de Bash:
VLLM_REPLICAS="$(kubectl -n llms get deployment/vllm \
  -o jsonpath='{.spec.replicas}')"

VLLM_BIG_REPLICAS="$(kubectl -n llms get deployment/vllm-big-model \
  -o jsonpath='{.spec.replicas}')"

VLLM_SMALL_REPLICAS="$(kubectl -n llms get deployment/vllm-small-model \
  -o jsonpath='{.spec.replicas}')"

printf 'vllm=%s big=%s small=%s\n' \
  "$VLLM_REPLICAS" \
  "$VLLM_BIG_REPLICAS" \
  "$VLLM_SMALL_REPLICAS"
Actualmente deberían ser 1, 0, 0.
4. Pausar KEDA
Esto evita que una solicitud vuelva a iniciar los deployments cold-start:
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
5. Detener Hermes y deshabilitar vLLM
sudo systemctl stop hermes-gateway.service

kubectl -n llms scale deployment/vllm --replicas=0
kubectl -n llms scale deployment/vllm-big-model --replicas=0
kubectl -n llms scale deployment/vllm-small-model --replicas=0
Esto no elimina ningún Service.
6. Esperar que liberen la GPU
VLLM_GPU_POD_NAMES="$(kubectl -n llms get pods \
  -l 'app in (vllm,vllm-big-model,vllm-small-model)' \
  --field-selector='status.phase!=Succeeded,status.phase!=Failed' \
  -o name)"

VLLM_GPU_PODS=()

if [[ -n "$VLLM_GPU_POD_NAMES" ]]; then
  mapfile -t VLLM_GPU_PODS <<< "$VLLM_GPU_POD_NAMES"
fi

if (( ${#VLLM_GPU_PODS[@]} > 0 )); then
  kubectl -n llms wait \
    --for=delete "${VLLM_GPU_PODS[@]}" \
    --timeout=5m
fi

VLLM_GPU_POD_NAMES="$(kubectl -n llms get pods \
  -l 'app in (vllm,vllm-big-model,vllm-small-model)' \
  --field-selector='status.phase!=Succeeded,status.phase!=Failed' \
  -o name)"

test -z "$VLLM_GPU_POD_NAMES"

nvidia-smi
No continuar si aparece otro proceso consumiendo la VRAM necesaria.
7. Habilitar llama.cpp
kubectl -n llms scale deployment/llama-server --replicas=1

kubectl -n llms rollout status \
  deployment/llama-server \
  --timeout=35m

kubectl -n llms get pods -l app=llama-server -o wide
kubectl -n llms logs deployment/llama-server --tail=300
Verificar que no existan errores CUDA OOM, arquitectura desconocida o KV cache.
8. Publicar la ruta en LiteLLM
kubectl apply -f kubernetes/proxy/litellm-config.yaml

kubectl -n llms rollout restart deployment/litellm

kubectl -n llms rollout status \
  deployment/litellm \
  --timeout=5m

kubectl -n llms logs deployment/litellm --since=5m
El endpoint interno ahora es:
http://llama-server.llms.svc.cluster.local:8080/v1
9. Actualizar y arrancar Hermes
Actualizar OPENAI_API_KEY con el nuevo LITELLM_MASTER_KEY mediante el backend de secretos configurado por Hermes. Después:
hermes config set model.context_length 65536
hermes config set model.max_tokens 16384
hermes config set compression.threshold_tokens 40000
hermes config set compression.proactive_prune_tokens 32000
hermes config set model.default qwen3.6-27b

sudo systemctl start hermes-gateway.service

hermes -z 'Responde exactamente: OK'
hermes gateway status
10. Verificar que los Services siguen presentes
kubectl -n llms get services \
  vllm vllm-big-model vllm-small-model llama-server litellm

kubectl -n llms get deployments \
  vllm vllm-big-model vllm-small-model llama-server
Estado esperado:
vllm               0
vllm-big-model     0
vllm-small-model   0
llama-server       1

---

## Agregar un modole big qwen 3.6 de 27B cuantizado a 6 bits

El deploy Q6 quedó preparado en llms, separado del IQ4_XS y con replicas: 0 por defecto.
Compatibilidad
- Archivo: Qwen3.6-27B-UD-Q6_K_XL.gguf
- Tamaño: 25,636,485,344 bytes, 23.876 GiB
- SHA-256: 8746881d40f280b1b6b858c656a347c754ed3d9cc8d2e1ad46b3635b87f611f8
- GPU: RTX 4070 Ti SUPER, 16,376 MiB, SM 8.9
- llama.cpp: compatible con qwen35, Q6_K_XL, Gated DeltaNet y CUDA Ada
- Contexto configurado: 65,536 tokens
- Uso: text-only; visión requiere descargar mmproj
No cabe completamente en VRAM. El despliegue reserva 4 GiB de margen y usa inferencia híbrida:
- Aproximadamente 12 GiB máximos para pesos y buffers CUDA.
- El resto de los pesos permanece en RAM.
- El KV F16 de 65K consume aproximadamente 4 GiB adicionales de RAM.
- Request: 32 GiB RAM.
- Límite: 44 GiB RAM.
Es compatible y debería cargar, pero será sensiblemente más lento que IQ4_XS por ejecutar más capas desde CPU/RAM. A cambio, Q6 ofrece mayor fidelidad y menor pérdida por cuantización.
Archivos
- kubernetes/llama-service/deployment-q6.yaml
- kubernetes/llama-service/model-download-q6-job.yaml
- kubernetes/llama-service/pvc-q6.yaml
- kubernetes/llama-service/service-q6.yaml
- specs/006_qwen36_27b_ud_q6_xl.md
La ruta LiteLLM es qwen3.6-27b-q6.
Descargar
Ejecutar dentro de Bash:
set -euo pipefail

kubectl apply -k kubernetes/llama-service

kubectl -n llms delete job download-qwen36-27b-ud-q6-k-xl \
  --ignore-not-found

kubectl apply \
  -f kubernetes/llama-service/model-download-q6-job.yaml

kubectl -n llms wait \
  --for=condition=Complete \
  job/download-qwen36-27b-ud-q6-k-xl \
  --timeout=6h10m

kubectl -n llms logs job/download-qwen36-27b-ud-q6-k-xl
Activar
Solo después de pausar KEDA y dejar vLLM en cero:
sudo systemctl stop hermes-gateway.service

kubectl -n llms scale deployment/llama-server --replicas=0
kubectl -n llms scale deployment/llama-server-q6 --replicas=1

kubectl -n llms rollout status \
  deployment/llama-server-q6 \
  --timeout=45m

kubectl -n llms logs deployment/llama-server-q6 --tail=300
Después:
kubectl apply -f kubernetes/proxy/litellm-config.yaml
kubectl -n llms rollout restart deployment/litellm
kubectl -n llms rollout status deployment/litellm --timeout=5m

hermes config set model.default qwen3.6-27b-q6
sudo systemctl start hermes-gateway.service
Los manifiestos pasaron dry-run contra el API server. No se aplicó ni se inició Q6 en el clúster.
