"""Consola falsa del AD600: misma salida que ad600_console.py con AD600_EMIT_FRAMES=1. Solo para pruebas."""
import os, sys, time, struct, base64, random, math
secs = float(sys.argv[1]); cmd = sys.argv[2]; log = sys.argv[3]
comp = int(os.environ.get("AD600_RT_COMPRESSION", "36"))
a = int(os.environ.get("AD600_SCAN_START_KHZ", "470000")); b = int(os.environ.get("AD600_SCAN_STOP_KHZ", "1000000"))
mask = int(os.environ.get("AD600_CURVE_SELECT", "126"))
print("fake console: comp=%d range=%d-%d mask=%d" % (comp, a, b, mask), flush=True)
time.sleep(1.5)
print("OWNERSHIP CLAIMED", flush=True); print("SCAN-READY gate open", flush=True); print("CONNECTED", flush=True)
lo = (a - 174000) // 25; hi = (b - 174000) // 25
n = max(1, (hi - lo) // comp)
CARR = {520125: -45, 531350: -58, 545000: -70}   # kHz: portadoras de prueba
def level(i, curve):
    f = 174000 + (lo + i * comp) * 25
    v = -95 + random.uniform(-3, 3) - (0 if curve == 1 else 4)
    for c, l in CARR.items():
        v = max(v, l - 12 * abs(f - c) / (comp * 25) + random.uniform(-1, 1))
    return v
t0 = time.time(); sent_quit = False
while time.time() - t0 < secs:
    try:
        if "quit" in open(cmd).read():
            print("clean disconnect sent", flush=True); sys.exit(0)
    except FileNotFoundError:
        pass
    for curve in range(1, 7):
        if not mask & (1 << curve): continue
        for k0 in range(0, n, 150):
            k1 = min(n, k0 + 150)
            amps = [int(round(level(i, curve) * 10)) for i in range(k0, k1)]
            raw = b"".join(struct.pack(">h", x) for x in amps)
            print("FRAME %d %d %d %s" % (curve, lo + k0 * comp, lo + (k1 - 1) * comp, base64.b64encode(raw).decode()), flush=True)
    time.sleep(0.4)
