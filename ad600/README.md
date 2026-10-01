# Motor de comunicación con el Shure AD600

Código de terceros, **sin modificar**, copiado de
[mbsound/AD600-Web-Based-Spectrum-Scan](https://github.com/mbsound/AD600-Web-Based-Spectrum-Scan)
(carpeta `engine/`, commit `b0a3cd86d0cf5915a7393ba3f2f8b912c1d70047`, 23-sep-2026). Licencia MIT, copyright (c) 2026 mbsound: ver `LICENSE`.

Shure no publica el protocolo de red del AD600; este motor lo implementa por ingeniería inversa de lo que
hace Wireless Workbench (ANSI E1.17 / ACN SDT+DMP sobre UDP 57383). Se usa desde `puente-rf.py` (clase `AD600`).

No se incluyen `server.js` ni su interfaz web. `ad600_bridge.py` se usa solo para ensamblar los barridos;
su servidor HTTP no se arranca.
