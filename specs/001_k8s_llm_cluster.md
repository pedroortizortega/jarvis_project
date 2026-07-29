# 001 — Cluster Kubernetes híbrido (PC + Raspberry Pi) para LLMs locales y agentes

## Objetivo

Montar un clúster Kubernetes que:

1. Sirva modelos LLM locales desde el PC (namespace `llms`), expuestos como API HTTP compatible con OpenAI, reutilizable por cualquier app de la LAN (no solo Hermes).
2. Sume Raspberry Pi como nodos worker en un namespace distinto (`hermes-agents`) donde corre Hermes Agent, que consume la API de LLMs vía la red interna del clúster.
3. Sea operable por un devops junior siguiendo pasos copy-paste, sin decisiones de diseño pendientes.

## Decisiones de arquitectura (y por qué)

| Decisión | Elección | Motivo |
|---|---|---|
| Distribución de Kubernetes | **k3s** (Rancher) | Un solo binario, bajo consumo de RAM/CPU, soporte ARM64 nativo (crítico para RPi), containerd embebido, Traefik y `local-path-provisioner` incluidos. `kubeadm`/k8s completo es innecesariamente pesado para RPi. |
| Rol del PC | Control-plane **+** worker, etiquetado para cargas LLM | Un solo nodo potente; no hace falta separar control-plane de cómputo en un clúster casero. |
| Rol de las RPi | Workers puros (sin control-plane) | RPi no tienen recursos ni disco fiable para etcd. |
| Red de pods | Flannel (default de k3s, backend VXLAN) | Suficiente para una LAN plana, cero configuración extra. |
| IPs de servicio en la LAN | **MetalLB** en modo Layer2 | Sin MetalLB, `LoadBalancer` no funciona en bare-metal; necesario para dar una IP fija de LAN a la API de LLMs. |
| Motor de inferencia | **vLLM** (`vllm/vllm-openai`) | Con RTX 4070 (16GB VRAM, confirmado con `nvidia-smi`) y expectativa de concurrencia real (múltiples sesiones de Hermes y/o varias apps a la vez), el continuous batching + PagedAttention de vLLM da mucho mejor throughput bajo carga concurrente que Ollama/llama.cpp — ver justificación completa en la sección C.1. Expone API OpenAI-compatible nativa (`/v1/chat/completions`, `/v1/completions`, `/v1/models`). |
| Multi-modelo (always-on + cold-start) | `PriorityClass` (preemption nativa de K8s) + **KEDA HTTP Add-on** (scale-to-zero) | Con una sola GPU física, dos pods no pueden reclamar `nvidia.com/gpu: 1` a la vez. Los modelos cold-start (grandes, de tarea específica, de pruebas) llevan mayor prioridad para desalojar automáticamente al modelo siempre-caliente cuando lo necesitan; KEDA los escala a 0 tras inactividad para liberar la GPU. Ver A8.0-A8.2. |
| Gateway/API unificada | **LiteLLM Proxy** delante de vLLM | Da un único endpoint OpenAI-compatible estable, permite añadir más backends (OpenAI, Anthropic, otro vLLM) después sin tocar clientes, hace rate-limiting/api-keys si luego expones la API a más apps. |
| Namespaces | `llms` (PC) y `hermes-agents` (RPi) | Aislamiento de recursos, RBAC y NetworkPolicy independientes por dominio. |
| Almacenamiento de modelos | `local-path-provisioner` (incluido en k3s) sobre disco del PC | Los modelos (GBs, formato HuggingFace) deben vivir en el disco rápido del PC, nunca en la SD de una RPi. |
| Afinidad de carga | `nodeSelector` + `taints/tolerations` | Garantiza que vLLM/LiteLLM solo corran en el PC (el único nodo con GPU) y Hermes solo en RPi, evitando que el scheduler los mezcle. |

Asunciones que debes confirmar/ajustar:
- El PC corre Linux (Ubuntu Server 22.04/24.04 recomendado, o **CachyOS/Arch** — ver notas específicas en A0). Si es Windows, hace falta WSL2 o una VM Linux — pídemelo y lo detallo aparte.
- El PC tiene una **NVIDIA RTX 4070 (16GB VRAM, confirmado con `nvidia-smi`)** — el paso `[GPU]` (A5) es **obligatorio**, no opcional: vLLM sin GPU es inviable en la práctica.
- 16GB de VRAM limitan el tamaño de modelo: cómodo con un ~7-8B en FP16 o un ~13B en AWQ/GPTQ de 4-bit como modelo siempre-caliente, dejando margen para el KV-cache. Ajusta `--gpu-memory-utilization` y el modelo elegido a ese límite.
- Plan confirmado de modelos: 2-3 en total — `qwen3` cuantizado (el que usa Hermes Agent) siempre-caliente, y 1-2 modelos cold-start (uno grande que no entra en VRAM junto a `qwen3`, y/o modelos de tareas específicas poco frecuentes o de experimentación). Ver A8.0-A8.2 para el diseño de desalojo automático + scale-to-zero.
- Todos los equipos están en la misma LAN/subred (ej. `192.168.1.0/24`).

---

## Parte A — Configurar Kubernetes en el PC (control-plane)

### A0. Preparación del sistema

```bash
# IP estática recomendada (ejemplo con netplan en Ubuntu Server)
sudo nano /etc/netplan/01-netcfg.yaml
```
```yaml
network:
  version: 2
  ethernets:
    eth0:
      dhcp4: no
      addresses: [192.168.1.10/24]
      routes:
        - to: default
          via: 192.168.1.1
      nameservers:
        addresses: [1.1.1.1, 8.8.8.8]
```
```bash
sudo netplan apply
```

#### A0-alt. Equivalente para CachyOS / Arch (sin netplan)

CachyOS no trae `netplan` (es una herramienta de Ubuntu/Debian). Usa **NetworkManager** (`nmcli`), que es lo que CachyOS trae activo por defecto:

```bash
# Ver el nombre exacto de la conexión activa (ethernet o wifi)
nmcli connection show

# IP estática (ajusta el nombre de conexión, IP, gateway y DNS a tu red real)
sudo nmcli connection modify "<nombre-conexion>" \
  ipv4.addresses 192.168.1.10/24 \
  ipv4.gateway 192.168.1.1 \
  ipv4.dns "1.1.1.1,8.8.8.8" \
  ipv4.method manual

sudo nmcli connection up "<nombre-conexion>"
```

Si en cambio administrás la red con `systemd-networkd` (no es lo típico en un desktop CachyOS, pero es posible en una instalación mínima), el equivalente al `.yaml` de netplan es un archivo `.network`:

```bash
sudo nano /etc/systemd/network/20-wired.network
```
```ini
[Match]
Name=eth0

[Network]
Address=192.168.1.10/24
Gateway=192.168.1.1
DNS=1.1.1.1
DNS=8.8.8.8
```
```bash
sudo systemctl enable --now systemd-networkd
sudo systemctl restart systemd-networkd
```

```bash
# Desactivar swap (obligatorio para kubelet) — igual en Ubuntu/Debian y CachyOS/Arch
sudo swapoff -a
sudo sed -i '/ swap / s/^/#/' /etc/fstab

# Módulos de red y sysctl requeridos — igual en ambos
cat <<EOF | sudo tee /etc/modules-load.d/k8s.conf
overlay
br_netfilter
EOF
sudo modprobe overlay
sudo modprobe br_netfilter

cat <<EOF | sudo tee /etc/sysctl.d/k8s.conf
net.bridge.bridge-nf-call-iptables  = 1
net.ipv4.ip_forward                 = 1
net.bridge.bridge-nf-call-ip6tables = 1
EOF
sudo sysctl --system

# Firewall (si usas ufw, Ubuntu/Debian) — abrir puertos que k3s necesita
sudo ufw allow 6443/tcp        # API server
sudo ufw allow 10250/tcp       # kubelet
sudo ufw allow 8472/udp        # VXLAN (Flannel)
sudo ufw allow 51820/udp       # solo si usas Flannel wireguard-backend
sudo ufw allow from 192.168.1.0/24
```

#### Firewall en CachyOS/Arch (sin ufw)

CachyOS no instala `ufw` por defecto; suele usar `firewalld` o `nftables` directo (o ningún firewall activo). Verifica cuál tenés:

```bash
systemctl status firewalld    # si existe y está activo
systemctl status nftables     # alternativa
```

Con **firewalld**:
```bash
sudo firewall-cmd --permanent --add-port=6443/tcp    # API server
sudo firewall-cmd --permanent --add-port=10250/tcp   # kubelet
sudo firewall-cmd --permanent --add-port=8472/udp    # VXLAN (Flannel)
sudo firewall-cmd --permanent --add-port=51820/udp   # solo si usas Flannel wireguard-backend
sudo firewall-cmd --permanent --add-source=192.168.1.0/24
sudo firewall-cmd --reload
```

Con **nftables** directo (ruleset por defecto de CachyOS, `/etc/nftables.conf`, tabla `inet filter`), el ruleset base trae `policy drop` tanto en `input` como en `forward`. Hace falta abrir los puertos de k3s en `input` **y** permitir el reenvío del tráfico de pods (Flannel VXLAN entre nodos) en `forward`, si no la red de pods queda rota aunque el host individual conteste:

```nft
#!/usr/bin/nft -f
# vim:set ts=2 sw=2 et:

destroy table inet filter
table inet filter {
  chain input {
    type filter hook input priority filter
    policy drop

    ct state invalid drop comment "early drop of invalid connections"
    ct state {established, related} accept comment "allow tracked connections"
    iif lo accept comment "allow from loopback"
    meta l4proto { icmp, icmpv6 } accept comment "allow icmp"
    tcp dport ssh accept comment "allow sshd"

    tcp dport 6443 accept comment "k3s API server"
    tcp dport 10250 accept comment "kubelet"
    udp dport 8472 accept comment "flannel VXLAN"
    udp dport 51820 accept comment "flannel wireguard (solo si usas ese backend)"

    pkttype host limit rate 5/second counter reject with icmpx type admin-prohibited
    counter
  }
  chain forward {
    type filter hook forward priority filter
    policy drop

    ct state {established, related} accept comment "allow tracked connections"
    iifname "cni0" accept comment "k3s pod bridge"
    oifname "cni0" accept comment "k3s pod bridge"
    iifname "flannel.1" accept comment "flannel vxlan iface"
    oifname "flannel.1" accept comment "flannel vxlan iface"
  }
}
```

```bash
sudo nano /etc/nftables.conf   # reemplazar chain input/forward con el bloque de arriba
sudo nft -f /etc/nftables.conf
sudo systemctl restart nftables
sudo nft list ruleset          # confirmar que las reglas quedaron cargadas
```

### A1. Instalar k3s (server/control-plane)

```bash
curl -sfL https://get.k3s.io | sh -s - \
  --write-kubeconfig-mode 644 \
  --disable servicelb \
  --node-name pc-master
```
`--disable servicelb` porque vamos a usar MetalLB en su lugar (servicelb de k3s entra en conflicto).

Verifica:
```bash
sudo systemctl status k3s
sudo k3s kubectl get nodes
```

### A2. Configurar `kubectl` como usuario normal

```bash
mkdir -p ~/.kube
sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
sudo chown $(id -u):$(id -g) ~/.kube/config
export KUBECONFIG=~/.kube/config
echo 'export KUBECONFIG=~/.kube/config' >> ~/.bashrc

kubectl get nodes -o wide
```

### A2.1 Troubleshooting: DNS externo intermitente desde pods

**Sintoma:** un pod puede resolver servicios internos del cluster pero falla al
resolver un dominio externo, por ejemplo Hermes durante `hermes auth` muestra:

```text
httpx.ConnectError: [Errno -3] Temporary failure in name resolution
```

**Causa confirmada en este cluster:** CoreDNS usaba `forward . /etc/resolv.conf`.
Los resolvers del nodo eran `1.1.1.1` y `8.8.8.8`, pero el trafico DNS UDP hacia
el exterior producia timeouts. CoreDNS registro errores como:

```text
auth.openai.com. A: read udp ... ->8.8.8.8:53: i/o timeout
```

Kubernetes DNS interno y el Service `kube-dns` estaban sanos; el problema era
solo el transporte UDP hacia los resolvers externos. Forzar TCP en CoreDNS evita
ese bloqueo o intermitencia sin cambiar la configuracion de cada pod:

```bash
kubectl patch configmap coredns -n kube-system --type merge -p '{"data":{"Corefile":".:53 {\n    errors\n    health\n    ready\n    kubernetes cluster.local in-addr.arpa ip6.arpa {\n      pods insecure\n      fallthrough in-addr.arpa ip6.arpa\n    }\n    hosts /etc/coredns/NodeHosts {\n      ttl 60\n      reload 15s\n      fallthrough\n    }\n    prometheus :9153\n    cache 30\n    loop\n    reload\n    loadbalance\n    import /etc/coredns/custom/*.override\n    forward . 1.1.1.1 8.8.8.8 {\n      force_tcp\n    }\n}\nimport /etc/coredns/custom/*.server\n"}}'
kubectl rollout restart deployment/coredns -n kube-system
kubectl rollout status deployment/coredns -n kube-system
```

Verificar desde el pod afectado:

```bash
kubectl -n hermes-agents exec deployment/hermes-agent-master -- \
  python -c 'import socket; print(socket.getaddrinfo("auth.openai.com", 443))'
kubectl -n kube-system logs deployment/coredns --since=2m
```

La resolucion debe devolver registros IPv4/IPv6 y los logs no deben contener
`i/o timeout` nuevos. Si TCP tambien falla, revisar el firewall/router para
permitir TCP y UDP al puerto 53, o configurar resolvers DNS accesibles en la
LAN. Para revertir al comportamiento de k3s, restaurar `forward . /etc/resolv.conf`
en el Corefile y reiniciar CoreDNS.

#### Equivalente en fish (CachyOS)

`export VAR=value` y `>> ~/.bashrc` son sintaxis de bash — fish no los reconoce igual y no lee `.bashrc`. Además, si `$KUBECONFIG` no queda bien seteado en la sesión, `kubectl` cae al comportamiento por defecto y falla con `localhost:8080: connection refused`.

```fish
mkdir -p ~/.kube
sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
sudo chown (id -u):(id -g) ~/.kube/config

set -x KUBECONFIG ~/.kube/config          # para la sesión actual
set -Ux KUBECONFIG ~/.kube/config         # persiste en todas las sesiones fish (variable universal, no hace falta tocar archivos de config)

kubectl get nodes -o wide
```

### A3. Guardar la URL y el token del clúster (los necesitarás para las RPi)

```bash
sudo cat /var/lib/rancher/k3s/server/node-token
# Guarda este valor, ej: K10abc...::server:xxxx
# La URL del server será: https://192.168.1.10:6443
```

### A4. Etiquetar el nodo PC para cargas de LLM

```bash
kubectl label node pc-master workload=llm
kubectl label node pc-master kubernetes.io/arch=amd64 --overwrite
```

### A5. `[GPU]` Habilitar la RTX 4070 en el clúster (obligatorio para vLLM)

```bash
# En el host (no en el pod):
sudo apt install -y nvidia-driver-<version> nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=containerd --config=/var/lib/rancher/k3s/agent/etc/containerd/config.toml.tmpl
sudo systemctl restart k3s

# Instala el device plugin de NVIDIA para k8s
kubectl create namespace nvidia-device-plugin
kubectl apply -n nvidia-device-plugin -f https://raw.githubusercontent.com/NVIDIA/k8s-device-plugin/v0.16.2/deployments/static/nvidia-device-plugin.yml

# Verifica que el nodo expone GPU como recurso
kubectl describe node pc-master | grep -A5 "Allocatable"
# Debe aparecer nvidia.com/gpu: 1
```

#### Equivalente en CachyOS/Arch

CachyOS usa `pacman` (no `apt`) y el driver NVIDIA se distribuye en paquetes separados. La RTX 4070 (Ada Lovelace) soporta los **módulos de kernel open-source de NVIDIA** (`nvidia-open`), la opción recomendada hoy salvo que necesites el driver propietario clásico.

```bash
uname -r

# Driver open-source (recomendado para RTX 4070) + utilidades.
# El repo de CachyOS mantiene "nvidia-open" precompilado y emparejado con su propio
# kernel "linux-cachyos" (igual que Arch hace con "nvidia" + "linux" stock) — no hace
# falta la variante -dkms en ese caso.
sudo pacman -S nvidia-open nvidia-utils nvidia-settings

# Usa la variante -dkms (recompila el módulo en cada actualización de kernel) SOLO si
# corrés un kernel que CachyOS no trackea con un paquete prebuilt propio (ej. un kernel
# compilado a mano o de otro repo de terceros):
# sudo pacman -S nvidia-open-dkms nvidia-utils nvidia-settings

sudo reboot
```

Verificá que el driver cargó antes de seguir:
```bash
nvidia-smi
# Debe listar tu RTX 4070; si falla, revisa `journalctl -k | grep -i nvidia`
```

`nvidia-container-toolkit` no siempre está en los repos oficiales de Arch/CachyOS — si `pacman -S nvidia-container-toolkit` falla, instalalo desde AUR con tu helper (`yay`/`paru`):
```bash
paru -S nvidia-container-toolkit   # o: yay -S nvidia-container-toolkit
```

El resto del paso (configurar containerd, reiniciar k3s, aplicar el device plugin) es idéntico a lo de arriba:
```bash
sudo nvidia-ctk runtime configure --runtime=containerd --config=/var/lib/rancher/k3s/agent/etc/containerd/config.toml.tmpl
sudo systemctl restart k3s

kubectl create namespace nvidia-device-plugin
kubectl apply -n nvidia-device-plugin -f https://raw.githubusercontent.com/NVIDIA/k8s-device-plugin/v0.16.2/deployments/static/nvidia-device-plugin.yml

kubectl describe node pc-master | grep -A5 "Allocatable"
# Debe aparecer nvidia.com/gpu: 1
```

#### Troubleshooting: el nodo queda `NotReady` después de instalar el driver/toolkit NVIDIA (CachyOS)

En una instalación real sobre CachyOS con Wi-Fi + kernel `linux-cachyos`, instalar `nvidia-container-toolkit` y configurar el runtime dejó el nodo inestable, con dos causas distintas encadenadas. Documentado acá para no repetir el diagnóstico si vuelve a pasar (por ejemplo tras un `pacman -Syu` que reinstale el toolkit).

**Síntoma 1 — el nodo hace `NotReady`/`Ready` en ciclos de ~15 minutos exactos, y `journalctl -u k3s` muestra:**
```
level=error msg="Shutdown request received: \"failed to start networking: network policy controller failed to wait for node.cloudprovider.kubernetes.io/uninitialized taint to be removed from Node trantor: context deadline exceeded\""
```
k3s se auto-apaga cuando su *network policy controller* no logra que el *cloud-controller-manager* embebido remueva el taint `node.cloudprovider.kubernetes.io/uninitialized` dentro de un timeout interno (~15 min), y `systemd` (`Restart=always`) lo reinicia, repitiendo el ciclo indefinidamente. La causa más probable: el nodo queda registrado con **dos IPs** (la IPv4 de la LAN + una IPv6 global dinámica que asigna el ISP vía SLAAC/DHCPv6), y esa ambigüedad dual-stack le impide al controlador reconciliar el nodo a tiempo — especialmente relevante en un nodo por Wi-Fi, donde la IPv6 es más propensa a rotar.

*Fix:* fijar explícitamente una sola IP y desactivar el controlador de network policy (no hace falta en un clúster casero de un solo control-plane). Editar `/etc/systemd/system/k3s.service` y agregar al final del bloque `ExecStart`:
```
	'--node-ip' \
	'192.168.100.13' \
	'--disable-network-policy' \
```
(ajustar la IP a la real del nodo). Luego:
```bash
sudo systemctl daemon-reload
sudo systemctl restart k3s
```

**Síntoma 2 — una vez resuelto el ciclo de reinicios, el nodo queda `NotReady` de forma persistente (ya no cíclica), con:**
```
kubectl describe node trantor
# Conditions: Ready False ... KubeletNotReady ... cni plugin not initialized
```
y en el log de containerd (`/var/lib/rancher/k3s/agent/containerd/containerd.log`):
```
error="cni config load failed: no network config found in /etc/cni/net.d: cni plugin not initialized"
```
containerd está buscando la configuración CNI en la ruta **por defecto del sistema** (`/etc/cni/net.d`, vacía) en vez de la ruta custom de k3s (`/var/lib/rancher/k3s/agent/etc/cni/net.d`, donde sí está `10-flannel.conflist`). Causa: al instalar `nvidia-container-toolkit`, el hook de pacman `nvidia-ctk-cdi.hook` generó desde cero `/var/lib/rancher/k3s/agent/etc/containerd/config.toml.tmpl` (antes no existía) con un contenido mínimo (`imports` + `version`), sin la sección que le indica a containerd dónde están las rutas CNI de k3s. Confirmable comparando la fecha de modificación del `.tmpl` (`stat ...`) contra la hora de instalación del toolkit en `/var/log/pacman.log`.

*Fix:* agregar explícitamente la sección de CNI al template (las rutas correctas se confirman con `sudo find /var/lib/rancher/k3s -maxdepth 4 -iname "*cni*"`):
```bash
sudo nano /var/lib/rancher/k3s/agent/etc/containerd/config.toml.tmpl
```
```toml
imports = ["/etc/containerd/conf.d/*.toml"]
version = 2

[plugins."io.containerd.cri.v1.runtime".cni]
  conf_dir = "/var/lib/rancher/k3s/agent/etc/cni/net.d"
  bin_dir = "/var/lib/rancher/k3s/data/cni"
```
```bash
sudo systemctl restart k3s
kubectl get nodes -o wide
# Debe pasar a Ready
```

**Síntoma 3 — el nodo ya está `Ready`, el device plugin (`nvidia-device-plugin-daemonset`) corre `1/1 Running`, pero `nvidia.com/gpu` sigue sin aparecer en `Allocatable`, y sus logs muestran:**
```
E factory.go:87] Incompatible strategy detected auto
E factory.go:88] If this is a GPU node, did you configure the NVIDIA Container Toolkit?
```
`nvidia-smi` funciona bien en el host, pero el pod del device plugin corre con el runtime de containerd por defecto (`runc`), no con el runtime `nvidia` que define `/etc/containerd/conf.d/99-nvidia.toml` — por eso el contenedor del plugin no puede ver la GPU para hacer el discovery vía NVML, aunque el host sí la vea.

*Fix:* crear un `RuntimeClass` que apunte al handler `nvidia` y decirle al daemonset del device plugin que lo use:
```bash
cat <<EOF | kubectl apply -f -
apiVersion: node.k8s.io/v1
kind: RuntimeClass
metadata:
  name: nvidia
handler: nvidia
EOF

kubectl patch daemonset nvidia-device-plugin-daemonset -n kube-system --type merge \
  -p '{"spec":{"template":{"spec":{"runtimeClassName":"nvidia"}}}}'

kubectl -n kube-system rollout status daemonset/nvidia-device-plugin-daemonset
kubectl describe node trantor | grep -A6 "Allocatable"
# Debe aparecer nvidia.com/gpu: 1
```

### A6. Instalar MetalLB (IPs de LoadBalancer en la LAN)

```bash
kubectl apply -f https://raw.githubusercontent.com/metallb/metallb/v0.14.8/config/manifests/metallb-native.yaml
kubectl -n metallb-system rollout status deployment/controller
```

Reserva un rango de IPs libres de tu LAN (fuera del rango DHCP de tu router), ej. `192.168.1.240-192.168.1.250`:

```yaml
# metallb-pool.yaml
apiVersion: metallb.io/v1beta1
kind: IPAddressPool
metadata:
  name: lan-pool
  namespace: metallb-system
spec:
  addresses:
    - 192.168.1.240-192.168.1.250
---
apiVersion: metallb.io/v1beta1
kind: L2Advertisement
metadata:
  name: lan-l2
  namespace: metallb-system
spec:
  ipAddressPools:
    - lan-pool
```
```bash
kubectl apply -f metallb-pool.yaml
```

### A7. Crear el namespace de LLMs

```bash
kubectl create namespace llms
```

### A8. Desplegar vLLM con almacenamiento persistente

vLLM descarga el modelo de HuggingFace Hub en el primer arranque y lo cachea en el PVC (evita re-descargar en cada reinicio del pod). Con 16GB de VRAM reales (confirmado con `nvidia-smi` — ver nota de VRAM en las Asunciones), `Qwen/Qwen2.5-7B-Instruct-AWQ` (4-bit) es un buen punto de partida para el modelo **siempre caliente**: buena calidad, cabe holgado junto al KV-cache y deja margen. Ajusta `--model` al que prefieras dentro del límite de VRAM.

Este modelo lleva `priorityClassName: llm-default` (ver A8.0) para que pueda ser **desalojado automáticamente** por un modelo cold-start de mayor prioridad cuando la GPU esté ocupada — ver A8.1/A8.2 para el diseño multi-modelo completo.

#### A8.0. PriorityClasses para desalojo automático (always-on vs cold-start)

Con una sola GPU (`nvidia.com/gpu: 1`), Kubernetes no permite que dos pods reclamen ese recurso a la vez. Para que un modelo cold-start grande pueda desalojar al modelo siempre-caliente cuando lo necesite (en vez de quedarse `Pending` para siempre), se usan dos `PriorityClass`:

```yaml
# priorityclasses.yaml
apiVersion: scheduling.k8s.io/v1
kind: PriorityClass
metadata:
  name: llm-default
value: 100
globalDefault: false
description: "Modelos LLM siempre-calientes; pueden ser desalojados por modelos de mayor prioridad si la GPU está ocupada."
---
apiVersion: scheduling.k8s.io/v1
kind: PriorityClass
metadata:
  name: llm-priority
value: 1000
globalDefault: false
description: "Modelos LLM cold-start/bajo demanda; desalojan a los de prioridad llm-default cuando necesitan la GPU."
```
```bash
kubectl apply -f priorityclasses.yaml
```

```yaml
# vllm.yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: vllm-models
  namespace: llms
spec:
  accessModes: ["ReadWriteOnce"]
  storageClassName: local-path
  resources:
    requests:
      storage: 100Gi   # ajusta al espacio libre real en el disco del PC
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: vllm
  namespace: llms
spec:
  replicas: 1
  strategy:
    type: Recreate   # obligatorio en Deployments que piden nvidia.com/gpu: 1 — ver troubleshooting debajo
  selector:
    matchLabels: {app: vllm}
  template:
    metadata:
      labels: {app: vllm}
    spec:
      nodeSelector:
        workload: llm
      priorityClassName: llm-default
      runtimeClassName: nvidia       # sin esto el contenedor no ve la GPU aunque el host sí (ver troubleshooting)
      enableServiceLinks: false      # evita que K8s inyecte VLLM_PORT y choque con la env var interna de vLLM (ver troubleshooting)
      containers:
        - name: vllm
          image: vllm/vllm-openai:latest
          args:
            - "--model=Qwen/Qwen2.5-7B-Instruct-AWQ"
            - "--quantization=awq"
            - "--max-model-len=8192"
            - "--gpu-memory-utilization=0.90"
            - "--served-model-name=qwen3"   # nombre que verán los clientes del gateway
          ports: [{containerPort: 8000}]
          volumeMounts:
            - {name: models, mountPath: /root/.cache/huggingface}
          resources:
            requests: {cpu: "4", memory: 8Gi}
            limits:
              memory: 16Gi       # ajusta a la RAM real del PC
              nvidia.com/gpu: 1
          readinessProbe:
            httpGet: {path: /health, port: 8000}
            initialDelaySeconds: 60
            periodSeconds: 10
      volumes:
        - name: models
          persistentVolumeClaim: {claimName: vllm-models}
---
apiVersion: v1
kind: Service
metadata:
  name: vllm
  namespace: llms
spec:
  selector: {app: vllm}
  ports: [{port: 8000, targetPort: 8000}]
  type: ClusterIP
```
```bash
kubectl apply -f vllm.yaml
kubectl -n llms rollout status deployment/vllm

# La primera vez tarda varios minutos (descarga el modelo desde HF Hub + compila CUDA graphs). Sigue el progreso:
kubectl -n llms logs -f deploy/vllm

# Prueba directa al pod (antes de meter LiteLLM delante)
kubectl -n llms exec deploy/vllm -- curl -s http://localhost:8000/v1/models
```

> Si el modelo elegido requiere aceptar licencia en HuggingFace o es un repo privado, añade un `Secret` con `HUGGING_FACE_HUB_TOKEN` como variable de entorno del contenedor — dímelo si es tu caso y te doy ese bloque.

#### Troubleshooting: tres errores reales al desplegar vLLM por primera vez

Documentado en orden de aparición — los tres manifiestos de A8, A8.2 y A8.3 ya incluyen los tres fixes de fábrica, pero si en algún momento se pierden (ej. editando a mano) o aparecen en un modelo nuevo, así se ven y así se resuelven:

**1. `RuntimeError: Failed to infer device type` — el contenedor no ve la GPU aunque `nvidia-smi` funcione en el host.**
Causa: el pod corre con el runtime `runc` por defecto de containerd, no con el runtime `nvidia` que expone los dispositivos NVIDIA dentro del contenedor. Fix: `runtimeClassName: nvidia` en el pod spec (ya en los manifiestos de arriba).

**2. `deployment exceeded its progress deadline` / pod nuevo en `Pending` para siempre tras editar el Deployment.**
Causa: la estrategia por defecto (`RollingUpdate`) intenta tener el pod viejo *y* el nuevo corriendo a la vez durante la transición (`maxSurge`) — imposible con una sola GPU (`nvidia.com/gpu: 1`), porque el pod nuevo nunca consigue el recurso mientras el viejo no se termine, y el viejo no se termina hasta que el nuevo esté `Ready`. Deadlock. Si ya te pasó y quedaron dos ReplicaSets peleando por la GPU:
```bash
kubectl -n llms get rs -l app=vllm
kubectl -n llms delete rs <nombre-del-replicaset-viejo>
```
Fix de fondo: `strategy: { type: Recreate }` en el Deployment (ya en los manifiestos) — mata el pod viejo por completo antes de crear el nuevo, sin pedir la GPU dos veces a la vez.

**3. `ValueError: VLLM_PORT '...' appears to be a URI` — el engine de vLLM no arranca.**
Causa: Kubernetes inyecta automáticamente variables de entorno tipo `<NOMBRE_SERVICE>_PORT` para cada `Service` del namespace (estilo Docker-links legacy). Como el `Service` se llama `vllm`, Kubernetes crea `VLLM_PORT=tcp://<ip>:8000`, que choca con la variable de entorno `VLLM_PORT` que vLLM usa para su propia configuración interna. Fix: `enableServiceLinks: false` en el pod spec (ya en los manifiestos) — desactiva esa inyección legacy para todos los Services del namespace.

### A8.1. Instalar KEDA + HTTP Add-on (scale-to-zero para modelos cold-start)

Necesario solo si vas a tener modelos "bajo demanda" (grandes que no entran junto al modelo siempre-caliente, de tareas específicas poco frecuentes, o de pruebas/experimentación). KEDA escala su `Deployment` de 0→1 cuando llega tráfico HTTP y de vuelta a 0 tras un período de inactividad.

```bash
helm repo add kedacore https://kedacore.github.io/charts
helm repo update

kubectl create namespace keda-system

helm install keda kedacore/keda --namespace keda-system
helm install http-add-on kedacore/keda-add-ons-http --namespace keda-system

kubectl -n keda-system rollout status deployment/keda-operator
kubectl -n keda-system rollout status deployment/keda-add-ons-http-controller-manager
kubectl -n keda-system rollout status deployment/keda-add-ons-http-interceptor
```

### A8.2. Desplegar el modelo cold-start grande (`Qwen3.6-27B`, no entra junto a `qwen3`)

Mismo patrón que A8 (`Deployment` + `PVC` + `Service`), pero con `priorityClassName: llm-priority` (para desalojar a `qwen3` cuando haga falta) y sin réplicas fijas — las controla KEDA vía `HTTPScaledObject`.

`Qwen3.6-27B` en 4-bit (AWQ/GPTQ) ≈ 13.5GB solo de pesos — deja poco margen dentro de los 16GB, por eso necesita la GPU exclusiva (de ahí el desalojo de `qwen3` vía `PriorityClass`). Ajustá `--max-model-len` corto (2-4K) si ves que no entra con el KV-cache.

> Pendiente antes de aplicar: confirmar el repo exacto de HuggingFace y si ya existe una cuantización AWQ/GPTQ publicada para `Qwen3.6-27B` (el benchmark lo confirma como modelo real, pero no tengo el path de HF verificado) — buscalo en huggingface.co/Qwen y reemplazá `<repo-hf-qwen3.6-27b>` abajo.

```yaml
# vllm-big-model.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: vllm-big-model
  namespace: llms
spec:
  replicas: 1   # KEDA sobreescribe esto dinámicamente (0 cuando no hay tráfico)
  strategy:
    type: Recreate   # obligatorio con nvidia.com/gpu: 1 — ver troubleshooting en A8
  selector:
    matchLabels: {app: vllm-big-model}
  template:
    metadata:
      labels: {app: vllm-big-model}
    spec:
      nodeSelector:
        workload: llm
      priorityClassName: llm-priority
      runtimeClassName: nvidia
      enableServiceLinks: false
      containers:
        - name: vllm
          image: vllm/vllm-openai:latest
          args:
            - "--model=<repo-hf-qwen3.6-27b>"   # ej. Qwen/Qwen3.6-27B-Instruct-AWQ, confirmar nombre exacto en HF
            - "--quantization=awq"              # ajusta a gptq si el repo que elijas usa esa cuantización
            - "--max-model-len=4096"            # corto a propósito: poco margen de VRAM en 16GB con un 27B en 4-bit
            - "--gpu-memory-utilization=0.95"
            - "--served-model-name=qwen3.6-27b"
          ports: [{containerPort: 8000}]
          volumeMounts:
            - {name: models, mountPath: /root/.cache/huggingface}
          resources:
            requests: {cpu: "4", memory: 8Gi}
            limits:
              memory: 24Gi
              nvidia.com/gpu: 1
          readinessProbe:
            httpGet: {path: /health, port: 8000}
            initialDelaySeconds: 60
            periodSeconds: 10
      volumes:
        - name: models
          persistentVolumeClaim: {claimName: vllm-models}   # reutiliza el mismo PVC de A8, o crea uno nuevo si preferís aislar el cache
---
apiVersion: v1
kind: Service
metadata:
  name: vllm-big-model
  namespace: llms
spec:
  selector: {app: vllm-big-model}
  ports: [{port: 8000, targetPort: 8000}]
  type: ClusterIP
---
apiVersion: http.keda.sh/v1alpha1
kind: HTTPScaledObject
metadata:
  name: vllm-big-model
  namespace: llms
spec:
  hosts:
    - vllm-big-model.llms.svc.cluster.local
  scaleTargetRef:
    name: vllm-big-model
    kind: Deployment
    apiVersion: apps/v1
    service: vllm-big-model
    port: 8000
  replicas:
    min: 0
    max: 1
  scaledownPeriod: 300   # segundos de inactividad antes de escalar a 0 (ajusta según cuán "de sobra" quieras que se sienta)
```
```bash
kubectl apply -f vllm-big-model.yaml
```

### A8.3. Desplegar el modelo cold-start chico (`Qwen3.5-9B`, tarea específica / experimentación)

Mismo patrón que A8.2, pero este sí entra cómodo junto a `qwen3` en VRAM (≈4.5GB de pesos en 4-bit) — igual queda como cold-start porque el uso es poco frecuente (tarea específica o pruebas), no por límite de memoria. El cold-start además es mucho más rápido que el del modelo grande por ser un modelo chico.

> Mismo pendiente que A8.2: confirmar el repo exacto de HuggingFace para `Qwen3.5-9B` y su cuantización disponible antes de aplicar.

```yaml
# vllm-small-model.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: vllm-small-model
  namespace: llms
spec:
  replicas: 1   # KEDA sobreescribe esto dinámicamente (0 cuando no hay tráfico)
  strategy:
    type: Recreate   # obligatorio con nvidia.com/gpu: 1 — ver troubleshooting en A8
  selector:
    matchLabels: {app: vllm-small-model}
  template:
    metadata:
      labels: {app: vllm-small-model}
    spec:
      nodeSelector:
        workload: llm
      priorityClassName: llm-priority
      runtimeClassName: nvidia
      enableServiceLinks: false
      containers:
        - name: vllm
          image: vllm/vllm-openai:latest
          args:
            - "--model=<repo-hf-qwen3.5-9b>"   # ej. Qwen/Qwen3.5-9B-Instruct-AWQ, confirmar nombre exacto en HF
            - "--quantization=awq"
            - "--max-model-len=16384"          # margen amplio de VRAM disponible para este tamaño
            - "--gpu-memory-utilization=0.90"
            - "--served-model-name=qwen3.5-9b"
          ports: [{containerPort: 8000}]
          volumeMounts:
            - {name: models, mountPath: /root/.cache/huggingface}
          resources:
            requests: {cpu: "2", memory: 4Gi}
            limits:
              memory: 12Gi
              nvidia.com/gpu: 1
          readinessProbe:
            httpGet: {path: /health, port: 8000}
            initialDelaySeconds: 45
            periodSeconds: 10
      volumes:
        - name: models
          persistentVolumeClaim: {claimName: vllm-models}
---
apiVersion: v1
kind: Service
metadata:
  name: vllm-small-model
  namespace: llms
spec:
  selector: {app: vllm-small-model}
  ports: [{port: 8000, targetPort: 8000}]
  type: ClusterIP
---
apiVersion: http.keda.sh/v1alpha1
kind: HTTPScaledObject
metadata:
  name: vllm-small-model
  namespace: llms
spec:
  hosts:
    - vllm-small-model.llms.svc.cluster.local
  scaleTargetRef:
    name: vllm-small-model
    kind: Deployment
    apiVersion: apps/v1
    service: vllm-small-model
    port: 8000
  replicas:
    min: 0
    max: 1
  scaledownPeriod: 300
```
```bash
kubectl apply -f vllm-small-model.yaml
```

Repite este mismo patrón (Deployment + Service + HTTPScaledObject, con `priorityClassName: llm-priority`) para cualquier modelo cold-start adicional que sumes más adelante, cambiando nombres, `--model` y recursos.

> Nota de comportamiento: si dos modelos cold-start reciben tráfico casi al mismo tiempo, solo uno consigue la GPU — el otro queda `Pending` hasta que el primero libere el recurso. No hay paralelismo real con una sola GPU; esto es una cola, no un error.
>
#### Troubleshooting: LiteLLM rechaza el `master_key` con `HTTP 400: No connected db.`

Con `image: ghcr.io/berriai/litellm:main-latest` (A9), cualquier request autenticada con `general_settings.master_key` puede fallar así, incluso con la key correcta:
```
litellm.proxy._types.ProxyException: No connected db.
```
Causa: ese build de LiteLLM intenta validar la key contra una base de datos (Postgres, para virtual keys/spend tracking) antes de caer al `master_key` plano, y este clúster no tiene ninguna DB configurada (no hace falta para este homelab). Fix: agregar a `general_settings` en `litellm-config.yaml`:
```yaml
    general_settings:
      master_key: sk-CAMBIA-ESTA-CLAVE
      allow_requests_on_db_unavailable: true
```
```bash
kubectl apply -f litellm-config.yaml
kubectl -n llms rollout restart deployment/litellm
```

#### Troubleshooting: `litellm.UnsupportedParamsError: ... ['reasoning_effort']`

Al conectar un cliente que manda `reasoning_effort` (Hermes lo hace por defecto para modelos que trata como "razonadores"), LiteLLM devuelve `HTTP 400` porque ese parámetro no es válido para `qwen3`/vLLM. Fix: agregar a `litellm-config.yaml`:
```yaml
    litellm_settings:
      drop_params: true
```
```bash
kubectl apply -f litellm-config.yaml
kubectl -n llms rollout restart deployment/litellm
```

#### Troubleshooting: contexto insuficiente para clientes agénticos (Hermes) — extender vía YaRN

Un cliente agéntico con muchas tools/skills cargadas (ej. Hermes con 28 tools + 65 skills) puede consumir 18-25k tokens solo en su system prompt, y encima pedir un `max_tokens` grande y fijo por request. Con `--max-model-len` chico (4096, o incluso 65536), esto dispara errores en cadena:
1. `max_tokens=X cannot be greater than max_model_len` — si `max_tokens` pedido excede el total configurado.
2. Con un `max_model_len` que sí alcanza para `max_tokens` pero no para `prompt + max_tokens` juntos, el mismo error reaparece en bucle (cada reintento agranda el prompt con el error anterior) y nunca converge.

Fix real: extender el contexto del modelo más allá de su `max_position_embeddings` nativo usando **YaRN** (soportado oficialmente por la familia Qwen2.5, no es un override crudo). En esta versión de vLLM (0.26.0) ya no existe el flag `--rope-scaling` suelto — se aplica vía `--hf-overrides`:
```yaml
            - "--max-model-len=131072"
            - "--hf-overrides"
            - '{"rope_scaling":{"rope_type":"yarn","factor":4.0,"original_max_position_embeddings":32768}}'
```
`factor` = `max-model-len` deseado ÷ `max_position_embeddings` nativo del modelo (acá 131072 ÷ 32768 = 4.0). Puede requerir subir `--gpu-memory-utilization` (el KV-cache crece con el contexto) y conviene bajar `--max-num-seqs` si el uso es interactivo/personal en vez de concurrente.

Del lado del cliente, si es Hermes, hay que igualar su propia cuenta de contexto para que calcule bien cuánto `max_tokens` pedir:
```bash
hermes config set model.context_length 131072
```

> Nota sobre el routing: el tráfico hacia un modelo cold-start debe pasar por el interceptor de KEDA HTTP Add-on (`keda-add-ons-http-interceptor-proxy.keda-system.svc.cluster.local`), no directo al `Service` del modelo — es lo que dispara el scale-from-zero. En A9 se detalla cómo apunta LiteLLM a cada tipo de modelo. Verifica la sintaxis exacta contra la versión de KEDA HTTP Add-on que instales (`helm show values kedacore/keda-add-ons-http`), el API de `HTTPScaledObject` cambia entre versiones.

#### Limitación conocida: KEDA no tiene en cuenta la VRAM real, solo tráfico HTTP

KEDA HTTP Add-on escala **exclusivamente en base a actividad de requests**: sube el Deployment de 0→1 en cuanto llega la primera request al interceptor, y vuelve a 0 tras `scaledownPeriod` sin tráfico. No consulta `nvidia-smi`, no mide VRAM libre, no tiene ninguna noción del estado real de la GPU.

Esto importa especialmente en este setup porque **el mismo PC es también el escritorio de uso diario** (Hyprland, Steam, etc.), y todo comparte la única GPU física:

- El desalojo automático vía `PriorityClass` (A8.0) **solo funciona entre pods de Kubernetes** — si en el momento en que llega una request para un modelo cold-start hay un **proceso fuera de Kubernetes** (un juego, transcodificación de video, etc.) consumiendo varios GB de VRAM directo en el host, Kubernetes no lo ve: sigue contando `nvidia.com/gpu: 1` como "disponible" (es una unidad discreta, no bytes reales de VRAM).
- El scheduler agenda el pod igual, KEDA lo escala igual, y vLLM puede fallar al arrancar con un **OOM de CUDA real** — aunque a nivel de Kubernetes todo se vea sano (`Pending`→`Running` normal hasta ese punto).
- No hay mitigación automática para este caso hoy. Si se vuelve un problema recurrente, la solución sería reemplazar el trigger de KEDA por uno basado en una métrica externa de VRAM libre real (ej. Prometheus + `nvidia-dcgm-exporter`) en vez del HTTP Add-on puro, para bloquear el scale-up si no hay memoria suficiente — no implementado en este spec, evaluar si hace falta según cuánto se repita el conflicto en la práctica.

### A9. Desplegar LiteLLM Proxy como gateway unificado (API estable para todas las apps)

```yaml
# litellm-config.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: litellm-config
  namespace: llms
data:
  config.yaml: |
    model_list:
      - model_name: qwen3
        litellm_params:
          model: openai/qwen3   # vLLM expone API OpenAI-compatible; "openai/" le dice a LiteLLM que use ese protocolo
          api_base: http://vllm.llms.svc.cluster.local:8000/v1
          api_key: "not-needed"  # vLLM no exige key interna; la seguridad la pone LiteLLM hacia afuera
      - model_name: qwen3.6-27b
        litellm_params:
          model: openai/qwen3.6-27b
          # Va al interceptor de KEDA (namespace keda-system), no directo al Service del modelo,
          # para que el scale-from-zero se dispare. El header Host es el que usa KEDA para
          # identificar a qué HTTPScaledObject/Deployment corresponde la request.
          api_base: http://keda-add-ons-http-interceptor-proxy.keda-system.svc.cluster.local:8080/v1
          extra_headers:
            Host: vllm-big-model.llms.svc.cluster.local
          api_key: "not-needed"
      - model_name: qwen3.5-9b
        litellm_params:
          model: openai/qwen3.5-9b
          api_base: http://keda-add-ons-http-interceptor-proxy.keda-system.svc.cluster.local:8080/v1
          extra_headers:
            Host: vllm-small-model.llms.svc.cluster.local
          api_key: "not-needed"
    general_settings:
      master_key: sk-CAMBIA-ESTA-CLAVE   # usada por Hermes y otras apps
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: litellm
  namespace: llms
spec:
  replicas: 1
  selector: {matchLabels: {app: litellm}}
  template:
    metadata: {labels: {app: litellm}}
    spec:
      nodeSelector:
        workload: llm
      containers:
        - name: litellm
          image: ghcr.io/berriai/litellm:main-latest
          args: ["--config", "/config/config.yaml"]
          ports: [{containerPort: 4000}]
          volumeMounts:
            - {name: config, mountPath: /config}
      volumes:
        - name: config
          configMap: {name: litellm-config}
---
apiVersion: v1
kind: Service
metadata:
  name: litellm
  namespace: llms
spec:
  selector: {app: litellm}
  ports: [{port: 4000, targetPort: 4000}]
  type: LoadBalancer   # MetalLB le asignará una IP del pool (ej. 192.168.1.240)
```
```bash
kubectl apply -f litellm-config.yaml
kubectl -n llms get svc litellm
# EXTERNAL-IP asignada por MetalLB, ej: 192.168.1.240
```

Prueba desde cualquier máquina de la LAN:
```bash
curl http://192.168.1.240:4000/v1/chat/completions \
  -H "Authorization: Bearer sk-CAMBIA-ESTA-CLAVE" \
  -H "Content-Type: application/json" \
  -d '{"model": "qwen3", "messages": [{"role":"user","content":"hola"}]}'
```

### A8.4. Runbook: cambiar el modelo de vLLM (el principal `qwen3`, o cualquier otro) y verificar que quedó bien

Aplica tanto para reemplazar el modelo principal (`vllm.yaml`) como cualquier modelo cold-start (`vllm-big-model.yaml`, `vllm-small-model.yaml`, o uno nuevo). Consolida, en un solo lugar, todos los gotchas reales encontrados al migrar de `Qwen2.5-7B-Instruct-AWQ` a `QuantTrio/Qwen3.5-9B-AWQ` — varios de estos rompieron el pod en producción antes de detectarlos.

#### Paso 0 — Antes de tocar el YAML: reunir 4 datos del modelo nuevo

No adivines ninguno de estos — cada uno rompió algo real la última vez que se asumió en vez de verificar:

1. **Tamaño real en disco (pesos) del quant elegido.** Mirá el listado de archivos del repo en HuggingFace (`/tree/main`) y sumá los `.safetensors`. No confíes en el nombre del repo ("-AWQ", "-4bit") para inferir el tamaño — quants de la misma familia pueden diferir mucho si el modelo es multimodal (los pesos de visión suman GBs extra que no vas a usar).
2. **¿Es multimodal?** Si el model card menciona imagen/video/vision encoder, hace falta el flag `--language-model-only` (o el equivalente que documente ese modelo) para que vLLM no cargue ni perfile el encoder de visión — si no, se come VRAM real que necesitás para el KV-cache, sin ningún beneficio (Hermes solo usa texto).
3. **Contexto nativo real (`max_position_embeddings`) y si ya soporta YaRN/RoPE-scaling nativo.** No asumas que el contexto nativo es el mismo que el modelo anterior. Si tu `--max-model-len` deseado ya cae dentro del nativo, **no** agregues `--hf-overrides` con `rope_scaling` — aplicar un override de YaRN pensado para un modelo con un nativo distinto corrompe el cálculo de posiciones RoPE de forma silenciosa (no tira error, solo degrada calidad).
4. **`--tool-call-parser` y `--reasoning-parser` correctos para la familia del modelo.** Buscá el comando de `vllm serve` de ejemplo en el model card — cada familia de modelo (Qwen2.5, Qwen3.x, Llama, etc.) usa un parser distinto. Un parser equivocado no siempre tira error obvio: puede fallar el tool-calling en silencio o mezclar el razonamiento con la respuesta final.

#### Paso 1 — Hacer la cuenta de VRAM antes de aplicar

```
presupuesto_total = VRAM_total_GB × --gpu-memory-utilization
margen_para_kv_cache = presupuesto_total − peso_del_modelo_GB (paso 0.1)
```
Si `margen_para_kv_cache` es chico (menos de ~3-4GB), el `--max-model-len` que quieras sostener probablemente no entre — vas a tener que subir `--gpu-memory-utilization`, bajar `--max-model-len`, o ambos. No hay forma de saber el número exacto sin arrancarlo (vLLM lo calcula en el arranque y lo imprime si falla — ver Paso 3), pero esta cuenta evita sorpresas groseras (ej. pedir 131072 de contexto con un modelo que ya pesa el 90% del presupuesto).

#### Paso 2 — Editar y aplicar

```bash
# editar kubernetes/llms/vllm.yaml (o el manifiesto del modelo cold-start correspondiente)
kubectl apply -f kubernetes/llms/vllm.yaml

# forzar que se recree el pod ya (si no, puede tardar en notar el cambio con strategy: Recreate)
kubectl -n llms delete pod -l app=vllm --wait=false
```

#### Paso 3 — Verificar que arrancó de verdad (no asumir por "Running")

Un pod puede aparecer `Running` momentáneamente y crashear segundos después (`CrashLoopBackOff`) — **nunca lo des por bueno sin ver los logs**.

```bash
# 3.1 — esperar a que esté Ready de verdad (no solo "creado")
until kubectl -n llms get pod -l app=vllm -o jsonpath='{.items[0].status.containerStatuses[0].ready}' 2>/dev/null | grep -q true; do
  restarts=$(kubectl -n llms get pod -l app=vllm -o jsonpath='{.items[0].status.containerStatuses[0].restartCount}' 2>/dev/null)
  if [ "$restarts" != "0" ] && [ -n "$restarts" ]; then
    echo "CRASHEÓ (restarts=$restarts) — ver logs"
    break
  fi
  sleep 5
done

# 3.2 — estado y reinicios
kubectl -n llms get pods -o wide
# READY debe ser 1/1 y RESTARTS debe quedarse en 0 (si sube, está en crash-loop)

# 3.3 — si crasheó, el error real casi siempre está en el traceback del intento anterior, no en el actual
kubectl -n llms logs deploy/vllm --previous --tail=100 | grep -B3 -A20 "Error\|Traceback"

# 3.4 — si arrancó bien, confirmar el health check y el modelo servido
kubectl -n llms exec deploy/vllm -- curl -s http://localhost:8000/v1/models
# debe listar el modelo con el "served_model_name" esperado y el max_model_len correcto

# 3.5 — probar una request real de punta a punta, pasando por LiteLLM (no solo directo al pod)
curl http://192.168.1.240:4000/v1/chat/completions \
  -H "Authorization: Bearer sk-CAMBIA-ESTA-CLAVE" \
  -H "Content-Type: application/json" \
  -d '{"model": "qwen3", "messages": [{"role":"user","content":"decí OK si me escuchás"}], "max_tokens": 20}'
```

#### Paso 4 — Si cambiaste `--max-model-len`, sincronizar Hermes

Si el modelo nuevo sirve un `qwen3` con un `max_model_len` distinto al anterior, **Hermes necesita enterarse** (spec 002, Fase 2.2) — si no, vuelve el loop de `ContextWindowExceededError` ya documentado:
```bash
kubectl -n hermes-agents exec deploy/hermes-agent-master -- hermes config set model.context_length <el-nuevo-max-model-len>
# re-exportar al repo (spec 002, Parte E3)
kubectl -n hermes-agents exec deploy/hermes-agent-master -- cat /opt/data/config.yaml > kubernetes/hermes/config/config.yaml
```

#### Errores reales ya encontrados — diagnóstico rápido

| Síntoma en el log | Causa | Fix |
|---|---|---|
| `max_tokens=X cannot be greater than max_model_len=Y` | El cliente (Hermes) pide más tokens de los que el modelo tiene configurados | Ver Paso 4, o troubleshooting de contexto insuficiente (arriba, A8) |
| `ValueError: ... KV cache is needed, which is larger than the available KV cache memory (X GiB)` | `--max-model-len` no entra en el presupuesto de VRAM | El propio error te dice el máximo real soportado ("estimated maximum model length is N") — bajá `--max-model-len` a algo por debajo de ese número, o subí `--gpu-memory-utilization` |
| `User-specified max_model_len (N) is greater than the derived max_model_len` | Intentaste poner un contexto mayor al nativo sin `VLLM_ALLOW_LONG_MAX_MODEL_LEN=1` (y sin YaRN real) | Confirmá el nativo real (Paso 0.3) — si de verdad hace falta extenderlo, usar YaRN vía `--hf-overrides` con el `original_max_position_embeddings` correcto, no el override crudo |
| `"auto" tool choice requires --enable-auto-tool-choice and --tool-call-parser` | Faltan esos dos flags | Agregarlos, con el parser correcto de la familia del modelo (Paso 0.4) |
| `unrecognized arguments: --rope-scaling ...` | Esa versión de vLLM ya no tiene ese flag suelto | Usar `--hf-overrides '{"rope_scaling": {...}}'` en su lugar |
| Encoder de visión perfilándose en los logs (`Encoder cache will be initialized...`) en un modelo que solo vas a usar para texto | Modelo multimodal sin `--language-model-only` | Agregar el flag — confirmar en logs que aparece `"running in text-only mode"` |
| `EngineDeadError: EngineCore encountered an issue` sin traceback legible, pod se reinicia solo, coincide con un request inusualmente grande (ej. una compresión de contexto) | Bug abierto y sin fix oficial en vLLM ([#36598](https://github.com/vllm-project/vllm/issues/36598)) + Triton ([#9939](https://github.com/triton-lang/triton/issues/9939)): en GPUs no-SM90 (Ada Lovelace/SM89 incluida — RTX 4070, 4090), las capas GDN de modelos híbridos Mamba/atención lineal (Qwen3.5, Qwen3-Next) disparan un autotuner de Triton que se queda sin VRAM libre para el benchmarking (ya reservada para KV-cache), o directamente segfaultea en el backend LLVM de Triton | Mitigación (no elimina el riesgo del todo, son bugs de upstream sin resolver): agregar env var `VLLM_TRITON_FORCE_FIRST_CONFIG=1` al contenedor — salta el benchmarking del autotuner y usa la primera config válida. Kubernetes igual se autocura solo (`Deployment` reinicia el pod) si llega a pasar |

---

## Parte B — Configurar las Raspberry Pi como nodos worker

Repite estos pasos en **cada** Raspberry Pi.

### B0. Flashear el sistema operativo

- Usa **Raspberry Pi Imager** → elige **Raspberry Pi OS Lite (64-bit)** (k3s necesita arquitectura de 64 bits).
- En las opciones avanzadas del Imager (icono de engranaje): configura hostname (`rpi-1`, `rpi-2`, …), habilita SSH, usuario/contraseña y, si quieres, la WiFi. Preferible cable Ethernet para estabilidad.

### B1. Primer arranque y ajustes base

```bash
ssh usuario@rpi-1.local

sudo raspi-config    # opcional: expandir filesystem si no lo hizo solo
```

IP estática (recomendado), edita `/etc/dhcpcd.conf`:
```
interface eth0
static ip_address=192.168.1.21/24
static routers=192.168.1.1
static domain_name_servers=1.1.1.1
```
(repite con `.22`, `.23`… para cada RPi)

### B2. Habilitar cgroups (requisito de k3s en RPi)

```bash
sudo nano /boot/firmware/cmdline.txt
```
Añade al final de la línea existente (todo en una sola línea, sin saltos):
```
cgroup_memory=1 cgroup_enable=memory
```
```bash
sudo reboot
```

### B3. Desactivar swap y aplicar sysctl (igual que en el PC)

```bash
sudo swapoff -a
sudo dphys-swapfile swapoff 2>/dev/null
sudo systemctl disable dphys-swapfile 2>/dev/null

cat <<EOF | sudo tee /etc/sysctl.d/k8s.conf
net.bridge.bridge-nf-call-iptables = 1
net.ipv4.ip_forward = 1
EOF
sudo sysctl --system
```

### B4. Unir la Raspberry Pi al clúster como agente (worker)

Usa la URL y el token guardados en el paso A3:

```bash
curl -sfL https://get.k3s.io | K3S_URL=https://192.168.1.10:6443 \
  K3S_TOKEN=K10abc...::server:xxxx \
  sh -s - --node-name rpi-1
```

Verifica desde el **PC**:
```bash
kubectl get nodes -o wide
# Debe aparecer rpi-1 con ROLES <none>, ARCH arm64, Ready
```

Repite B0–B4 para cada Raspberry Pi adicional (`rpi-2`, `rpi-3`, …).

### B5. Etiquetar los nodos RPi para Hermes

Desde el PC:
```bash
kubectl label node rpi-1 workload=agent
kubectl label node rpi-2 workload=agent   # repite por cada RPi
```

### B6. (Opcional pero recomendado) Taint para blindar cada pool de nodos

Evita que el scheduler mande Hermes al PC o vLLM a una RPi por error:

```bash
kubectl taint node pc-master dedicated=llm:NoSchedule
kubectl taint node rpi-1 dedicated=agent:NoSchedule
kubectl taint node rpi-2 dedicated=agent:NoSchedule
```
Luego añade a los manifiestos de vLLM/LiteLLM (A8/A9) y de Hermes (B8) la tolerancia correspondiente:
```yaml
      tolerations:
        - key: dedicated
          operator: Equal
          value: llm        # o "agent" en el caso de Hermes
          effect: NoSchedule
```

### B7. Crear el namespace de Hermes

```bash
kubectl create namespace hermes-agents
```

### B8. Desplegar Hermes Agent en las RPi

```yaml
# hermes.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: hermes-agent
  namespace: hermes-agents
spec:
  replicas: 1
  selector: {matchLabels: {app: hermes-agent}}
  template:
    metadata: {labels: {app: hermes-agent}}
    spec:
      nodeSelector:
        workload: agent
      tolerations:
        - {key: dedicated, operator: Equal, value: agent, effect: NoSchedule}
      containers:
        - name: hermes-agent
          image: <tu-imagen-de-hermes>:latest   # build arm64/multi-arch obligatorio
          env:
            - name: LLM_API_BASE
              value: "http://litellm.llms.svc.cluster.local:4000/v1"
            - name: LLM_API_KEY
              value: "sk-CAMBIA-ESTA-CLAVE"
          resources:
            requests: {cpu: "250m", memory: 256Mi}
            limits: {memory: 512Mi}
```
```bash
kubectl apply -f hermes.yaml
kubectl -n hermes-agents rollout status deployment/hermes-agent
kubectl -n hermes-agents logs -f deploy/hermes-agent
```

> **Importante:** la imagen de Hermes debe ser multi-arch (`linux/arm64`) o construida específicamente para ARM64, si no el pod quedará en `CrashLoopBackOff`/`exec format error` en las RPi. Si hoy tu imagen es solo `amd64`, dímelo y te doy el spec para el build multi-arch con `docker buildx`.

---

## Parte C — Kubernetes como gestor de LLMs reutilizable por otras apps

### C.1 Por qué vLLM y no Ollama/llama.cpp aquí

Con una RTX 4070 y la expectativa de tener Hermes corriendo varias sesiones a la vez (y potencialmente otras apps golpeando el mismo gateway), lo que importa es el comportamiento bajo **concurrencia**, no solo la latencia de una request aislada:

- **Ollama / llama.cpp**: procesan requests concurrentes con paralelismo limitado y cada una reserva su propio contexto de KV-cache de forma poco eficiente — con varias sesiones activas a la vez, cada una se ralentiza notablemente porque compiten por turnos.
- **vLLM**: usa *continuous batching* — en cada paso de generación mete todas las requests activas (ej. 3 sesiones de Hermes + 1 request de otra app) en el mismo batch de GPU, procesándolas juntas token a token en vez de en cola. Combinado con *PagedAttention* (gestiona el KV-cache en páginas de memoria, como pagina RAM un SO), aprovecha mucho mejor los 12GB de VRAM disponibles, permitiendo más sesiones simultáneas antes de degradar.

Esto es exactamente el patrón esperado en este clúster: múltiples réplicas/sesiones de Hermes en las RPi (namespace `hermes-agents`) más otras apps futuras, todas contra el mismo backend en `llms`. Es la razón por la que el spec usa vLLM desde el arranque en vez de partir con Ollama y migrar después.

### C.2 Diseño para reutilización fuera de Hermes

1. **Un único punto de entrada, no acoplado a Hermes.** El `Service` `litellm` (A9) con IP fija de MetalLB es el contrato: cualquier app de la LAN habla el protocolo OpenAI contra esa IP:puerto. Añadir una segunda app consumidora no requiere tocar vLLM ni Hermes.
2. **DNS amigable en lugar de IP cruda.** En tu router (o un Pi-hole/dnsmasq si ya tienes uno) crea un registro tipo `llm.home.arpa → 192.168.1.240`. Así el endpoint queda como `http://llm.home.arpa:4000/v1/...` y sobrevive si cambias la IP del pool de MetalLB.
3. **Multi-modelo sin re-desplegar el gateway.** Añadir un modelo nuevo requiere una segunda réplica/Deployment de vLLM (cada proceso vLLM sirve un modelo) más una entrada nueva en el `ConfigMap` `litellm-config` (`model_list`) y `kubectl rollout restart deployment/litellm -n llms` — los clientes que ya apuntan al gateway no cambian nada.
4. **Separar por API key por app consumidora.** LiteLLM soporta múltiples `virtual keys` con límites de rate/budget independientes por key — útil cuando además de Hermes tengas otra app llamando al mismo gateway y quieras aislar cuotas o ver métricas de uso por consumidor.
5. **Escalar a más potencia de cómputo más adelante.** Si añades una segunda GPU o máquina x86, solo hace falta unirla con `workload=llm` y añadir otra réplica/Deployment de vLLM (o un modelo distinto) detrás del mismo gateway — sin cambios en Hermes ni en otros consumidores.
6. **Aislamiento de red entre namespaces (recomendado antes de exponer el gateway a más apps).**
   ```yaml
   # netpol-llms.yaml
   apiVersion: networking.k8s.io/v1
   kind: NetworkPolicy
   metadata:
     name: allow-only-litellm-ingress
     namespace: llms
   spec:
     podSelector: {matchLabels: {app: vllm}}
     policyTypes: [Ingress]
     ingress:
       - from:
           - podSelector: {matchLabels: {app: litellm}}
   ```
   Esto obliga a que **solo** LiteLLM pueda hablar directo con vLLM; todo lo demás (Hermes incluido) pasa por el gateway.
7. **Observabilidad (mejora futura, no bloqueante para el MVP):** `kube-prometheus-stack` vía Helm, más el endpoint `/metrics` que vLLM expone nativamente (throughput, uso de KV-cache, requests en cola) — útil para saber cuándo la RTX 4070 se está quedando corta de VRAM antes de que Hermes empiece a fallar por timeout o preemption.

---

## Checklist de validación final

```bash
kubectl get nodes -o wide                     # pc-master (amd64) + rpi-N (arm64), todos Ready
kubectl get pods -n llms -o wide               # vllm y litellm corriendo en pc-master
kubectl -n llms describe pod -l app=vllm | grep -A3 "Limits"   # confirma nvidia.com/gpu: 1 asignado
kubectl get pods -n hermes-agents -o wide       # hermes-agent corriendo en rpi-N
kubectl get svc -n llms litellm                 # EXTERNAL-IP asignada, no <pending>
curl http://<IP-metallb>:4000/v1/models -H "Authorization: Bearer sk-..."   # responde con el modelo cargado
kubectl -n hermes-agents logs deploy/hermes-agent | grep -i "connect\|error"
```

## Fuera de alcance de este spec (a definir si se necesita)

- HTTPS/TLS en el gateway (hoy es HTTP plano en LAN; si se expone fuera de la LAN, hace falta cert-manager + Ingress).
- Backup/DR de `vllm-models` PVC.
- Build multi-arch de la imagen de Hermes (paso B8, nota final) si aún no existe.
- Autenticación/RBAC de Kubernetes por usuario (hoy se asume acceso admin único vía `~/.kube/config` del PC).
- Token de HuggingFace (`HUGGING_FACE_HUB_TOKEN`) si el modelo elegido requiere licencia aceptada o es privado (nota en A8).
- Tuning fino de `--max-model-len` / `--gpu-memory-utilization` según el modelo final elegido y el uso real observado de VRAM.
