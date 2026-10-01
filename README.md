# Coordinador RF

Coordinación de frecuencias para micrófonos e in-ears inalámbricos (Shure y Sennheiser),
con monitor de canales y espectro en vivo. Se distribuye como app de escritorio autónoma
(Mac y Windows), sin instalar Python ni nada más.

| Archivo | Qué es |
|---|---|
| `coordinador-rf.html` | La app (un solo archivo) |
| `puente-rf.py` | Puente local en Python: sirve la app en `127.0.0.1:8765`, abre su ventana, lee el analizador USB (RF Explorer, tinySA) y habla con los receptores Shure (TCP 2202) y Sennheiser SSC (UDP 45) |
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

## Desarrollo

```
pip install -r requirements.txt
python3 puente-rf.py --demo      # con receptores simulados; --ventana para abrirlo en su ventana
```

Para publicar una versión con enlace de descarga: crear una etiqueta `v1.x`; el workflow sube los archivos a *Releases*.
