#!/bin/bash
# Crea un certificado de firma propio (gratis, sin cuenta de Apple) para que las versiones sucesivas de la app tengan la misma
# identidad y macOS no vuelva a pedir permisos (red local, Documentos…) tras cada actualización.
# Uso: mac/crear-certificado.sh [carpeta de salida]   -> escribe certificado.p12, clave.txt y certificado.p12.base64
set -euo pipefail
OUT="${1:-.}"; mkdir -p "$OUT"
NAME="Coordinador RF (autofirmado)"
PASS="$(openssl rand -hex 16)"
openssl req -x509 -newkey rsa:2048 -nodes -days 7300 -sha256 -subj "/CN=$NAME" \
  -keyout "$OUT/clave.pem" -out "$OUT/cert.pem" \
  -addext "basicConstraints=critical,CA:false" \
  -addext "keyUsage=critical,digitalSignature" \
  -addext "extendedKeyUsage=critical,codeSigning" 2>/dev/null
# macOS solo importa los .p12 con cifrado clásico: OpenSSL 3 necesita -legacy; LibreSSL (el de macOS) no lo conoce ni lo necesita.
if ! openssl pkcs12 -export -legacy -inkey "$OUT/clave.pem" -in "$OUT/cert.pem" -name "$NAME" -passout "pass:$PASS" -out "$OUT/certificado.p12" 2>/dev/null; then
  openssl pkcs12 -export -inkey "$OUT/clave.pem" -in "$OUT/cert.pem" -name "$NAME" -passout "pass:$PASS" -out "$OUT/certificado.p12"
fi
rm -f "$OUT/clave.pem"
base64 < "$OUT/certificado.p12" | tr -d '\n' > "$OUT/certificado.p12.base64"
printf '%s' "$PASS" > "$OUT/clave.txt"
echo "Listo en $OUT: certificado.p12.base64 (secreto MAC_CERT_P12) y clave.txt (secreto MAC_CERT_PASSWORD)."
