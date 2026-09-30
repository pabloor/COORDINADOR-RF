#!/bin/bash
# Monta dist/CoordinadorRF.zip (Mac + Windows) a partir de los archivos del repositorio.
set -e
cd "$(dirname "$0")"
OUT=dist/CoordinadorRF
rm -rf dist && mkdir -p "$OUT/Windows" "$OUT/Coordinador RF.app/Contents/"{MacOS,Resources}
cp mac/Info.plist "$OUT/Coordinador RF.app/Contents/"
cp mac/CoordinadorRF "$OUT/Coordinador RF.app/Contents/MacOS/" && chmod +x "$OUT/Coordinador RF.app/Contents/MacOS/CoordinadorRF"
cp mac/icono.png mac/icono.icns puente-rf.py coordinador-rf.html "$OUT/Coordinador RF.app/Contents/Resources/"
cp puente-rf.py coordinador-rf.html "windows/Iniciar Coordinador RF.bat" "$OUT/Windows/"
cp LEEME.txt "$OUT/"
(cd dist && zip -qr CoordinadorRF.zip CoordinadorRF)
echo "Listo: dist/CoordinadorRF.zip"
