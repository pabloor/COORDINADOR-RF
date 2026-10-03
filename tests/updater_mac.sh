#!/bin/bash
# Prueba de actualización completa sobre la app REAL de Mac (se ejecuta en un Mac de GitHub).
#   1) Una descarga con la huella manipulada se rechaza y no se toca nada.
#   2) Una descarga buena se instala: la app se cierra, se sustituye, se guarda la anterior y la nueva se abre y responde.
# Uso: tests/updater_mac.sh <zip de la app compilada>
set -u
ZIP="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
W="$(mktemp -d)"; PORT_APP=8799; PORT_SRV=8777
ASSET="Coordinador-RF-Mac.zip"; [ "$(uname -m)" = "x86_64" ] && ASSET="Coordinador-RF-Mac-Intel.zip"
APPDIR="$W/Aplicaciones"; APP="$APPDIR/Coordinador RF.app"; SERVE="$W/serve"; CFG="$HOME/Library/Application Support/CoordinadorRF"
mkdir -p "$APPDIR" "$SERVE"
SRV=""
cleanup() { pkill -f "$APPDIR/" 2>/dev/null; [ -n "$SRV" ] && kill "$SRV" 2>/dev/null; }
fail() {
  echo "FALLO: $*"
  echo "--- servidor local de la prueba ---"; curl -sS -m 5 "http://127.0.0.1:$PORT_SRV/good.json" 2>&1 | head -c 400; echo
  echo "--- python del sistema hacia ese servidor ---"
  python3 -c "import urllib.request as u;print(u.getproxies());print(u.urlopen('http://127.0.0.1:$PORT_SRV/good.json',timeout=5).read()[:60])" 2>&1 | tail -3
  echo "--- proxies del sistema ---"; env | grep -i proxy; scutil --proxy 2>&1 | head -12
  echo "--- diagnóstico del puente ---"; [ -n "${K:-}" ] && curl -s -m 10 "http://127.0.0.1:$PORT_APP/diagnostics?k=$K" | head -c 3000
  echo "--- salida de la app ---"; tail -30 "$W/app.log" 2>/dev/null
  echo "--- CoordinadorRF.log ---"; tail -40 "$CFG/CoordinadorRF.log" 2>/dev/null
  echo "--- actualizacion.log ---"; cat "$CFG/actualizacion.log" 2>/dev/null; cleanup; exit 1
}
ok() { echo "  ok    $*"; }

ditto -x -k "$ZIP" "$APPDIR" || fail "no se pudo descomprimir la app"
[ -d "$APP" ] || fail "no está Coordinador RF.app en el zip"

# La «versión nueva»: la misma app con una marca, vuelta a firmar y comprimida como lo haría la release.
mkdir -p "$W/nueva" && ditto "$APP" "$W/nueva/Coordinador RF.app"
echo marca > "$W/nueva/Coordinador RF.app/Contents/Resources/MARCA"
codesign --force --deep -s - "$W/nueva/Coordinador RF.app" || fail "no se pudo firmar la versión de prueba"
ditto -c -k --keepParent "$W/nueva/Coordinador RF.app" "$SERVE/$ASSET"
SHA="$(shasum -a 256 "$SERVE/$ASSET" | cut -d' ' -f1)"; SIZE="$(stat -f %z "$SERVE/$ASSET")"
mkjson() { cat > "$SERVE/$1" <<JSON
{"tag_name":"v99.0","html_url":"https://github.com/pabloor/COORDINADOR-RF/releases/tag/v99.0","body":"","assets":[{"name":"$ASSET","browser_download_url":"http://127.0.0.1:$PORT_SRV/$ASSET","digest":"sha256:$2","size":$SIZE}]}
JSON
}
mkjson bad.json "0000000000000000000000000000000000000000000000000000000000000000"; mkjson good.json "$SHA"
python3 "$(dirname "$0")/serve_dir.py" "$SERVE" "$PORT_SRV" >"$W/srv.log" 2>&1 & SRV=$!
for _ in $(seq 1 60); do curl -s -o /dev/null -m 1 "http://127.0.0.1:$PORT_SRV/good.json" && break; sleep 0.5; done
curl -s -o /dev/null -m 2 "http://127.0.0.1:$PORT_SRV/good.json" || { cat "$W/srv.log"; fail "el servidor local de la prueba no arranca"; }

start_app() {
  CRF_UPDATE_API="http://127.0.0.1:$PORT_SRV/$1" "$APP/Contents/MacOS/Coordinador RF" --sin-navegador --puerto "$PORT_APP" > "$W/app.log" 2>&1 &
  APID=$!
  for _ in $(seq 1 60); do curl -s -o /dev/null -m 1 "http://127.0.0.1:$PORT_APP/" && return 0; sleep 1; done
  fail "la app no arranca"
}
getkey() { curl -s "http://127.0.0.1:$PORT_APP/" | sed -n 's/.*name="puente-rf" content="\([^"]*\)".*/\1/p' | head -1; }
U="http://127.0.0.1:$PORT_APP"

echo "1) descarga manipulada"
start_app bad.json; K="$(getkey)"; [ -n "$K" ] || fail "no se pudo leer la clave del puente"
INFO="$(curl -s -m 15 "$U/update?k=$K")"
echo "$INFO" | grep -q '"canInstall": true' || fail "el puente no ofrece actualizar: $INFO"; ok "el puente ofrece actualizar a una app instalada"
curl -s -m 10 -X POST -H 'Content-Type: application/json' -d '{}' "$U/update/install?k=$K" | grep -q '"ok": true' || fail "no acepta la petición de instalar"
ST=""; for _ in $(seq 1 60); do ST="$(curl -s -m 5 "$U/update/status?k=$K")"; echo "$ST" | grep -q '"state": "error"' && break; sleep 1; done
echo "$ST" | grep -q 'SHA-256' || fail "no ha rechazado la descarga manipulada: $ST"; ok "se rechaza la descarga con la huella falsa"
[ ! -e "$APP/Contents/Resources/MARCA" ] || fail "se instaló algo manipulado"
kill -0 "$APID" 2>/dev/null || fail "la app se cerró tras un intento fallido"; ok "la app sigue funcionando y sin cambios"
kill "$APID"; wait "$APID" 2>/dev/null; sleep 2

echo "2) actualización buena"
start_app good.json; K="$(getkey)"
curl -s -m 10 -X POST -H 'Content-Type: application/json' -d '{}' "$U/update/install?k=$K" | grep -q '"ok": true' || fail "no acepta la petición de instalar"
DONE=0; for _ in $(seq 1 150); do
  if [ -e "$APP/Contents/Resources/MARCA" ] && curl -s -o /dev/null -m 1 "$U/"; then DONE=1; break; fi; sleep 1
done
[ "$DONE" = 1 ] || fail "la versión nueva no está instalada y respondiendo"; ok "la versión nueva está instalada y responde"
kill -0 "$APID" 2>/dev/null && fail "la app antigua sigue en marcha"; ok "la app antigua se cerró"
BAK="$CFG/versiones-anteriores/previous.app"
{ [ -d "$BAK" ] && [ ! -e "$BAK/Contents/Resources/MARCA" ]; } || fail "no se guardó la versión anterior"; ok "la versión anterior queda guardada"
grep -q "actualización terminada" "$CFG/actualizacion.log" || fail "el registro no dice que terminó"; ok "el registro de la actualización está completo"
[ ! -e "$APPDIR/.Coordinador RF.actualizando" ] || fail "quedó la carpeta temporal"; ok "no queda la carpeta temporal"
codesign --verify --deep --strict "$APP" || fail "la app instalada tiene la firma rota"; ok "la app instalada tiene la firma íntegra"
cleanup
echo "Actualización completa verificada."
