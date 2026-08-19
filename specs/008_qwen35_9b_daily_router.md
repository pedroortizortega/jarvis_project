# 008 - Qwen3.5-9B Q6_K diario y Qwen3.6-27B bajo demanda

## Objetivo

Servir `Qwen3.5-9B-Q6_K` como modelo interactivo diario y conservar
`Qwen3.6-27B-Q3_K_S` como modelo grande bajo demanda. Hermes permanece como
servicio systemd en el host y consume LiteLLM en `192.168.1.241:4000`.
Kubernetes administra modelos, GPU y routing dentro de `llms`.

La RTX 4070 Ti SUPER expone una sola GPU. Un unico `llama-router` usa
`--models-max 1`: nunca mantiene ambos modelos cargados a la vez. El 9B se
precarga al iniciar. El 27B se activa mediante un cambio controlado que aisla
primero el trafico del gateway systemd.

## Procedencia Hugging Face

Qwen no publica GGUF oficiales de Qwen3.5-9B ni Qwen3.6-27B. Publica los pesos
base oficiales en Safetensors. Los GGUF elegidos estan alojados en Hugging Face
y su metadata declara tanto `base_model:Qwen/...` como
`base_model:quantized:Qwen/...`. Esto prueba la relacion con el upstream
oficial, pero no convierte a Unsloth en publicador oficial de Qwen.

| Perfil | Repositorio GGUF | Modelo base oficial | Revision | Archivo | SHA-256 |
| --- | --- | --- | --- | --- | --- |
| Diario | `unsloth/Qwen3.5-9B-GGUF` | `Qwen/Qwen3.5-9B` | `3885219b6810b007914f3a7950a8d1b469d598a5` | `Qwen3.5-9B-Q6_K.gguf` | `91898433cf5ce0a8f45516a4cc3e9343b6e01d052d01f684309098c66a326c59` |
| Grande | `unsloth/Qwen3.6-27B-GGUF` | `Qwen/Qwen3.6-27B` | `82d411acf4a06cfb8d9b073a5211bf410bfc29bf` | `Qwen3.6-27B-Q3_K_S.gguf` | `4afb4abcf0207a484b0d7e92c0421b74e8ce1c7a7250bb9d824b79288da68f20` |

El archivo diario mide `7,458,301,152` bytes y el grande
`12,358,727,904` bytes. Ambos usan licencia Apache-2.0.

Los recursos incluyen labels y annotations `model.huggingface.co/*` para
conservar repositorio, revision, upstream y tags. El label
`base-model-official=true` se refiere exclusivamente al modelo base `Qwen/*`.

## Intercambio seguro

La implementacion del router en llama.cpp b10156:

1. Selecciona el modelo menos usado cuando alcanza `models-max`.
2. Solicita su descarga y espera el estado `unloaded`.
3. Vuelve a comprobar el limite bajo mutex.
4. Inicia el proceso del modelo solicitado solo despues de liberar el anterior.

Una solicitud concurrente puede recibir `model limit reached, try again later`.
Cambiar de modelo durante una generacion activa la cancela; `stop-timeout=30`
evita que un proceso que no termine bloquee permanentemente el cambio. El
router prioriza exclusividad de GPU, no continuidad de solicitudes cruzadas.
El startup probe espera explicitamente al 9B con `autoload=false`. Readiness y
liveness consultan la salud del router sin seleccionar un modelo, para no
desalojar accidentalmente el 27B durante una generacion larga.

Hermes ejecuta compresion, titulos y revisiones en background con el modelo
predeterminado. Por eso no es seguro enviar una peticion 27B aislada mientras el
gateway sigue usando el 9B: los dos flujos se desalojan entre si. El script
`switch-model.sh` detiene Hermes, escala LiteLLM temporalmente a cero, precarga
el perfil elegido, actualiza `model.default` y restaura los servicios. Durante
una sesion grande, todas las tareas auxiliares de Hermes usan tambien el 27B.

## Perfiles

### Diario `qwen3.5-9b`

- Q6_K y 65,536 tokens de contexto.
- KV `q8_0` en GPU.
- `fit-target=2048` MiB.
- Se carga al arrancar el pod.

### Grande `qwen3.6-27b-q3`

- Q3_K_S y 65,536 tokens de contexto.
- KV F16 en RAM mediante `no-kv-offload`.
- `fit-target=2048` MiB.
- Se carga solo cuando la solicitud selecciona el modelo.
- LiteLLM tambien publica el alias `qwen3.6-27b` hacia este perfil.

## Recursos

```text
kubernetes/llama-service/pvc-daily.yaml
kubernetes/llama-service/model-download-daily-job.yaml
kubernetes/llama-service/router-config.yaml
kubernetes/llama-service/deployment-router.yaml
kubernetes/llama-service/service-router.yaml
kubernetes/llama-service/switch-model.sh
```

Los Deployments de IQ4, Q3 independiente, Q6 y vLLM permanecen disponibles
pero deben tener cero replicas mientras `llama-router` este activo.

## Activacion

```bash
set -euo pipefail

kubectl apply -f kubernetes/llama-service/pvc-daily.yaml
kubectl -n llms delete job download-qwen35-9b-q6-k --ignore-not-found
kubectl apply -f kubernetes/llama-service/model-download-daily-job.yaml
kubectl -n llms wait --for=condition=Complete \
  job/download-qwen35-9b-q6-k --timeout=3h10m
kubectl -n llms logs job/download-qwen35-9b-q6-k

kubectl apply -f kubernetes/llama-service/router-config.yaml
kubectl apply -f kubernetes/llama-service/deployment-router.yaml
kubectl apply -f kubernetes/llama-service/service-router.yaml

kubectl -n llms scale deployment/llama-server-q3 --replicas=0
kubectl -n llms wait --for=delete pod -l app=llama-server-q3 --timeout=5m
kubectl -n llms scale deployment/llama-router --replicas=1
kubectl -n llms rollout status deployment/llama-router --timeout=35m

kubectl apply -f kubernetes/proxy/litellm-config.yaml
kubectl -n llms rollout restart deployment/litellm
kubectl -n llms rollout status deployment/litellm --timeout=5m

hermes config set model.default qwen3.5-9b
```

No es necesario mover ni reinstalar Hermes. El gateway systemd conserva su
`base_url` de LiteLLM y solo cambia el nombre del modelo predeterminado.

Para el uso normal posterior:

```bash
cd kubernetes/llama-service
./switch-model.sh large
./switch-model.sh daily
```

## Resultado inicial

El 29 de julio de 2026 se verifico:

- Q6_K, arquitectura `qwen35`, 8.95B parametros y contexto efectivo 65,536.
- Aproximadamente 8,068 MiB de VRAM para el proceso 9B y mas de 7 GiB libres.
- LiteLLM respondio `OK` y rechazo una credencial invalida con HTTP 400.
- Hermes systemd, despues de recargar su configuracion, respondio `OK`.
- El switch aislado 9B -> 27B sostuvo una salida de 256 tokens y el regreso
  27B -> 9B respondio `DAILY_OK`.
- Decode corto observado: 82.2 tok/s con thinking en 9B; una respuesta breve
  sin thinking alcanzo entre 93 y 147 tok/s. El 27B dio 16.45 tok/s durante
  256 tokens y 21.2 tok/s en la prueba breve. Estos valores no sustituyen un
  benchmark de contexto largo.
