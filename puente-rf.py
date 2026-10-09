#!/usr/bin/env python3
"""Puente de telemetría para Coordinador RF.

El navegador no puede abrir conexiones TCP/UDP con los receptores, así que este programa lo hace por él
y sirve los datos a la app en http://127.0.0.1:8765 (solo accesible desde este ordenador).

Protocolos:
  shure  Shure "Command Strings" por TCP, puerto 2202 (ULX-D, QLX-D y familia).
         Telemetría con < SET 0 METER_RATE > y mensajes < SAMPLE x ALL nn aaa eee >:
         RF = aaa - 128 dBm, audio = eee - 50 dBFS (guía de comandos de ULX-D).
  ssc    Sennheiser Sound Control (SSC v1): JSON por UDP, puerto 45 (EW-DX EM 2 / EM 4).
         Suscripciones a /m/rxN (rssi, rsqi, divi, af), /rxN y /mates/txN (batería, avisos).

Además sirve la propia app: abre http://127.0.0.1:8765 en cualquier navegador (también Safari).
Y lee el analizador de espectro por USB (RF Explorer o tinySA), así que no depende del navegador.
Para el analizador hace falta el módulo pyserial:  pip3 install pyserial

Uso:
  python3 puente-rf.py                 # arranca el puente
  python3 puente-rf.py --demo          # además crea dos receptores simulados para probar
  python3 puente-rf.py --puerto 9000   # otro puerto local
  python3 puente-rf.py --sin-navegador # no abrir el navegador al arrancar
  python3 puente-rf.py --auto-cerrar 90  # apagarse 90 s después de cerrar la app en el navegador
  python3 puente-rf.py --ventana       # abrir la app en su propia ventana (necesita: pip3 install pywebview)

Seguridad: el puente solo escucha en 127.0.0.1 y exige una clave (se crea la primera vez y se guarda en
la carpeta de configuración del usuario: en Mac, ~/Library/Application Support/CoordinadorRF). Al abrir la app
desde el propio puente se rellena sola. Sin clave nadie puede leer ni cambiar nada.
Solo biblioteca estándar de Python 3.8+.
"""
import argparse, errno, ipaddress, json, shutil, os, queue, random, re, secrets, signal, socket, sys, threading, time, urllib.request, webbrowser
import subprocess, ssl, unicodedata, hashlib, platform, plistlib, base64
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

VERSION = "1.25"
try:
    import serial  # pyserial: solo hace falta para el analizador
    from serial.tools import list_ports
except ImportError:
    serial = None
PORT = 8765
LOCK = threading.RLock()
DEVICES = {}  # id -> Driver
FROZEN = getattr(sys, "frozen", False)  # empaquetado como app (PyInstaller)
HERE = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))


def now():
    return time.time()


# ---------------------------------------------------------------------------------------------
# Drivers
# ---------------------------------------------------------------------------------------------
class Driver(threading.Thread):
    default_port = 0

    def __init__(self, cfg):
        super().__init__(daemon=True)
        self.cfg = cfg
        self.id = cfg["id"]
        self.host = cfg["host"]
        self.port = int(cfg.get("port") or self.default_port)
        self.halt = threading.Event()
        self.online = False
        self.error = "Conectando…"
        self.model = ""
        self.ch = {}  # "1" -> {freq, name, rf, af, ant, batt, bars, battMin, tx, mute, txMute, interf, warnings, t}
        self.last_rx = 0.0
        self.counts = {}          # tipo de mensaje -> cuántos han llegado (para el diagnóstico)
        self.seen = []            # primeros mensajes de cada tipo y últimos recibidos, tal cual (para el diagnóstico)
        self.t_connect = 0.0

    def note(self, kind, raw):
        """Anota un mensaje recibido: contador por tipo y una muestra legible (los repetidos no llenan el registro)."""
        n = self.counts[kind] = self.counts.get(kind, 0) + 1
        if n <= 2:
            self.seen.append(f"{kind}: {raw[:200]}")
            del self.seen[:-150]

    def chan(self, n):
        return self.ch.setdefault(str(int(n)), {})

    def snapshot(self):
        with LOCK:
            return {"id": self.id, "kind": self.cfg["kind"], "host": self.host, "port": self.port,
                    "name": self.cfg.get("name", ""), "online": self.online, "error": self.error,
                    "model": self.model, "channels": {k: dict(v) for k, v in self.ch.items()},
                    "samples": self.counts.get("SAMPLE", 0), "up": round(now() - self.t_connect) if self.online and self.t_connect else 0}

    def stop(self):
        self.halt.set()
        self.close()

    def close(self):
        pass


class ShureDriver(Driver):
    default_port = 2202
    ANT = {"AX": "A", "XB": "B", "AB": "AB", "XX": "-"}

    def __init__(self, cfg):
        super().__init__(cfg)
        self.sock = None

    def close(self):
        try:
            if self.sock:
                self.sock.close()
        except OSError:
            pass

    def send(self, text):
        if self.sock:
            self.sock.sendall(text.encode("ascii"))

    def set_frequency(self, ch, khz):
        self.send(f"< SET {int(ch)} FREQUENCY {int(khz):06d} >")

    def arm_meters(self):
        """Pide la telemetría (SAMPLE): para todo el equipo y, si hace falta, canal a canal (algunos modelos, como los Axient, solo
        la envían así). Un equipo que no la admita responde con un error, que queda anotado en el diagnóstico."""
        self.send("< SET 0 METER_RATE 00500 >")
        if not self.counts.get("SAMPLE"):
            with LOCK:
                chans = sorted(k for k in self.ch if k.isdigit() and k != "0")
            for n in chans[:8]:
                time.sleep(0.15)
                self.send(f"< SET {n} METER_RATE 00500 >")

    def run(self):
        while not self.halt.is_set():
            try:
                self.sock = socket.create_connection((self.host, self.port), timeout=5)
                self.sock.settimeout(1.0)
                with LOCK:
                    self.online, self.error = True, ""
                self.t_connect = now()
                self.counts, self.seen = {}, []
                # Cada orden por separado y con una pausa: algunos equipos solo atienden bien la primera de un paquete.
                for cmd in ("< GET MODEL >", "< GET 0 ALL >"):
                    self.send(cmd)
                    time.sleep(0.15)
                buf, last_poll, self.last_rx, armed = "", now(), now(), False
                while not self.halt.is_set():
                    try:
                        data = self.sock.recv(4096)
                    except socket.timeout:
                        data = None
                    if data is not None:
                        if not data:
                            raise ConnectionError("el receptor ha cerrado la conexión")
                        buf += data.decode("latin-1")
                        self.last_rx = now()
                        buf = self.consume(buf)
                    if not armed and now() - self.t_connect > 1.5:  # ya han llegado los canales: se pide la telemetría
                        self.arm_meters()
                        armed = True
                    if now() - last_poll > 30:  # refresco periódico (y comprobación de vida); se vuelve a pedir la telemetría
                        self.send("< GET 0 ALL >")
                        time.sleep(0.15)
                        self.arm_meters()
                        last_poll = now()
                    if now() - self.last_rx > 15:
                        raise ConnectionError("el receptor no responde")
            except (OSError, ConnectionError) as e:
                with LOCK:
                    self.online = False
                    self.error = str(e) or e.__class__.__name__
            self.close()
            self.sock = None
            self.halt.wait(3)

    def consume(self, buf):
        """Extrae mensajes "< ... >". Los nombres van entre llaves y pueden contener < o >."""
        while True:
            a = buf.find("<")
            if a < 0:
                return ""
            b = a + 1
            while True:
                b = buf.find(">", b)
                if b < 0:
                    return buf[a:] if len(buf) - a < 4096 else ""
                seg = buf[a + 1:b]
                if seg.count("{") <= seg.count("}"):
                    break
                b += 1
            self.handle(seg.strip())
            buf = buf[b + 1:]

    def handle(self, msg):
        t = msg.split()
        if not t:
            return
        with LOCK:
            kind = "SAMPLE" if t[0] == "SAMPLE" else f"{t[0]} {t[2]}" if len(t) > 2 and t[1].isdigit() else " ".join(t[:2])
            self.note(kind, msg)
            if t[0] == "REP":
                m = re.match(r"REP\s+(\d+)\s+(\w+)\s*(.*)$", msg, re.S)
                if m:
                    if m.group(1) != "0":
                        self.rep(self.chan(m.group(1)), m.group(2), m.group(3).strip())
                    return
                m = re.match(r"REP\s+(\w+)\s*(.*)$", msg, re.S)
                if m and m.group(1) == "MODEL":
                    self.model = m.group(2).strip().strip("{}").strip()
            elif t[0] == "SAMPLE" and len(t) >= 3 and t[1].isdigit() and t[1] != "0":
                c = self.chan(t[1])
                c["t"] = now()
                if (t[2] == "ALL" and len(t) == 12 and t[7].isalpha() and len(t[7]) == 2 and
                        all(t[i].isdigit() for i in (3, 4, 5, 6, 8, 9, 10, 11))):
                    # Axient Digital (AD4D/AD4Q): SAMPLE n ALL calidad mapaAudio picoAudio rmsAudio antenas mapaA rssiA mapaB rssiB
                    # RSSI y audio van con desfase de 120 (dBm y dBFS); calidad 0-5 (255 = sin dato).
                    q, pk, rms, ra, rb = int(t[3]), int(t[5]), int(t[6]), int(t[9]), int(t[11])
                    c["qual"] = q if q <= 5 else None
                    c["af"], c["afPeak"] = rms - 120, pk - 120
                    c["rfA"], c["rfB"], c["rf"] = ra - 120, rb - 120, max(ra, rb) - 120
                    c.pop("raw", None)
                elif t[2] == "ALL" and len(t) == 6 and len(t[3]) == 2 and t[3].isalpha() and t[4].isdigit() and t[5].isdigit():
                    c["ant"], c["rf"], c["af"] = self.ANT.get(t[3], t[3]), int(t[4]) - 128, int(t[5]) - 50
                    c.pop("raw", None)
                else:
                    c["raw"] = msg  # formato de otro modelo: se muestra tal cual

    @staticmethod
    def rep(c, key, v):
        def num():
            try:
                return int(v)
            except ValueError:
                return None
        n = num()
        if key == "FREQUENCY" and n:
            c["freq"] = n
        elif key == "CHAN_NAME":
            c["name"] = v.strip().strip("{}").strip()
        elif key == "BATT_CHARGE":
            c["batt"] = None if n in (None, 255) else n
        elif key == "BATT_BARS":
            c["bars"] = None if n in (None, 255) else n
        elif key == "BATT_RUN_TIME":
            c["battMin"] = n if n is not None and n < 65533 else None
        elif key == "TX_BATT_CHARGE_PERCENT":      # Axient Digital
            c["batt"] = None if n is None or n > 100 else n
        elif key == "TX_BATT_BARS":
            c["bars"] = None if n in (None, 255) else n
        elif key == "TX_BATT_MINS":
            c["battMin"] = n if n is not None and n < 65533 else None
        elif key == "TX_MODEL":
            c["tx"] = None if v in ("UNKN", "UNKNOWN", "") else v
        elif key == "TX_TYPE":
            c["tx"] = None if v == "UNKN" else v
        elif key == "AUDIO_MUTE":
            c["mute"] = v == "ON"
        elif key == "TX_MUTE_STATUS":
            c["txMute"] = None if v == "UNKN" else v == "ON"
        elif key == "RF_INT_DET":
            c["interf"] = v == "CRITICAL"
        elif key == "ENCRYPTION_WARNING":
            c["encWarn"] = v == "ON"
        elif key == "RX_RF_LVL" and n is not None:
            c["rf"] = n - 128
        elif key == "AUDIO_LVL" and n is not None:
            c["af"] = n - 50
        elif key == "RF_ANTENNA":
            c["ant"] = ShureDriver.ANT.get(v, v)


class SSCDriver(Driver):
    default_port = 45
    DIV = {0: "-", 1: "A", 2: "B"}

    def __init__(self, cfg):
        super().__init__(cfg)
        self.sock = None

    def close(self):
        try:
            if self.sock:
                self.sock.close()
        except OSError:
            pass

    def send(self, obj):
        self.sock.sendto(json.dumps(obj, separators=(",", ":")).encode(), (self.host, self.port))

    def set_frequency(self, ch, khz):
        self.send({f"rx{int(ch)}": {"frequency": int(khz)}})

    def subscribe(self):
        p = {"min": 1000, "max": 250, "count": 100000, "lifetime": 30}
        self.send({"device": {"identity": {"product": None}, "name": None}})
        for n in range(1, 5):  # EM 2: rx1-rx2; EM 4: rx1-rx4. Las que no existen devuelven error y se ignoran.
            rx, tx = f"rx{n}", f"tx{n}"
            for tree in ({"m": {rx: {"rssi": None, "rsqi": None, "divi": None, "af": None}}},
                         {rx: {"frequency": None, "name": None, "mute": None, "warnings": None}},
                         {"mates": {tx: {"battery": {"gauge": None, "lifetime": None}, "warnings": None,
                                         "type": None, "mute": None}}}):
                self.send({"osc": {"state": {"subscribe": [dict({"#": p}, **tree)]}}})

    def run(self):
        last_sub = 0.0
        while not self.halt.is_set():
            try:
                if self.sock is None:
                    self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    self.sock.settimeout(1.0)
                if now() - last_sub > 10:  # las suscripciones caducan: se renuevan
                    self.subscribe()
                    last_sub = now()
                try:
                    data, _ = self.sock.recvfrom(65535)
                except socket.timeout:
                    data = None
                if data:
                    self.last_rx = now()
                    self.handle(data)
                with LOCK:
                    self.online = now() - self.last_rx < 12
                    if self.online:
                        self.error = ""
                    elif not self.error or self.error == "Conectando…":
                        self.error = "Sin respuesta: revisa la IP y que el acceso SSC esté activado en el receptor"
            except OSError as e:
                with LOCK:
                    self.online, self.error = False, str(e)
                self.close()
                self.sock = None
                self.halt.wait(3)

    def handle(self, data):
        try:
            obj = json.loads(data.decode("utf-8", "replace"))
        except ValueError:
            return  # algunos errores de SSC llegan con JSON mal formado
        if not isinstance(obj, dict):
            return
        with LOCK:
            dev = obj.get("device")
            if isinstance(dev, dict):
                prod = (dev.get("identity") or {}).get("product")
                if prod:
                    self.model = prod
            for key, val in (obj.get("m") or {}).items():
                if key.startswith("rx") and isinstance(val, dict):
                    c = self.chan(key[2:])
                    c["t"] = now()
                    if isinstance(val.get("rssi"), (int, float)):
                        c["rf"] = round(val["rssi"], 1)
                    if isinstance(val.get("rsqi"), (int, float)):
                        c["rfq"] = val["rsqi"]
                    if val.get("divi") in self.DIV:
                        c["ant"] = self.DIV[val["divi"]]
                    if isinstance(val.get("af"), (int, float)):
                        c["af"] = round(val["af"], 1)
            for key, val in obj.items():
                if re.fullmatch(r"rx\d", key) and isinstance(val, dict):
                    c = self.chan(key[2:])
                    if isinstance(val.get("frequency"), int):
                        c["freq"] = val["frequency"]
                    if isinstance(val.get("name"), str):
                        c["name"] = val["name"].strip()
                    if isinstance(val.get("mute"), bool):
                        c["mute"] = val["mute"]
                    if isinstance(val.get("warnings"), list):
                        c["rxWarn"] = val["warnings"]
            for key, val in (obj.get("mates") or {}).items():
                if re.fullmatch(r"tx\d", key) and isinstance(val, dict):
                    c = self.chan(key[2:])
                    bat = val.get("battery") or {}
                    if isinstance(bat.get("gauge"), int):
                        c["batt"] = bat["gauge"]
                    if isinstance(bat.get("lifetime"), int):
                        c["battMin"] = bat["lifetime"]
                    if isinstance(val.get("warnings"), list):
                        c["txWarn"] = val["warnings"]
                    if isinstance(val.get("type"), str):
                        c["tx"] = val["type"]
                    if isinstance(val.get("mute"), bool):
                        c["txMute"] = val["mute"]


KINDS = {"shure": ShureDriver, "ssc": SSCDriver}


# ---------------------------------------------------------------------------------------------
# Analizadores de espectro por USB (necesitan pyserial)
# ---------------------------------------------------------------------------------------------
ANALYZER = None
AN_SUBS = []  # colas de los navegadores conectados a /analyzer/events
AN_STATE = {"info": "Sin analizador.", "error": ""}


def an_publish(ev):
    with LOCK:
        subs = list(AN_SUBS)
        if ev.get("type") == "status":
            AN_STATE.update(info=ev.get("info", ""), error=ev.get("error", ""))
    for q in subs:
        try:
            q.put_nowait(ev)
        except queue.Full:
            pass  # navegador lento: se descarta ese barrido


def list_serial():
    if serial is None:
        return None
    out = []
    for p in list_ports.comports():
        hint = ""
        if p.vid == 0x10C4:
            hint = "rfe"   # puente USB Silicon Labs CP210x del RF Explorer
        elif p.vid == 0x0483 and p.pid == 0x5740:
            hint = "tinysa"
        out.append({"device": p.device, "desc": p.description or "", "hint": hint})
    return out


class Analyzer(threading.Thread):
    def __init__(self, cfg):
        super().__init__(daemon=True)
        self.cfg = dict(cfg)
        self.halt = threading.Event()
        self.cmdq = queue.Queue()
        self.ser = None

    def status(self, info=None, error=""):
        an_publish({"type": "status", "info": info if info is not None else AN_STATE["info"], "error": error})

    def sweep(self, a, b, levels):
        an_publish({"type": "sweep", "a": round(a, 3), "b": round(b, 3), "l": [round(v, 1) for v in levels]})

    def update(self, cfg):
        self.cmdq.put(dict(cfg))

    def stop(self):
        self.halt.set()

    def run(self):
        try:
            self.open()
            self.loop()
        except Exception as e:  # puerto ocupado, cable desconectado…
            if not self.halt.is_set():
                self.status(info="", error=f"Error con el analizador: {e}")
        finally:
            try:
                self.goodbye()
            except Exception:
                pass
            try:
                self.ser and self.ser.close()
            except Exception:
                pass


class RFExplorer(Analyzer):
    """Especificación: github.com/RFExplorer/RFExplorer-for-.NET/wiki/RF-Explorer-UART-API-interface-specification"""
    @staticmethod
    def cmd(body):
        b = body.encode("ascii") if isinstance(body, str) else body
        return bytes([35, len(b) + 2]) + b

    def set_range(self):
        amp = lambda v: ("-" if v < 0 else "0") + f"{abs(v):03d}"
        a, b = int(self.cfg["start"]), int(self.cfg["stop"])
        self.ser.write(self.cmd(f"C2-F:{a:07d},{b:07d},{amp(-20)},{amp(-120)}"))

    def open(self):
        self.ser = serial.Serial(self.cfg["port"], 500000, timeout=0.1)
        self.buf, self.startK, self.step, self.pts, self.model = bytearray(), None, None, None, ""
        self.ser.write(self.cmd("C0"))
        self.set_range()
        pts = self.cfg.get("rfePts")
        if pts not in (None, "", "auto"):
            self.ser.write(self.cmd(b"CJ" + bytes([max(0, min(255, int(pts) // 16 - 1))])))
        self.status("RF Explorer conectado a 500 kbps. Esperando su configuración…")

    def goodbye(self):
        self.ser.write(self.cmd("CH"))

    def loop(self):
        while not self.halt.is_set():
            try:
                self.cfg.update(self.cmdq.get_nowait())
                self.set_range()
            except queue.Empty:
                pass
            d = self.ser.read(4096)
            if d:
                self.buf += d
                self.parse()

    def parse(self):
        B = self.buf
        while True:
            s = 0
            while s < len(B) and B[s] not in (35, 36):
                s += 1
            if s:
                del B[:s]
            if len(B) < 4:
                return
            if B[0] == 35:  # "#": texto hasta CR LF
                e = B.find(b"\r\n")
                if e < 0:
                    if len(B) > 1024:
                        del B[:1]
                    return
                self.on_line(B[:e].decode("latin-1"))
                del B[:e + 2]
                continue
            t = B[1]
            if t in (83, 115):  # $S / $s
                h, x = 3, B[2]
                cand = [self.pts, x if t == 83 else (x + 1) * 16, (x + 1) * 16 if t == 83 else x]
            elif t == 122:  # $z
                h, cand = 4, [B[2] * 256 + B[3]]
            else:
                del B[:1]
                continue
            cand = list(dict.fromkeys(c for c in cand if c))
            ok, wait = 0, False
            for c in cand:
                if len(B) < h + c + 2:
                    wait = True
                    continue
                if B[h + c] == 13 and B[h + c + 1] == 10:
                    ok = c
                    break
            if not ok:
                if wait and len(B) < 70000:
                    return
                del B[:1]
                continue
            if self.startK:
                self.sweep(self.startK, self.startK + (ok - 1) * self.step / 1000, [-B[h + i] / 2 for i in range(ok)])
            del B[:h + ok + 2]

    def on_line(self, line):
        if line.startswith("#C2-F:"):
            f = [x.strip() for x in line[6:].split(",")]
            self.startK, self.step, self.pts = int(f[0]), int(f[1]), int(f[4])
            end = self.startK + (self.pts - 1) * self.step / 1000
            rbw = f", RBW {int(f[10])} kHz" if len(f) > 10 and f[10].isdigit() else ""
            self.status(f"RF Explorer{self.model}: {self.startK / 1000:.3f}–{end / 1000:.3f} MHz, {self.pts} puntos{rbw}.")
        elif line.startswith("#C2-M:"):
            f = [x.strip() for x in line[6:].split(",")]
            names = {"000": "433M", "001": "868M", "002": "915M", "003": "WSUB1G", "004": "2.4G", "005": "WSUB3G",
                     "006": "6G", "010": "WSUB1G+"}
            self.model = " " + names.get(f[0], "") + (f" (firmware {f[2]})" if len(f) > 2 else "")


class TinySA(Analyzer):
    """scanraw inicio fin puntos -> "{" + ("x" + uint16 LE) * puntos + "}" y "ch> ". dBm = v/32 - 128 (o - 174 en Ultra)."""
    def read_until(self, token, timeout, buf=b""):
        buf, t0 = bytearray(buf), now()
        while not self.halt.is_set() and now() - t0 < timeout:
            i = buf.find(token)
            if i >= 0:
                return bytes(buf[:i]), bytes(buf[i + len(token):])
            d = self.ser.read(4096)
            if d:
                buf += d
        return None, bytes(buf)

    def open(self):
        self.ser = serial.Serial(self.cfg["port"], 115200, timeout=0.1)
        self.ser.reset_input_buffer()
        self.ser.write(b"version\r")
        txt, rest, t0 = "", b"", now()
        while now() - t0 < 3:
            seg, rest = self.read_until(b"ch> ", 3 - (now() - t0), rest)
            if seg is None:
                break
            txt = seg.decode("latin-1", "replace")
            if re.search(r"tinysa|version|v\d", txt, re.I):
                break
        ultra = bool(re.search(r"tinySA4|ultra", txt, re.I))
        tiny = self.cfg.get("tiny", "auto")
        self.scale = (174 if ultra else 128) if tiny == "auto" else int(tiny)
        ver = next((l.strip() for l in txt.splitlines() if re.search("tinysa", l, re.I)), "tinySA")
        self.status(f"{ver} ({'tinySA Ultra' if self.scale == 174 else 'tinySA básico'}). Barriendo…")

    def goodbye(self):
        self.ser.write(b"resume\r")

    def loop(self):
        while not self.halt.is_set():
            try:
                while True:
                    self.cfg.update(self.cmdq.get_nowait())
            except queue.Empty:
                pass
            a, b = int(self.cfg["start"]), int(self.cfg["stop"])
            n = max(16, min(3000, int(self.cfg.get("points") or 450)))
            self.ser.write(f"scanraw {a * 1000} {b * 1000} {n}\r".encode())
            data, t0 = bytearray(), now()
            while not self.halt.is_set():
                o = data.find(b"{")
                if o >= 0 and len(data) >= o + 2 + 3 * n:
                    break
                if now() - t0 > 30:
                    raise TimeoutError("el tinySA no ha devuelto el barrido")
                d = self.ser.read(8192)
                if d:
                    data += d
            if self.halt.is_set():
                return
            end = o + 1 + 3 * n
            if data[end] == 125:
                self.sweep(a, b, [(data[q + 1] | (data[q + 2] << 8)) / 32 - self.scale for q in range(o + 1, end, 3)])
            self.read_until(b"ch> ", 3, bytes(data[end + 1:]))


class AD600(Analyzer):
    """Shure AD600 por red (UDP 57383, sin USB). Usa el motor de código abierto de la carpeta ad600/ (MIT,
    mbsound/AD600-Web-Based-Spectrum-Scan): un subproceso mantiene la sesión con el equipo y este hilo
    convierte sus barridos en eventos del analizador. El AD600 solo admite un controlador de escaneo a la
    vez: hay que cerrar Wireless Workbench. Al desconectar hay que dejar que libere su sesión (goodbye)."""
    BAND = (174000, 2000000)  # kHz

    @staticmethod
    def ensure_modules():
        os.environ.setdefault("AD600_ENGINE_SCRATCH", os.path.join(config_dir(), "ad600"))
        d = os.path.join(HERE, "ad600")
        if os.path.isdir(d) and d not in sys.path:
            sys.path.insert(0, d)
        import ad600_bridge, ad600_discovery, ad600_engine
        return ad600_bridge, ad600_discovery, ad600_engine

    @staticmethod
    def curve_mask(ant):
        """CURVE_SELECT: 0x7E = las seis antenas; una sola (A=1 … F=6) = 1 << n."""
        try:
            n = int(ant)
        except (TypeError, ValueError):
            return 0x7E
        return 1 << n if 1 <= n <= 6 else 0x7E

    def scan_cfg(self):
        return {"startHz": int(self.cfg["start"]) * 1000, "stopHz": int(self.cfg["stop"]) * 1000,
                "rbwHz": int(self.cfg.get("rbw") or 350) * 1000, "curveMask": self.curve_mask(self.cfg.get("ant")),
                "repeat": 0xFF}

    def find_device(self, disc):
        host = str(self.cfg.get("host") or "").strip()
        if host:
            ifd = disc.iface_for_host(host)
            if not ifd:
                raise RuntimeError(f"Ninguna conexión de red de este ordenador está en la misma red que {host}.")
            ifaces = [ifd]
        else:
            ifaces = [i for i in disc.list_interfaces() if i.get("ipv4") and not str(i["ipv4"]).startswith("127.")]
        if not ifaces:
            raise RuntimeError("Este ordenador no tiene ninguna conexión de red activa.")

        def probe(i):
            try:
                return i, disc.discover(i, timeout=4)
            except Exception:
                return i, None
        with ThreadPoolExecutor(max_workers=len(ifaces)) as ex:
            found = [(i, r) for i, r in ex.map(probe, ifaces) if r]
        if host:
            found = [(i, r) for i, r in found if r.get("device_ip") == host] or found
        if not found:
            raise RuntimeError("No encuentro ningún AD600 en la red. Comprueba que está encendido y en la misma red "
                               "(sin aislamiento entre dispositivos) y que el Mac ha permitido las conexiones de red locales.")
        ifd, dev = found[0]
        if not dev.get("device_cid"):
            raise RuntimeError(f"Veo un AD600 en {dev.get('device_ip')} pero no se identifica por la red. "
                               "Revisa que el Mac y el AD600 estén en la misma red.")
        return ifd, dev

    @staticmethod
    def throttled_bridge(br_mod):
        """El Bridge original reconstruye el barrido completo con CADA trozo (frame) que llega: en rangos anchos son miles
        de trozos por barrido y el Python se atasca. Esta versión publica como mucho cada AD600_PUBLISH_MIN segundos
        (0,25 por defecto) y guarda el resto para flush(), sin tocar el código original."""
        gap = float(os.environ.get("AD600_PUBLISH_MIN", "0.25"))

        class ThrottledBridge(br_mod.Bridge):
            _last_pub, _dirty = 0.0, False

            def _publish(self):  # se llama con el cerrojo ya tomado, desde feed()
                t = time.time()
                if t - self._last_pub < gap:
                    self._dirty = True
                    return
                self._last_pub, self._dirty = t, False
                super()._publish()

            def flush(self):
                with self._cv:
                    if self._dirty:
                        self._last_pub, self._dirty = time.time(), False
                        super()._publish()
        return ThrottledBridge

    def open(self):
        self.br_mod, disc, eng_mod = self.ensure_modules()
        self.status("Buscando el AD600 en la red…")
        ifd, dev = self.find_device(disc)
        self.ip = dev.get("device_ip")
        self.br = self.throttled_bridge(self.br_mod)(port=0)  # solo ensambla barridos: su servidor HTTP no se arranca
        self.eng = eng_mod.Engine(bridge=self.br)
        self.br.on_config_change = self.eng.apply_config
        self.br.apply_configuration(self.scan_cfg())
        self.eng.start(dev, ifd)
        self.br.sweep_start()
        self.rearm_until = 0.0
        self.status(f"AD600 encontrado en {self.ip}. Conectando: tarda entre 20 y 30 s…")

    def describe(self):
        st, tr = self.eng.status(), self.br.trace
        if st.get("blocked"):
            return "", ("El AD600 tiene el escaneo ocupado por otro controlador (Wireless Workbench u otro programa) "
                        "o por una sesión anterior. Ciérralo y, si sigue igual, apaga y enciende el AD600.")
        if not st.get("running") and now() > self.rearm_until:
            return "", "Se ha perdido la conexión con el AD600. Pulsa Desconectar y vuelve a conectar."
        if not tr:
            if now() < self.rearm_until:
                return f"AD600 {self.ip}: aplicando el cambio, se reconecta (20-30 s)…", ""
            return f"AD600 {self.ip}: {st.get('note') or 'conectando'}… (la primera vez tarda entre 20 y 30 s)", ""
        rbw = int(tr["stepHz"] / 1000)
        pct = tr.get("coveragePct", 0)
        n = self.curve_mask(self.cfg.get("ant")).bit_length() - 1
        ant = f"antena {'ABCDEF'[n - 1]}" if self.curve_mask(self.cfg.get("ant")) != 0x7E else "todas las antenas"
        return (f"AD600 {self.ip}: {tr['startHz'] / 1e6:.1f}–{tr['stopHz'] / 1e6:.1f} MHz, {rbw} kHz por punto, {ant}."
                + ("" if tr.get("coverageComplete") else f" Completando el primer barrido: {pct} %."), "")

    def goodbye(self):
        try:
            self.eng.stop()  # desconexión limpia: libera la sesión de escaneo del AD600
        except Exception:
            pass

    def loop(self):
        last, t_pub, last_txt = -1, 0.0, None
        while not self.halt.is_set():
            changed = False
            try:
                while True:
                    self.cfg.update(self.cmdq.get_nowait())
                    changed = True
            except queue.Empty:
                pass
            if changed:
                self.rearm_until = now() + 90  # cambia rango, RBW o antenas: el motor se reconecta solo
                self.br.apply_configuration(self.scan_cfg())
            self.br.flush()
            tr = self.br.trace
            if tr and tr["sweepId"] != last and now() - t_pub >= 0.25:
                last, t_pub = tr["sweepId"], now()
                self.sweep(tr["startHz"] / 1000, tr["stopHz"] / 1000, tr["amplitudesDbm"])
            txt = self.describe()
            if txt != last_txt:
                last_txt = txt
                self.status(*txt)
            time.sleep(0.1)


AN_TYPES = {"rfe": RFExplorer, "tinysa": TinySA, "ad600": AD600}
AN_THREADS = []  # hilos de analizador lanzados: al salir se espera a que se despidan


def analyzer_shutdown(wait=8.0):
    """Para el analizador y espera a que termine de despedirse. El AD600 tiene que liberar su sesión de
    escaneo; si se mata de golpe se queda ocupado hasta apagarlo y encenderlo."""
    try:
        analyzer_cmd({"action": "stop"})
    except Exception:
        pass
    t0 = now()
    for t in list(AN_THREADS):
        t.join(max(0.1, wait - (now() - t0)))


def analyzer_cmd(body):
    global ANALYZER
    act = body.get("action")
    if act == "stop":
        with LOCK:
            a, ANALYZER = ANALYZER, None
        if a:
            a.stop()
        an_publish({"type": "status", "info": "Analizador desconectado.", "error": ""})
        return
    if act == "range":
        cfg = {k: body[k] for k in ("start", "stop", "points", "rbw", "ant") if k in body}
        if ANALYZER:
            ANALYZER.update(cfg)
        return
    if act != "start":
        raise ValueError("acción desconocida")
    kind, port = body.get("type"), str(body.get("port") or "")
    if kind not in AN_TYPES:
        raise ValueError("tipo de analizador desconocido")
    start, stop = int(body["start"]), int(body["stop"])
    if kind == "ad600":
        lo, hi = AD600.BAND
        if not lo <= start < stop <= hi:
            raise ValueError("el AD600 cubre de 174 a 2000 MHz")
        if int(body.get("rbw") or 350) not in (50, 100, 350, 900):
            raise ValueError("la resolución del AD600 puede ser 50, 100, 350 o 900 kHz")
        cfg = {"start": start, "stop": stop, "rbw": int(body.get("rbw") or 350), "ant": str(body.get("ant") or "all"),
               "host": str(body.get("host") or "")}
    else:
        if serial is None:
            raise ValueError("Falta el módulo pyserial. Instálalo con: pip3 install pyserial (y vuelve a arrancar el puente)")
        known = [p["device"] for p in list_serial()]
        if port not in known:
            raise ValueError("ese puerto no está conectado. Pulsa Buscar puertos")
        if not 1000 <= start < stop <= 7000000:
            raise ValueError("rango de frecuencias no válido")
        cfg = {"port": port, "start": start, "stop": stop, "points": body.get("points"),
               "rfePts": body.get("rfePts"), "tiny": str(body.get("tiny") or "auto")}
    analyzer_cmd({"action": "stop"})
    a = AN_TYPES[kind](cfg)
    with LOCK:
        ANALYZER = a
        AN_THREADS[:] = [t for t in AN_THREADS if t.is_alive()] + [a]
    an_publish({"type": "status", "info": "Abriendo el analizador…" if kind == "ad600" else "Abriendo el puerto del analizador…", "error": ""})
    a.start()


def set_devices(items):
    if not isinstance(items, list):
        raise ValueError("se esperaba una lista de receptores")
    cfgs = {}
    for it in items[:64]:
        if not isinstance(it, dict):
            continue
        kind, host = it.get("kind"), str(it.get("host", "")).strip()
        if kind not in KINDS or not re.fullmatch(r"[A-Za-z0-9.\-]{1,253}", host):
            continue
        port = it.get("port")
        if port not in (None, ""):
            port = int(port)
            if not 1 <= port <= 65535:
                continue
        did = str(it.get("id") or f"{kind}-{host}-{port or ''}")[:64]
        cfgs[did] = {"id": did, "kind": kind, "host": host, "port": port, "name": str(it.get("name", ""))[:40]}
    with LOCK:
        for did in list(DEVICES):
            d = DEVICES[did]
            c = cfgs.get(did)
            if not c or (c["kind"], c["host"], int(c["port"] or d.default_port)) != (d.cfg["kind"], d.host, d.port):
                d.stop()
                del DEVICES[did]
            else:
                d.cfg["name"] = c["name"]
        for did, c in cfgs.items():
            if did not in DEVICES:
                d = KINDS[c["kind"]](c)
                DEVICES[did] = d
                d.start()


# ---------------------------------------------------------------------------------------------
# Descubrimiento de receptores en la red
# ---------------------------------------------------------------------------------------------
DISCOVER_LOCK = threading.Lock()
MAX_HOSTS = 1024


def local_interfaces():
    """Interfaces IPv4 activas (cable, Wi-Fi…) como ipaddress.IPv4Interface, sin la de loopback."""
    out = []

    def add(ip, mask):
        try:
            iface = ipaddress.IPv4Interface(f"{ip}/{mask}")
        except ValueError:
            return
        if not iface.ip.is_loopback:
            out.append(iface)

    for cmd in (["ifconfig"], ["ip", "-4", "-o", "addr"], ["ipconfig"]):
        try:
            txt = subprocess.run(cmd, capture_output=True, text=True, timeout=3, errors="replace").stdout
        except (OSError, subprocess.SubprocessError):
            continue
        if cmd[0] == "ifconfig":  # macOS: "inet 192.168.1.20 netmask 0xffffff00"; Linux: "netmask 255.255.255.0"
            for ip, mask in re.findall(r"inet (\d+\.\d+\.\d+\.\d+)\s+netmask\s+(0x[0-9a-fA-F]{8}|\d+\.\d+\.\d+\.\d+)", txt):
                add(ip, str(ipaddress.IPv4Address(int(mask, 16))) if mask.startswith("0x") else mask)
        elif cmd[0] == "ip":
            for ip, pfx in re.findall(r"inet (\d+\.\d+\.\d+\.\d+)/(\d+)", txt):
                add(ip, pfx)
        else:  # Windows, en inglés o en español
            ips = re.findall(r"IPv4[^:\n]*:\s*(\d+\.\d+\.\d+\.\d+)", txt)
            masks = re.findall(r"(?:Subnet Mask|M.scara de subred)[^:\n]*:\s*(\d+\.\d+\.\d+\.\d+)", txt)
            for ip, mask in zip(ips, masks):
                add(ip, mask)
        if out:
            break
    if not out:  # último recurso: la IP principal, suponiendo una red /24
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sk:
                sk.connect(("10.255.255.255", 1))
                add(sk.getsockname()[0], "255.255.255.0")
        except OSError:
            pass
    seen, res = set(), []
    for i in out:
        if i.network not in seen:
            seen.add(i.network)
            res.append(i)
    return res


def probe_shure(host):
    """Pregunta el modelo por el puerto de control de Shure (2202). Solo cuenta si responde con REP."""
    data = b""
    try:
        with socket.create_connection((host, 2202), timeout=0.5) as sk:
            sk.settimeout(0.3)
            sk.sendall(b"< GET MODEL >")
            t0 = now()
            while now() - t0 < 1.5 and b"MODEL" not in data:
                try:
                    d = sk.recv(1024)
                except socket.timeout:
                    continue
                if not d:
                    break
                data += d
    except OSError:
        return None
    if b"REP" not in data:
        return None
    m = re.search(rb"REP MODEL \{?([^}>]*)", data)
    model = m.group(1).decode("latin-1").strip() if m else ""
    return {"kind": "shure", "host": host, "port": 2202, "model": model or "Equipo Shure", "name": ""}


def mdns_ssc(ifaces, timeout=1.5):
    """Busca equipos Sennheiser que se anuncian como "_ssc._udp" (DNS-SD). Devuelve sus IPs."""
    name = b"".join(bytes([len(p)]) + p for p in (b"_ssc", b"_udp", b"local")) + b"\x00"
    query = b"\x00\x00\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00" + name + b"\x00\x0c\x80\x01"  # PTR, respuesta unicast
    found = set()
    try:
        sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sk.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        sk.settimeout(0.2)
    except OSError:
        return []
    with sk:
        for iface in ifaces or [None]:
            try:
                if iface is not None:
                    sk.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(str(iface.ip)))
                sk.sendto(query, ("224.0.0.251", 5353))
            except OSError:
                pass
        t0 = now()
        while now() - t0 < timeout:
            try:
                data, addr = sk.recvfrom(9000)
            except socket.timeout:
                continue
            except OSError:
                continue
            if b"_ssc" in data:
                found.add(addr[0])
    return sorted(found)


def probe_ssc(hosts, timeout=1.5):
    """Pide la identidad por SSC (UDP 45) a cada dirección y se queda con las que responden."""
    found = {}
    msg = json.dumps({"device": {"identity": {"product": None}, "name": None}}).encode()
    try:
        sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sk.settimeout(0.2)
    except OSError:
        return []
    with sk:
        for h in hosts:
            try:
                sk.sendto(msg, (h, 45))
            except OSError:
                pass
        t0 = now()
        while now() - t0 < timeout:
            try:
                data, addr = sk.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:  # en Windows los "puerto inalcanzable" llegan como error: se ignoran
                continue
            try:
                obj = json.loads(data.decode("utf-8", "replace"))
            except ValueError:
                continue
            dev = obj.get("device") if isinstance(obj, dict) else None
            if isinstance(dev, dict):
                prod = (dev.get("identity") or {}).get("product") or "Equipo Sennheiser"
                found[addr[0]] = {"kind": "ssc", "host": addr[0], "port": 45, "model": prod,
                                  "name": dev.get("name") if isinstance(dev.get("name"), str) else ""}
    return list(found.values())


def interface_list():
    """Redes a las que está conectado el ordenador, para que el usuario elija por cuál buscar."""
    res = []
    for i in local_interfaces():
        res.append({"ip": str(i.ip), "network": str(i.network), "link_local": i.ip.is_link_local,
                    "hosts": i.network.num_addresses})
    return res


def discover(ranges="", iface=""):
    if not DISCOVER_LOCK.acquire(blocking=False):
        raise ValueError("ya hay una búsqueda en marcha")
    try:
        notes, nets = [], []
        ifaces = local_interfaces()
        own = {str(i.ip) for i in ifaces}
        if iface.strip():  # solo la conexión elegida (la IP del ordenador en esa red)
            ifaces = [i for i in ifaces if str(i.ip) == iface.strip()]
            if not ifaces:
                raise ValueError(f"la conexión {iface} ya no está disponible: actualiza la lista de redes")
        if ranges.strip():
            for tok in re.split(r"[\s,;]+", ranges.strip()):
                try:
                    net = ipaddress.ip_network(tok, strict=False)
                except ValueError:
                    raise ValueError(f"rango no válido: {tok} (usa por ejemplo 192.168.10.0/24)")
                if net.version != 4:
                    raise ValueError("solo se admiten direcciones IPv4")
                if net.num_addresses > MAX_HOSTS:
                    raise ValueError(f"{tok} es demasiado grande: como mucho {MAX_HOSTS} direcciones (una /22)")
                nets.append(net)
        else:
            if not ifaces:
                notes.append("No se ha encontrado ninguna conexión de red activa.")
            for i in ifaces:
                net = i.network
                if net.num_addresses > MAX_HOSTS or i.ip.is_link_local:
                    small = ipaddress.ip_network(f"{i.ip}/24", strict=False)
                    if i.ip.is_link_local:
                        notes.append(f"Tu ordenador tiene una IP automática ({i.ip}); los equipos pueden estar en cualquier "
                                     f"parte de 169.254.x.x y solo se revisa {small}. Los Sennheiser se encuentran igualmente "
                                     "por su anuncio de red. Para el resto, usa un router con DHCP o IPs fijas.")
                    else:
                        notes.append(f"La red {net} es muy grande: solo se revisa {small}. Indica otro rango si hace falta.")
                    net = small
                nets.append(net)
        hosts = list(dict.fromkeys(str(h) for n in nets for h in (n.hosts() if n.num_addresses > 2 else [n.network_address])
                                   if str(h) not in own))
        with ThreadPoolExecutor(max_workers=128) as ex:
            shure = [r for r in ex.map(probe_shure, hosts) if r]
        announced = mdns_ssc(ifaces)
        ssc = probe_ssc(list(dict.fromkeys(hosts + announced)))
        found = shure + ssc
        with LOCK:
            known = {(d.cfg["kind"], d.host) for d in DEVICES.values()}
        for f in found:
            f["known"] = (f["kind"], f["host"]) in known
        return {"found": found, "ranges": [str(n) for n in nets], "hosts": len(hosts), "notes": notes}
    finally:
        DISCOVER_LOCK.release()


def snapshot():
    with LOCK:
        devs = list(DEVICES.values())
    return {"bridge": VERSION, "t": now(), "devices": [d.snapshot() for d in devs]}


# ---------------------------------------------------------------------------------------------
# Servidor HTTP (EventSource para la telemetría, POST para cambios)
# ---------------------------------------------------------------------------------------------
def config_dir():
    home = os.path.expanduser("~")
    if sys.platform == "darwin":
        d = os.path.join(home, "Library", "Application Support", "CoordinadorRF")
    elif os.name == "nt":
        d = os.path.join(os.environ.get("APPDATA", home), "CoordinadorRF")
    else:
        d = os.path.join(home, ".config", "coordinador-rf")
    try:
        os.makedirs(d, exist_ok=True)
        return d
    except OSError:
        return HERE


def load_key():
    path = os.path.join(config_dir(), "puente-rf.clave")
    try:
        k = open(path).read().strip()
        if k:
            return k
    except OSError:
        pass
    k = secrets.token_hex(4) + "-" + secrets.token_hex(4)
    try:
        with open(path, "w") as fh:
            fh.write(k + "\n")
    except OSError:
        pass
    return k


KEY = ""
SSE_CLIENTS = 0        # navegadores con la app abierta (conexiones de eventos activas)
IDLE_SINCE = now()     # desde cuándo no hay ninguno



# ---------------------------------------------------------------------------------------------
# Archivos del usuario: proyectos, informes y registros. Van a Documentos/Coordinador RF (visible en el
# Finder y fácil de copiar); si no se puede escribir ahí, a la carpeta de configuración.
# ---------------------------------------------------------------------------------------------
MAX_BODY = 25_000_000  # los proyectos con un escaneo largo pueden pasar de 1 MB
UPDATE_REPO = "pabloor/COORDINADOR-RF"
_DOCS = {}


def user_dir(sub):
    if "base" not in _DOCS:
        base = os.path.join(os.path.expanduser("~"), "Documents", "Coordinador RF")
        try:
            os.makedirs(base, exist_ok=True)
            if not os.access(base, os.W_OK):
                raise OSError("sin permiso de escritura")
        except OSError:
            base = config_dir()
        _DOCS["base"] = base
    d = os.path.join(_DOCS["base"], sub)
    try:
        os.makedirs(d, exist_ok=True)
        return d
    except OSError:
        d = os.path.join(config_dir(), sub)
        os.makedirs(d, exist_ok=True)
        return d


def slug(name, default="archivo"):
    t = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9._-]+", "-", t).strip("-._")[:40] or default


def write_atomic(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(data)
    os.replace(tmp, path)


def write_atomic_bytes(path, data):
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


PROJ_ID = re.compile(r"^[a-z0-9]{3,32}$")


def proj_paths():
    d = user_dir("Proyectos")
    out = {}
    for fn in os.listdir(d):
        m = re.search(r"--([a-z0-9]{3,32})\.json$", fn)
        if m:
            out[m.group(1)] = os.path.join(d, fn)
    return d, out


def projects_list():
    d, files = proj_paths()
    out = []
    for pid, path in files.items():
        try:
            with open(path, encoding="utf-8") as f:
                j = json.load(f)
            out.append({"id": pid, "name": str(j.get("name") or pid), "t": int(j.get("t") or 0)})
        except (OSError, ValueError):
            continue
    return {"dir": d, "projects": sorted(out, key=lambda x: -x["t"])}


def project_get(pid):
    if not PROJ_ID.match(pid or ""):
        raise ValueError("identificador no válido")
    _, files = proj_paths()
    if pid not in files:
        raise ValueError("proyecto no encontrado")
    with open(files[pid], encoding="utf-8") as f:
        return json.load(f)


def project_save(body):
    pid = str(body.get("id") or "")
    if not PROJ_ID.match(pid) or not isinstance(body.get("state"), dict):
        raise ValueError("proyecto no válido")
    name = str(body.get("name") or "Proyecto")[:80]
    d, files = proj_paths()
    path = os.path.join(d, f"{slug(name, 'proyecto')}--{pid}.json")
    write_atomic(path, json.dumps({"id": pid, "name": name, "t": int(body.get("t") or now() * 1000), "state": body["state"]},
                                  ensure_ascii=False))
    old = files.get(pid)
    if old and old != path:
        try:
            os.remove(old)
        except OSError:
            pass
    return d


def project_delete(pid):
    if not PROJ_ID.match(pid or ""):
        raise ValueError("identificador no válido")
    _, files = proj_paths()
    if pid in files:
        os.remove(files[pid])


def reveal(path):
    """Abre una carpeta o un archivo con el programa del sistema (Finder en Mac)."""
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", path])
        elif os.name == "nt":
            os.startfile(path)  # noqa
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass


def save_file(kind, name, content, open_it):
    """Guarda un archivo de texto en Documentos/Coordinador RF/<tipo>. Los informes (HTML) se abren en el navegador
    del sistema para imprimirlos o guardarlos como PDF; los demás se muestran en el Finder."""
    sub = {"informe": "Informes", "registro": "Registros", "imagen": "Imágenes"}.get(kind)
    if not sub or not isinstance(content, str):
        raise ValueError("archivo no válido")
    ext = {"informe": ".html", "imagen": ".png"}.get(kind, ".csv")
    path = os.path.join(user_dir(sub), f"{slug(name, kind)}-{time.strftime('%Y%m%d-%H%M%S')}{ext}")
    if kind == "imagen":   # la imagen llega como PNG en base64 (con o sin el prefijo data:)
        try:
            data = base64.b64decode(content.split(",", 1)[-1], validate=True)
        except (ValueError, TypeError):
            raise ValueError("imagen no válida")
        if not data.startswith(b"\x89PNG\r\n\x1a\n") or len(data) > MAX_BODY:
            raise ValueError("imagen no válida")
        write_atomic_bytes(path, data)
    else:
        write_atomic(path, content)
    if open_it:
        if kind == "informe":
            webbrowser.open("file://" + path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
        else:
            reveal(os.path.dirname(path))
    return path


def notify(title, text):
    """Notificación del sistema. En Mac con osascript; en otros sistemas se ignora si no hay forma sencilla."""
    title, text = str(title or "Coordinador RF")[:80], str(text or "")[:200]
    try:
        if sys.platform == "darwin":
            lit = lambda x: json.dumps(x, ensure_ascii=False)
            subprocess.Popen(["osascript", "-e", f"display notification {lit(text)} with title {lit(title)}"])
        elif shutil.which("notify-send"):
            subprocess.Popen(["notify-send", title, text])
    except Exception:
        pass


def tail(path, n):
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 60000))
            return b"\n".join(f.read().splitlines()[-n:]).decode("utf-8", "replace")
    except OSError:
        return "(no existe)"


def diagnostics():
    """Texto para pegar en un mensaje cuando algo no funciona: versiones, estado y últimas líneas de los registros."""
    with LOCK:
        devs = []
        for d in DEVICES.values():
            devs.append(f"  {d.cfg.get('kind')} {d.host}:{d.port} en línea={d.online} error={d.error or '-'} modelo={d.model or '-'}")
            devs.append("    mensajes recibidos: " + (", ".join(f"{k}×{v}" for k, v in sorted(d.counts.items())) or "ninguno"))
            devs.extend("    · " + x for x in d.seen[-80:])
            for n, c in sorted(d.ch.items()):
                devs.append(f"    canal {n}: " + ", ".join(f"{k}={v}" for k, v in c.items() if k != "t"))
        an = dict(AN_STATE)
    scratch = os.environ.get("AD600_ENGINE_SCRATCH") or os.path.join(config_dir(), "ad600")
    parts = [
        f"Coordinador RF puente {VERSION}",
        f"Sistema: {sys.platform} {os.name}, Python {sys.version.split()[0]}, empaquetado={FROZEN}",
        f"Carpeta de configuración: {config_dir()}",
        f"Carpeta de documentos: {os.path.join(_DOCS['base']) if 'base' in _DOCS else '(aún sin usar)'}",
        f"Analizador USB (pyserial): {'sí' if serial else 'no'}",
        f"Analizador: {an.get('info') or '-'} | error: {an.get('error') or '-'}",
        "Receptores:" + ("\n" + "\n".join(devs) if devs else " ninguno"),
        "", "--- CoordinadorRF.log (últimas líneas) ---", tail(os.path.join(config_dir(), "CoordinadorRF.log"), 60),
        "", "--- actualizacion.log (últimas líneas) ---", tail(os.path.join(config_dir(), "actualizacion.log"), 20),
        "", "--- AD600 console_out.log (últimas líneas) ---", tail(os.path.join(scratch, "console_out.log"), 80),
    ]
    return "\n".join(parts)


def _ver(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v))[:3])


_UPD = {"t": 0.0, "data": None}
UPDATE_TEST = "CRF_UPDATE_API"  # solo para pruebas: otra dirección para «la última release» (y se admiten descargas desde 127.0.0.1)
UA = {"User-Agent": "CoordinadorRF", "Accept": "application/vnd.github+json"}


def _urlopen(req, timeout):
    """Abre una dirección probando primero los certificados del sistema y después los de certifi (en la app
    empaquetada puede faltar alguno)."""
    url = req.full_url if hasattr(req, "full_url") else str(req)
    if url.startswith(("http://127.0.0.1:", "http://localhost:")):  # lo local nunca pasa por un proxy del sistema
        return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=timeout)
    ctxs = [ssl.create_default_context()]
    try:
        import certifi
        ctxs.append(ssl.create_default_context(cafile=certifi.where()))
    except Exception:
        pass
    err = None
    for ctx in ctxs:
        try:
            return urllib.request.urlopen(req, timeout=timeout, context=ctx)
        except (ssl.SSLError, urllib.error.URLError) as e:
            err = e
    raise err


def mac_asset_name():
    """El archivo de la release que corresponde a la app que está en marcha (Apple Silicon o Intel)."""
    return "Coordinador-RF-Mac-Intel.zip" if platform.machine().lower() in ("x86_64", "amd64") else "Coordinador-RF-Mac.zip"


def asset_url_ok(url):
    if os.environ.get(UPDATE_TEST):
        return str(url).startswith("http://127.0.0.1:")
    return str(url).startswith(f"https://github.com/{UPDATE_REPO}/releases/download/")


def app_bundle():
    """Ruta de «Coordinador RF.app» si esto es la app de Mac instalada; si no (Windows, desarrollo), None."""
    if not (FROZEN and sys.platform == "darwin"):
        return None
    m = re.match(r"^(.*?\.app)/Contents/MacOS/", os.path.realpath(sys.executable))
    return m.group(1) if m else None


def can_install(asset):
    """(posible, motivo). Sin motivo = no es un caso en el que haya algo que explicar (Windows, desarrollo)."""
    b = app_bundle()
    if not b:
        return False, ""
    if not asset:
        return False, "Esta versión no trae el archivo para tu Mac."
    if "/AppTranslocation/" in b:
        return False, "macOS está ejecutando la app desde una ubicación temporal. Muévela a Aplicaciones, ábrela desde ahí y vuelve a intentarlo."
    if not os.access(os.path.dirname(b), os.W_OK):
        return False, f"No puedo escribir en {os.path.dirname(b)}. Mueve la app a Aplicaciones."
    return True, ""


def check_update():
    """Consulta la última release publicada (repositorio público). Se guarda 15 minutos (2 si falló); si falla, no molesta."""
    d = _UPD["data"]
    if d is not None and now() - _UPD["t"] < (120 if d.get("error") else 15 * 60):
        return d
    try:
        api = os.environ.get(UPDATE_TEST) or f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest"
        with _urlopen(urllib.request.Request(api, headers=UA), 6) as r:
            j = json.load(r)
        tag = str(j.get("tag_name") or "")
        asset = None
        for a in j.get("assets") or []:
            dg = str(a.get("digest") or "")
            if a.get("name") == mac_asset_name() and dg.startswith("sha256:") and asset_url_ok(a.get("browser_download_url")):
                asset = {"name": a["name"], "url": a["browser_download_url"], "digest": dg, "size": int(a.get("size") or 0)}
        newer = _ver(tag) > _ver(VERSION)
        ok, why = can_install(asset)
        data = {"current": VERSION, "latest": tag.lstrip("v"), "url": j.get("html_url") or f"https://github.com/{UPDATE_REPO}/releases",
                "newer": newer, "notes": str(j.get("body") or "")[:600], "asset": asset, "canInstall": bool(newer and ok), "installReason": why if newer else ""}
    except Exception as e:
        data = {"current": VERSION, "error": str(e)[:120]}
    _UPD.update(t=now(), data=data)
    return data


# --- Actualización con un clic (solo la app de Mac) -------------------------------------------------------
# Descarga la release, comprueba su SHA-256 (el que publica GitHub), la extrae junto a la app instalada y deja
# que un script la sustituya cuando esta salga. Si la nueva no responde en 150 s (la primera vez puede tardar), el script deshace el cambio.
UPD_ST = {"state": "idle", "pct": 0, "msg": "", "error": ""}
UPD_LOCK = threading.Lock()

UPDATE_SCRIPT = r"""#!/bin/bash
# Coordinador RF: sustituye la app por la nueva versión cuando la actual ha salido y la vuelve a abrir.
# Si la nueva no responde en 150 s, vuelve a la anterior. Uso: instalar.sh PID APP_ACTUAL APP_NUEVA CARPETA_COPIA PUERTO LOG [argumentos de la app…]
PID="$1"; OLD="$2"; NEW="$3"; BAK="$4"; PORT="$5"; LOG="$6"; shift 6
OPENER="${CRF_OPENER:-open}"
STAGE="$(dirname "$NEW")"
log() { echo "$(date '+%F %T') $*" >> "$LOG"; }
launch() { if [ "$#" -gt 0 ]; then "$OPENER" "$OLD" --args "$@"; else "$OPENER" "$OLD"; fi; }
for _ in $(seq 1 $(( ${CRF_EXIT_WAIT:-120} * 2 ))); do kill -0 "$PID" 2>/dev/null || break; sleep 0.5; done
if kill -0 "$PID" 2>/dev/null; then log "la app no se cerró: no se toca nada"; rm -rf "$STAGE"; exit 1; fi
mkdir -p "$BAK"; rm -rf "$BAK/previous.app"
if ! mv "$OLD" "$BAK/previous.app"; then log "no se pudo apartar la versión actual"; rm -rf "$STAGE"; launch "$@"; exit 1; fi
if ! mv "$NEW" "$OLD"; then log "no se pudo colocar la versión nueva: se deja la anterior"; mv "$BAK/previous.app" "$OLD"; rm -rf "$STAGE"; launch "$@"; exit 1; fi
rm -rf "$STAGE"
command -v xattr >/dev/null 2>&1 && xattr -dr com.apple.quarantine "$OLD" 2>/dev/null
log "versión nueva colocada: abriéndola"
launch "$@"
for _ in $(seq 1 $(( ${CRF_HEALTH_WAIT:-150} * 2 ))); do
  if curl -s -o /dev/null -m 1 "http://127.0.0.1:$PORT/"; then log "la versión nueva responde: actualización terminada"; exit 0; fi
  sleep 0.5
done
log "la versión nueva no responde en ${CRF_HEALTH_WAIT:-150} s: se vuelve a la anterior"
pkill -f "$OLD/Contents/MacOS/" 2>/dev/null; sleep 1
rm -rf "$OLD"
mv "$BAK/previous.app" "$OLD" && launch "$@"
exit 1
"""


def upd_set(**kw):
    with UPD_LOCK:
        UPD_ST.update(kw)


def update_download(asset, dest_dir):
    """Descarga el zip y comprueba tamaño y SHA-256. Si algo no cuadra, lo borra y falla: nunca se instala algo sin verificar."""
    if not asset_url_ok(asset.get("url")):
        raise ValueError("dirección de descarga no permitida")
    size = int(asset.get("size") or 0)
    if not 0 < size <= 300_000_000:
        raise ValueError("tamaño de la descarga no válido")
    want = str(asset.get("digest") or "").split(":", 1)[-1].lower()
    if not re.fullmatch(r"[0-9a-f]{64}", want):
        raise ValueError("falta la huella SHA-256 de la descarga")
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, "descarga.zip")
    h, got = hashlib.sha256(), 0
    try:
        with _urlopen(urllib.request.Request(asset["url"], headers={"User-Agent": "CoordinadorRF"}), 30) as r, open(path, "wb") as f:
            while True:
                chunk = r.read(65536)
                if not chunk:
                    break
                got += len(chunk)
                if got > size + 1024:
                    raise ValueError("la descarga es más grande de lo anunciado")
                h.update(chunk)
                f.write(chunk)
                upd_set(pct=int(100 * got / size), msg=f"Descargando… {int(100 * got / size)} %")
        if got != size:
            raise ValueError("la descarga ha quedado incompleta")
        if h.hexdigest() != want:
            raise ValueError("la descarga no coincide con la huella SHA-256 publicada: no se instala")
    except Exception:
        try:
            os.remove(path)
        except OSError:
            pass
        raise
    return path


def verify_new_app(new, old):
    """La app descargada tiene que ser de Coordinador RF, estar completa y con la firma íntegra."""
    with open(os.path.join(new, "Contents", "Info.plist"), "rb") as f:
        pn = plistlib.load(f)
    with open(os.path.join(old, "Contents", "Info.plist"), "rb") as f:
        po = plistlib.load(f)
    if pn.get("CFBundleIdentifier") != po.get("CFBundleIdentifier"):
        raise ValueError("el paquete descargado no es de Coordinador RF")
    if not os.access(os.path.join(new, "Contents", "MacOS", str(pn.get("CFBundleExecutable") or "")), os.X_OK):
        raise ValueError("la app descargada está incompleta")
    r = subprocess.run(["codesign", "--verify", "--deep", "--strict", new], capture_output=True, timeout=120)
    if r.returncode:
        raise ValueError("la firma de la app descargada no es válida")


def _update_worker(info):
    old = app_bundle()
    base = os.path.join(config_dir(), "actualizacion")
    stage = os.path.join(os.path.dirname(old), ".Coordinador RF.actualizando")  # junto a la app: mismo disco, el cambio es instantáneo
    try:
        shutil.rmtree(base, ignore_errors=True)
        zpath = update_download(info["asset"], base)
        upd_set(state="comprobando", pct=100, msg="Comprobando la descarga…")
        shutil.rmtree(stage, ignore_errors=True)
        os.makedirs(stage)
        subprocess.run(["ditto", "-x", "-k", zpath, stage], check=True, timeout=180)
        apps = [d for d in os.listdir(stage) if d.endswith(".app")]
        if len(apps) != 1:
            raise ValueError("el zip descargado no tiene el formato esperado")
        new = os.path.join(stage, apps[0])
        verify_new_app(new, old)
        upd_set(state="instalando", msg="Instalando…")
        script = os.path.join(base, "instalar.sh")
        with open(script, "w", encoding="utf-8") as f:
            f.write(UPDATE_SCRIPT)
        os.chmod(script, 0o755)
        args = [a for a in sys.argv[1:] if not a.startswith("-psn_")]
        analyzer_shutdown()  # libera la sesión de escaneo del AD600 antes de cerrar
        subprocess.Popen(["/bin/bash", script, str(os.getpid()), old, new, os.path.join(config_dir(), "versiones-anteriores"), str(PORT),
                          os.path.join(config_dir(), "actualizacion.log")] + args,
                         start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
        upd_set(state="reiniciando", msg="Reiniciando Coordinador RF…")
        time.sleep(1.5)  # que la interfaz alcance a ver el aviso
        os._exit(0)
    except Exception as e:
        shutil.rmtree(stage, ignore_errors=True)
        upd_set(state="error", error=str(e)[:240] or "error desconocido")


def update_install():
    with UPD_LOCK:
        if UPD_ST["state"] in ("descargando", "comprobando", "instalando", "reiniciando"):
            raise ValueError("ya hay una actualización en curso")
    info = check_update()
    if not info.get("newer"):
        raise ValueError("ya tienes la última versión")
    if not info.get("canInstall"):
        raise ValueError(info.get("installReason") or "Solo la app de Mac instalada puede actualizarse sola. Descarga la nueva versión desde la página de la release.")
    upd_set(state="descargando", pct=0, msg="Descargando… 0 %", error="")
    threading.Thread(target=_update_worker, args=(info,), daemon=True).start()


def open_url(url):
    if not str(url).startswith(f"https://github.com/{UPDATE_REPO}"):
        raise ValueError("dirección no permitida")
    webbrowser.open(url)


def sse_enter():
    global SSE_CLIENTS
    with LOCK:
        SSE_CLIENTS += 1


def sse_exit():
    global SSE_CLIENTS, IDLE_SINCE
    with LOCK:
        SSE_CLIENTS -= 1
        if SSE_CLIENTS <= 0:
            SSE_CLIENTS, IDLE_SINCE = 0, now()


def watchdog(idle):
    """Apaga el puente cuando lleva `idle` segundos sin ninguna pestaña de la app abierta."""
    while True:
        time.sleep(3)
        with LOCK:
            n, since = SSE_CLIENTS, IDLE_SINCE
        if n == 0 and now() - since > idle:
            print("La app lleva un rato cerrada: el puente se apaga.", flush=True)
            analyzer_shutdown()
            os._exit(0)


class Handler(BaseHTTPRequestHandler):
    server_version = "PuenteRF/" + VERSION

    def cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Private-Network", "true")

    def reply(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def host_ok(self):
        """Solo se atiende a 127.0.0.1 / localhost: evita ataques de "DNS rebinding" desde webs externas."""
        h = (self.headers.get("Host") or "").lower()
        if h in (f"127.0.0.1:{PORT}", f"localhost:{PORT}", "127.0.0.1", "localhost"):
            return True
        self.reply({"error": "host no permitido"}, 403)
        return False

    def serve_app(self):
        try:
            html = open(os.path.join(HERE, "coordinador-rf.html"), encoding="utf-8").read()
        except OSError:
            body = "Pon coordinador-rf.html en la misma carpeta que puente-rf.py y recarga.".encode()
            self.send_response(404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        # La clave va dentro de la página: solo la puede leer quien la abre desde este ordenador.
        html = html.replace("<head>", f'<head>\n<meta name="puente-rf" content="{KEY}">', 1)
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def sse(self, gen):
        self.send_response(200)
        self.cors()
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        sse_enter()
        try:
            for item in gen:
                self.wfile.write(item.encode())
                self.wfile.flush()
        except OSError:
            return
        finally:
            sse_exit()

    def authorised(self):
        q = parse_qs(urlparse(self.path).query)
        if q.get("k", [""])[0] == KEY:
            return True
        self.reply({"error": "Clave incorrecta. Cópiala de la ventana del puente."}, 401)
        return False

    def do_OPTIONS(self):
        self.send_response(204)
        self.cors()
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if not self.host_ok():
            return
        if path in ("/", "/index.html"):
            return self.serve_app()
        if not self.authorised():
            return
        if path == "/status":
            self.reply(snapshot())
        elif path == "/events":
            def gen():
                while True:
                    yield "data: " + json.dumps(snapshot(), ensure_ascii=False) + "\n\n"
                    time.sleep(0.5)
            self.sse(gen())
        elif path == "/projects":
            self.reply(projects_list())
        elif path == "/projects/get":
            try:
                self.reply(project_get(parse_qs(urlparse(self.path).query).get("id", [""])[0]))
            except (ValueError, OSError) as e:
                self.reply({"error": str(e)}, 404)
        elif path == "/diagnostics":
            self.reply({"text": diagnostics()})
        elif path == "/interfaces":
            self.reply({"interfaces": interface_list()})
        elif path == "/update":
            self.reply(check_update())
        elif path == "/update/status":
            with UPD_LOCK:
                self.reply(dict(UPD_ST))
        elif path == "/serial/ports":
            ports = list_serial()
            if ports is None:
                self.reply({"ports": [], "error": "Falta el módulo pyserial. Instálalo con: pip3 install pyserial"})
            else:
                self.reply({"ports": ports})
        elif path == "/analyzer/events":
            q = queue.Queue(maxsize=20)
            with LOCK:
                AN_SUBS.append(q)
                first = dict(AN_STATE, type="status")

            def gen():
                yield "data: " + json.dumps(first, ensure_ascii=False) + "\n\n"
                while True:
                    try:
                        yield "data: " + json.dumps(q.get(timeout=5), ensure_ascii=False) + "\n\n"
                    except queue.Empty:
                        yield ": sigue vivo\n\n"
            try:
                self.sse(gen())
            finally:
                with LOCK:
                    if q in AN_SUBS:
                        AN_SUBS.remove(q)
        else:
            self.reply({"error": "ruta desconocida"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        if not self.host_ok() or not self.authorised():
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(min(n, MAX_BODY)) or b"null")
        except ValueError:
            return self.reply({"error": "JSON no válido"}, 400)
        try:
            if path == "/devices":
                set_devices(body)
                return self.reply(snapshot())
            if path == "/discover":
                b = body if isinstance(body, dict) else {}
                return self.reply(discover(str(b.get("ranges", "")), str(b.get("iface", ""))))
            if path == "/analyzer":
                analyzer_cmd(body if isinstance(body, dict) else {})
                return self.reply({"ok": True})
            if path == "/projects/save":
                return self.reply({"ok": True, "dir": project_save(body if isinstance(body, dict) else {})})
            if path == "/projects/delete":
                project_delete(str((body or {}).get("id") or ""))
                return self.reply({"ok": True})
            if path == "/reveal":
                kind = (body or {}).get("kind")
                reveal(user_dir({"projects": "Proyectos", "informes": "Informes", "registros": "Registros", "imagenes": "Imágenes"}.get(kind, "Proyectos")))
                return self.reply({"ok": True})
            if path == "/files":
                return self.reply({"ok": True, "path": save_file(body.get("kind"), body.get("name"), body.get("content"), bool(body.get("open")))})
            if path == "/notify":
                notify(body.get("title"), body.get("text"))
                return self.reply({"ok": True})
            if path == "/update/install":
                update_install()
                return self.reply({"ok": True})
            if path == "/open":
                open_url(body.get("url"))
                return self.reply({"ok": True})
            if path == "/frequency":
                did, ch, khz = body.get("id"), int(body.get("ch")), int(body.get("khz"))
                if not 30000 <= khz <= 2000000:
                    raise ValueError("frecuencia fuera de rango")
                with LOCK:
                    d = DEVICES.get(did)
                if not d or not d.online:
                    raise ValueError("el receptor no está conectado")
                d.set_frequency(ch, khz)
                return self.reply({"ok": True})
        except (ValueError, TypeError, AttributeError, KeyError, OSError) as e:
            return self.reply({"error": str(e)}, 400)
        self.reply({"error": "ruta desconocida"}, 404)

    def log_message(self, *args):
        pass


# ---------------------------------------------------------------------------------------------
# Receptores simulados (--demo)
# ---------------------------------------------------------------------------------------------
def demo_shure(port):
    """ULXD4Q de mentira: 4 canales, SAMPLE periódico, interferencia de vez en cuando en el canal 3."""
    st = {n: {"FREQUENCY": f, "CHAN_NAME": nm, "BATT_CHARGE": b, "BATT_BARS": b // 20, "BATT_RUN_TIME": b * 6,
              "TX_TYPE": "ULXD2", "AUDIO_MUTE": "OFF", "RF_INT_DET": "NONE", "TX_MUTE_STATUS": "OFF"}
          for n, f, nm, b in ((1, 540100, "VOZ 1", 90), (2, 541500, "VOZ 2", 64), (3, 543300, "CORO", 35), (4, 546025, "PRESENT", 12))}

    def client(conn):
        rate, last, buf = 0, 0, ""
        conn.settimeout(0.1)
        try:
            while True:
                try:
                    d = conn.recv(4096)
                    if not d:
                        return
                    buf += d.decode("latin-1")
                except socket.timeout:
                    pass
                out = []
                for msg in re.findall(r"<([^>]*)>", buf):
                    t = msg.split()
                    if t[:1] == ["GET"] and t[-1] == "MODEL":
                        out.append("< REP MODEL {ULXD4Q                          } >")
                    elif t[:1] == ["GET"] and t[-1] == "ALL":
                        for n, s in st.items():
                            for k, v in s.items():
                                v = f"{{{v:<8}}}" if k == "CHAN_NAME" else (f"{v:06d}" if k == "FREQUENCY" else
                                                                          f"{v:03d}" if k in ("BATT_CHARGE", "BATT_BARS") else
                                                                          f"{v:05d}" if k == "BATT_RUN_TIME" else v)
                                out.append(f"< REP {n} {k} {v} >")
                    elif t[:1] == ["SET"] and "METER_RATE" in t:
                        rate = int(t[-1]) / 1000
                        out.append(f"< REP {t[1]} METER_RATE {t[-1]} >")
                    elif t[:1] == ["SET"] and "FREQUENCY" in t and t[1] in "1234":
                        st[int(t[1])]["FREQUENCY"] = int(t[-1])
                        out.append(f"< REP {t[1]} FREQUENCY {int(t[-1]):06d} >")
                buf = buf[buf.rfind(">") + 1:] if ">" in buf else buf
                if rate and now() - last >= rate:
                    last = now()
                    cyc = int(now()) % 60
                    newint = "CRITICAL" if 20 <= cyc < 26 else "NONE"
                    if st[3]["RF_INT_DET"] != newint:
                        st[3]["RF_INT_DET"] = newint
                        out.append(f"< REP 3 RF_INT_DET {newint} >")
                    for n in st:
                        rf = 128 - 55 + random.randint(-6, 6) - (25 if n == 3 and newint == "CRITICAL" else 0)
                        out.append(f"< SAMPLE {n} ALL {random.choice(['AX', 'XB'])} {rf:03d} {random.randint(10, 45):03d} >")
                if out:
                    conn.sendall("".join(out).encode("ascii"))
        except OSError:
            return
        finally:
            conn.close()

    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen(4)

    def accept():
        while True:
            c, _ = srv.accept()
            threading.Thread(target=client, args=(c,), daemon=True).start()
    threading.Thread(target=accept, daemon=True).start()


def demo_ssc(port):
    """EW-DX EM 2 de mentira: responde a suscripciones SSC y a cambios de frecuencia."""
    st = {1: {"frequency": 552300, "name": "GTR", "gauge": 80, "life": 420, "type": "SK"},
          2: {"frequency": 556750, "name": "BAJO", "gauge": 18, "life": 70, "type": "SK"}}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", port))
    sock.settimeout(0.2)
    clients = {}

    def loop():
        last = 0
        while True:
            try:
                data, addr = sock.recvfrom(65535)
                obj = json.loads(data)
                if "osc" in obj:
                    clients[addr] = now()
                    sock.sendto(data, addr)  # eco de la suscripción, como hace el equipo
                elif "device" in obj:
                    sock.sendto(json.dumps({"device": {"identity": {"product": "EW-DX EM 2"}, "name": "EM2-DEMO"}}).encode(), addr)
                else:
                    for k, v in obj.items():
                        if re.fullmatch(r"rx[12]", k) and isinstance(v.get("frequency"), int):
                            st[int(k[2])]["frequency"] = v["frequency"]
                            sock.sendto(json.dumps({k: {"frequency": v["frequency"]}}).encode(), addr)
            except (socket.timeout, ValueError, KeyError, AttributeError):
                pass
            if now() - last >= 1:
                last = now()
                for addr in [a for a, t in clients.items() if now() - t < 40]:
                    for n, s in st.items():
                        low = s["gauge"] < 20
                        msgs = [{"m": {f"rx{n}": {"rssi": round(-60 + random.uniform(-5, 5), 1), "rsqi": random.randint(80, 100),
                                                  "divi": random.choice([1, 2]), "af": round(random.uniform(-40, -12), 1)}}},
                                {f"rx{n}": {"frequency": s["frequency"], "name": s["name"], "mute": False, "warnings": []}},
                                {"mates": {f"tx{n}": {"battery": {"gauge": s["gauge"], "lifetime": s["life"]},
                                                      "warnings": ["LowBattery"] if low else [], "type": s["type"], "mute": False}}}]
                        for m in msgs:
                            sock.sendto(json.dumps(m).encode(), addr)
    threading.Thread(target=loop, daemon=True).start()


# ---------------------------------------------------------------------------------------------
# ---------------------------------------------------------------------------------------------
# Ventana de la app
# ---------------------------------------------------------------------------------------------
def copy_clipboard(text):
    """Copia al portapapeles del sistema (macOS: pbcopy). Devuelve si se ha podido."""
    if sys.platform != "darwin":
        return False
    try:
        subprocess.run(["pbcopy"], input=text.encode("utf-8"), timeout=5, check=True)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


class WindowApi:
    """Lo único que la página puede pedir a la ventana: poner el nombre del proyecto en el título."""

    def titulo(self, nombre):
        import webview
        if webview.windows:
            n = str(nombre or "").strip()[:80]
            webview.windows[0].set_title("Coordinador RF" + (f" — {n}" if n else ""))


def project_menu(webview_module=None, menu_module=None):
    """Menú «Proyecto» de la barra de menús de macOS. Cada opción llama a una función de la página (menuProyecto,
    menuTexto, menuCargar). Copiar y cargar archivos se hacen aquí porque el portapapeles y el selector de archivos del
    sistema no se pueden usar desde la página sin un gesto del usuario."""
    wv = webview_module
    if wv is None:
        import webview as wv
    if menu_module is None:
        from webview import menu as menu_module
    Menu, Action, Sep = menu_module.Menu, menu_module.MenuAction, menu_module.MenuSeparator

    def js(code):
        return wv.windows[0].evaluate_js(code) if wv.windows else None

    def acc(nombre):
        return lambda: js(f"menuProyecto({json.dumps(nombre)})")

    def copiar(kind, ok_msg):
        def f():
            txt = js(f"menuTexto({json.dumps(kind)})")
            if isinstance(txt, str) and txt:
                ok = copy_clipboard(txt)
                js(f"toast({json.dumps(ok_msg if ok else 'No se ha podido copiar al portapapeles.')})")
        return f

    def abrir(tipos, funcion):
        def f():
            if not wv.windows:
                return
            dialog = wv.FileDialog.OPEN if hasattr(wv, "FileDialog") else wv.OPEN_DIALOG
            res = wv.windows[0].create_file_dialog(dialog, allow_multiple=False, file_types=(tipos, "Todos los archivos (*.*)"))
            if not res:
                return
            path = res[0] if isinstance(res, (list, tuple)) else res
            try:
                if os.path.getsize(path) > MAX_BODY:
                    raise OSError("archivo demasiado grande")
                with open(path, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError as e:
                js(f"toast({json.dumps('No se ha podido leer el archivo: ' + str(e))})")
                return
            js(f"{funcion}({json.dumps(os.path.basename(path))},{json.dumps(text)})")
        return f

    def exportar_wwb():
        """El menú pide a la página el show de Wireless Workbench y lo guarda donde elija el usuario."""
        if not wv.windows:
            return
        res = js("menuShwExport()")
        if not isinstance(res, dict) or not res.get("ok"):
            js(f"toast({json.dumps((res or {}).get('msg') if isinstance(res, dict) else 'No se ha podido exportar.')})")
            return
        dialog = wv.FileDialog.SAVE if hasattr(wv, "FileDialog") else wv.SAVE_DIALOG
        dest = wv.windows[0].create_file_dialog(dialog, save_filename=res.get("name") or "Coordinador RF.shw",
                                                file_types=("Show de Wireless Workbench (*.shw)",))
        if not dest:
            return
        path = dest[0] if isinstance(dest, (list, tuple)) else dest
        if not str(path).lower().endswith(".shw"):
            path = str(path) + ".shw"
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(res["text"])
        except OSError as e:
            js(f"toast({json.dumps('No se ha podido guardar el archivo: ' + str(e))})")
            return
        js(f"toast({json.dumps(res.get('msg', 'Exportado.'))})")

    cargar = abrir("Proyectos (*.json;*.txt)", "menuCargar")
    importar_wwb = abrir("Show de Wireless Workbench (*.shw;*.xml)", "menuShw")

    return [Menu("Proyecto", [
        Action("Cambiar de proyecto…", acc("cambiar")),
        Sep(),
        Action("Nuevo proyecto…", acc("nuevo")),
        Action("Duplicar proyecto…", acc("duplicar")),
        Action("Renombrar proyecto…", acc("renombrar")),
        Action("Borrar proyecto…", acc("borrar")),
        Action("Vaciar este proyecto…", acc("vaciar")),
        Sep(),
        Action("Abrir carpeta de proyectos", acc("carpeta")),
        Action("Cargar archivo como proyecto nuevo…", cargar),
        Action("Importar show de Wireless Workbench…", importar_wwb),
        Action("Exportar para Wireless Workbench…", exportar_wwb),
        Action("Pegar proyecto copiado…", acc("pegar")),
        Sep(),
        Action("Revisar proyecto…", acc("revisar")),
        Action("Informe imprimible / PDF…", acc("informe")),
        Action("Copiar lista de frecuencias (CSV)", copiar("csv", "Lista copiada. Pégala en una hoja de cálculo.")),
        Action("Copiar proyecto", copiar("proyecto", "Proyecto copiado. Guárdalo en un archivo .json para cargarlo después.")),
    ])]


def monitor_menu(webview_module=None, menu_module=None):
    """Menú «Monitor» de la barra de menús de macOS: avisos y registro del monitor (llaman a menuMonitor de la página).
    El menú nativo no tiene casillas, así que cada opción de aviso se alterna y la página dice cómo ha quedado."""
    wv = webview_module
    if wv is None:
        import webview as wv
    if menu_module is None:
        from webview import menu as menu_module
    Menu, Action, Sep = menu_module.Menu, menu_module.MenuAction, menu_module.MenuSeparator

    def acc(nombre):
        return lambda: wv.windows[0].evaluate_js(f"menuMonitor({json.dumps(nombre)})") if wv.windows else None

    return [Menu("Monitor", [
        Action("Activar o desactivar el aviso sonoro", acc("sonido")),
        Action("Activar o desactivar la notificación del sistema", acc("notif")),
        Action("Probar avisos", acc("probar")),
        Sep(),
        Action("Exportar registro de eventos…", acc("registro")),
        Action("Borrar registro de eventos", acc("borrar")),
    ])]


def apply_bundle_info(info):
    """Nombre y versión que macOS muestra en la barra de menús y en «Acerca de Coordinador RF» (PyInstaller deja la versión en 0.0.0)."""
    info["CFBundleName"] = "Coordinador RF"
    info["CFBundleDisplayName"] = "Coordinador RF"
    info["CFBundleShortVersionString"] = VERSION
    info["CFBundleVersion"] = VERSION
    info["NSHumanReadableCopyright"] = "Coordinación de frecuencias RF · github.com/" + UPDATE_REPO


def run_window(url):
    """Abre la app en una ventana propia con pywebview (en Mac, el motor de Safari).
    Bloquea hasta que se cierra la ventana. Devuelve False si no se puede abrir."""
    try:
        import webview
    except Exception:
        return False
    try:
        if sys.platform == "darwin":  # nombre en la barra de menús en vez de "Python"
            try:
                from Foundation import NSBundle
                info = NSBundle.mainBundle().infoDictionary()
                if info is not None:
                    apply_bundle_info(info)
            except Exception:
                pass
        kw = {"private_mode": False}  # sin esto, pywebview borra los datos guardados al cerrar
        api = None
        import inspect
        menu_ok = "menu" in inspect.signature(webview.start).parameters  # pywebview 4.1 o posterior
        if sys.platform == "darwin" and menu_ok:  # en Mac todo lo de «Proyecto» va en la barra de menús (la página oculta lo suyo con ?ventana=1)
            try:
                kw["menu"] = project_menu() + monitor_menu()
                api = WindowApi()
                url = url + "/?ventana=1"
            except Exception as e:
                print(f"No se ha podido crear el menú Proyecto ({e}): se usan los controles de la ventana.", flush=True)
                kw.pop("menu", None)
        webview.create_window("Coordinador RF", url, js_api=api, width=1440, height=920, min_size=(960, 640))
        icon = os.path.join(HERE, "icono.png")
        if os.path.isfile(icon):
            kw["icon"] = icon
        try:
            webview.start(**kw)
        except TypeError:  # versiones antiguas de pywebview
            webview.start(private_mode=False)
        return True
    except Exception as e:
        print(f"No se ha podido abrir la ventana propia ({e}). Se usa el navegador.", flush=True)
        return False


def open_app_window(url):
    """Sin pywebview: ventana de aplicación de Chrome o Edge (sin barra de navegador) o, si no hay, el navegador."""
    try:
        if sys.platform == "darwin":
            for app in ("Google Chrome", "Microsoft Edge", "Brave Browser", "Chromium"):
                if any(os.path.isdir(os.path.join(d, app + ".app")) for d in ("/Applications", os.path.expanduser("~/Applications"))):
                    subprocess.Popen(["open", "-na", app, "--args", f"--app={url}"])
                    return
        elif os.name == "nt":
            subprocess.Popen(f'start "" msedge --app={url}', shell=True)  # Edge viene con Windows
            return
        else:
            for b in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge"):
                if shutil.which(b):
                    subprocess.Popen([b, f"--app={url}"])
                    return
    except OSError:
        pass
    webbrowser.open(url)


def main():
    global KEY, PORT, IDLE_SINCE
    ap = argparse.ArgumentParser(description="Puente de telemetría para Coordinador RF")
    ap.add_argument("--puerto", type=int, default=8765, help="puerto local del puente (8765)")
    ap.add_argument("--demo", action="store_true", help="crear receptores simulados para probar")
    ap.add_argument("--sin-navegador", action="store_true", help="no abrir la app en el navegador al arrancar")
    ap.add_argument("--ventana", action="store_true", help="abrir la app en su propia ventana en vez del navegador")
    ap.add_argument("--auto-cerrar", type=int, default=0, metavar="SEGUNDOS",
                    help="apagarse cuando pasen estos segundos sin la app abierta en ningún navegador")
    if FROZEN and len(sys.argv) == 1:
        sys.argv.append("--ventana")  # como app: doble clic y se abre su ventana
    a = ap.parse_args()
    if FROZEN or sys.stdout is None or sys.stderr is None:
        # Sin consola (app de ventana): los mensajes van a un registro en vez de perderse o romper print().
        try:
            log = open(os.path.join(config_dir(), "CoordinadorRF.log"), "a", encoding="utf-8", buffering=1)
            sys.stdout = sys.stderr = log
            print(f"=== {time.strftime('%Y-%m-%d %H:%M:%S')} ===", flush=True)
        except OSError:
            pass
    url = f"http://127.0.0.1:{a.puerto}"
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", a.puerto), Handler)
    except OSError as e:
        if e.errno != errno.EADDRINUSE:
            raise
        # El puerto está ocupado: si es otro puente ya en marcha, basta con abrir la app.
        try:
            with urllib.request.urlopen(url + "/", timeout=2) as r:
                ours = b'name="puente-rf"' in r.read(4096)
        except OSError:
            ours = False
        if ours:
            print(f"El puente ya estaba en marcha. Abriendo {url}")
            if a.ventana:
                if not run_window(url):
                    open_app_window(url)
            elif not a.sin_navegador:
                webbrowser.open(url)
            sys.exit(0)
        print(f"El puerto {a.puerto} lo está usando otro programa. Prueba con: python3 puente-rf.py --puerto 8766")
        sys.exit(1)
    KEY = load_key()
    try:
        os.remove(os.path.join(config_dir(), "actualizacion", "descarga.zip"))  # resto de una actualización anterior
    except OSError:
        pass
    PORT = a.puerto
    if a.demo:
        demo_shure(22020)
        demo_ssc(4545)
        set_devices([{"id": "demo-ulxd", "kind": "shure", "host": "127.0.0.1", "port": 22020, "name": "ULXD4Q demo"},
                     {"id": "demo-ewdx", "kind": "ssc", "host": "127.0.0.1", "port": 4545, "name": "EW-DX EM 2 demo"}])
    srv.daemon_threads = True
    print(f"Puente RF {VERSION} en marcha.")
    print(f"  Abre la app en el navegador:  http://127.0.0.1:{a.puerto}")
    print(f"  Clave (solo si abres la app desde el archivo): {KEY}")
    print("  Analizador USB: " + ("disponible" if serial else "falta pyserial (pip3 install pyserial)"))
    if a.demo:
        print("Modo demo: ULXD4Q simulado (4 canales) y EW-DX EM 2 simulado (2 canales).")
    if a.ventana:
        # El servidor va en segundo plano: la ventana necesita el hilo principal.
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        if run_window(url):
            print("Ventana cerrada: el puente se apaga.", flush=True)
            analyzer_shutdown()
            os._exit(0)
        open_app_window(url)
        a.auto_cerrar = a.auto_cerrar or 90
        IDLE_SINCE = now() + 30
        threading.Thread(target=watchdog, args=(a.auto_cerrar,), daemon=True).start()
        print(f"Se apagará solo {a.auto_cerrar} s después de cerrar la app.", flush=True)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            print("\nPuente detenido.")
            analyzer_shutdown()
        return
    if a.auto_cerrar > 0:
        print(f"Se apagará solo {a.auto_cerrar} s después de cerrar la app en el navegador.")
    else:
        print("Deja esta ventana abierta mientras uses la app. Para salir, ciérrala o pulsa Ctrl+C.")
    if not a.sin_navegador:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    if a.auto_cerrar > 0:
        IDLE_SINCE = now() + min(30, a.auto_cerrar)  # margen para que el navegador abra la app
        threading.Thread(target=watchdog, args=(a.auto_cerrar,), daemon=True).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nPuente detenido.")
        analyzer_shutdown()


def run_ad600_console():
    """Reentrada: el motor del AD600 lanza su consola como `<este programa> -u ad600_console.py …`. En la app
    empaquetada ese programa soy yo, así que en vez de abrir la app se ejecuta la consola."""
    d = os.path.join(HERE, "ad600")
    if os.path.isdir(d):
        sys.path.insert(0, d)
    sys.argv = sys.argv[2:]  # [ad600_console.py, segundos, fichero de órdenes, registro]
    if sys.stdout is None:
        sys.stdout = os.fdopen(1, "w", buffering=1, encoding="utf-8", errors="replace")
    ppid = os.getppid()

    def guard():  # si el puente muere sin despedirse, cerrar la sesión limpiamente en vez de dejarla colgada
        while True:
            time.sleep(1.0)
            if os.getppid() != ppid:
                os.kill(os.getpid(), signal.SIGINT)
                return
    threading.Thread(target=guard, daemon=True).start()
    import ad600_console
    ad600_console.main()


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "-u" and os.path.basename(sys.argv[2]) == "ad600_console.py":
        run_ad600_console()
    else:
        main()
