"""Arranca el puente REAL con el descubrimiento del AD600 simulado y una consola falsa que emite el mismo formato de
datos (FRAME ...) que la original. Sirve para probar el adaptador, la interfaz y el apagado limpio sin un equipo.
No prueba el protocolo real con el AD600. Uso: python3 tests/ad600_harness.py <puerto>"""
import importlib.util, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ["AD600_CONSOLE_PY"] = os.path.join(ROOT, "tests", "fake_ad600_console.py")
sys.path.insert(0, os.path.join(ROOT, "ad600"))
import ad600_discovery as d
d.list_interfaces = lambda: [{"name": "en0", "ipv4": "192.168.1.10", "netmask": "255.255.255.0", "mac": "aa:bb:cc:dd:ee:ff"}]
d.discover = lambda iface, timeout=4, **k: {"device_cid": "ddac0650000011dda000000eddcccccc", "device_ip": "192.168.1.101",
                                           "device_port": 57383, "model": "AD600", "name": "AD600 prueba", "iface": "en0"}
spec = importlib.util.spec_from_file_location("puente", os.path.join(ROOT, "puente-rf.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
sys.argv = ["puente-rf.py", "--sin-navegador", "--puerto", sys.argv[1]]
m.main()
