# Coordinador RF

Coordinación de frecuencias para micrófonos e in-ears inalámbricos (Shure y Sennheiser), con monitor de canales y
espectro en vivo. Se distribuye como aplicación de escritorio autónoma (Mac y Windows), sin instalar Python ni nada más.

## Descargar e instalar

En [Releases](https://github.com/pabloor/COORDINADOR-RF/releases/latest):

- **Mac con chip de Apple (M1, M2…):** `Coordinador-RF-Mac.zip`. **Mac Intel:** `Coordinador-RF-Mac-Intel.zip`.
  Descomprime, arrastra `Coordinador RF.app` a Aplicaciones (sustituye la anterior) y ábrela con clic derecho → Abrir
  la primera vez, porque la app no está firmada por Apple. Acepta los avisos de red local y de la carpeta Documentos.
- **Windows:** `Coordinador.RF.exe` (necesita Edge WebView2, incluido en Windows 11).

## Actualizar

La app avisa dentro de la ventana cuando hay una versión nueva. En el **Mac** basta con pulsar **Actualizar ahora**: la app descarga la
release, comprueba su huella SHA-256 (la que publica GitHub), se cierra liberando antes el AD600 y los receptores, se sustituye por la
nueva y se vuelve a abrir. Tus proyectos se conservan, y la versión anterior queda guardada en
`~/Library/Application Support/CoordinadorRF/versiones-anteriores/`. Si la nueva no responde en 150 s (la primera vez puede tardar), vuelve sola a la anterior
(registro en `actualizacion.log`, en esa misma carpeta). Solo actúa cuando pulsas el botón: no la uses en mitad de un evento.

Condiciones: la app tiene que estar en Aplicaciones (si se abre desde Descargas, macOS la ejecuta en una zona de solo lectura y la app lo
avisa) y la versión que tengas ya debe llevar esta función (de la 1.5 o anteriores se pasa a mano esta vez). Al no estar firmada por
Apple, macOS puede volver a pedir los permisos de red local y de Documentos tras actualizar, salvo que las versiones se firmen con un
certificado propio (ver *Firma estable* más abajo). En Windows el aviso lleva a la descarga.

## Qué hace

- **Coordinación:** grupos de equipos con biblioteca Shure/Sennheiser (bandas y reglas de separación), canales de TV,
  exclusiones, escaneos CSV y cálculo de intermodulación (3.º, 5.º, 7.º/9.º y con 3 transmisores).
- **Gráfica de coordinación:** la barra superior indica la frecuencia de la vista, la del cursor, el canal de TV y la línea
  seleccionada. Pasando el ratón por la franja inferior (donde se dibujan los productos de intermodulación) un cuadro indica qué
  portadoras producen cada producto y se resaltan en la gráfica. Las líneas de las portadoras se **arrastran con el ratón** (pasos de 25 kHz; con ⇧ de 5 kHz): la frecuencia queda
  bloqueada, la intermodulación se recalcula al momento y *deshacer* devuelve la línea de una vez. Arrastrar el fondo desplaza la vista.
- **Nombre por canal**, deshacer/rehacer (⌘Z, ⇧⌘Z) y **informe imprimible o PDF** (menú *Proyecto → Informe imprimible / PDF*: se abre en el navegador del
  sistema; desde ahí, Imprimir → Guardar como PDF).
- **Varios proyectos** con nombre. En la app de Mac todo lo de proyectos está en la barra de menús, en **Proyecto** (cambiar, nuevo, duplicar,
  renombrar, borrar, vaciar, abrir la carpeta, cargar archivo, pegar, copiar lista CSV y copiar proyecto) y el nombre del proyecto actual va en
  el título de la ventana; en el navegador siguen el selector de la cabecera y la sección Proyecto del panel. Se guardan en la app y, con el puente conectado, como archivos en
  `Documentos/Coordinador RF/Proyectos`, de donde se recuperan si se pierden los datos de la app. Informes y registros van a
  `Documentos/Coordinador RF/Informes` y `Registros`.
- **Monitor de canales:** el botón del emisor pasa solo a «Tx encendido» cuando el receptor tiene un emisor sincronizado. Los avisos
  (sonido y notificación del sistema), probar avisos y el registro están en el menú **Monitor** de la barra de menús del Mac (en el navegador, en
  *Espectro en vivo*); el umbral sigue en *Espectro en vivo* y el botón *Receptores* en *Coordinación*. Estado de cada canal con los niveles del analizador y los datos de los receptores en red, con aviso
  sonoro y notificación del sistema ante alarmas, y registro exportable a CSV.
- **Receptores de red (Shure TCP 2202, Sennheiser SSC UDP 45):** el selector *Red por la que buscar* limita la búsqueda a una
  conexión del ordenador (cable, Wi-Fi…) en vez de revisarlas todas. *Importar a la coordinación* crea un grupo con sus frecuencias;
  *Asignar a receptores* enlaza canales virtuales con canales libres compatibles; *Enviar a receptores* programa las frecuencias y después
  vuelve a leer cada canal: la tabla marca *Verificado en el receptor* o *No confirmado* (con la frecuencia real), y los fallos entran en el registro de alarmas.
- **Espectro en vivo:** RF Explorer, tinySA (USB), simulador y Shure AD600 (red). *Capturar como escaneo* guarda lo que ve el analizador
  (el barrido actual o el máximo de 10 s a 2 min) como escaneo del sitio: se funde con el que hubiera (solo sustituye la parte
  capturada), activa «evitar» y propone un umbral por encima del ruido. La coordinación evita desde ese momento lo que lo supere.
- **Diagnóstico:** botón que reúne versión, estado y registros para explicar un fallo (no incluye la clave del puente).

## Firma estable (Mac, opcional)

Sin cuenta de desarrollador de Apple, cada versión se firma «ad hoc» y macOS la ve como una app distinta: puede volver a pedir permisos tras
actualizar. Para evitarlo, las versiones se pueden firmar con un certificado propio gratuito, el mismo siempre:

1. `bash mac/crear-certificado.sh carpeta` (crea `certificado.p12.base64` y `clave.txt`).
2. En GitHub → *Settings → Secrets and variables → Actions* crea `MAC_CERT_P12` (contenido de `certificado.p12.base64`) y
   `MAC_CERT_PASSWORD` (contenido de `clave.txt`).
3. Las siguientes compilaciones usan `mac/firmar.sh` con ese certificado; `tests/firma_mac.sh` comprueba en CI que dos versiones distintas quedan con la misma identidad.

La primera vez que se pase de la firma ad hoc a la del certificado, macOS pedirá los permisos una vez más; desde ahí se conservan.

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
| `mac/firmar.sh`, `mac/crear-certificado.sh` | Firma de la app de Mac con certificado propio (opcional) |
| `.github/workflows/` | `tests.yml` (pruebas) y `build.yml` (compila `.app` para Mac Apple Silicon e Intel y `.exe`, y prueba la actualización completa sobre la app real con `tests/updater_mac.sh`) |

## Desarrollo

```
pip install -r requirements.txt playwright && python -m playwright install chromium
python3 puente-rf.py --demo      # con receptores simulados; --ventana para abrirlo en su ventana
python3 tests/e2e.py             # todas las pruebas (PW_CHROMIUM=/ruta/chromium si hace falta)
python3 tests/rendimiento.py 3000 8   # coste del espectro en vivo (puntos y segundos); con más de 3000 puntos simula un AD600
```

**Publicar una versión:** sube `VERSION` en `puente-rf.py` (la actualización compara versiones con ese número) y, en *Actions → Compilar app → Run workflow*, escribe la etiqueta
(por ejemplo `v1.6`). La compilación crea la etiqueta, compila para Mac y Windows y publica la release con las notas de
`.github/release-notes.md` (actualízalas antes).
