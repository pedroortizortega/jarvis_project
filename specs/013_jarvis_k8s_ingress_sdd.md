# JARVIS Spec 010 - Software Design Document (SDD)
## K8s Deployment: Piper TTS API con Ingress

**Estado:** Draft  
**Fecha:** 2026-07-30  
**Versión:** 1.0  
**Autor:** Pedro Ortiz

---

## 1. Resumen Ejecutivo

Este documento define la arquitectura de despliegue de **Piper TTS** en Kubernetes utilizando **Deployment + Ingress** como estrategia de exposición. La solución permite exponer la API de síntesis de voz a través de HTTPS con routing basado en paths.

---

## 2. Requisitos

### 2.1 Requisitos Funcionales
- [x] Servicio TTS local (Piper TTS v1.6.0)
- [x] Conversión texto→fonemas (es_ES)
- [x] Síntesis de audio en español
- [x] API REST para síntesis
- [x] Endpoint `/health` para monitoreo
- [ ] Endpoint `/api/tts` para síntesis
- [ ] Soporte para múltiples voces
- [x] Exportación audio (WAV, MP3)

### 2.2 Requisitos No Funcionales
- **Disponibilidad:** 99.9% (replicas: 1-2)
- **Latencia:** < 500ms (local inference)
- **Escalabilidad:** Horizontal (1-4 replicas)
- **Seguridad:** TLS/HTTPS, OAuth2
- **Recursos:** 2-4Gi RAM, 1-2 CPU/core
- **Compatibilidad:** Linux x86_64, ARM64

---

## 3. Arquitectura

### 3.1 Diagrama de Componentes

```
┌─────────────────────────────────────────────────────────┐
│                    Kubernetes Cluster                    │
├─────────────────────────────────────────────────────────┤
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐ │
│  │   Pod 1     │    │   Pod 2     │    │   Pod N     │ │
│  │ ┌─────────┐ │    │ ┌─────────┐ │    │ ┌─────────┐ │ │
│  │ │Piper API│ │    │ │Piper API│ │    │ │Piper API│ │ │
│  │ │(Python) │ │    │ │(Python) │ │    │ │(Python) │ │ │
│  │ └────┬────┘ │    │ └────┬────┘ │    │ └────┬────┘ │ │
│  └──────┼──────┘    └──────┼──────┘    └──────┼──────┘ │
│         │                  │                  │        │
│         └──────────────────┼──────────────────┘        │
│                            │                            │
│  ┌─────────────────────────┴─────────────────────────┐ │
│  │              Kubernetes Service                    │ │
│  │         (ClusterIP - Interno)                      │ │
│  └───────────────────────────────────────────────────┘ │
│                            │                            │
│                            ▼                            │
│  ┌─────────────────────────┴─────────────────────────┐ │
│  │               Ingress Controller                    │ │
│  │         (NGINX / Traefik / HAProxy)                 │ │
│  └───────────────────────────────────────────────────┘ │
│                            │                            │
│                            ▼                            │
│  ┌─────────────────────────┴─────────────────────────┐ │
│  │                    Ingress                          │ │
│  │         Host: tts.jarvis.local                      │ │
│  │         Paths: /api/tts, /health, /audio            │ │
│  └───────────────────────────────────────────────────┘ │
│                            │                            │
│                            ▼                            │
│  ┌─────────────────────────┴─────────────────────────┐ │
│  │                  Load Balancer                       │ │
│  │         (Cloud LB / MetalLB / Host-based)           │ │
│  └───────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
```

### 3.2 Flujo de Solicitud

```
1. Cliente (HTTP/HTTPS)
   │
   ▼
2. Ingress Controller (NGINX)
   │ - TLS Termination
   │ - Path Routing
   │ - Rate Limiting
   ▼
3. Kubernetes Service (ClusterIP)
   │ - Service Discovery
   │ - Load Balancing
   ▼
4. Piper TTS API Pod
   │ - Text→Phonemes
   │ - Model Inference
   │ - Audio Export
   ▼
5. Response (WAV/MP3)
```

---

## 4. Diseño Detallado

### 4.1 Deployment: Piper TTS API

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: piper-tts
  labels:
    app: piper-tts
    version: "1.0"
spec:
  replicas: 2
  selector:
    matchLabels:
      app: piper-tts
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 1
  template:
    metadata:
      labels:
        app: piper-tts
        version: "1.0"
      annotations:
        prometheus.io/scrape: "true"
        prometheus.io/port: "8080"
    spec:
      containers:
      - name: piper-api
        image: piper-tts:latest
        imagePullPolicy: IfNotPresent
        ports:
        - containerPort: 8000
          name: api
        - containerPort: 8080
          name: metrics
        env:
        - name: MODEL_PATH
          value: "/models/es_ES-medium"
        - name: SPEAKER_ID
          value: "davefx"
        - name: SAMPLE_RATE
          value: "22050"
        volumeMounts:
        - name: model-volume
          mountPath: /models
        resources:
          requests:
            memory: "2Gi"
            cpu: "1000m"
          limits:
            memory: "4Gi"
            cpu: "2000m"
        livenessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 30
          periodSeconds: 10
          timeoutSeconds: 5
        readinessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 10
          periodSeconds: 5
        securityContext:
          runAsNonRoot: true
          runAsUser: 1000
          allowPrivilegeEscalation: false
      volumes:
      - name: model-volume
        persistentVolumeClaim:
          claimName: piper-model-pvc
      affinity:
        podAntiAffinity:
          preferredDuringSchedulingIgnoredDuringExecution:
          - weight: 100
            podAffinityTerm:
              labelSelector:
                matchExpressions:
                - key: app
                  operator: In
                  values:
                  - piper-tts
              topologyKey: kubernetes.io/hostname
```

### 4.2 Service: Piper TTS

```yaml
apiVersion: v1
kind: Service
metadata:
  name: piper-tts
  labels:
    app: piper-tts
spec:
  type: ClusterIP
  selector:
    app: piper-tts
  ports:
  - name: api
    port: 8000
    targetPort: 8000
    protocol: TCP
  - name: metrics
    port: 8080
    targetPort: 8080
    protocol: TCP
```

### 4.3 Ingress: Piper TTS

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: piper-tts
  annotations:
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
    nginx.ingress.kubernetes.io/rate-limit: "100"
    nginx.ingress.kubernetes.io/rate-limit-window: "60s"
    nginx.ingress.kubernetes.io/rewrite-target: /
    nginx.ingress.kubernetes.io/health-check-path: "/health"
    cert-manager.io/cluster-issuer: "letsencrypt-prod"
spec:
  ingressClassName: nginx
  tls:
  - hosts:
    - tts.jarvis.local
    - tts.jarvis.internal
    secretName: piper-ttls-secret
  rules:
  - host: tts.jarvis.local
    http:
      paths:
      - path: /api/tts
        pathType: Prefix
        backend:
          service:
            name: piper-tts
            port:
              number: 8000
      - path: /audio
        pathType: Prefix
        backend:
          service:
            name: piper-tts
            port:
              number: 8000
      - path: /health
        pathType: Prefix
        backend:
          service:
            name: piper-tts
            port:
              number: 8000
```

### 4.4 PersistentVolumeClaim: Piper Model

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: piper-model-pvc
  labels:
    app: piper-tts
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 500Mi
  storageClassName: standard
```

---

## 5. API Endpoints

### 5.1 REST API (Piper TTS)

| Endpoint | Method | Descripción |
|----------|--------|-------------|
| `/api/tts` | POST | Síntesis de audio |
| `/health` | GET | Salud del servicio |
| `/audio/{id}` | GET | Descargar archivo audio |
| `/metrics` | GET | Métricas Prometheus |

### 5.2 Request/Response

**POST /api/tts**

```json
{
  "text": "Hola, Señor. Este es un mensaje de prueba.",
  "voice": "es_ES-medium",
  "sample_rate": 22050,
  "format": "wav",
  "speed": 1.0,
  "pitch": 1.0
}
```

**Response:**

```json
{
  "success": true,
  "audio_url": "/audio/tts_20260730_123456.wav",
  "duration_ms": 3500,
  "sample_rate": 22050,
  "format": "wav"
}
```

---

## 6. Monitoreo y Observabilidad

### 6.1 Métricas Prometheus

- `piper_tts_requests_total` (counter)
- `piper_tts_request_duration_seconds` (histogram)
- `piper_tts_model_inference_time_seconds` (histogram)
- `piper_tts_audio_size_bytes` (gauge)

### 6.2 Logs

```yaml
# Log Aggregation (EFK/Loki)
apiVersion: v1
kind: ConfigMap
metadata:
  name: piper-tts-logging
data:
  logging.yaml: |
    loggers:
      piper_tts:
        level: INFO
    handlers:
      console:
        class: logging.StreamHandler
        formatter: json
```

---

## 7. Seguridad

### 7.1 Autenticación

- **OAuth2** (Google Workspace)
- **API Keys** (opcional)
- **JWT Tokens** (opcional)

### 7.2 Autorización

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: piper-reader
rules:
- apiGroups: [""]
  resources: ["pods", "services"]
  verbs: ["get", "list", "watch"]
```

---

## 8. Estrategia de Despliegue

### 8.1 Fase 1: Desarrollo (Local)

```bash
# Minikube / Kind
kubectl create deployment piper-tts --image=piper-tts:latest
kubectl expose deployment piper-tts --type=NodePort --port=8000
```

### 8.2 Fase 2: Testing (Staging)

```bash
# Ingress + TLS
kubectl apply -f ingress.yaml
kubectl apply -f tls-secret.yaml
```

### 8.3 Fase 3: Producción

```bash
# HPA + Auto-scaling
kubectl autoscale deployment piper-tts --cpu-percent=70 --min=2 --max=4
```

---

## 9. Checklist de Implementación

- [ ] Crear PersistentVolumeClaim para modelos
- [ ] Configurar Ingress Controller (NGINX/Traefik)
- [ ] Configurar Certificate Manager (Cert-Manager)
- [ ] Implementar API Flask/FastAPI
- [ ] Integrar Piper TTS con API
- [ ] Configurar Health Checks
- [ ] Implementar Monitoreo (Prometheus/Grafana)
- [ ] Configurar Logging (ELK/Loki)
- [ ] Documentar API
- [ ] Tests de integración

---

## 10. Consideraciones Técnicas

### 10.1 Recursos

| Componente | Requests | Limits |
|------------|----------|--------|
| CPU | 1 core | 2 cores |
| RAM | 2Gi | 4Gi |
| Storage | 500Mi (models) | 1Gi (logs) |

### 10.2 Latencia

- **Inicio frío:** 1-2 segundos
- **Poder:** 100-300ms
- **Red:** 10-50ms

### 10.3 Escalabilidad

- **Horizontal:** 1-4 replicas
- **Vertical:** 4Gi-8Gi RAM
- **Modelo:** Cuantización INT4 (4-5GB vs 7-9GB)

---

## 11. Referencias

- [Piper TTS Documentation](https://github.com/rhasspy/piper)
- [Kubernetes Ingress](https://kubernetes.io/docs/concepts/services-networking/ingress/)
- [Cert-Manager](https://cert-manager.io/docs/)
- [NGINX Ingress Controller](https://kubernetes.github.io/ingress-nginx/)

---

**Fin del SDD**
