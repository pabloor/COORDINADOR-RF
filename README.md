# Coordinador RF

Coordinación de frecuencias para micrófonos e in-ears inalámbricos (Shure y Sennheiser), con monitor de canales y
espectro en vivo. Se distribuye como aplicación de escritorio autónoma (Mac y Windows), sin instalar Python ni nada más.

## Descargar e instalar

En [Releases](https://github.com/pabloor/COORDINADOR-RF/releases/latest):

- **Mac con chip de Apple (M1, M2…):** `Coordinador-RF-Mac.zip`. **Mac Intel:** `Coordinador-RF-Mac-Intel.zip`.
  Descomprime, arrastra `Coordinador RF.app` a Aplicaciones (sustituye la anterior) y ábrela con clic derecho → Abrir
  la primera vez, porque la app no está firmada por Apple. Acepta los avisos de red local y de la carpeta Documentos.
- **Windows:** `Coordinador.RF.exe` (necesita Edge WebView2, incluido en Windows 11).

La app avisa dentro de la ventana cuando hay una versión nueva. No se actualiza sola: descarga la nueva y sustituye la anterior.
Los proyectos se conservan.

## Qué hace

- **Coordinación:** grupos de equipos con biblioteca Shure/Sennheiser (bandas y reglas de separación), canales de TV,
  exclusiones, escaneos CSV y cálculo de intermodulación (3.º, 5.º, 7.º/9.º y con 3 transmisores).
- **Nombre por canal**, deshacer/rehacer (⌘Z, ⇧⌘Z) y **informe imprimible o PDF** (botón *Informe*: se abre en el navegador del
  sistema; desde ahí, Imprimir → Guardar como PDF).
- **Varios proyectos** con nombre (selector de la cabecera). Se guardan en la app y, con el puente conectado, como archivos en
  `Documentos/Coordinador RF/Proyectos`, de donde se recuperan si se pierden los datos de la app. Informes y registros van a
  `Documentos/Coordinador RF/Informes` y `Registros`.
- **Monitor de canales:** estado de cada canal con los niveles del analizador y los datos de los receptores en red, con aviso
  sonoro y notificación del sistema ante alarmas, y registro exportable a CSV.
- **Receptores de red (Shure TCP 2202, Sennheiser SSC UDP 45):** *Importar a la coordinación* crea un grupo con sus frecuencias;
  *Asignar a receptores* enlaza canales virtuales con canales libres compatibles; *Enviar a receptores* programa las frecuencias.
- **Espectro en vivo:** RF Explorer, tinySA (USB), simulador y Shure AD600 (red).
- **Diagnóstico:** botón que reúne versión, estado y registros para explicar un fallo (no incluye la clave del puente).

## Shure AD600 (por red) — experimental

En **Espectro en vivo → Analizador → Shure AD600 (red)**. Se busca solo en la red; si no aparece, escribe su IP. Cubre 174–2000 MHz,
con resolución de 50, 100, 350 o 900 kHz y las antenas A–F (o todas a la vez).

- Shure no publica este protocolo: se usa el motor de código abierto de
  [mbsound/AD600-Web-Based-Spectrum-Scan](https://github.com/mbsound/AD600-Web-Based-Spectrum-Scan) (ingeniería inversa de Wireless Workbench),
  copiado sin modificar en `ad600/` con su licencia MIT.
- **Cierra Wireless Workbench** antes de conectar: el AD600 solo admite un controlador de escaneo a la vez.
- Conectar tarda entre 20 y 30 s; cambiar el rango, la resolución o las antenas reconecta otros 20-30 s.
- **Sal siempre con Desconectar o cerrando la app con normalidad**: así se libera la sesión de escaneo del equipo. Si se cierra a la
  fuerza, el AD600 puede quedarse con el escaneo ocupado hasta apagarlo y encenderlo.
- No usar con un equipo que esté en uso en directo hasta haberlo probado antes. Probado solo con un equipo simulado.

## Archivos

| Archivo | Qué es |
|---|---|
| `coordinador-rf.html` | La app (un solo archivo) |
| `puente-rf.py` | Puente local en Python: sirve la app en `127.0.0.1:8765`, abre su ventana, guarda proyectos e informes, lee el analizador USB y el AD600, habla con los receptores y avisa de versiones nuevas |
| `ad600/` | Motor de comunicación con el AD600 (código de terceros, MIT, sin modificar: ver `ad600/README.md`) |
| `tests/` | Pruebas de extremo a extremo (`python3 tests/e2e.py`) con puente real, receptores demo y AD600 simulado |
| `mac/icono.*` | Iconos |
| `.github/workflows/` | `tests.yml` (pruebas) y `build.yml` (compila `.app` para Mac Apple Silicon e Intel, y `.exe`) |

## Desarrollo

```
pip install -r requirements.txt playwright && python -m playwright install chromium
python3 puente-rf.py --demo      # con receptores simulados; --ventana para abrirlo en su ventana
python3 tests/e2e.py             # todas las pruebas (PW_CHROMIUM=/ruta/chromium si hace falta)
```

**Publicar una versión:** sube `VERSION` en `puente-rf.py` y, en *Actions → Compilar app → Run workflow*, escribe la etiqueta
(por ejemplo `v1.6`). La compilación crea la etiqueta, compila para Mac y Windows y publica la release con las notas de
`.github/release-notes.md` (actualízalas antes).
