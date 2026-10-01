# Coordinador RF

Coordinación de frecuencias para micrófonos e in-ears inalámbricos (Shure y Sennheiser),
con monitor de canales y espectro en vivo. Se distribuye como app de escritorio autónoma
(Mac y Windows), sin instalar Python ni nada más.

| Archivo | Qué es |
|---|---|
| `coordinador-rf.html` | La app (un solo archivo) |
| `puente-rf.py` | Puente local en Python: sirve la app en `127.0.0.1:8765`, abre su ventana, lee el analizador USB (RF Explorer, tinySA) y habla con los receptores Shure (TCP 2202) y Sennheiser SSC (UDP 45) |
| `ad600/` | Motor de comunicación con el Shure AD600 (código de terceros, MIT, sin modificar: ver `ad600/README.md`) |
| `mac/icono.*` | Iconos |
| `.github/workflows/build.yml` | Compila `Coordinador RF.app` (Mac) y `Coordinador RF.exe` (Windows) con PyInstaller |

## Usar la app

Descarga el archivo de tu sistema desde la pestaña **Actions** → última compilación →
*Artifacts* (o desde *Releases* si hay una versión publicada con etiqueta `v*`).

- **Mac:** descomprime, arrastra `Coordinador RF.app` a Aplicaciones y ábrela con clic derecho → Abrir
  la primera vez (la app no está firmada por Apple). Si pregunta por conexiones de red, acepta:
  hace falta para los receptores Sennheiser.
- **Windows:** doble clic en `Coordinador RF.exe` (necesita Edge WebView2, incluido en Windows 11).

Los proyectos se guardan en la ventana de la app. Para pasar un proyecto de otra copia:
*Proyecto → Copiar proyecto* allí y *Pegar proyecto* aquí (las IPs de los receptores van dentro).
Registro de errores: `~/Library/Application Support/CoordinadorRF/CoordinadorRF.log` (Mac) o
`%APPDATA%\CoordinadorRF\CoordinadorRF.log` (Windows).

## Receptores de red y canales virtuales

Cada canal de la coordinación puede ser **virtual** (solo existe en el cálculo) o estar **enlazado** a un
canal de un receptor de red (columna *Receptor* de la tabla de frecuencias).

- **Importar a la coordinación** (Monitor de canales → Receptores, junto a cada receptor): crea un grupo con
  las frecuencias actuales del receptor, bloqueadas, y lo enlaza canal a canal. Reconoce el modelo (ULX-D, QLX-D,
  Axient Digital, EW-DX…) y su banda; si no, crea un grupo personalizado.
- **Asignar a receptores** (cabecera de cada grupo): enlaza los canales virtuales ya coordinados con canales
  libres de receptores compatibles (misma marca y, si se reconoce, mismo modelo). También se puede elegir a mano en la columna *Receptor*.
- **Enviar a receptores** (por grupo o para todos): programa en los receptores las frecuencias coordinadas,
  previa confirmación con la lista de cambios. Después hay que sincronizar los emisores.

## Analizador de espectro Shure AD600 (por red) — experimental

En **Espectro en vivo → Analizador → Shure AD600 (red)**. Se busca solo en la red; si no aparece, escribe su IP.
Cubre 174–2000 MHz, con resolución de 50, 100, 350 o 900 kHz y las antenas A–F (o todas a la vez). Los barridos
llegan al monitor, a la cascada y a *Guardar como escaneo* igual que los de un RF Explorer.

- Shure no publica este protocolo: se usa el motor de código abierto de
  [mbsound/AD600-Web-Based-Spectrum-Scan](https://github.com/mbsound/AD600-Web-Based-Spectrum-Scan) (ingeniería inversa de Wireless Workbench).
- **Cierra Wireless Workbench** antes de conectar: el AD600 solo admite un controlador de escaneo a la vez.
- Conectar tarda entre 20 y 30 s. Cambiar el rango, la resolución o las antenas reconecta otros 20-30 s.
- **Sal siempre con Desconectar o cerrando la app con normalidad**: la app libera la sesión de escaneo del equipo. Si se
  cierra a la fuerza, el AD600 puede quedarse con el escaneo ocupado hasta apagarlo y encenderlo.
- En el Mac, acepta el aviso de acceso a la red local la primera vez (hace falta para encontrar el equipo).
- No usar con un equipo que esté en uso en directo hasta haberlo probado antes.

## Desarrollo

```
pip install -r requirements.txt
python3 puente-rf.py --demo      # con receptores simulados; --ventana para abrirlo en su ventana
```

Para publicar una versión con enlace de descarga: crear una etiqueta `v1.x`; el workflow sube los archivos a *Releases*.
