# Coordinador RF

Aplicación web (un solo archivo, `index.html`) para coordinar frecuencias de
micrófonos e in-ears inalámbricos:

- **Coordinación**: grupos de equipos (biblioteca Shure / Sennheiser con bandas y
  reglas de separación), canales de TV ocupados, exclusiones, escaneos y cálculo
  de intermodulación (3º, 5º, 7º/9º orden y con 3 transmisores).
- **Monitor de canales**: estado de cada canal y receptores Shure/Sennheiser en red
  a través del puente local.
- **Espectro en vivo**: RF Explorer, tinySA o simulador, con cascada.

Abre `index.html` en el navegador. Para USB y receptores en red hace falta el
puente local (`http://127.0.0.1:8765`), que todavía no está en este repositorio.
