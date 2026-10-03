"""Pruebas de extremo a extremo de Coordinador RF: puente real + interfaz en Chromium (Playwright).
Cada bloque arranca su propio puente con una carpeta de usuario temporal, así que no se afectan entre sí.
Uso:  python3 tests/e2e.py [bloque ...]      (sin argumentos, todos).   PW_CHROMIUM=/ruta/al/chromium si hace falta."""
import contextlib, functools, glob, hashlib, http.server, importlib.util, json, os, re, shutil, socket, subprocess, sys, tempfile, threading, time, urllib.request
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    print(("  ok    " if ok else "  FALLO ") + name + (f"  [{detail}]" if detail and not ok else ""), flush=True)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.contextmanager
def bridge(kind="demo", env=None):
    home, port = tempfile.mkdtemp(prefix="crf-"), free_port()
    log = open(os.path.join(home, "bridge.log"), "w")
    cmd = [sys.executable, os.path.join(ROOT, "puente-rf.py"), "--sin-navegador", "--puerto", str(port)] + (["--demo"] if kind == "demo" else [])
    if kind == "ad600":
        cmd = [sys.executable, os.path.join(ROOT, "tests", "ad600_harness.py"), str(port)]
    pr = subprocess.Popen(cmd, env=dict(os.environ, HOME=home, AD600_ENGINE_SCRATCH=os.path.join(home, "ad600"), **(env or {})), stdout=log, stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            urllib.request.urlopen(url + "/", timeout=1).read(10)
            break
        except OSError:
            time.sleep(0.25)
    try:
        yield {"url": url, "home": home, "docs": os.path.join(home, "Documents", "Coordinador RF"), "log": os.path.join(home, "bridge.log")}
    finally:
        pr.terminate()
        try:
            pr.wait(10)
        except subprocess.TimeoutExpired:
            pr.kill()


class Browser:
    def __init__(self):
        self.pw = sync_playwright().start()
        exe = os.environ.get("PW_CHROMIUM")
        self.b = self.pw.chromium.launch(executable_path=exe, args=["--no-sandbox"]) if exe else self.pw.chromium.launch(args=["--no-sandbox"])
        self.errors = []

    def page(self, url, wait=1200):
        ctx = self.b.new_context(viewport={"width": 1500, "height": 950})
        pg = ctx.new_page()
        pg.on("pageerror", lambda e: self.errors.append(str(e)))
        pg.on("dialog", lambda d: d.accept())
        pg.goto(url)
        pg.wait_for_timeout(wait)
        return ctx, pg

    def close(self):
        self.b.close()
        self.pw.stop()


def coordinate(pg):
    pg.click("#btnCoord")
    pg.wait_for_function("()=>!busy", timeout=40000)
    pg.wait_for_timeout(400)


def ask(pg, fn, text):
    """Ejecuta una función de la app que pide un nombre en el diálogo propio y lo contesta."""
    pg.evaluate(f"()=>{{{fn}()}}")
    pg.wait_for_selector("#nameModal:not([hidden])")
    if text is not None:
        pg.fill("#nmText", text)
    pg.press("#nmText", "Enter")
    pg.wait_for_timeout(400)


# ---------------------------------------------------------------------------------------------
def t_coordinacion(B):
    print("Coordinación, nombres, deshacer")
    with bridge() as br:
        ctx, pg = B.page(br["url"])
        check("arranca con los dos grupos de ejemplo y sin frecuencias", pg.evaluate("()=>state.groups.length===2&&state.groups.every(g=>g.freqs.every(e=>e.f==null))"))
        coordinate(pg)
        n = pg.evaluate("()=>state.groups.reduce((n,g)=>n+g.freqs.filter(e=>e.f!=null).length,0)")
        check("coordinar asigna los 12 canales", n == 12, n)
        check("sin problemas de compatibilidad", pg.evaluate("()=>analysis.C.length===12&&analysis.C.every(c=>!c.issues.length)"))
        fs = pg.evaluate("()=>state.groups[0].freqs.map(e=>e.f).sort((a,b)=>a-b)")
        check("separación mínima entre portadoras del primer grupo (350 kHz)", all(b - a >= 350 for a, b in zip(fs, fs[1:])), fs)
        # nombres y deshacer / rehacer (el historial vive en la sesión de la página)
        pg.fill("input.chname >> nth=0", "Voz principal")
        pg.press("input.chname >> nth=0", "Enter")
        pg.evaluate("()=>{document.activeElement&&document.activeElement.blur()}")
        pg.click("#undoBtn")
        check("deshacer quita el último cambio (el nombre)", pg.evaluate("()=>!state.groups[0].freqs[0].name"))
        pg.click("#undoBtn")
        check("deshacer otra vez vacía la coordinación", pg.evaluate("()=>state.groups.every(g=>g.freqs.every(e=>e.f==null))"))
        pg.click("#redoBtn")
        pg.click("#redoBtn")
        check("rehacer lo devuelve todo", pg.evaluate("()=>state.groups[0].freqs[0].name")=="Voz principal" and pg.evaluate("()=>state.groups[0].freqs[0].f!=null"))
        pg.keyboard.press("Control+z")
        check("Ctrl+Z deshace", pg.evaluate("()=>!state.groups[0].freqs[0].name"))
        pg.keyboard.press("Control+y")
        check("Ctrl+Y rehace", pg.evaluate("()=>state.groups[0].freqs[0].name")=="Voz principal")
        pg.click('[data-tab="mon"]')
        pg.wait_for_timeout(500)
        check("el nombre del canal aparece en el monitor", pg.eval_on_selector_all(".tile .th span", "els=>els.some(e=>e.textContent==='Voz principal')"))
        pg.click('[data-tab="coord"]')
        pg.reload()
        pg.wait_for_timeout(1000)
        check("el nombre sobrevive a recargar", pg.evaluate("()=>state.groups[0].freqs[0].name")=="Voz principal")
        ctx.close()


def t_proyectos(B):
    print("Proyectos y copia en disco")
    with bridge() as br:
        legacy = {"opts": {"occBw": 100}, "tv": [], "excl": "", "groups": [{"name": "Grupo antiguo", "qty": 2, "min": 470000, "max": 520000, "step": 25, "preset": "analog",
                                                                           "freqs": [{"f": 480000, "locked": True}, {"f": 490000, "locked": True}]}]}
        ctx = B.b.new_context(viewport={"width": 1500, "height": 950})
        ctx.add_init_script("if(!localStorage.getItem('coordinador-rf.index.v1')) localStorage.setItem('coordinador-rf.v1', %s)" % json.dumps(json.dumps(legacy)))
        pg = ctx.new_page()
        pg.on("pageerror", lambda e: B.errors.append(str(e)))
        pg.on("dialog", lambda d: d.accept())
        pg.goto(br["url"])
        pg.wait_for_timeout(1500)
        check("el proyecto único de versiones anteriores se conserva como «Proyecto 1»",
              pg.evaluate("()=>projIdx.list.length===1&&state.groups[0].name==='Grupo antiguo'&&state.groups[0].freqs[0].f===480000"))
        ask(pg, "projNew", "Boda García")
        check("proyecto nuevo con nombre", pg.evaluate("()=>curProject().name==='Boda García'&&state.groups[0].name==='ULX-D G51'"))
        first = pg.evaluate("()=>projIdx.list[0].id")
        pg.select_option("#projSel", first)
        pg.wait_for_timeout(400)
        check("volver al primero recupera sus datos", pg.evaluate("()=>state.groups[0].name==='Grupo antiguo'"))
        ask(pg, "projDup", None)
        ask(pg, "projRen", "Copia B")
        check("duplicar y renombrar", pg.evaluate("()=>projIdx.list.map(p=>p.name).join('|')")=="Proyecto 1|Boda García|Copia B")
        pg.wait_for_timeout(3500)
        files = glob.glob(os.path.join(br["docs"], "Proyectos", "*.json"))
        check("hay un archivo en disco por proyecto", len(files) == 3, [os.path.basename(f) for f in files])
        key = pg.evaluate("()=>state.net.key")
        check("los archivos no llevan la clave del puente", all(key not in open(f, encoding="utf-8").read() for f in files))
        ctx.close()
        ctx, pg = B.page(br["url"], wait=3000)   # navegador vacío: recupera del disco
        names = pg.evaluate("()=>projIdx.list.map(p=>p.name).sort().join('|')")
        check("en un navegador vacío se recuperan los proyectos del disco (sin dejar uno vacío de sobra)", names == "Boda García|Copia B|Proyecto 1", names)
        n0 = len(glob.glob(os.path.join(br["docs"], "Proyectos", "*.json")))
        pg.evaluate("()=>{projDel()}")
        pg.wait_for_timeout(1500)
        n1 = len(glob.glob(os.path.join(br["docs"], "Proyectos", "*.json")))
        check("borrar un proyecto también lo borra del disco", n1 == n0 - 1, (n0, n1))
        pg.evaluate("()=>importProject({groups:[{name:'Importado',qty:1,min:470000,max:500000,step:25,freqs:[{f:480000}]}]},'Pegado')")
        check("importar crea un proyecto nuevo", pg.evaluate("()=>curProject().name==='Pegado'&&state.groups[0].name==='Importado'"))
        ctx.close()


def t_receptores(B):
    print("Receptores de red (demo), importar, asignar y enviar")
    with bridge() as br:
        ctx, pg = B.page(br["url"])
        pg.click('[data-tab="mon"]')
        pg.wait_for_function("()=>net.snap&&net.snap.devices.length===2&&net.snap.devices.every(d=>d.online&&Object.keys(d.channels).length)", timeout=20000)
        ewdx = pg.evaluate("()=>net.snap.devices.find(d=>/EW-DX/.test(d.model)).id")
        pg.evaluate(f"()=>importDevice('{ewdx}')")
        g = pg.evaluate("()=>{const g=state.groups[state.groups.length-1];return {n:g.freqs.length,s:g.model&&g.model.series,locked:g.freqs.every(e=>e.locked&&e.rx),names:g.freqs.map(e=>e.name)}}")
        check("importar un EW-DX crea un grupo reconocido con sus 2 canales bloqueados y enlazados", g["n"] == 2 and g["s"] == "senn-ewdx" and g["locked"], g)
        check("importar trae el nombre de cada canal del receptor", g["names"][0] == "GTR", g["names"])
        pg.evaluate(f"()=>importDevice('{ewdx}')")
        check("importar dos veces el mismo receptor avisa y no duplica", "ya está" in pg.inner_text("#toast"))
        pg.click('[data-tab="coord"]')
        coordinate(pg)
        gid = pg.evaluate("()=>state.groups.find(g=>g.model&&g.model.series==='shure-ulxd'&&!g.freqs.some(e=>e.rx)).id")
        pg.evaluate(f"()=>assignGroup('{gid}')")
        pg.wait_for_timeout(300)
        check("asignar enlaza canales virtuales con canales libres de receptores compatibles", pg.evaluate(f"()=>state.groups.find(g=>g.id==='{gid}').freqs.filter(e=>e.rx).length")>=1)
        pg.evaluate(f"()=>pushToReceivers('{gid}')")
        pg.wait_for_timeout(2500)
        check("enviar programa la frecuencia en el receptor", pg.evaluate(f"()=>state.groups.find(g=>g.id==='{gid}').freqs.filter(e=>e.rx).every(e=>rxFreq(rxChan(e).c)===e.f)"))
        ctx.close()


def t_alertas_informe(B):
    print("Alertas, registro, informe, diagnóstico y aviso de versión")
    with bridge() as br:
        ctx, pg = B.page(br["url"])
        posts = []
        pg.on("request", lambda r: posts.append(r.url.split("?")[0].rsplit("/", 1)[-1]) if r.method == "POST" else None)
        pg.evaluate("()=>{window.__b=0;beep=()=>{window.__b++}}")
        pg.click('[data-tab="mon"]')
        pg.evaluate("()=>{logEvent('x','bad',true)}")
        check("con los avisos apagados no suena ni notifica", pg.evaluate("()=>window.__b")==0 and "notify" not in posts)
        pg.check("#alSound"); pg.check("#alNotify")
        pg.wait_for_timeout(300)
        b0 = pg.evaluate("()=>window.__b")
        pg.evaluate("()=>{logEvent('Canal 1: sin portadora','warn',true)}")
        pg.evaluate("()=>{logEvent('Canal 1: sin portadora','warn',true)}")
        pg.evaluate("()=>{logEvent('Canal 2: sin portadora','warn',true)}")
        pg.wait_for_timeout(2000)
        check("una alarma repetida no vuelve a sonar y el sonido tiene pausa", pg.evaluate("()=>window.__b") - b0 == 1)
        n = posts.count("notify")
        check("varias alarmas seguidas salen en una sola notificación", n == 2, posts)   # 1 al activar + 1 agrupada
        pg.click("#logExport")
        pg.wait_for_timeout(800)
        regs = glob.glob(os.path.join(br["docs"], "Registros", "*.csv"))
        check("el registro se exporta a CSV", len(regs) == 1 and "Alarma" in open(regs[0], encoding="utf-8").read())
        # informe
        pg.click('[data-tab="coord"]')
        coordinate(pg)
        pg.fill("input.chname >> nth=0", "Voz <principal>")
        pg.press("input.chname >> nth=0", "Enter")
        pg.click("#reportBtn")
        pg.wait_for_timeout(1500)
        inf = glob.glob(os.path.join(br["docs"], "Informes", "*.html"))
        html = open(inf[0], encoding="utf-8").read() if inf else ""
        check("el informe se guarda como HTML con el nombre escapado", "Voz &lt;principal>" in html or "Voz &lt;principal&gt;" in html)
        check("el informe incluye mapa, grupos y condiciones", "<svg" in html and "ULX-D G51" in html and "Condiciones del cálculo" in html)
        # diagnóstico
        pg.click('[data-tab="live"]')
        pg.click("#lvDiag")
        pg.wait_for_selector("#diagModal:not([hidden])")
        t = pg.input_value("#dgText")
        key = pg.evaluate("()=>state.net.key")
        check("el diagnóstico trae versión y estado, y no la clave", "puente 1." in t and "Proyecto" in t and key not in t)
        ctx.close()
        # aviso de versión (respuesta simulada)
        ctx = B.b.new_context()
        pg = ctx.new_page()
        pg.route("**/update?*", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps({"current": "1.0", "latest": "9.9", "url": "https://github.com/pabloor/COORDINADOR-RF/releases/tag/v9.9", "newer": True, "notes": ""})))
        pg.goto(br["url"])
        pg.wait_for_timeout(1800)
        check("aparece el aviso de versión nueva", pg.is_visible("#updBar"))
        pg.click("#updNo")
        pg.reload()
        pg.wait_for_timeout(1800)
        check("«Ahora no» lo oculta hasta la siguiente versión", not pg.is_visible("#updBar"))
        ctx.close()


def t_ad600(B):
    print("AD600 simulado (descubrimiento y consola falsos)")
    with bridge("ad600") as br:
        ctx, pg = B.page(br["url"])
        pg.click('[data-tab="live"]')
        pg.select_option("#lvSrc", "ad600")
        vis = lambda i: pg.is_visible(i)
        check("la opción AD600 muestra resolución, antenas e IP y oculta puerto y puntos", vis("#lvAdRbw") and vis("#lvAdAnt") and vis("#lvAdHost") and not vis("#lvPort") and not vis("#lvPts"))
        pg.fill("#lvA", "470"); pg.press("#lvA", "Tab"); pg.fill("#lvB", "560"); pg.press("#lvB", "Tab")
        pg.select_option("#lvAdRbw", "350")
        pg.click("#lvConn")
        pg.wait_for_function("()=>live.f&&live.f.length>20", timeout=25000)
        pg.wait_for_timeout(1200)
        info = pg.evaluate("()=>{const f=live.f,l=live.l;let im=0;for(let i=1;i<l.length;i++)if(l[i]>l[im])im=i;return {n:f.length,a:f[0],peak:f[im]}}")
        check("llegan barridos con la rejilla pedida", info["n"] == 257 and info["a"] == 470000, info)
        check("la portadora simulada (520,125 MHz) aparece en su sitio", abs(info["peak"] - 520125) <= 400, info)
        pg.select_option("#lvAdRbw", "100")
        pg.wait_for_function("()=>live.f&&live.f.length>400", timeout=30000)
        check("cambiar la resolución reconecta con la rejilla nueva", pg.evaluate("()=>live.f.length")==900)
        pg.select_option("#lvAdAnt", "2")
        pg.wait_for_timeout(4000)
        check("cambiar de antena sigue entregando barridos", "antena B" in pg.inner_text("#lvStatus"), pg.inner_text("#lvStatus")[:120])
        pg.click("#lvConn")
        pg.wait_for_timeout(5000)
        ctx.close()
        log = open(br["log"], encoding="utf-8", errors="replace").read()
        check("al desconectar se envía la despedida limpia al equipo", log.count("clean disconnect sent") >= 3, log[-300:])
        ctx, pg = B.page(br["url"])
        pg.click('[data-tab="live"]')
        pg.select_option("#lvSrc", "ad600")
        pg.fill("#lvA", "100"); pg.press("#lvA", "Tab")
        pg.click("#lvConn")
        pg.wait_for_timeout(1500)
        check("un rango fuera de 174–2000 MHz se rechaza con un mensaje claro", "174" in pg.inner_text("#lvStatus"), pg.inner_text("#lvStatus"))
        ctx.close()



def _puente():
    spec = importlib.util.spec_from_file_location("puente", os.path.join(ROOT, "puente-rf.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@contextlib.contextmanager
def static_server(directory):
    class Q(http.server.ThreadingHTTPServer):
        def handle_error(self, request, client_address):  # clientes que cortan a propósito: sin ruido
            pass
    H = functools.partial(http.server.SimpleHTTPRequestHandler, directory=directory)
    H.log_message = lambda *a, **k: None
    srv = Q(("127.0.0.1", free_port()), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield srv.server_address[1]
    finally:
        srv.shutdown()


def t_actualizacion(B):
    print("Actualización con un clic (descarga verificada, script de relevo, interfaz)")
    m = _puente()
    # ---- descarga: solo se acepta lo que coincide con la huella publicada
    srv_dir = tempfile.mkdtemp()
    data = os.urandom(400_000)
    open(os.path.join(srv_dir, "a.zip"), "wb").write(data)
    good = "sha256:" + hashlib.sha256(data).hexdigest()
    with static_server(srv_dir) as port:
        url = f"http://127.0.0.1:{port}/a.zip"
        def dl(asset, test_mode=True):
            (os.environ.__setitem__ if test_mode else os.environ.pop)(*((m.UPDATE_TEST, "x") if test_mode else (m.UPDATE_TEST, None)))
            d = tempfile.mkdtemp()
            try:
                return m.update_download(asset, d), d
            except ValueError as e:
                return str(e), d
        r, d = dl({"url": url, "digest": good, "size": len(data)})
        check("descarga con la huella correcta", os.path.isfile(os.path.join(d, "descarga.zip")), r)
        for nombre, asset, modo in [
            ("huella falsa", {"url": url, "digest": "sha256:" + "0" * 64, "size": len(data)}, True),
            ("tamaño anunciado menor que el real", {"url": url, "digest": good, "size": 1000}, True),
            ("tamaño anunciado mayor que el real", {"url": url, "digest": good, "size": len(data) + 5000}, True),
            ("sin huella", {"url": url, "digest": "", "size": len(data)}, True),
            ("dirección que no es de la release", {"url": "https://evil.example/a.zip", "digest": good, "size": len(data)}, False),
            ("http fuera del modo de pruebas", {"url": url, "digest": good, "size": len(data)}, False)]:
            r, d = dl(asset, modo)
            check(f"se rechaza: {nombre}", not os.path.exists(os.path.join(d, "descarga.zip")) and not os.path.isfile(r), r)
        os.environ.pop(m.UPDATE_TEST, None)
    # ---- script de relevo con «apps» de mentira y un opener falso
    def relevo(new_ok, exit_wait="5", health="4", pid_alive=False, args=()):
        t = tempfile.mkdtemp(prefix="upd-")
        port = free_port()
        old, new, bak, log = f"{t}/Aplic/Coordinador RF.app", f"{t}/stage/Coordinador RF.app", f"{t}/bak", f"{t}/log.txt"
        for d, mark, ok in ((old, "viejo", True), (new, "nuevo", new_ok)):
            os.makedirs(d)
            open(f"{d}/id", "w").write(mark)
            if ok:
                open(f"{d}/ok", "w").write("1")
        op = f"{t}/opener.sh"
        open(op, "w").write(f'#!/bin/bash\necho "abre $@" >> "{t}/aperturas.txt"\nif [ -f "$1/ok" ]; then (cd "$1" && nohup python3 -m http.server {port} --bind 127.0.0.1 >/dev/null 2>&1 &); fi\n')
        os.chmod(op, 0o755)
        open(f"{t}/s.sh", "w").write(m.UPDATE_SCRIPT)
        app = subprocess.Popen(["sleep", "1000" if pid_alive else "1"])
        threading.Thread(target=app.wait, daemon=True).start()
        subprocess.run(["bash", f"{t}/s.sh", str(app.pid), old, new, bak, str(port), log, *args], timeout=60,
                       env=dict(os.environ, CRF_OPENER=op, CRF_EXIT_WAIT=exit_wait, CRF_HEALTH_WAIT=health))
        app.kill()
        subprocess.run(["pkill", "-f", f"http.server {port}"])
        rd = lambda f: open(f).read() if os.path.exists(f) else None
        return {"app": rd(f"{old}/id"), "copia": rd(f"{bak}/previous.app/id"), "stage": os.path.exists(t + "/stage"),
                "aperturas": (rd(f"{t}/aperturas.txt") or "").strip().splitlines(), "log": rd(log) or ""}
    a = relevo(True, args=("--sin-navegador", "--puerto", "8799"))
    check("relevo: la versión nueva queda instalada y la anterior guardada", a["app"] == "nuevo" and a["copia"] == "viejo" and not a["stage"], a)
    check("relevo: se reabre con los mismos argumentos", a["aperturas"] and a["aperturas"][0].endswith("--args --sin-navegador --puerto 8799"), a["aperturas"])
    bb = relevo(False)
    check("relevo: si la nueva no responde se vuelve a la anterior y se reabre", bb["app"] == "viejo" and len(bb["aperturas"]) == 2 and "se vuelve a la anterior" in bb["log"], bb)
    c = relevo(True, exit_wait="3", pid_alive=True)
    check("relevo: si la app no se cierra, no se toca nada", c["app"] == "viejo" and not c["aperturas"] and not c["stage"], c)
    # ---- API del puente con una «release» local
    rel = tempfile.mkdtemp()
    asset = m.mac_asset_name()
    open(os.path.join(rel, asset), "wb").write(data)
    with static_server(rel) as port:
        json.dump({"tag_name": "v99.0", "html_url": "https://github.com/pabloor/COORDINADOR-RF/releases/tag/v99.0", "body": "",
                   "assets": [{"name": asset, "browser_download_url": f"http://127.0.0.1:{port}/{asset}", "digest": good, "size": len(data)}]},
                  open(os.path.join(rel, "latest.json"), "w"))
        with bridge(env={m.UPDATE_TEST: f"http://127.0.0.1:{port}/latest.json"}) as br:
            ctx, pg = B.page(br["url"])
            key = pg.evaluate("()=>state.net.key")
            j = json.load(urllib.request.urlopen(f"{br['url']}/update?k={key}"))
            check("el puente ve la versión nueva y el archivo que le corresponde", j.get("newer") and (j.get("asset") or {}).get("name") == asset, j)
            check("fuera de la app de Mac instalada no ofrece actualizar solo", j.get("canInstall") is False)
            try:
                urllib.request.urlopen(urllib.request.Request(f"{br['url']}/update/install?k={key}", data=b"{}", headers={"Content-Type": "application/json"}))
                rej = ""
            except urllib.error.HTTPError as e:
                rej = json.load(e).get("error", "")
            check("pedir la instalación fuera de la app instalada se rechaza con un mensaje", "Mac instalada" in rej, rej)
            ctx.close()
    # ---- interfaz con respuestas simuladas
    with bridge() as br:
        def pagina(upd, status_seq=None, install=None):
            ctx = B.b.new_context(viewport={"width": 1500, "height": 900})
            pg = ctx.new_page()
            pg.on("pageerror", lambda e: B.errors.append(str(e)))
            pg.dialogs = []
            pg.on("dialog", lambda d: (pg.dialogs.append(d.message), d.accept()))
            J = lambda r, o, code=200: r.fulfill(status=code, content_type="application/json", body=json.dumps(o))
            pg.route("**/update?*", lambda r: J(r, upd))
            seq = list(status_seq or [])
            pg.route("**/update/status?*", lambda r: (J(r, seq.pop(0)) if seq else r.abort()))
            pg.route("**/update/install?*", lambda r: J(r, install or {"ok": True}, 400 if install else 200))
            pg.goto(br["url"])
            pg.wait_for_timeout(1800)
            return ctx, pg
        base = {"current": "1.5", "latest": "9.9", "url": "https://github.com/pabloor/COORDINADOR-RF/releases/tag/v9.9", "newer": True, "notes": "", "asset": {}}
        ctx, pg = pagina(dict(base, canInstall=True, installReason=""))
        check("si se puede, el aviso ofrece «Actualizar ahora»", pg.is_visible("#updInstall") and pg.inner_text("#updGo") == "Ver novedades")
        ctx.close()
        why = "Muévela a Aplicaciones, ábrela desde ahí y vuelve a intentarlo."
        ctx, pg = pagina(dict(base, canInstall=False, installReason=why))
        check("si no se puede, explica por qué y ofrece la descarga a mano", not pg.is_visible("#updInstall") and why in pg.inner_text("#updTxt") and pg.inner_text("#updGo") == "Ver novedades y descargar")
        ctx.close()
        seq = [{"state": "descargando", "pct": 30, "msg": "Descargando… 30 %", "error": ""}, {"state": "instalando", "pct": 100, "msg": "Instalando…", "error": ""},
               {"state": "reiniciando", "pct": 100, "msg": "Reiniciando Coordinador RF…", "error": ""}]
        ctx, pg = pagina(dict(base, canInstall=True, installReason=""), seq)
        pg.evaluate("()=>{window.__t=[];new MutationObserver(()=>window.__t.push(document.querySelector('#updTxt').textContent)).observe(document.querySelector('#updTxt'),{childList:true,characterData:true,subtree:true})}")
        pg.click("#updInstall")
        pg.wait_for_timeout(4000)
        t = " | ".join(pg.evaluate("()=>[...new Set(window.__t)]"))
        check("pide confirmación y muestra descarga, instalación y reinicio", pg.dialogs and "9.9" in pg.dialogs[0] and "Descargando… 30 %" in t and "Instalando" in t and "Reiniciando" in t, t)
        ctx.close()
        err = "la descarga no coincide con la huella SHA-256 publicada: no se instala"
        ctx, pg = pagina(dict(base, canInstall=True, installReason=""), [{"state": "error", "pct": 0, "msg": "", "error": err}])
        pg.click("#updInstall")
        pg.wait_for_timeout(1800)
        check("ante un error lo cuenta y deja la descarga manual", err in pg.inner_text("#updTxt") and pg.is_visible("#updGo") and pg.is_enabled("#updInstall"))
        ctx.close()


BLOQUES = {"coordinacion": t_coordinacion, "proyectos": t_proyectos, "receptores": t_receptores, "alertas": t_alertas_informe, "ad600": t_ad600, "actualizacion": t_actualizacion}

if __name__ == "__main__":
    want = sys.argv[1:] or list(BLOQUES)
    B = Browser()
    try:
        for k in want:
            BLOQUES[k](B)
    finally:
        B.close()
    check("sin errores de JavaScript en la interfaz", not B.errors, B.errors[:3])
    bad = [n for n, ok in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(bad)} de {len(RESULTS)} comprobaciones correctas" + (f"; fallan: {bad}" if bad else ""))
    sys.exit(1 if bad else 0)
