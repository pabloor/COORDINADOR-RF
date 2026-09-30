# Coordinador RF

Coordinación de frecuencias para micrófonos e in-ears inalámbricos (Shure y Sennheiser),
con monitor de canales y espectro en vivo. Se usa como aplicación de escritorio.

| Archivo | Qué es |
|---|---|
| `coordinador-rf.html` | La app (un solo archivo) |
| `puente-rf.py` | Puente local en Python: sirve la app en `127.0.0.1:8765`, lee el analizador USB (RF Explorer, tinySA) y habla con los receptores Shure (TCP 2202) y Sennheiser SSC (UDP 45) |
| `mac/` | Lanzador, `Info.plist` e iconos del `Coordinador RF.app` |
| `windows/` | Lanzador `.bat` |
| `build.sh` | Monta `dist/CoordinadorRF.zip` (Mac + Windows) |

Desarrollo: `python3 puente-rf.py --demo` (receptores simulados). Distribución: `./build.sh`.
Instrucciones para el usuario final en `LEEME.txt`.
