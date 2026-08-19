# SPEC 010 — Voz local estilo JARVIS para Hermes con Piper

**Estado:** Implementada  
**Fecha de propuesta:** 2026-07-29  
**Fecha de implementación:** 2026-08-01  
**Host:** `trantor`  
**Change ID:** `add-local-jarvis-voice`  
**Capacidad:** `voice-output`  
**Motor:** Piper `1.6.0`  
**Modelo primario:** `es_ES-davefx-medium`  
**Prosodia aprobada:** `length_scale: 1.15`  
**Perfil opcional en inglés:** `en_GB-alan-medium` (no evaluado)

---

## 1. Propuesta

### 1.1 Intención

Dotar a Hermes de salida de voz local, masculina, sobria y de baja latencia para respuestas en español, inspirada en el arquetipo de un asistente británico elegante sin imitar ni afirmar ser un personaje, intérprete o voz protegida concreta.

La primera versión utilizará el proveedor Piper ya soportado por Hermes y el modelo `es_ES-davefx-medium`. La voz `en_GB-alan-medium` se reserva como perfil opcional para contenido en inglés.

### 1.2 Motivación

La configuración actual usa `tts.provider: edge`, que depende de un servicio externo. Hermes 0.19.0 ya incluye integración nativa con Piper, descarga automática de voces y caché de la instancia del modelo. Una implementación basada en Piper permite:

- síntesis completamente local después de descargar el modelo;
- ausencia de credenciales o cuotas de API;
- menor exposición de texto a terceros;
- funcionamiento sin GPU;
- instalación y reversión sencillas;
- entrega de audio mediante el mecanismo `text_to_speech` existente.

### 1.3 Alcance de la primera versión

Incluye:

1. instalar la dependencia opcional `piper-tts` en el entorno de Hermes;
2. configurar Piper como proveedor TTS activo;
3. utilizar `es_ES-davefx-medium` como voz principal;
4. mantener texto como canal autoritativo y fallback;
5. sintetizar audio completo, no streaming;
6. permitir activación bajo demanda mediante `text_to_speech` y los modos de voz ya disponibles en Hermes;
7. entregar el audio como archivo o nota de voz según las capacidades del canal;
8. comprobar funcionamiento local, latencia, inteligibilidad, caché y recuperación ante fallos;
9. documentar copia de seguridad, reversión y límites conocidos.

### 1.4 Fuera de alcance

La primera versión no incluye:

- clonación biométrica de una voz humana real;
- imitación de actores o personajes protegidos;
- palabra de activación permanente (*wake word*);
- captura continua del micrófono;
- reconocimiento de voz o STT nuevo;
- síntesis incremental o streaming;
- enrutamiento automático multilingüe entre dos modelos;
- modificación del núcleo de Hermes si la integración nativa resulta suficiente;
- caché persistente de textos o audios generados;
- reproducción remota interrumpible de una nota de voz ya enviada por Telegram.

### 1.5 Criterios de éxito

El cambio se considera exitoso cuando:

- Hermes sintetiza una frase española con Piper sin consultar una API externa;
- el modelo se descarga una sola vez y se reutiliza desde la caché local;
- el audio resultante es válido, reproducible e inteligible;
- una petición desde Telegram entrega una nota de voz o archivo de audio reproducible;
- una petición desde el cliente local puede reproducirse con el servidor de audio disponible;
- un fallo de Piper no elimina ni bloquea la respuesta textual;
- la configuración puede revertirse a Edge restaurando el respaldo o cambiando un único campo;
- no se introducen secretos ni telemetría de texto hacia terceros.

---

## 2. Hechos, decisiones y supuestos

### 2.1 Hechos confirmados

- La versión instalada de Hermes es `0.19.0`.
- La configuración efectiva actual contiene `tts.provider: edge`.
- Hermes reconoce un proveedor nativo `piper`.
- La dependencia de Python `piper` no está instalada actualmente en el entorno comprobado.
- Hermes descarga voces no presentes mediante `python -m piper.download_voices <voice>`.
- La caché predeterminada de voces es `~/.hermes/cache/piper-voices/`.
- Hermes acepta un nombre de catálogo o una ruta absoluta terminada en `.onnx`.
- El modelo `es_ES-davefx-medium` es español de España, un solo hablante, calidad media y 22.050 Hz.
- El archivo ONNX publicado pesa aproximadamente 63 MB.
- El dataset declarado por la tarjeta del modelo usa licencia CC0.
- El host dispone de `ffmpeg`, `ffplay`, `pw-play` y `paplay`.
- El repositorio contiene cambios previos no confirmados; esta propuesta no debe alterarlos.

### 2.2 Decisiones

| ID | Decisión | Motivo |
|---|---|---|
| D-001 | Usar Piper para la versión inicial | Es local, ligero, soportado de forma nativa y reversible. |
| D-002 | Usar `es_ES-davefx-medium` como modelo primario | Hermes responde principalmente en español; una voz inglesa degradaría nombres y frases españolas. |
| D-003 | Mantener `en_GB-alan-medium` como opción inglesa | Proporciona un perfil británico cuando el texto esté realmente en inglés. |
| D-004 | Comenzar con parámetros de inferencia del modelo | Evita ajustar prosodia sin una prueba auditiva comparativa. |
| D-005 | Sintetizar la respuesta completa | Es el comportamiento nativo y reduce complejidad en la primera versión. |
| D-006 | Activación bajo demanda | Evita audio no solicitado, latencia y ruido en todas las respuestas. |
| D-007 | No persistir caché de audio | Reduce exposición de contenido; solo se conserva el modelo descargado. |
| D-008 | Mantener siempre la respuesta textual | Garantiza accesibilidad y recuperación ante fallos de audio. |
| D-009 | No modificar código de Hermes inicialmente | La capacidad ya existe; se favorecerá configuración antes que un fork innecesario. |
| D-010 | **Excepción a D-009**: parchear `tools/tts_tool.py` para forzar `libopus` en salidas `.ogg` | La configuración no podía resolverlo. La conversión de Piper delegaba el códec a ffmpeg, que por defecto usa `libvorbis` para `.ogg`; Telegram solo decodifica Ogg/Opus en burbujas de voz. Sin el parche, AC-008 es inalcanzable. El propio código ya forzaba `libopus` en otros dos caminos (`tts_tool.py:957` y `:2027`), por lo que el parche corrige una omisión, no introduce un criterio nuevo. |
| D-011 | `length_scale: 1.15`, resto de parámetros en valores del modelo | Elegido por evaluación auditiva del usuario sobre siete variantes (1.00 → 1.25). Cumple T-403. |

### 2.3 Supuestos a validar durante implementación

- El paquete `piper-tts` dispone de una rueda compatible con el Python y la arquitectura del host.
- La voz `davefx` ofrece una calidad subjetiva aceptable para el usuario.
- El tiempo de síntesis de una respuesta habitual es suficientemente bajo para uso interactivo.
- Telegram enruta el formato de salida generado como nota de voz o audio sin conversión defectuosa.

---

## 3. Especificación delta

## ADDED Requirements

### REQ-VOICE-001 — Síntesis local

Hermes **DEBE** poder sintetizar voz con Piper sin transmitir el texto a un servicio TTS externo después de que dependencias y modelo estén disponibles localmente.

#### Escenario: síntesis sin red después del calentamiento

- **DADO** que `piper-tts` y `es_ES-davefx-medium` están almacenados localmente
- **Y** que la red externa no está disponible
- **CUANDO** Hermes sintetiza una frase española
- **ENTONCES** genera un archivo de audio reproducible
- **Y** no intenta invocar Edge, OpenAI, ElevenLabs u otro proveedor externo

### REQ-VOICE-002 — Modelo español predeterminado

Hermes **DEBE** usar `es_ES-davefx-medium` como voz predeterminada de la capacidad.

#### Escenario: selección efectiva

- **DADO** que la configuración del cambio está activa
- **CUANDO** se consulta la configuración TTS efectiva
- **ENTONCES** `tts.provider` es `piper`
- **Y** `tts.piper.voice` es `es_ES-davefx-medium`

### REQ-VOICE-003 — Descarga y caché del modelo

Hermes **DEBE** descargar el modelo solamente cuando no exista en la caché local y **DEBE** reutilizarlo en las síntesis posteriores.

#### Escenario: primera síntesis

- **DADO** que la voz no existe en `~/.hermes/cache/piper-voices/`
- **CUANDO** se solicita la primera síntesis
- **ENTONCES** Hermes descarga el `.onnx` y su `.onnx.json`
- **Y** genera el audio solicitado

#### Escenario: síntesis posterior

- **DADO** que los dos archivos del modelo ya existen y son válidos
- **CUANDO** se solicita otra síntesis
- **ENTONCES** Hermes reutiliza los archivos locales
- **Y** no vuelve a descargarlos

### REQ-VOICE-004 — Activación explícita

La voz **DEBE** activarse únicamente cuando el usuario solicite salida de audio, cuando se invoque `text_to_speech` o cuando un modo de voz existente esté habilitado explícitamente.

#### Escenario: respuesta textual normal

- **DADO** que no se ha solicitado voz
- **CUANDO** Hermes responde a un mensaje normal
- **ENTONCES** entrega texto
- **Y** no genera audio automáticamente

#### Escenario: solicitud explícita

- **DADO** que el usuario solicita escuchar la respuesta
- **CUANDO** Hermes invoca `text_to_speech`
- **ENTONCES** genera y entrega audio con Piper

### REQ-VOICE-005 — Fallback textual seguro

Un error de instalación, descarga, carga, síntesis, conversión o entrega de audio **NO DEBE** suprimir la respuesta textual.

#### Escenario: Piper falla

- **DADO** que Piper devuelve una excepción o un archivo vacío
- **CUANDO** Hermes procesa una respuesta
- **ENTONCES** conserva la respuesta textual completa
- **Y** informa brevemente que el audio no pudo generarse
- **Y** no afirma que la nota de voz se entregó

### REQ-VOICE-006 — Integridad del audio

El resultado **DEBE** existir, tener tamaño mayor que cero y poder decodificarse antes de declararse correcto.

#### Escenario: verificación de artefacto

- **DADO** que Piper ha terminado la síntesis
- **CUANDO** se valida el archivo de salida con `ffprobe`
- **ENTONCES** se detecta al menos una pista de audio válida
- **Y** la duración es mayor que cero

### REQ-VOICE-007 — Entrega por Telegram

Cuando la solicitud se origine en Telegram, Hermes **DEBE** entregar el audio mediante el adaptador nativo y **DEBE** mantener texto legible en la conversación.

#### Escenario: nota de voz reproducible

- **DADO** que Telegram está conectado y autorizado
- **CUANDO** el usuario solicita una prueba de voz
- **ENTONCES** recibe un adjunto de audio reproducible
- **Y** recibe o conserva el contenido textual equivalente

### REQ-VOICE-008 — Reproducción local

Cuando la síntesis se use desde un cliente local con reproducción habilitada, Hermes **DEBE** reproducir el archivo mediante un backend disponible del sistema.

#### Escenario: reproducción local

- **DADO** que existe un servidor de audio y al menos un reproductor compatible
- **CUANDO** el modo de voz local genera una respuesta
- **ENTONCES** el audio se reproduce sin bloquear permanentemente la sesión

### REQ-VOICE-009 — Privacidad

El cambio **NO DEBE** almacenar de forma persistente el texto sintetizado ni una caché de audios de conversación, salvo los archivos temporales necesarios para la entrega.

#### Escenario: limpieza temporal

- **DADO** que un audio ya fue entregado o reproducido
- **CUANDO** termina su ciclo de vida temporal
- **ENTONCES** puede eliminarse sin afectar el modelo cacheado
- **Y** el modelo ONNX permanece disponible para futuras síntesis

### REQ-VOICE-010 — Configuración reversible

La activación **DEBE** conservar una copia de seguridad de `~/.hermes/config.yaml` y permitir volver al proveedor anterior.

#### Escenario: rollback

- **DADO** que Piper produce un resultado inaceptable
- **CUANDO** se ejecuta el procedimiento de reversión
- **ENTONCES** `tts.provider` vuelve a `edge`
- **Y** `hermes config check` no reporta errores nuevos

### REQ-VOICE-011 — Ajuste de prosodia controlado

Los parámetros `length_scale`, `noise_scale`, `noise_w_scale`, `volume` y `normalize_audio` **DEBEN** permanecer inicialmente en los valores del modelo y **PUEDEN** modificarse después de una prueba auditiva A/B documentada.

#### Escenario: configuración inicial

- **DADO** que no existe una prueba auditiva aprobada
- **CUANDO** se activa Piper
- **ENTONCES** no se fuerzan parámetros avanzados arbitrarios

### REQ-VOICE-012 — Perfil inspirado, no imitación

La funcionalidad **DEBE** describirse como una voz original de asistente sobrio y tecnológico; **NO DEBE** presentarse como clon o reproducción de una voz humana o personaje concreto.

#### Escenario: presentación al usuario

- **DADO** que se documenta o presenta la voz
- **CUANDO** se describe su identidad
- **ENTONCES** se utilizan términos como “estilo de asistente británico” o “inspirada en el arquetipo”
- **Y** no se atribuye identidad, afiliación o interpretación ajena

---

## 4. Diseño técnico

### 4.1 Arquitectura

```text
Usuario / canal
      │
      ▼
Respuesta textual de Hermes ───────────────► texto (siempre disponible)
      │
      ├── sin solicitud de voz ────────────► fin
      │
      └── solicitud/modo de voz
                 │
                 ▼
        tool text_to_speech
                 │
                 ▼
      proveedor tts.provider=piper
                 │
                 ├── carga en memoria de la voz cacheada
                 ├── síntesis local completa
                 └── archivo temporal de audio
                              │
                  ┌───────────┴───────────┐
                  ▼                       ▼
           adaptador Telegram       reproductor local
           nota/archivo de voz      ffplay/pw-play/paplay
```

### 4.2 Componentes existentes reutilizados

| Componente | Responsabilidad |
|---|---|
| `tools/tts_tool.py` | Selección del proveedor, descarga/carga del modelo y síntesis. |
| `text_to_speech` | Interfaz de herramienta para convertir texto en audio. |
| `tts.provider` | Selección de Piper. |
| `tts.piper.voice` | Nombre de catálogo o ruta ONNX. |
| `~/.hermes/cache/piper-voices/` | Caché persistente del modelo. |
| Adaptadores de gateway | Entrega nativa de `MEDIA:` según plataforma y extensión. |
| Modo de voz local | Reproducción de respuestas sintetizadas. |

### 4.3 Configuración objetivo mínima

```yaml
tts:
  provider: piper
  piper:
    voice: es_ES-davefx-medium
```

No se añadirán parámetros avanzados durante la activación inicial. Si la evaluación auditiva justifica ajustes, se registrarán con sus resultados antes y después.

**Configuración final tras la evaluación auditiva (Fase 4):**

```yaml
tts:
  provider: piper
  piper:
    voice: es_ES-davefx-medium
    length_scale: 1.15
```

`length_scale` se lee en `tools/tts_tool.py:2284`. El `SynthesisConfig` solo se construye si al menos una de `length_scale`, `noise_scale`, `noise_w_scale`, `volume` o `normalize_audio` está presente (`tts_tool.py:2265-2288`); las ausentes conservan los valores del modelo.

> Nota operativa: `hermes config set` emite el aviso *"not a recognized config key"* para `tts.piper.length_scale`. El aviso es incorrecto en este caso — el efecto se verificó por duración de audio, no por la configuración declarada.

### 4.4 Modelo de voz

#### Primario: `es_ES-davefx-medium`

- idioma: español de España (`es_ES`);
- hablantes: 1;
- calidad: media;
- muestreo: 22.050 Hz;
- formato: ONNX y JSON de configuración;
- tamaño ONNX observado: 63.201.294 bytes;
- dataset declarado: `davefx`, licencia CC0;
- afinado desde `en_US-lessac-medium`.

#### Opcional: `en_GB-alan-medium`

Se evaluará únicamente si el texto está en inglés o si una fase posterior implementa enrutamiento por idioma. No será el perfil principal para español.

### 4.5 Flujo de primera ejecución

1. Hermes recibe una solicitud explícita de voz.
2. El proveedor comprueba si `es_ES-davefx-medium` está en caché.
3. Si falta, ejecuta el descargador oficial de Piper.
4. Verifica que existen `.onnx` y `.onnx.json`.
5. Carga el modelo y mantiene la instancia en la caché del proceso.
6. Sintetiza el texto completo.
7. Convierte o empaqueta el audio en el formato esperado por el canal.
8. Valida tamaño y decodificación.
9. Entrega el audio y conserva la respuesta textual.

### 4.6 Latencia y rendimiento

La aceptación se basará en medición real sobre el host, no en una cifra inventada. Se medirán por separado:

- tiempo de instalación;
- tiempo de descarga inicial;
- tiempo de carga fría del modelo;
- tiempo de síntesis fría;
- tiempo de síntesis caliente;
- duración del audio;
- factor de tiempo real: `tiempo_síntesis / duración_audio`;
- memoria residente antes y después de cargar la voz.

Objetivos iniciales:

- la síntesis caliente **DEBERÍA** producir audio más rápido que su duración (`RTF < 1`);
- una frase corta **DEBERÍA** comenzar su entrega en menos de 5 segundos después del calentamiento;
- la carga del proveedor **NO DEBE** degradar respuestas que no solicitan voz.

Si el host no cumple los objetivos, se evaluará `x_low` o `low` antes de abandonar Piper.

### 4.7 Interrupción

- En reproducción local, la sesión **DEBERÍA** poder detener el proceso de reproducción activo sin detener Hermes.
- En Telegram, una nota de voz ya entregada no puede ser interrumpida desde el servidor; el usuario controla la reproducción en el cliente.
- La síntesis completa en curso puede cancelarse solo si el flujo existente expone cancelación segura; no se añadirá concurrencia nueva en esta fase.

### 4.8 Caché

- Se conserva el modelo y su configuración en la caché de Piper.
- La instancia cargada puede mantenerse en memoria durante la vida del proceso.
- No se crea una caché por hash del texto.
- Los audios temporales deben seguir el ciclo de limpieza existente de Hermes.

### 4.9 Seguridad y privacidad

- No se requieren claves de API.
- Los textos permanecen en el host durante la síntesis.
- La descarga inicial del modelo contacta Hugging Face o el origen configurado por Piper.
- Se debe registrar el origen, revisión o hash del modelo descargado durante implementación para hacer reproducible la instalación.
- No se ejecutarán scripts incluidos en repositorios de modelos.
- Se verificará que los archivos descargados sean ONNX/JSON y no artefactos ejecutables inesperados.
- No se habilitará captura continua de micrófono.

### 4.10 Observabilidad

La implementación debe poder distinguir en logs:

- proveedor seleccionado;
- descarga inicial;
- ruta del modelo utilizado;
- carga fría o reutilización de instancia;
- tiempo de síntesis;
- conversión y entrega;
- error de dependencia, descarga, modelo, síntesis o canal.

Los logs no deben almacenar el texto completo sintetizado por defecto.

### 4.11 Fallos y degradación

| Fallo | Conducta esperada |
|---|---|
| `piper-tts` ausente | Informar dependencia faltante; conservar texto. |
| descarga fallida | No cambiar silenciosamente a un servicio externo; conservar texto. |
| modelo incompleto/corrupto | Rechazarlo, informar y permitir descarga limpia posterior. |
| audio vacío o inválido | No entregarlo ni declarar éxito. |
| adaptador Telegram falla | Conservar el texto y reportar el fallo de entrega. |
| calidad subjetiva insuficiente | Revertir o evaluar otra voz Piper; no alterar el núcleo. |
| rendimiento insuficiente | Medir y probar un tier menor antes de cambiar de motor. |

### 4.12 Alternativas consideradas

| Alternativa | Ventaja | Motivo para no elegirla inicialmente |
|---|---|---|
| Edge TTS | Integración actual y voces fluidas | Requiere servicio externo y red. |
| CosyVoice | Mayor expresividad y clonación | Más pesado, más complejo y mayor superficie de seguridad. |
| ElevenLabs | Alta calidad | Servicio externo, credenciales, coste y envío de texto. |
| OpenAI TTS | Calidad y simplicidad API | Servicio externo y coste. |
| Voz Piper inglesa para todo | Perfil británico | Pronunciación española previsiblemente inferior. |
| Modelo Piper personalizado | Identidad de voz propia | Requiere dataset, entrenamiento, evaluación y gobernanza adicionales. |

CosyVoice permanece como alternativa de segunda fase si Piper no alcanza la calidad requerida y existen recursos suficientes.

---

## 5. Plan de implementación

### Fase 0 — Salvaguarda y línea base

- [x] T-001 Hermes `0.19.0`, Python `3.11.15`, `x86_64`, servicio bajo systemd de usuario.
- [x] T-002 Respaldo `~/.hermes/config.yaml.bak-fase1-20260801T063056Z` (modo 600).
- [x] T-003 Estado previo: `tts.provider: edge` — valor por defecto, no configurado; el archivo no contenía bloque `tts:`. `hermes config check` sin errores.
- [ ] T-004 Muestra de control con Edge — **omitida**. La línea base efectiva era el default de fábrica, no una configuración elegida; no aportaba comparación útil.

### Fase 1 — Dependencia local

- [x] T-101 `piper-tts 1.6.0` instalado en `/home/pedro/.hermes/hermes-agent/venv` con `uv pip install`. Dependencias arrastradas: `onnxruntime 1.28.0`, `numpy 2.4.6`, `flatbuffers 25.12.19`, `pathvalidate 3.3.1` — ninguna sustituyó una versión previa.
- [x] T-102 `from piper import PiperVoice` verificado con el intérprete exacto del gateway. Proveedores ONNX: `CPUExecutionProvider` (sin GPU).
- [x] T-103 Versión registrada: `piper-tts 1.6.0`.

### Fase 2 — Modelo y configuración

- [x] T-201 El modelo ya estaba en caché con nombre genérico `model.onnx`; renombrado a `es_ES-davefx-medium.onnx` (+ `.onnx.json`). Se evitó una redescarga de 63 MB.
- [x] T-202 Identidad confirmada antes de renombrar: `dataset: davefx`, `quality: medium`, `num_speakers: 1`, `sample_rate: 22050`, 63.201.294 bytes — coincide con §4.4.
- [x] T-203 SHA-256:
  - `es_ES-davefx-medium.onnx` → `6658b03b1a6c316ee4c265a9896abc1393353c2d9e1bca7d66c2c442e222a917`
  - `es_ES-davefx-medium.onnx.json` → `0e0dda87c732f6f38771ff274a6380d9252f327dca77aa2963d5fbdf9ec54842`
- [x] T-204 Aplicado `tts.provider: piper` y `tts.piper.voice: es_ES-davefx-medium`.
- [x] T-205 `hermes config check` → exit 0, sin errores nuevos, antes de reiniciar.

### Fase 3 — Pruebas funcionales

- [x] T-301 Frase de control española sintetizada con acentos, números, hora y siglas técnicas.
- [x] T-302 `ffprobe`: `pcm_s16le`, 22.050 Hz, mono, 16,08 s. Reproducción local confirmada.
- [x] T-303 Carga fría 0,590 s; síntesis fría 0,305 s; caliente 0,297 s. RSS 49,7 MB → 137,5 MB.
- [x] T-304 Verificado bajo namespace de red aislado (`unshare -rn`): la conexión a Hugging Face falla con `OSError` y aun así sintetiza en 0,067 s.
- [x] T-305 Nota de voz nativa recibida y reproducible en Telegram, con el texto equivalente en el mismo mensaje. Requirió resolver cuatro defectos — ver §13.
- [x] T-306 Con voz inexistente y sin red, `text_to_speech_tool` devuelve JSON `success: false` con campo `error`; no lanza excepción ni interrumpe el proceso.

### Fase 4 — Evaluación auditiva

- [x] T-401 Evaluada inteligibilidad sobre la frase de control (acentos, «veintitrés», «21:35», «Kubernetes», «PostgreSQL», «HTTPS»).
- [x] T-402 Comparadas siete variantes de `length_scale`: 1.00, 1.06, 1.09, 1.12, 1.15, 1.19, 1.25. Se superó deliberadamente el límite de dos variantes a petición del usuario, para acotar el punto de transición con más resolución.
- [x] T-403 `length_scale: 1.15` elegido por aprobación auditiva del usuario. Ningún otro parámetro fue modificado.
- [ ] T-404 `en_GB-alan-medium` — **pendiente**. No evaluado; queda fuera del alcance de esta implementación.

### Fase 5 — Operación y cierre

- [x] T-501 Activación y caché documentadas — ver §14.
- [x] T-502 Limpieza de temporales documentada — ver §14.
- [x] T-503 Rollback documentado y ampliado — ver §7 y §14.
- [x] T-504 Evidencia de criterios de aceptación registrada — ver §8.
- [x] T-505 Estado cambiado a `Implementada` con evidencia medida.

---

## 6. Matriz de trazabilidad

| Requisito | Diseño | Tareas | Evidencia esperada |
|---|---|---|---|
| REQ-VOICE-001 | 4.1, 4.5, 4.9 | T-101–T-102, T-304 | Síntesis válida con red deshabilitada. |
| REQ-VOICE-002 | 4.3, 4.4 | T-201, T-204 | Salida de `hermes config get tts`. |
| REQ-VOICE-003 | 4.5, 4.8 | T-201–T-203, T-303 | Una descarga y posteriores reutilizaciones. |
| REQ-VOICE-004 | 1.3, 4.1 | T-305, T-501 | Sin audio espontáneo; audio bajo demanda. |
| REQ-VOICE-005 | 4.11 | T-306 | Texto presente durante fallo provocado. |
| REQ-VOICE-006 | 4.5 | T-202, T-301–T-302 | `ffprobe` válido y duración positiva. |
| REQ-VOICE-007 | 4.2 | T-305 | Mensaje real recibido en Telegram. |
| REQ-VOICE-008 | 4.1, 4.7 | T-302 | Reproducción local confirmada. |
| REQ-VOICE-009 | 4.8, 4.9 | T-502 | Inspección de temporales y caché. |
| REQ-VOICE-010 | 4.11, 7 | T-002, T-503 | Rollback ejecutable y configuración válida. |
| REQ-VOICE-011 | 4.3, 4.6 | T-401–T-403 | Hoja A/B y parámetros aprobados. |
| REQ-VOICE-012 | 1.1, 4.9 | T-501 | Documentación sin afirmaciones de clonación. |

---

## 7. Rollback

1. Detener cualquier prueba de síntesis en curso sin matar procesos no relacionados.
2. Restaurar el respaldo fechado de `~/.hermes/config.yaml` o establecer `tts.provider: edge`.
3. Ejecutar `hermes config check`.
4. Reiniciar o recargar Hermes únicamente si el método de ejecución lo requiere.
5. Comprobar `hermes config get tts`.
6. Ejecutar una respuesta textual y confirmar que el gateway sigue operativo.
7. Conservar la caché de Piper salvo que exista una razón explícita para eliminarla; no afecta al proveedor Edge.
8. Desinstalar `piper-tts` solo si se desea una reversión completa y después de identificar el entorno virtual correcto.

---

## 8. Criterios de aceptación

- [x] AC-001 `hermes config check` → exit 0 en cada cambio aplicado.
- [x] AC-002 `hermes config get tts` devuelve `provider: piper`, `voice: es_ES-davefx-medium`, `length_scale: 1.15`.
- [x] AC-003 Modelo localizado en caché tras el renombrado al nombre de catálogo; sin descarga adicional.
- [x] AC-004 Síntesis sucesivas reutilizan el modelo; la instancia queda cacheada en el proceso.
- [x] AC-005 `ffprobe`: pista válida, duración > 0, en WAV y en Ogg/Opus.
- [x] AC-006 Frase de control inteligible; acentos, «veintitrés», «21:35» y siglas técnicas correctos.
- [x] AC-007 `RTF 0.018` en caliente — 55× más rápido que tiempo real. Superado con amplio margen.
- [x] AC-008 Burbuja de voz nativa recibida y **reproducida** en Telegram. Requirió el parche D-010.
- [x] AC-009 Verificado bajo aislamiento de red real, no simulado.
- [x] AC-010 Fallo provocado devuelve error estructurado; la respuesta textual sobrevive.
- [x] AC-011 Sin textos de conversación en logs persistentes nuevos. El nivel `-vv` usado durante el diagnóstico sí registra contenido y fue retirado al terminar.
- [x] AC-012 Rollback documentado y ejecutable — ver §7 y §14.
- [x] AC-013 El usuario aprobó la voz `davefx` con `length_scale: 1.15`.

**No cubierto:** T-004 (muestra de control con Edge, omitida por irrelevante) y T-404 (`en_GB-alan-medium`, no evaluado).

---

## 9. Prueba de voz propuesta

Texto español de control:

> Buenas noches, Master. Los sistemas están operativos. La temperatura es de veintitrés grados, la tarea número 42 terminó a las 21:35 y no se detectaron anomalías. Conviene revisar Kubernetes, PostgreSQL y la API HTTPS antes del despliegue.

Texto inglés opcional para `en_GB-alan-medium`:

> Good evening, Master. All systems are operational. Task forty-two completed at twenty-one thirty-five, and no anomalies were detected.

La evaluación debe puntuar de 1 a 5:

- inteligibilidad;
- naturalidad;
- ritmo;
- autoridad serena;
- pronunciación técnica;
- fatiga auditiva;
- adecuación al estilo del asistente.

---

## 10. Preguntas resueltas y pendientes

### Resueltas por decisión de diseño

- **Idioma primario:** español de España.
- **Voz primaria:** `es_ES-davefx-medium`.
- **Salida:** audio local o adjunto nativo según canal.
- **Activación:** explícita y bajo demanda.
- **Modo de síntesis:** respuesta completa, no streaming.
- **Fallback:** texto siempre disponible; sin cambio silencioso a TTS externo.
- **Caché:** modelo sí; audio y texto no.
- **GPU:** no requerida para la primera versión.

### Pendientes de validación humana

- ¿La voz `davefx` cumple la expectativa subjetiva de estilo?
- ¿Debe la fase posterior activar voz automáticamente en algún chat o mantenerla siempre bajo demanda?
- ¿Se justifica una voz personalizada después de evaluar Piper?

Estas preguntas no bloquean la implementación técnica de la primera muestra; sí bloquean declarar definitiva la identidad vocal.

---

## 11. Fuentes

- Hermes Agent, documentación oficial de TTS: `https://hermes-agent.nousresearch.com/docs/user-guide/features/tts`
- Código local de Hermes 0.19.0: `tools/tts_tool.py`
- Catálogo de voces Piper: `https://huggingface.co/rhasspy/piper-voices`
- Modelo primario: `https://huggingface.co/rhasspy/piper-voices/tree/main/es/es_ES/davefx/medium`
- Modelo inglés opcional: `https://huggingface.co/rhasspy/piper-voices/tree/main/en/en_GB/alan/medium`

---

## 12. Puerta de aprobación

Esta especificación no autoriza todavía cambios en `~/.hermes/config.yaml`, instalaciones de paquetes ni reinicios de servicios.

La implementación puede comenzar cuando el usuario apruebe explícitamente:

1. Piper como motor local;
2. `es_ES-davefx-medium` como primera voz de prueba;
3. activación bajo demanda;
4. texto como fallback obligatorio.

> **Cerrada el 2026-08-01.** Los cuatro puntos fueron aprobados y ejecutados. La excepción D-010 (parche al núcleo) se aprobó por separado tras demostrar que AC-008 era inalcanzable sólo con configuración.

---

## 13. Defectos encontrados durante la implementación

Ninguno era previsible desde la propuesta. Se documentan porque afectan a cualquier reinstalación.

### 13.1 Doble unidad systemd (infraestructura)

Existían dos unidades homónimas `hermes-gateway.service`: una de sistema en `/etc/systemd/system/` y una de usuario en `~/.config/systemd/user/`. La de usuario poseía el lock (`~/.hermes/gateway.pid`); la de sistema arrancaba instancias que morían al instante con `Gateway already running`, en bucle (38 reinicios acumulados).

**Resolución:** la unidad de **usuario** es la autoritativa. La de sistema quedó `disable --now`. El *lingering* ya estaba activo, por lo que sobrevive reinicios del host.

**Consecuencia para el diagnóstico:** los logs del gateway están en `journalctl --user -u hermes-gateway`. Buscar en el journal del sistema devuelve un falso vacío.

### 13.2 STT forzado a inglés

`tools/transcription_tools.py:89` define `DEFAULT_LOCAL_STT_LANGUAGE = "en"` y la línea 1192 lo impone explícitamente a Whisper. Sin `stt.language` configurado, todo audio en español se transcribía fonéticamente como inglés.

**Resolución:** `stt.language: es`. Orden de resolución: `stt.local.language` → `stt.language` → `HERMES_LOCAL_STT_LANGUAGE` → default.

**Pendiente:** `stt.local.model` sigue en `base`, débil para español; `faster-whisper` cae a CPU int8 por ausencia de `libcublas.so.12`.

### 13.3 Dedup de sesión que anula la respuesta de voz automática

`gateway/run.py:15717` descarta la respuesta de voz automática si detecta una llamada previa a `text_to_speech` en `agent_messages`, que abarca el historial de la sesión y no sólo el turno actual. Basta con que el agente invoque la herramienta una vez para que el modo `/voice tts` quede inerte durante el resto de esa sesión.

**Mitigación operativa:** `/new` o `/reset` en el chat. No hay corrección de código aplicada.

**Corolario de diseño:** la entrega nativa depende de que el modelo copie el tag `MEDIA:<ruta>` en su mensaje final (`gateway/platforms/base.py:1456`). `qwen3.5-9b` no lo hace de forma fiable. Por eso el modo de voz del gateway es más robusto que confiar en la herramienta.

### 13.4 Códec Vorbis en burbujas de voz (D-010)

`_generate_piper_tts` construía la conversión ffmpeg sin especificar códec; para destinos `.ogg`, ffmpeg elige `libvorbis`. Telegram acepta la subida y dibuja la burbuja, pero no puede decodificarla: burbuja estática.

El reparador de contenedor (`_sniff_audio_container`) sólo comprueba el **contenedor**, no el códec, por lo que un Ogg válido con códec equivocado lo atraviesa sin corrección y `_ffmpeg_transcode_to_opus` nunca se dispara.

**Parche aplicado** en `tools/tts_tool.py`:

```python
conv_cmd = [ffmpeg, "-i", wav_path, "-y", "-loglevel", "error"]
if output_path.endswith(".ogg"):
    conv_cmd += ["-c:a", "libopus", "-b:a", "32k", "-application", "voip"]
conv_cmd.append(output_path)
```

Resultado: `opus`, 48 kHz, mono; además reduce el tamaño a la mitad. Sin regresión en `.mp3`.

**Respaldo:** `tools/tts_tool.py.bak-preopus-20260801T072403Z`.

**Riesgo asumido:** una actualización de Hermes puede sobrescribir el parche. Si reaparece Vorbis en las notas de voz, reaplicar. Procede reportarlo aguas arriba.

---

## 14. Manual de operación

### 14.1 Activación

| Vía | Efecto |
|---|---|
| `/voice on` | Responde con voz cuando recibe una nota de voz |
| `/voice tts` | Voz en todas las respuestas |
| `/voice status` | Modo actual |
| `/voice off` | Sólo texto |

Estado persistido en `~/.hermes/gateway_voice_mode.json`.

Con el modo activo, **pedir con normalidad**. Solicitar explícitamente «háblame por voz» induce al modelo a invocar `text_to_speech` y dispara el dedup de §13.3, que anula la entrega automática.

### 14.2 Caché y temporales

- Modelo: `~/.hermes/cache/piper-voices/es_ES-davefx-medium.onnx` + `.onnx.json`. **Conservar.** Borrarlo fuerza una redescarga de 63 MB.
- Audios de respuesta: `~/.hermes/cache/audio/` y `/tmp/hermes_voice/`. Desechables; se limpian tras la entrega.
- Al limpiar temporales, no incluir `piper-voices/` en el borrado.

### 14.3 Verificación rápida

```bash
hermes config get tts
ffprobe -v error -show_entries stream=codec_name -of csv=p=0 <archivo.ogg>   # debe decir: opus
```

### 14.4 Rollback

Además del procedimiento de §7:

- **Sólo prosodia:** eliminar `tts.piper.length_scale` → vuelve al ritmo del modelo.
- **Sólo el parche del códec:** restaurar `tools/tts_tool.py.bak-preopus-*` y reiniciar. La voz sigue funcionando; se pierde la burbuja nativa.
- **Todo el cambio:** restaurar el respaldo de `config.yaml`, `hermes config check`, reiniciar con `systemctl --user restart hermes-gateway`.

### 14.5 Diagnóstico

Los logs viven en el journal de **usuario**. Para elevar la verbosidad sin que una actualización de Hermes borre el cambio, usar un drop-in en lugar de editar la unidad:

```ini
# ~/.config/systemd/user/hermes-gateway.service.d/10-debug-verbosity.conf
[Service]
ExecStart=
ExecStart=/home/pedro/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main gateway run -vv
```

`agent.verbose` en la configuración **no** controla el nivel de log; lo fijan los flags de CLI (`gateway/run.py:24213`). Retirar el drop-in al terminar: registra contenido de conversación.
