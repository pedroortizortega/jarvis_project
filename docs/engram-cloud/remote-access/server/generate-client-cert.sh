#!/usr/bin/env sh
# Emite un certificado de cliente mTLS para una identidad remota nueva.
#
# Corre SOLO en la máquina que tiene la CA privada (hoy: trantor,
# ~/.config/engram-cloud/pki/ca/). Ver docs/engram-cloud/architecture.md
# para qué es cada archivo de la PKI.
#
# Uso:
#   ./generate-client-cert.sh <identidad>
#
# Ejemplo:
#   ./generate-client-cert.sh laptop-trabajo
#
# Salida: ~/.config/engram-cloud/pki/clients/<identidad>/{<identidad>.crt,.key}
# más una copia de ca.crt al lado, lista para copiar a la máquina remota
# como client.crt / client.key / ca.crt.

set -eu

NAME="${1:?Uso: $0 <identidad>}"
PKI="${ENGRAM_PKI_DIR:-$HOME/.config/engram-cloud/pki}"
PROJECT="${ENGRAM_PROJECT:-jarvis_project}"
DAYS="${ENGRAM_CERT_DAYS:-730}"

if [ ! -f "$PKI/ca/ca.crt" ] || [ ! -f "$PKI/ca/ca.key" ]; then
    echo "error: no se encontró la CA en $PKI/ca/ (ca.crt + ca.key)" >&2
    echo "       este script solo corre en la máquina que tiene la CA privada" >&2
    exit 1
fi

OUT="$PKI/clients/$NAME"
mkdir -p "$OUT"

openssl genrsa -out "$OUT/$NAME.key" 2048
openssl req -new -key "$OUT/$NAME.key" \
    -out "$OUT/$NAME.csr" -subj "/CN=$NAME/O=$PROJECT"
printf 'extendedKeyUsage=clientAuth\n' > "$OUT/$NAME.ext"
openssl x509 -req -in "$OUT/$NAME.csr" \
    -CA "$PKI/ca/ca.crt" -CAkey "$PKI/ca/ca.key" -CAcreateserial \
    -out "$OUT/$NAME.crt" -days "$DAYS" -sha256 \
    -extfile "$OUT/$NAME.ext"

cp "$PKI/ca/ca.crt" "$OUT/ca.crt"
chmod 600 "$OUT/$NAME.key"

echo
echo "Certificado emitido para '$NAME' (válido $DAYS días):"
openssl x509 -in "$OUT/$NAME.crt" -noout -subject -dates
echo
echo "Archivos listos para copiar a la máquina remota (canal seguro, ej. scp"
echo "sobre Tailnet — nunca por un canal sin cifrar):"
echo "  $OUT/$NAME.crt  -> client/client.crt"
echo "  $OUT/$NAME.key  -> client/client.key"
echo "  $OUT/ca.crt     -> client/ca.crt"
echo
echo "Falta además crear la identidad y emitir su token de aplicación desde"
echo "el dashboard admin (POST /dashboard/admin/users, .../grants, .../tokens"
echo "— ver docs/engram-cloud/client-setup.md, 'Antes que nada')."
