---
name: web-search-fallback
description: Fallback para agent-browser: usa curl/wget cuando el navegador no esté disponible
tags: web, fallback, curl, wget, research
---

# Web Search Fallback

## 🎯 Descripción

Esta skill proporciona una alternativa funcional a las herramientas de navegador (browser_navigate, browser_click, etc.) cuando agent-browser no está disponible o falla en el sistema.

Usa curl o wget para descargar contenido web, extraer información y procesarla sin dependencias de navegador.

## 📌 Cuándo Usar

Activa este fallback cuando:
- agent-browser no está instalado (bash: agent-browser: orden no encontrada)
- El navegador falla con errores como Auto-launch failed: Chrome not found
- Necesitas investigación web rápida sin instalar componentes
- Estás en un entorno headless sin control completo del sistema

## 🔧 Procedimiento

### 1. Búsqueda Básica con curl

curl -s "https://es.wikipedia.org/wiki/Entrelazamiento_cu%C3%A1ntico&format=json"

Explicación:
- -s: silencioso (sin progreso bar)
- URL codificada para caracteres especiales
- format=json: API de Wikipedia para texto limpio

### 2. Extraer Contenido de JSON

curl -s "URL" | python3 -m json.tool | grep -A 10 '"extract"'

### 3. Descargar HTML Completo

curl -s -o archivo.html "https://ejemplo.com/articulo"

### 4. Obtener Texto Limpio

curl -s "URL" | grep -v "<script\|<style\|<head" | sed 's/<[^>]*>//g'

## 📚 Ejemplo: Investigación de Entrelazamiento Cuántico

### Paso 1: Llamar a la API de Wikipedia

curl -s "https://es.wikipedia.org/w/api.php?action=query&prop=extracts&exintro&titles=Entrelazamiento_cu%C3%A1ntico&format=json"

### Paso 2: Parsear el Resultado

curl -s "URL" | python3 -c '
import sys, json
data = json.load(sys.stdin)
extract = data["query"]["pages"]["255605"]["extract"]
print(extract.replace("\\u00e1", "á").replace("\\u00e9", "é").replace("\\u00ed", "í"))
'

### Paso 3: Obtener Resumen (Intro)

curl -s "URL" | grep -o '"extract":"[^"]*"' | head -1 | cut -d'"' -f4

## 🛠️ Herramientas Disponibles

| Herramienta | Uso |
|-------------|-----|
| curl -s | Download silencioso |
| curl -L | Seguir redirects |
| curl -o file.html | Guardar archivo |
| curl -I | Headers solo |
| wget -qO- | Download silencioso a stdout |
| grep -v | Filtrar líneas |
| python3 -m json.tool | Formatear JSON |
| sed | Limpieza de texto |

## ⚡ Ejemplos Rápidos

### Búsqueda en Google (extractor de snippets)

curl -s "https://www.google.com/search?q=entrelazamiento+cu%C3%A1ntico&tbm=bsc" | grep -o '<div class="gs_snippet">[^<]*</div>' | head -5

### Extraer enlaces de un sitio

curl -s "URL" | grep -o 'href="[^"]*"' | sed 's/href="//;s/"$//'

### Descargar PDF

curl -s -L -o documento.pdf "URL"

### Verificar disponibilidad de URL

curl -s -o /dev/null -w "%{http_code}" "URL"
# 200 = OK, 404 = No encontrado

## ⚠️ Limitaciones

- No soporta JavaScript: curl no ejecuta scripts dinámicos
- No renderiza contenido visual: Solo texto plano
- Captcha: Puede requerir verificación manual
- Tamaño: URLs muy grandes pueden exceder límites de curl
- Autenticación: Cookies/headers complejos requieren configuración manual

## 💡 Consejos

1. Usa APIs cuando estén disponibles (Wikipedia, GitHub, etc.)
2. Codifica URLs para caracteres especiales: Entrelazamiento+cu%C3%A1ntico
3. Usa JSON para datos estructurados (más limpio que HTML)
4. Combina con grep/python para procesar respuestas
5. Guarda respuestas importantes para análisis posterior

## 🔗 Referencias

- curl manual: https://curl.se/manual/
- Wikipedia API: https://es.wikipedia.org/w/api.php?action=help&format=json
- wget manual: https://www.gnu.org/software/wget/manual/wget.html

---

Nota: Esta skill complementa las herramientas browser_* existentes. Úsala cuando el navegador no esté disponible o falle.
