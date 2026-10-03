#!/bin/bash
# Prueba (macOS) de mac/firmar.sh: con un certificado de usar y tirar, dos "versiones" distintas de la app deben quedar con
# el mismo requisito de identidad (así macOS las ve como la misma app); sin certificado, la firma ad hoc sigue funcionando.
set -euo pipefail
cd "$(dirname "$0")/.."
W="$(mktemp -d)"
mkapp() {  # mkapp ruta versión
  mkdir -p "$1/Contents/MacOS"
  printf '#!/bin/bash\necho %s\n' "$2" > "$1/Contents/MacOS/prueba"; chmod +x "$1/Contents/MacOS/prueba"
  cat > "$1/Contents/Info.plist" <<PL
<?xml version="1.0" encoding="UTF-8"?><plist version="1.0"><dict>
<key>CFBundleIdentifier</key><string>local.coordinador-rf</string><key>CFBundleExecutable</key><string>prueba</string>
<key>CFBundleVersion</key><string>$2</string></dict></plist>
PL
}
echo "== sin certificado (ad hoc)"
mkapp "$W/adhoc.app" 1; env -u MAC_CERT_P12 -u MAC_CERT_PASSWORD bash mac/firmar.sh "$W/adhoc.app"
echo "== con certificado propio"
bash mac/crear-certificado.sh "$W/cert"
export MAC_CERT_P12="$(cat "$W/cert/certificado.p12.base64")" MAC_CERT_PASSWORD="$(cat "$W/cert/clave.txt")"
mkapp "$W/v1.app" 1; mkapp "$W/v2.app" 2
bash mac/firmar.sh "$W/v1.app" | tee "$W/o1"; bash mac/firmar.sh "$W/v2.app" | tee "$W/o2"
r1="$(grep '^Requisito' "$W/o1")"; r2="$(grep '^Requisito' "$W/o2")"
echo "$r1"; echo "$r2"
case "$r1" in *"certificate leaf"*) ;; *) echo "FALLO: el requisito no depende del certificado"; exit 1;; esac
[ "$r1" = "$r2" ] || { echo "FALLO: dos versiones no tienen la misma identidad"; exit 1; }
adhoc="$(codesign -dr - "$W/adhoc.app" 2>&1 | grep designated || true)"
case "$adhoc" in *cdhash*) echo "ad hoc: la identidad cambia con cada versión (esperado)";; *) echo "AVISO: requisito ad hoc: $adhoc";; esac
echo "firma: todo correcto"
