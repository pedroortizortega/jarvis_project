# Instalación en Kubernetes

Guía para desplegar Engram Cloud desde cero. Usa `NAMESPACE=engram` y
`HOST=engram.lan` como ejemplo — sustituilos por los tuyos.

Los manifiestos base (sin las correcciones de esta guía) están en
[`kubernetes/engram/`](../../kubernetes/engram/) de este repo, como
referencia histórica del primer diseño. **Esta guía incluye dos fixes que
esos archivos no tienen** — aplicalos si los copiás.

## Prerrequisitos

```sh
kubectl get ingressclass traefik
kubectl api-resources --api-group=traefik.io | grep TLSOption
kubectl -n kube-system get service traefik   # anotá la IP externa real
kubectl get storageclass                     # la que uses para el PVC de Postgres
```

Necesitás: Traefik instalado con el CRD `TLSOption` (`traefik.io/v1alpha1`),
una StorageClass para el PVC de Postgres, y capacidad de emitir
certificados (una CA privada, aunque sea autofirmada).

## 1. Namespace

```sh
kubectl create namespace mcps
```

## 2. CA privada y certificados

No reuses una CA existente para otra cosa — generá una dedicada. Guardala
**fuera del repositorio de código**, con la clave privada protegida
(`chmod 600`).

```sh
PKI=~/.config/engram-cloud/pki
mkdir -p "$PKI"/{ca,server,clients}
umask 077

# CA
openssl genrsa -out "$PKI/ca/ca.key" 4096
openssl req -x509 -new -nodes -key "$PKI/ca/ca.key" -sha256 -days 3650 \
  -out "$PKI/ca/ca.crt" -subj "/CN=Engram Cloud Private CA"

# Certificado de servidor — SAN debe coincidir con el host del Ingress
HOST=engram.lan
openssl genrsa -out "$PKI/server/$HOST.key" 2048
openssl req -new -key "$PKI/server/$HOST.key" -out "$PKI/server/$HOST.csr" \
  -subj "/CN=$HOST"
printf 'subjectAltName=DNS:%s\nextendedKeyUsage=serverAuth\n' "$HOST" > "$PKI/server/$HOST.ext"
openssl x509 -req -in "$PKI/server/$HOST.csr" \
  -CA "$PKI/ca/ca.crt" -CAkey "$PKI/ca/ca.key" -CAcreateserial \
  -out "$PKI/server/$HOST.crt" -days 730 -sha256 -extfile "$PKI/server/$HOST.ext"
rm -f "$PKI/server/$HOST.csr"

# Un primer certificado de cliente (tu propio operador)
NAME=operator
mkdir -p "$PKI/clients/$NAME"
openssl genrsa -out "$PKI/clients/$NAME/$NAME.key" 2048
openssl req -new -key "$PKI/clients/$NAME/$NAME.key" \
  -out "$PKI/clients/$NAME/$NAME.csr" -subj "/CN=$NAME"
printf 'extendedKeyUsage=clientAuth\n' > "$PKI/clients/$NAME/$NAME.ext"
openssl x509 -req -in "$PKI/clients/$NAME/$NAME.csr" \
  -CA "$PKI/ca/ca.crt" -CAkey "$PKI/ca/ca.key" -CAcreateserial \
  -out "$PKI/clients/$NAME/$NAME.crt" -days 730 -sha256 -extfile "$PKI/clients/$NAME/$NAME.ext"
rm -f "$PKI/clients/$NAME/$NAME.csr"
```

Certificados de cliente adicionales (uno por identidad — ver
[client-setup.md](client-setup.md)) se emiten repitiendo el último bloque
con otro `NAME`.

## 3. Secrets

```sh
kubectl -n mcps create secret generic engram-postgres-auth \
  --from-literal=POSTGRES_PASSWORD="$(openssl rand -hex 24)"

DB_PASS=$(kubectl -n mcps get secret engram-postgres-auth -o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 -d)
kubectl -n mcps create secret generic engram-cloud-config \
  --from-literal=ENGRAM_DATABASE_URL="postgres://engram:${DB_PASS}@engram-postgres.mcps.svc.cluster.local:5432/engram_cloud?sslmode=disable" \
  --from-literal=ENGRAM_JWT_SECRET="$(openssl rand -base64 48)" \
  --from-literal=ENGRAM_CLOUD_TOKEN="$(openssl rand -hex 32)" \
  --from-literal=ENGRAM_CLOUD_ALLOWED_PROJECTS="tu_proyecto"

kubectl -n mcps create secret generic engram-client-ca \
  --from-file=ca.crt="$PKI/ca/ca.crt"

kubectl -n mcps create secret tls engram-server-tls \
  --cert="$PKI/server/$HOST.crt" --key="$PKI/server/$HOST.key"
```

`ENGRAM_CLOUD_TOKEN` es el bearer de arranque del operador — no lo repartas
a clientes normales. Cada cliente real recibe su propio token emitido más
tarde vía el dashboard (ver [client-setup.md](client-setup.md)).

`ENGRAM_CLOUD_TOKEN_PEPPER` (opcional acá, pero **requerido** para poder
emitir tokens por identidad después): agregalo ahora si ya sabés que vas a
tener múltiples clientes.
```sh
kubectl -n mcps patch secret engram-cloud-config --type merge \
  -p "{\"data\":{\"ENGRAM_CLOUD_TOKEN_PEPPER\":\"$(openssl rand -base64 32 | base64 -w0)\"}}"
```

## 4. Manifiestos

`namespace.yaml` ya se aplicó en el paso 1. El resto:

**`service.yaml`**
```yaml
apiVersion: v1
kind: Service
metadata:
  name: engram-cloud
  namespace: mcps
spec:
  type: ClusterIP
  selector:
    app: engram-cloud
  ports:
    - name: http
      port: 8080
      targetPort: http
```

**`deployment.yaml`** — con el fix de `runAsUser` (ver "Bugs de hardening"
más abajo):
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: engram-cloud
  namespace: mcps
spec:
  replicas: 1
  selector:
    matchLabels: { app: engram-cloud }
  template:
    metadata:
      labels: { app: engram-cloud }
    spec:
      automountServiceAccountToken: false
      securityContext:
        runAsNonRoot: true
        runAsUser: 10001   # UID real de la imagen — ver nota abajo
        runAsGroup: 10001
        seccompProfile: { type: RuntimeDefault }
      containers:
        - name: engram
          image: ghcr.io/gentleman-programming/engram:v1.20.0   # fijá la versión, nunca "latest"
          imagePullPolicy: IfNotPresent
          command: ["engram", "cloud", "serve"]
          ports:
            - name: http
              containerPort: 8080
          env:
            - { name: ENGRAM_CLOUD_HOST, value: "0.0.0.0" }
            - { name: ENGRAM_PORT, value: "8080" }
          envFrom:
            - secretRef: { name: engram-cloud-config }
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities: { drop: ["ALL"] }
          resources:
            requests: { cpu: 100m, memory: 256Mi }
            limits: { cpu: "1", memory: 1Gi }
```

**`postgres-pvc.yaml`**
```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: engram-postgres-data
  namespace: mcps
spec:
  accessModes: ["ReadWriteOnce"]
  resources:
    requests: { storage: 10Gi }
  # storageClassName: <la tuya, si no es la default del clúster>
```

**`postgres-service.yaml`**
```yaml
apiVersion: v1
kind: Service
metadata:
  name: engram-postgres
  namespace: mcps
spec:
  type: ClusterIP
  selector: { app: engram-postgres }
  ports:
    - { name: postgres, port: 5432, targetPort: postgres }
```

**`postgres-deployment.yaml`** — con el fix del initContainer (ver "Bugs
de hardening"):
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: engram-postgres
  namespace: mcps
spec:
  replicas: 1
  strategy: { type: Recreate }
  selector:
    matchLabels: { app: engram-postgres }
  template:
    metadata:
      labels: { app: engram-postgres }
    spec:
      automountServiceAccountToken: false
      securityContext:
        runAsNonRoot: true
        runAsUser: 999    # UID real de postgres:16.4-bookworm
        runAsGroup: 999
        fsGroup: 999
        seccompProfile: { type: RuntimeDefault }
      initContainers:
        - name: fix-data-ownership
          image: postgres:16.4-bookworm
          command: ["sh", "-c", "chown -R 999:999 /var/lib/postgresql/data"]
          securityContext:
            runAsUser: 0
            runAsNonRoot: false
            allowPrivilegeEscalation: false
            capabilities: { add: ["CHOWN"], drop: ["ALL"] }
          volumeMounts:
            - { name: data, mountPath: /var/lib/postgresql/data }
      containers:
        - name: postgres
          image: postgres:16.4-bookworm
          imagePullPolicy: IfNotPresent
          ports:
            - { name: postgres, containerPort: 5432 }
          env:
            - { name: POSTGRES_DB, value: engram_cloud }
            - { name: POSTGRES_USER, value: engram }
            - name: POSTGRES_PASSWORD
              valueFrom:
                secretKeyRef: { name: engram-postgres-auth, key: POSTGRES_PASSWORD }
            - { name: PGDATA, value: /var/lib/postgresql/data/pgdata }
          securityContext:
            allowPrivilegeEscalation: false
            capabilities: { drop: ["ALL"] }
          resources:
            requests: { cpu: 100m, memory: 256Mi }
            limits: { cpu: "1", memory: 1Gi }
          volumeMounts:
            - { name: data, mountPath: /var/lib/postgresql/data }
      volumes:
        - name: data
          persistentVolumeClaim: { claimName: engram-postgres-data }
```

**`mtls-tlsoption.yaml`**
```yaml
apiVersion: traefik.io/v1alpha1
kind: TLSOption
metadata:
  name: engram-mtls
  namespace: mcps
spec:
  minVersion: VersionTLS12
  clientAuth:
    secretNames: ["engram-client-ca"]
    clientAuthType: RequireAndVerifyClientCert
```

**`ingress.yaml`** — el punto de exposición; aplicalo al final, cuando ya
probaste que todo lo demás levanta:
```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: engram
  namespace: mcps
  annotations:
    traefik.ingress.kubernetes.io/router.tls.options: engram-engram-mtls@kubernetescrd
spec:
  ingressClassName: traefik
  tls:
    - hosts: ["engram.lan"]
      secretName: engram-server-tls
  rules:
    - host: engram.lan
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service: { name: engram-cloud, port: { name: http } }
```

## 5. Bugs de hardening encontrados (y ya corregidos arriba)

Dos bugs reales de infraestructura genérica de K3s/`local-path` que este
proyecto encontró desplegando Engram Cloud endurecido (`runAsNonRoot`,
capabilities dropeadas). Aplican a cualquier clúster con las mismas
características, no son específicos de este repo:

1. **La imagen de `engram` corre con un usuario de nombre no numérico**
   (`engram`). `runAsNonRoot: true` sin `runAsUser` numérico hace que el
   kubelet rechace el contenedor ("cannot verify user is non-root").
   Confirmá el UID real de tu imagen antes de fijarlo:
   ```sh
   kubectl -n mcps run uid-check --rm -i --restart=Never \
     --image=ghcr.io/gentleman-programming/engram:v1.20.0 --command -- id
   ```
   (En `v1.20.0` es `10001`; puede cambiar en otras versiones.)

2. **PVCs `local-path` (K3s) no reciben el `fsGroup` automático** — son
   `hostPath` por debajo, y Kubernetes solo aplica el ajuste de propietario
   a ciertos tipos de volumen. Un Postgres non-root con capabilities
   dropeadas no puede `chown`/`chmod` su propio directorio de datos al
   iniciar. El fix es el `initContainer` efímero de arriba: corre como
   root pero **solo** con `CAP_CHOWN`, arregla el dueño una vez, y el
   contenedor real arranca ya endurecido. Si tu StorageClass sí soporta
   `fsGroup` (EBS, la mayoría de los clústers gestionados), podés omitir
   el `initContainer`.

## 6. Aplicar y verificar

```sh
kubectl apply -f namespace.yaml -f postgres-pvc.yaml -f postgres-service.yaml \
  -f postgres-deployment.yaml -f service.yaml -f deployment.yaml -f mtls-tlsoption.yaml

kubectl -n mcps rollout status deployment/engram-postgres
kubectl -n mcps rollout status deployment/engram-cloud
kubectl -n mcps get pods
```

Recién cuando ambos estén `Running 1/1`, aplicá el Ingress:

```sh
kubectl apply -f ingress.yaml
kubectl -n mcps get ingress engram
```

Verificación end-to-end (necesita el certificado de cliente del paso 2 y
que `HOST` resuelva a la IP correcta desde donde corras esto — por LAN, la
IP externa de Traefik; ver [client-setup.md](client-setup.md) para el
resto de las opciones de red):

```sh
curl -sS -w "\nHTTP %{http_code}\n" \
  --cacert "$PKI/ca/ca.crt" \
  --cert "$PKI/clients/operator/operator.crt" \
  --key "$PKI/clients/operator/operator.key" \
  https://engram.lan/
```
`HTTP 404` con cuerpo `404 page not found` es éxito — significa que la
petición llegó hasta la aplicación Engram (que no tiene ruta en `/`), no
que algo falló. Un `curl: (35)`/`(56)` sin certificado válido, en cambio,
confirma que el mTLS está rechazando correctamente conexiones no
autorizadas.

Seguí con [client-setup.md](client-setup.md) para conectar el primer
agente de verdad.
