#!/bin/bash
# Firma la app de Mac. Con los secretos MAC_CERT_P12 (base64) y MAC_CERT_PASSWORD usa el certificado propio de mac/crear-certificado.sh:
# la identidad es la misma en todas las versiones y macOS conserva los permisos concedidos. Sin ellos, firma "ad hoc" (como hasta ahora).
set -euo pipefail
APP="${1:?uso: firmar.sh ruta/App.app}"
NAME="Coordinador RF (autofirmado)"
if [ -n "${MAC_CERT_P12:-}" ] && [ -n "${MAC_CERT_PASSWORD:-}" ]; then
  TMP="$(mktemp -d)"; KC="$TMP/firma.keychain-db"; KCPASS="$(uuidgen)"
  security create-keychain -p "$KCPASS" "$KC"
  security set-keychain-settings -lut 3600 "$KC"
  security unlock-keychain -p "$KCPASS" "$KC"
  printf '%s' "$MAC_CERT_P12" | base64 --decode > "$TMP/c.p12"
  security import "$TMP/c.p12" -k "$KC" -P "$MAC_CERT_PASSWORD" -T /usr/bin/codesign
  security set-key-partition-list -S apple-tool:,apple: -s -k "$KCPASS" "$KC" >/dev/null
  security list-keychains -d user -s "$KC" $(security list-keychains -d user | tr -d '"')
  if ! codesign --force --deep --sign "$NAME" --keychain "$KC" "$APP" 2>"$TMP/err"; then
    cat "$TMP/err"
    echo "La identidad no se acepta sin confiar en ella: se marca como de confianza para firmar código y se reintenta."
    security find-certificate -c "$NAME" -p "$KC" > "$TMP/cert.pem"
    sudo security add-trusted-cert -d -r trustRoot -p codeSign -k /Library/Keychains/System.keychain "$TMP/cert.pem"
    codesign --force --deep --sign "$NAME" --keychain "$KC" "$APP"
  fi
  echo "Firmada con el certificado propio."
else
  codesign --force --deep -s - "$APP"
  echo "Firmada ad hoc (sin certificado propio)."
fi
codesign --verify --deep --strict "$APP"
codesign -dr - "$APP" 2>&1 | sed -n 's/^designated => /Requisito de identidad: /p'
