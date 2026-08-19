# Engram Cloud — memoria persistente centralizada

Documentación de referencia para desplegar y operar **Engram Cloud**: el
backend que centraliza la memoria persistente de los agentes de IA
(Claude Code, Codex, OpenCode, Hermes/Jarvis) en un único servidor, con
Kubernetes + PostgreSQL, expuesto de forma privada por LAN y por Tailscale.

Esta carpeta documenta el **cómo** (arquitectura, instalación, conexión de
clientes) de forma reusable. El **qué pasó realmente** en este repo —
decisiones tomadas, bugs encontrados, comandos exactos corridos, fechas —
vive en [`specs/011_engram_cloud_centralized.md`](../../specs/011_engram_cloud_centralized.md).
Si algo acá y allá no coincide, el spec 011 es la fuente de verdad sobre lo
que quedó desplegado hoy; estos documentos son la guía general para volver
a hacerlo (o hacerlo en otro clúster) desde cero.

## Índice

1. [Arquitectura](architecture.md) — qué componentes hay, cómo se
   conectan, y por qué está diseñado así.
2. [Instalación en Kubernetes](installation.md) — desplegar Engram Cloud
   desde cero: prerrequisitos, PKI, secrets, manifiestos, verificación.
3. [Conectar clientes](client-setup.md) — cómo sumar un agente nuevo:
   mismo nodo, LAN, Tailnet, otra máquina sin `kubectl`, y **Raspberry Pi**.

## Resumen de 30 segundos

- Un `Deployment` de Engram Cloud + un `Deployment` de PostgreSQL, en su
  propio namespace de Kubernetes.
- Expuesto **solo** por Traefik con **mTLS obligatorio** (certificado de
  cliente) más un bearer token de aplicación — nunca público, nunca
  `LoadBalancer`/`NodePort` directo.
- Dos caminos de red posibles hacia el mismo servicio: LAN local y/o
  Tailscale (Tailnet) — se elige uno, otro, o ambos.
- Cada agente/máquina tiene su **propia identidad**: certificado de
  cliente + token de aplicación propios, revocables por separado.
- El binario `engram` es Go, con builds oficiales `linux/amd64` y
  `linux/arm64` — corre igual en un servidor x86 que en una Raspberry Pi.
