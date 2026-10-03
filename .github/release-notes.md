**Mac con chip de Apple (M1, M2, M3…):** descarga `Coordinador-RF-Mac.zip`.
**Mac con procesador Intel:** descarga `Coordinador-RF-Mac-Intel.zip`.
Descomprime y arrastra `Coordinador RF.app` a Aplicaciones (sustituye la anterior; tus proyectos se conservan).
La primera vez, clic derecho → Abrir. Si pregunta por conexiones de red o por la carpeta Documentos, acepta:
hace falta para los receptores, el AD600 y para guardar los proyectos y los informes.

**Windows:** descarga `Coordinador.RF.exe` y haz doble clic.

**Novedades de la 1.11**
- **Monitor más legible:** en cada canal con receptor, RF en 10 puntos, calidad en 5 puntos y audio en 7 LED (RMS y pico);
  el valor exacto de audio aparece al pasar el ratón. Los niveles por antena van en su propia línea.

**Novedades de la 1.10**
- **Shure Axient Digital (AD4D/AD4Q):** el monitor ya interpreta sus medidores: RF por antena, audio RMS y pico, calidad 0-5, batería y emisor.
  Los niveles usan el desfase habitual (RSSI y audio − 120); contrástalos con la pantalla del equipo y avisa si difieren.
- El diagnóstico recoge más ejemplos de lo que responde cada receptor.

**Novedades de la 1.9**
- **Receptores Shure (probado con Axient Digital en mente):** los medidores (RF, audio) se piden también canal a canal y las órdenes iniciales se envían por separado.
  Si un receptor no manda niveles, el monitor lo avisa, y «Diagnóstico» recoge lo que responde el equipo.
- El aviso de versión nueva se comprueba cada 15 minutos y al volver a la ventana (antes, cada 6 horas).

**Novedades de la 1.8**
- **Intermodulación al pasar el ratón:** sobre un producto en la franja inferior de la gráfica, el cuadro indica qué portadoras lo producen y se resaltan.
- **Verificación tras enviar a receptores:** se vuelve a leer cada canal y la tabla marca «Verificado en el receptor» o «No confirmado»; los fallos suenan como alarma.
- **Capturar como escaneo** desde el analizador: barrido actual o máximo de 10 s a 2 min; se funde con el escaneo existente, activa «evitar» y propone el umbral.
- Firma de la app de Mac con certificado propio opcional, para que macOS conserve los permisos entre versiones.

**Novedades de la 1.7**
- **Red por la que buscar:** al buscar receptores puedes elegir la conexión (cable, Wi-Fi…) en vez de revisar todas.
- **Gráfica de coordinación con leyenda:** barra superior con la frecuencia de la vista, la del cursor, el canal de TV y la línea elegida.
- **Mover frecuencias con el ratón:** arrastra una línea de la gráfica; queda bloqueada y la intermodulación se recalcula al instante.

**Novedades de la 1.6**
- **Actualización con un clic en Mac:** cuando haya una versión nueva, el aviso de la ventana trae el botón *Actualizar ahora*.
  La app descarga la release, comprueba su huella SHA-256, se sustituye y se reabre; guarda la versión anterior y, si la nueva no
  arranca en unos minutos, vuelve sola a la anterior. Requiere que la app esté en Aplicaciones.
- Esta vez, si vienes de la 1.5 o anterior, hay que instalarla a mano; desde aquí en adelante, con un clic.

Novedades de la 1.5: varios proyectos con copia en disco, nombre por canal, informe imprimible/PDF, deshacer y rehacer, avisos del
monitor, receptores de red (importar, asignar y enviar), analizador Shure AD600 por red (experimental), diagnóstico, aviso de
versión nueva y versión para Mac Intel.
