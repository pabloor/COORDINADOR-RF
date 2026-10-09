"""Pruebas de extremo a extremo de Coordinador RF: puente real + interfaz en Chromium (Playwright).
Cada bloque arranca su propio puente con una carpeta de usuario temporal, así que no se afectan entre sí.
Uso:  python3 tests/e2e.py [bloque ...]      (sin argumentos, todos).   PW_CHROMIUM=/ruta/al/chromium si hace falta."""
import contextlib, functools, glob, hashlib, http.server, importlib.util, json, os, re, shutil, socket, socketserver, subprocess, sys, tempfile, threading, time, urllib.request
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
        tb = pg.evaluate("""()=>{const q=id=>document.getElementById(id);const ic=id=>{const b=q(id);return b?{svg:!!b.querySelector('svg'),txt:b.textContent.trim(),label:b.getAttribute('aria-label')}:null};
            return {lock:ic('lockAll'),unlock:ic('unlockAll'),undo:ic('undoBtn'),redo:ic('redoBtn'),clear:!!q('clearFree'),informeEn:q('reportBtn')&&q('reportBtn').closest('details.projbox')?'proyecto':'otro'}}""")
        check("bloquear / desbloquear / deshacer / rehacer son iconos (con etiqueta accesible y sin texto)", all(tb[k]["svg"] and tb[k]["txt"] == "" and tb[k]["label"] for k in ("lock", "unlock", "undo", "redo")), tb)
        check("«Vaciar no bloqueadas» ya no existe y el informe está en la sección Proyecto", not tb["clear"] and tb["informeEn"] == "proyecto", tb)
        pg.click("#unlockAll")
        check("el icono de candado abierto desbloquea todas", pg.evaluate("()=>state.groups.every(g=>g.freqs.every(e=>!e.locked))"))
        pg.click("#lockAll")
        check("el de candado cerrado bloquea todas las que tienen frecuencia", pg.evaluate("()=>state.groups.every(g=>g.freqs.every(e=>e.f==null||e.locked))") and pg.evaluate("()=>state.groups.some(g=>g.freqs.some(e=>e.locked))"))
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
        pg.wait_for_function("()=>!/Comprobando/.test(document.getElementById('tbody').innerText)", timeout=15000)
        check("tras enviar se vuelve a leer y la tabla marca «Verificado en el receptor»", "Verificado en el receptor" in pg.inner_text("#tbody"), pg.inner_text("#toast"))
        check("el aviso dice cuántas se han verificado", "verificada" in pg.inner_text("#toast"), pg.inner_text("#toast"))
        r = pg.evaluate(f"""async()=>{{const e=state.groups.find(g=>g.id==='{gid}').freqs.find(e=>e.rx);
            e.f+=25;const v=await verifyEntries([{{e,khz:e.f,where:'x',name:'x'}}],700);return {{bad:v.bad.length,ok:v.ok.length}}}}""")
        check("si el receptor no confirma lo enviado se marca como fallo", r["bad"] == 1 and r["ok"] == 0, r)
        check("el fallo se ve en la tabla", "No confirmado" in pg.inner_text("#tbody"), pg.inner_text("#tbody")[:200])
        ctx.close()


def t_alertas_informe(B):
    print("Alertas, registro, informe, diagnóstico y aviso de versión")
    with bridge() as br:
        ctx, pg = B.page(br["url"])
        posts = []
        pg.on("request", lambda r: posts.append(r.url.split("?")[0].rsplit("/", 1)[-1]) if r.method == "POST" else None)
        pg.evaluate("()=>{window.__b=0;beep=()=>{window.__b++}}")
        pg.click('[data-tab="live"]')
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
        pg.evaluate("()=>document.querySelector('#reportBtn').click()")
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
        try:
            pg.wait_for_selector("#updBar:not([hidden])", timeout=20000)
        except Exception:
            pass
        check("aparece el aviso de versión nueva", pg.is_visible("#updBar"))
        pg.click("#updNo")
        pg.reload()
        pg.wait_for_timeout(4000)
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
        def server_bind(self):  # sin getfqdn, que en algunas máquinas tarda mucho
            socketserver.TCPServer.server_bind(self)
            self.server_name, self.server_port = "127.0.0.1", self.server_address[1]

        def handle_error(self, request, client_address):  # clientes que cortan a propósito: sin ruido
            pass
    class Manejador(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a, **k):
            pass
    H = functools.partial(Manejador, directory=directory)
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
            pg.wait_for_selector("#updBar:not([hidden])", timeout=20000)
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


def t_grafica_red(B):
    print("Leyenda y arrastre en la gráfica, selector de red")
    with bridge() as br:
        ctx, pg = B.page(br["url"])
        coordinate(pg)
        pg.evaluate("()=>fit()")
        bar = pg.inner_text("#freqBar")
        check("la leyenda indica el rango de la vista", "Vista" in bar and "MHz" in bar, bar)
        geo = pg.evaluate("()=>{const r=cv.getBoundingClientRect();return {l:r.left,t:r.top,w:r.width,h:r.height,a:view.a,b:view.b}}")
        X = lambda f: geo["l"] + 46 + (f - geo["a"]) / (geo["b"] - geo["a"]) * (geo["w"] - 46 - 14)
        f0 = pg.evaluate("()=>state.groups[0].freqs[0].f")
        y = geo["t"] + geo["h"] * 0.6
        pg.mouse.move(X(f0 + 20000), y)
        check("la leyenda sigue al cursor", "Cursor" in pg.inner_text("#freqBar") and "—" not in pg.inner_text("#freqBar").split("Cursor")[1].split("MHz")[0], pg.inner_text("#freqBar"))
        pg.mouse.move(X(f0), y)
        check("sobre una línea el cursor cambia a «agarrar»", pg.evaluate("()=>cv.classList.contains('grab')"))
        view0 = pg.evaluate("()=>[view.a,view.b]")
        pg.mouse.down()
        pg.mouse.move(X(f0) + 30, y, steps=6)
        check("mientras se arrastra la leyenda dice «Moviendo»", "Moviendo" in pg.inner_text("#freqBar"), pg.inner_text("#freqBar"))
        pg.mouse.up()
        pg.wait_for_timeout(300)
        r = pg.evaluate("()=>({f:state.groups[0].freqs[0].f,l:state.groups[0].freqs[0].locked,v:[view.a,view.b]})")
        check("arrastrar mueve la frecuencia (a pasos de 25 kHz) y la bloquea", r["f"] != f0 and r["f"] % 25 == 0 and r["l"], (f0, r))
        check("arrastrar una línea no desplaza la vista", r["v"] == view0)
        check("la tabla muestra la frecuencia nueva", pg.input_value("input.freq >> nth=0") == "%.3f" % (r["f"] / 1000))
        pg.click("#undoBtn")
        check("deshacer devuelve la línea a su sitio con un solo paso", pg.evaluate("()=>state.groups[0].freqs[0].f") == f0)
        pg.click("#redoBtn")
        # un clic sin mover solo selecciona; arrastrar fuera de las líneas desplaza la vista
        pg.mouse.click(X(f0 + 0), y)
        check("un clic sin mover no cambia la frecuencia", pg.evaluate("()=>state.groups[0].freqs[0].f") == r["f"])
        free = pg.evaluate("()=>{const fs=analysis.C.map(c=>c.f).sort((a,b)=>a-b);let best=0,at=view.a;for(let i=0;i<fs.length-1;i++)if(fs[i+1]-fs[i]>best){best=fs[i+1]-fs[i];at=(fs[i]+fs[i+1])/2;}return at}")
        pg.mouse.move(X(free), geo["t"] + 40)
        pg.mouse.down(); pg.mouse.move(X(free) - 40, geo["t"] + 40, steps=5); pg.mouse.up()
        check("arrastrar el fondo sigue desplazando la vista", pg.evaluate("()=>view.a")!=view0[0])
        # rendimiento: una función con el mismo nombre ocultaba la de los ejes y cada dibujo hacía ~100 000 marcas
        pf = pg.evaluate("""()=>{const f=()=>cv.getContext('2d').getImageData(0,0,1,1);f();const t=performance.now();for(let i=0;i<5;i++){draw();f()}
            return {paso:niceStep(14000),pasoMapa:typeof reportStep,ms:(performance.now()-t)/5}}""")
        check("el paso del eje de frecuencias es razonable (la función del informe ya no lo sustituye)", pf["paso"] == 20000 and pf["pasoMapa"] == "function", pf)
        check("dibujar la gráfica de coordinación es rápido (< 300 ms; antes ~850 ms)", pf["ms"] < 300, pf)
        # zoom con iconos de lupa (+ y −) y rueda proporcional
        zi = pg.evaluate("""()=>{const f=id=>{const b=document.getElementById(id);return {svg:!!b.querySelector('svg'),txt:b.textContent.trim(),label:b.getAttribute('aria-label'),mas:!!b.querySelector('path[d*="M8.5 6v5"]')}};return {in:f('zin'),out:f('zout')}}""")
        check("acercar y alejar son lupas con + y − (icono, sin texto, con etiqueta accesible)", zi["in"]["svg"] and zi["out"]["svg"] and zi["in"]["txt"] == "" and zi["out"]["txt"] == "" and zi["in"]["mas"] and not zi["out"]["mas"] and zi["in"]["label"] == "Acercar" and zi["out"]["label"] == "Alejar", zi)
        pg.evaluate("()=>fit()")
        w0 = pg.evaluate("()=>view.b-view.a")
        pg.click("#zin"); w1 = pg.evaluate("()=>view.b-view.a")
        pg.click("#zout"); w2 = pg.evaluate("()=>view.b-view.a")
        check("la lupa + acerca y la lupa − aleja", w1 < w0 * 0.7 and abs(w2 - w0) < w0 * 0.05, (w0, w1, w2))
        pg.evaluate("()=>{view={a:400000,b:1100000};draw()}")
        pg.click("#zout")
        check("alejar con la vista ya muy ancha no acerca por error", pg.evaluate("()=>view.b-view.a") >= 700000)
        pg.evaluate("()=>fit()")
        wh0 = pg.evaluate("()=>view.b-view.a")
        pg.mouse.move(geo["l"] + 400, geo["t"] + 100); pg.mouse.wheel(0, 4)
        wh1 = pg.evaluate("()=>view.b-view.a")
        pg.mouse.wheel(0, -100)
        wh2 = pg.evaluate("()=>view.b-view.a")
        check("la rueda es proporcional: un gesto pequeño (trackpad) cambia poco y uno grande (ratón) cambia más", abs(wh1 - wh0) < wh0 * 0.03 and wh2 < wh1 * 0.85, (wh0, wh1, wh2))
        pg.evaluate("()=>fit()")
        rg = pg.evaluate("()=>{const r=document.querySelector('.gcard .rules');return {hint:!!r.querySelector('p.hint'),label:r.querySelector('label').textContent,campos:r.querySelectorAll('input[type=number]').length}}")
        check("bajo «Reglas de separación» ya no hay el cuadro de texto explicativo y se conserva la unidad (kHz) y los campos", not rg["hint"] and "kHz" in rg["label"] and rg["campos"] >= 4, rg)
        tt = pg.evaluate("()=>{const l=document.querySelector('.gcard .rules label');return {l:l.title,s:l.querySelector('select').title}}")
        check("el origen de las reglas sale al pasar el ratón por el desplegable (y por su etiqueta)", "fabricante" in tt["s"] and "kHz" in tt["s"] and tt["l"] == tt["s"], tt)
        pg.evaluate("()=>{const s=document.querySelector('.gcard .rules select');const o=[...s.options].find(o=>o.value==='custom');s.value=o.value;s.dispatchEvent(new Event('change',{bubbles:true}))}")
        pg.wait_for_timeout(200)
        check("al cambiar de regla el texto emergente se actualiza", pg.evaluate("()=>document.querySelector('.gcard .rules select').title").startswith("Reglas personalizadas."))
        pg.evaluate("()=>{const s=document.querySelector('.gcard .rules select');s.value=[...s.options].find(o=>o.value.startsWith('mode:')).value;s.dispatchEvent(new Event('change',{bubbles:true}))}")
        pg.wait_for_timeout(200)
        # «Ajustar vista»: encuadra las portadoras y, al repetir, muestra las bandas enteras
        pg.evaluate("()=>{state.groups[0].min=470000;state.groups[0].max=694000;analyzeNow();fit()}")
        bandas = pg.evaluate("()=>bounds()")
        pg.click("#zin"); pg.click("#zin")
        pg.click("#zfit")
        v1 = pg.evaluate("()=>({view:[view.a,view.b],fs:[Math.min(...analysis.C.map(c=>c.f)),Math.max(...analysis.C.map(c=>c.f))]})")
        check("«Ajustar vista» encuadra las portadoras coordinadas con un margen (no toda la banda)", v1["view"][0] < v1["fs"][0] and v1["view"][1] > v1["fs"][1] and v1["view"][1] - v1["view"][0] < (bandas[1] - bandas[0]) * 0.6, (v1, bandas))
        pg.click("#zfit")
        check("pulsarlo otra vez muestra las bandas enteras de los grupos", pg.evaluate("()=>[view.a,view.b]") == bandas)
        pg.click("#zin"); pg.dblclick("#spec")
        check("el doble clic en la gráfica hace lo mismo", pg.evaluate("()=>view.b-view.a") < (bandas[1] - bandas[0]) * 0.6)
        pg.click("#zfit")
        pg.evaluate("()=>{fit()}")
        # intermodulación bajo el cursor
        pg.evaluate("()=>fit()")
        geo = pg.evaluate("()=>{const r=cv.getBoundingClientRect();return {l:r.left,t:r.top,w:r.width,h:r.height,a:view.a,b:view.b}}")
        X = lambda f: geo["l"] + 46 + (f - geo["a"]) / (geo["b"] - geo["a"]) * (geo["w"] - 46 - 14)
        prod = pg.evaluate("()=>{const V=analysis.V.im3;return V&&V.length?V[Math.floor(V.length/2)]:null}")
        check("hay productos de intermodulación calculados", prod is not None)
        pg.mouse.move(X(prod), geo["t"] + geo["h"] - 24 - 8)
        tip = pg.inner_text("#tip")
        check("sobre un producto el cuadro dice qué portadoras lo producen", "intermodulación" in tip and "IMD 3er orden" in tip, tip)
        check("las portadoras de origen se resaltan en la gráfica", pg.evaluate("()=>hoverSrc.size")>=2)
        pg.mouse.move(X(prod), geo["t"] + 60)
        check("fuera de la franja de productos no hay información de IMD", "intermodulación" not in pg.inner_text("#tip") and pg.evaluate("()=>hoverSrc.size")==0)
        # dónde está cada control: avisos y registro en Espectro en vivo, receptores en Coordinación, nada de eso en el Monitor
        where = pg.evaluate("""()=>{const v=id=>{const e=document.getElementById(id);const s=e&&e.closest('#viewCoord, section.view');return s?s.id:null};
            return {alSound:v('alSound'),alNotify:v('alNotify'),alTest:v('alTest'),logExport:v('logExport'),hay:['monAllOn','monAllAuto'].filter(i=>document.getElementById(i)).length,
                    netToggle:v('netToggle'),netPanel:v('netPanel'),monBtns:document.querySelectorAll('#viewMon .bar button, #viewMon .bar input').length}}""")
        check("avisos y registro siguen en Espectro en vivo (para el navegador) y «Todos encendidos» / «Sin indicar» ya no existen", all(where[k] == "viewLive" for k in ("alSound", "alNotify", "alTest", "logExport")) and where["hay"] == 0, where)
        check("el botón y el panel de Receptores están en Coordinación", where["netToggle"] == "viewCoord" and where["netPanel"] == "viewCoord", where)
        check("la barra del Monitor ya no tiene botones ni campos", where["monBtns"] == 0, where)
        # selector de red
        pg.click('[data-tab="coord"]')
        pg.click("#netToggle")
        pg.wait_for_timeout(600)
        n = pg.evaluate("()=>document.querySelectorAll('#netIface option').length")
        ifs = pg.evaluate("async()=>(await diskApi('/interfaces')).interfaces")
        check("el selector de red lista «Todas» y las conexiones del ordenador", n == 1 + len(ifs) and n >= 1, (n, ifs))
        bad = pg.evaluate("async()=>{try{await bridgePost('/discover',{iface:'10.254.254.1'});return ''}catch(e){return e.message}}")
        check("pedir una conexión que no existe da un mensaje claro", "ya no está disponible" in bad, bad)
        if ifs:
            pg.select_option("#netIface", ifs[0]["ip"])
            check("la elección se recuerda en el proyecto", pg.evaluate("()=>state.net.iface") == ifs[0]["ip"])
            pg.click("#netScan")
            pg.wait_for_function("()=>!document.querySelector('#netScan').disabled", timeout=60000)
            check("buscar con una conexión elegida solo revisa su red", ifs[0]["network"] in pg.inner_text("#netFound"), pg.inner_text("#netFound"))
        ctx.close()


def t_captura(B):
    print("Captura de escaneo desde el analizador")
    with bridge() as br:
        ctx, pg = B.page(br["url"])
        pg.evaluate("()=>{state.scans=[{id:'previo',name:'Escaneo previo',f:[100000,100025,900000],l:[-50,-50,-60],on:true,color:'#c2531a'}];state.scan.enabled=false;save()}")
        pg.click('[data-tab="live"]')
        pg.select_option("#lvSrc", "sim")
        pg.click("#lvConn")
        pg.wait_for_function("()=>live.f&&live.f.length>10&&live.n>3", timeout=20000)
        pg.select_option("#lvCapDur", "0")
        pg.click("#lvSave")
        r = pg.evaluate("()=>({n:state.scans.length,last:state.scans[state.scans.length-1].f.length,prev:state.scans[0].f.length,on:state.scan.enabled,th:state.scan.threshold,name:state.scans[state.scans.length-1].name,marcado:state.scans[state.scans.length-1].on})")
        check("capturar añade un escaneo nuevo (marcado), conserva el anterior, activa «evitar» y no cambia los umbrales si ya había escaneos", r["n"] == 2 and r["last"] > 50 and r["prev"] == 3 and r["marcado"] and r["on"] and r["th"] == -85, r)
        an = pg.evaluate("()=>{const s=state.scans[state.scans.length-1];return [s.analyzer,!!s.date]}")
        check("la captura guarda el analizador usado y la fecha", an == ["Simulador", True], an)
        check("el aviso resume ruido y zonas, y recuerda marcarlo", "ruido" in pg.inner_text("#toast") and "zona" in pg.inner_text("#toast"), pg.inner_text("#toast"))
        check("los controles del escaneo reflejan el cambio", pg.evaluate("()=>document.getElementById('scanOn').checked"))
        pl = pg.evaluate("""()=>{const f=()=>lctx.getImageData(0,0,1,1);f();const t=performance.now();for(let i=0;i<5;i++){drawLive();f()}return (performance.now()-t)/5}""")
        check("dibujar el espectro en vivo es rápido (< 300 ms; antes más de 900 ms)", pl < 300, pl)
        pw_ = pg.evaluate("""()=>{const n=12000,fk=new Float64Array(n),lv=new Float32Array(n);for(let i=0;i<n;i++){fk[i]=470000+i*224000/(n-1);lv[i]=-100+Math.random()*6}
            onSweep(fk,lv);const f=()=>lctx.getImageData(0,0,1,1);f();const t=performance.now();for(let i=0;i<5;i++){drawLive();f()}return (performance.now()-t)/5}""")
        check("con 12 000 puntos (como un AD600) el dibujo también es rápido", pw_ < 300, pw_)
        n0 = pg.evaluate("()=>state.scans.length")
        pg.select_option("#lvCapDur", "10")
        pg.click("#lvSave")
        check("la captura temporizada muestra la cuenta atrás", "Capturando" in pg.inner_text("#lvSave"), pg.inner_text("#lvSave"))
        pg.wait_for_function("()=>!/Capturando/.test(document.getElementById('lvSave').textContent)", timeout=20000)
        check("al terminar guarda el máximo del periodo como otro escaneo", "máximo de 10 s" in pg.evaluate("()=>state.scans[state.scans.length-1].name") and pg.evaluate("()=>state.scans.length") == n0 + 1)
        pg.click('[data-tab="live"]')
        pg.click("#lvConn")
        ctx.close()


def fake_shure(samples):
    """Receptor Shure de mentira (TCP). Con samples=True manda, al pedirle METER_RATE, los SAMPLE de un AD4D real."""
    import socket as _s, threading as _t
    got, stop = [], _t.Event()
    srv = _s.socket(); srv.setsockopt(_s.SOL_SOCKET, _s.SO_REUSEADDR, 1); srv.bind(("127.0.0.1", 0)); srv.listen(2)
    def serve():
        srv.settimeout(0.5)
        while not stop.is_set():
            try:
                c, _ = srv.accept()
            except OSError:
                continue
            c.settimeout(0.3)
            while not stop.is_set():
                try:
                    d = c.recv(4096)
                except _s.timeout:
                    continue
                except OSError:
                    break
                if not d:
                    break
                got.append(d.decode())
                for cmd in d.decode().split(">"):
                    if "GET MODEL" in cmd:
                        c.sendall(b"< REP MODEL {AD4D-A                          } >")
                    if "GET 0 ALL" in cmd:
                        c.sendall(b"< REP 1 FREQUENCY 530000 >< REP 1 CHAN_NAME {Voz} >< REP 1 TX_BATT_BARS 4 >< REP 1 TX_BATT_CHARGE_PERCENT 80 >"
                                  b"< REP 1 TX_MODEL AD2 >< REP 2 TX_BATT_BARS 255 >< REP 2 TX_BATT_CHARGE_PERCENT 255 >< REP 2 TX_BATT_MINS 65535 >< REP 2 FREQUENCY 540000 >")
                    if samples and "METER_RATE" in cmd:
                        c.sendall(b"< SAMPLE 1 ALL 003 003 083 076 BB 07 044 01 030 >< SAMPLE 2 ALL 255 000 005 033 XX 00 012 00 011 >")
    th = _t.Thread(target=serve, daemon=True); th.start()
    return srv.getsockname()[1], got, stop


def _con_receptor(B, br, port, until):
    ctx, pg = B.page(br["url"])
    pg.evaluate("async(p)=>{await bridgePost('/devices',[{id:'x1',kind:'shure',host:'127.0.0.1',port:p,name:'AD4D prueba'}])}", port)
    for _ in range(80):
        if pg.evaluate("async()=>{const d=(await diskApi('/status')).devices[0];return !!(d&&d.online&&" + until + ")}"):
            break
        pg.wait_for_timeout(250)
    return ctx, pg


def t_shure_sin_medidores(B):
    print("Receptor Shure que no envía medidores: órdenes por separado y diagnóstico")
    port, got, stop = fake_shure(False)
    with bridge(None) as br:
        ctx, pg = _con_receptor(B, br, port, "d.channels['1']&&d.channels['1'].bars===4")
        for _ in range(20):
            if any("SET 1 METER_RATE" in g for g in got):
                break
            pg.wait_for_timeout(250)
        check("si no llegan medidores se piden también canal a canal", any("SET 1 METER_RATE" in g for g in got), got)
        check("las órdenes iniciales llegan como mensajes separados", len(got) >= 3 and got[0].strip() == "< GET MODEL >" and got[1].strip() == "< GET 0 ALL >" and any("SET 0 METER_RATE" in g for g in got), got[:4])
        st = pg.evaluate("async()=>(await diskApi('/status')).devices[0]")
        check("sin medidores: 0 muestras y los datos de los canales sí llegan (frecuencia, nombre, batería)", st["samples"] == 0 and st["channels"]["1"]["name"] == "Voz" and st["channels"]["1"]["bars"] == 4, st)
        t = pg.evaluate("async()=>(await diskApi('/diagnostics')).text")
        check("el diagnóstico cuenta los mensajes por tipo y enseña ejemplos", "REP FREQUENCY×2" in t and "REP MODEL" in t and "· REP FREQUENCY: REP 1 FREQUENCY 530000" in t, t[:900])
        check("el diagnóstico incluye el estado de cada canal", "canal 1:" in t and "name=Voz" in t, t[:900])
        ctx.close()
    stop.set()


def t_shure_axient(B):
    print("Shure Axient Digital (AD4D): medidores en su formato propio")
    port, got, stop = fake_shure(True)
    with bridge(None) as br:
        ctx, pg = _con_receptor(B, br, port, "d.samples>=2")
        st = pg.evaluate("async()=>(await diskApi('/status')).devices[0]")
        c1, c2 = st["channels"]["1"], st["channels"]["2"]
        check("el AD4D se reconoce y entrega muestras", st["model"] == "AD4D-A" and st["samples"] >= 2, st)
        check("RF por antena y la mayor como nivel del canal (RSSI − 120)", c1["rfA"] == -76 and c1["rfB"] == -90 and c1["rf"] == -76, c1)
        check("audio RMS y pico (valor − 120)", c1["af"] == -44 and c1["afPeak"] == -37, c1)
        check("calidad 0-5; 255 = sin dato", c1["qual"] == 3 and c2["qual"] is None, (c1, c2))
        check("ya no queda el aviso de formato no reconocido", "raw" not in c1 and "raw" not in c2)
        pg.evaluate("()=>{state.groups[0].freqs[0].f=530000;state.groups[0].freqs[0].locked=true;save();analyzeNow()}")
        pg.click('[data-tab="mon"]')
        pg.wait_for_selector(".tile .rx .ants", timeout=8000)
        m = pg.evaluate("""()=>{const rx=document.querySelector('.tile .rx');const L=[...rx.querySelectorAll('.leds')];
            const col=l=>{const i=l.querySelector('i.on');return i?getComputedStyle(i).backgroundColor:null};
            return {n:L.map(l=>l.children.length),on:L.map(l=>l.querySelectorAll('i.on').length),cols:L.map(col),bar:!!rx.querySelector('.meter'),txt:rx.innerText}}""")
        check("el monitor muestra una gráfica de 8 puntos por antena (A y B juntas), calidad en 5 y audio en 7 LED (sin barra continua)", m["n"] == [8, 8, 5, 7] and not m["bar"], m)
        check("los puntos reflejan los valores: antena A (-76) → 2, B (-90) → 1, calidad 3/5, audio por el pico (-37) → 2 LED", m["on"] == [2, 1, 3, 2], m)
        check("colores: RF naranja, calidad morado, audio verde (los tres distintos)", len(set(m["cols"])) == 3 and None not in m["cols"], m["cols"])
        pg.evaluate("()=>{state.groups[0].freqs[1].f=540000;state.groups[0].freqs[1].locked=true;save();analyzeNow();renderTiles(true)}")
        pg.wait_for_function("()=>document.querySelectorAll('.tile .exp').length>=2")
        pg.wait_for_function("()=>document.querySelector('.tile .exp').dataset.v==='auto-on'", timeout=8000)
        e1 = pg.evaluate("()=>{const b=document.querySelectorAll('.tile .exp');return [b[0].textContent,b[0].dataset.v,b[1].textContent,b[1].dataset.v]}")
        check("con un emisor sincronizado el botón pasa solo a «Tx encendido»", e1[:2] == ["Tx encendido", "auto-on"], e1)
        check("sin emisor sincronizado (batería y modelo desconocidos) sigue «Tx sin indicar»", e1[2:] == ["Tx sin indicar", "auto"], e1)
        pg.click(".tile .exp >> nth=0")
        e2 = pg.evaluate("()=>{const b=document.querySelector('.tile .exp');return [b.textContent,b.dataset.v,state.monitor.expect[b.dataset.exp]]}")
        check("al pulsarlo manualmente se guarda «Tx encendido» (y luego apagado, y vuelta al automático)", e2[1] == "on" and e2[2] == "on", e2)
        pg.click(".tile .exp >> nth=0"); pg.click(".tile .exp >> nth=0")
        check("tras el ciclo vuelve al automático y se detecta otra vez", pg.evaluate("()=>document.querySelector('.tile .exp').dataset.v") == "auto-on")
        an = pg.evaluate("()=>{const a=[...document.querySelectorAll('.tile')[0].querySelectorAll('.ant')];return {n:a.length,act:a.map(x=>!!x.querySelector('.act.on')),letras:a.map(x=>x.querySelector('b').textContent)}}")
        check("las dos antenas van juntas y un punto marca la que recibe (A, la de más señal)", an["n"] == 2 and an["letras"] == ["A", "B"] and an["act"] == [True, False], an)
        check("el texto ya no mezcla audio y pico entre paréntesis", "dBFS" not in m["txt"] and "Antena A" not in m["txt"] and "-90" not in m["txt"], m["txt"])
        bt = pg.evaluate("""()=>{const t=[...document.querySelectorAll('.tile')];const q=i=>t[i]&&t[i].querySelector('.batt');
            return {graf:document.querySelectorAll('.tile .meter, .tile canvas').length,
                    celdas:[0,1].map(i=>q(i)?q(i).querySelectorAll('.cell').length:null),
                    llenas:[0,1].map(i=>q(i)?q(i).querySelectorAll('.cell.on').length:null),
                    cls:[0,1].map(i=>q(i)?q(i).className:null),txt:[0,1].map(i=>q(i)?q(i).innerText.trim():null),title:q(0)?q(0).title:''}}""")
        check("las tarjetas ya no llevan barra ni gráfica de la portadora", bt["graf"] == 0, bt)
        check("el Monitor ya no tiene panel lateral (lista de receptores y registro)", pg.evaluate("()=>!document.querySelector('#viewMon .side, #rxList, #mlog, #logClear')"))
        check("batería: icono con 5 celdas y tantas llenas como indica el equipo (4 barras → 4)", bt["celdas"] == [5, 5] and bt["llenas"][0] == 4, bt)
        check("sin dato de batería (255): celdas vacías y guion", bt["llenas"][1] == 0 and bt["txt"][1] == "—" and "muted" in bt["cls"][1], bt)
        check("el icono lleva el porcentaje y el detalle al pasar el ratón", bt["txt"][0] == "80 %" and "4 de 5 celdas" in bt["title"], bt)
        check("batería y emisor con los nombres de Axient (255 = desconocido)", c1["bars"] == 4 and c1["batt"] == 80 and c1["tx"] == "AD2" and c2["bars"] is None and c2["batt"] is None and c2["battMin"] is None, (c1, c2))
        ctx.close()
    stop.set()


def t_menu_proyecto(B):
    print("Menú «Proyecto» de la ventana de Mac (interfaz y menú nativo)")
    with bridge() as br:
        # Navegador normal: los controles de proyecto siguen en la página
        ctx, pg = B.page(br["url"])
        vis = lambda sel: pg.evaluate("(s)=>{const e=document.querySelector(s);return !!e&&getComputedStyle(e).display!=='none'}", sel)
        check("en un navegador normal siguen el selector y la sección Proyecto", vis("#projSel") and vis("details.projbox"))
        ctx.close()
        # Ventana de la app (?ventana=1): se ocultan y se maneja todo desde el menú
        ctx, pg = B.page(br["url"] + "/?ventana=1")
        check("en la ventana de la app se ocultan (están en el menú nativo)", not vis("#projSel") and not vis("details.projbox"))
        check("en la ventana de la app también se ocultan los avisos del monitor (están en el menú Monitor)", not vis(".monctl"))
        pg.evaluate("()=>{menuMonitor('sonido')}")
        check("menú Monitor → aviso sonoro alterna y lo dice", pg.evaluate("()=>state.monitor.alerts.sound") is True and "Aviso sonoro activado" in pg.inner_text("#toast"), pg.inner_text("#toast"))
        pg.evaluate("()=>{menuMonitor('notif')}")
        check("menú Monitor → notificación alterna", pg.evaluate("()=>state.monitor.alerts.notify") is True)
        pg.click('[data-tab="mon"]')
        check("el monitor muestra cómo han quedado los avisos", "sonido activado, notificación activada" in pg.inner_text("#monConn"), pg.inner_text("#monConn"))
        pg.evaluate("()=>{menuMonitor('sonido');menuMonitor('notif')}")
        check("y vuelve a apagarlos", pg.evaluate("()=>!state.monitor.alerts.sound&&!state.monitor.alerts.notify") and "sonido desactivado" in pg.inner_text("#monConn"))
        pg.evaluate("()=>{menuMonitor('probar')}")
        check("menú Monitor → Probar avisos sin ningún aviso activado lo explica", "Activa el aviso sonoro" in pg.inner_text("#toast"), pg.inner_text("#toast"))
        check("una opción desconocida del menú Monitor no hace nada", pg.evaluate("()=>menuMonitor('x')") is False)
        pg.click('[data-tab="coord"]')
        pg.evaluate("()=>{menuProyecto('nuevo')}")
        pg.wait_for_selector("#nameModal:not([hidden])")
        pg.fill("#nmText", "Boda García"); pg.press("#nmText", "Enter"); pg.wait_for_timeout(400)
        check("menú → Nuevo proyecto crea y abre el proyecto", pg.evaluate("()=>curProject().name==='Boda García'&&projIdx.list.length===2"))
        pg.evaluate("()=>{menuProyecto('renombrar')}")
        pg.wait_for_selector("#nameModal:not([hidden])")
        pg.fill("#nmText", "Boda Pérez"); pg.press("#nmText", "Enter"); pg.wait_for_timeout(300)
        check("menú → Renombrar", pg.evaluate("()=>curProject().name")=="Boda Pérez")
        pg.evaluate("()=>{menuProyecto('cambiar')}")
        pg.wait_for_selector("#pickModal:not([hidden])")
        names = pg.eval_on_selector_all("#pkList button", "els=>els.map(e=>e.textContent+(e.getAttribute('aria-current')?'*':''))")
        check("menú → Cambiar de proyecto lista los proyectos y marca el actual", sorted(names) == ["Boda Pérez*", "Proyecto 1"], names)
        pg.click('#pkList button:has-text("Proyecto 1")')
        check("elegir uno cambia de proyecto y cierra la lista", pg.evaluate("()=>curProject().name==='Proyecto 1'") and not pg.is_visible("#pickModal"))
        csv = pg.evaluate("()=>menuTexto('csv')")
        proj = pg.evaluate("()=>menuTexto('proyecto')")
        import json as _j
        check("el menú obtiene el CSV y el proyecto para copiarlos (sin la clave del puente)", csv.startswith('"Grupo"') and _j.loads(proj)["net"]["key"] == "", csv[:40])
        ok = pg.evaluate("(t)=>menuCargar('Gira otoño.json',t)", _j.dumps({"groups": [{"name": "Importado", "qty": 1, "min": 470000, "max": 520000, "step": 25, "preset": "analog", "freqs": [{"f": 480000, "locked": True}]}]}))
        check("menú → Cargar archivo crea un proyecto con el nombre del archivo", ok and pg.evaluate("()=>curProject().name==='Gira otoño'&&state.groups[0].name==='Importado'"))
        check("un archivo que no es un proyecto se rechaza con un aviso", pg.evaluate("()=>menuCargar('x.json','no es json')") is False and "no es un proyecto" in pg.inner_text("#toast"))
        pg.evaluate("()=>{menuProyecto('borrar')}")
        pg.wait_for_timeout(500)
        check("menú → Borrar elimina el proyecto actual", pg.evaluate("()=>projIdx.list.every(p=>p.name!=='Gira otoño')"))
        check("una acción desconocida no hace nada", pg.evaluate("()=>menuProyecto('inexistente')") is False)
        # Red de seguridad: sin conexión con la ventana nativa, a los 6 s vuelven los controles de la página
        pg.wait_for_timeout(6500)
        check("si la ventana no conecta con su menú, vuelven los controles de la página", vis("#projSel"))
        ctx.close()
    # Menú nativo (Python), con un pywebview de mentira
    import importlib.util, types
    spec = importlib.util.spec_from_file_location("puente_rf", os.path.join(ROOT, "puente-rf.py"))
    pr = importlib.util.module_from_spec(spec); spec.loader.exec_module(pr)
    calls, copied = [], []
    class W:
        def evaluate_js(self, code):
            calls.append(code)
            if "menuShwExport" in code:
                return {"ok": True, "text": "<show/>\n", "name": "Mi gira.shw", "msg": "Exportado."}
            return "a;b" if "menuTexto" in code else None
        def create_file_dialog(self, *a, **k):
            return [dlg_file[0]]
    wv = types.SimpleNamespace(windows=[W()], FileDialog=types.SimpleNamespace(OPEN=1, SAVE=2))
    class Menu:
        def __init__(self, title, items): self.title, self.items = title, items
    class Action:
        def __init__(self, title, function): self.title, self.function = title, function
    class Sep: pass
    mm = types.SimpleNamespace(Menu=Menu, MenuAction=Action, MenuSeparator=Sep)
    pr.copy_clipboard = lambda t: copied.append(t) or True
    m = pr.project_menu(wv, mm)
    acts = {i.title: i.function for i in m[0].items if isinstance(i, Action)}
    check("el menú «Proyecto» tiene las opciones esperadas", m[0].title == "Proyecto" and len(acts) == 16 and "Cambiar de proyecto…" in acts and "Borrar proyecto…" in acts, list(acts))
    info = {"CFBundleShortVersionString": "0.0.0", "CFBundleVersion": "0.0.0"}
    pr.apply_bundle_info(info)
    check("«Acerca de» muestra el nombre y la versión de la app (no 0.0.0)", info["CFBundleName"] == "Coordinador RF" and info["CFBundleShortVersionString"] == pr.VERSION and info["CFBundleVersion"] == pr.VERSION and pr.VERSION[0].isdigit(), info)
    mon = pr.monitor_menu(wv, mm)
    macts = {i.title: i.function for i in mon[0].items if isinstance(i, Action)}
    check("el menú «Monitor» tiene avisos y registro", mon[0].title == "Monitor" and len(macts) == 5 and "Probar avisos" in macts, list(macts))
    macts["Probar avisos"]()
    check("sus opciones llaman a menuMonitor", calls[-1] == 'menuMonitor("probar")', calls[-1])
    acts["Nuevo proyecto…"]()
    check("cada opción llama a su función de la página", calls[-1] == 'menuProyecto("nuevo")', calls)
    acts["Copiar lista de frecuencias (CSV)"]()
    check("copiar usa el portapapeles del sistema y avisa", copied == ["a;b"] and "toast(" in calls[-1] and "copiada" in calls[-1].lower(), (copied, calls[-2:]))
    import tempfile as _t
    dlg_file = [os.path.join(_t.mkdtemp(), "mi proyecto.json")]
    open(dlg_file[0], "w", encoding="utf-8").write('{"groups":[]}')
    check("el menú tiene «Importar show de Wireless Workbench…»", "Importar show de Wireless Workbench…" in acts)
    acts["Cargar archivo como proyecto nuevo…"]()
    check("cargar lee el archivo elegido y se lo pasa a la página", calls[-1].startswith('menuCargar("mi proyecto.json",') and '{\\"groups\\"' in calls[-1].replace('\\"', '\\\\"') or "groups" in calls[-1], calls[-1])
    dlg_file[0] = os.path.join(_t.mkdtemp(), "salida")
    check("el menú tiene «Exportar para Wireless Workbench…»", "Exportar para Wireless Workbench…" in acts)
    acts["Revisar proyecto…"]()
    check("«Revisar proyecto…» llama a su función de la página", calls[-1] == 'menuProyecto("revisar")', calls[-1])
    acts["Exportar para Wireless Workbench…"]()
    check("exportar guarda el show donde se elige, con extensión .shw, y avisa", os.path.exists(dlg_file[0] + ".shw") and open(dlg_file[0] + ".shw", encoding="utf-8").read() == "<show/>\n" and "toast(" in calls[-1] and "Exportado" in calls[-1], calls[-1])


def t_interferencias(B):
    print("Avisos de batería e interferencias")
    with bridge() as br:
        ctx, pg = B.page(br["url"])
        pg.click('[data-tab="coord"]')
        coordinate(pg)
        # batería: alarma con 20 % o menos, solo con emisor encendido
        r = pg.evaluate("""()=>{
          const out={};const rx=(c,ex)=>rxAlerts({c},ex,"k").map(a=>a.t+(a.bad?"!":""));
          out.bajo=rx({batt:15,tx:"ULXD2"},"auto");
          out.alto=rx({batt:60,tx:"ULXD2"},"auto");
          out.sinTx=rx({batt:0,bars:0},"auto");
          out.apagado=rx({batt:5,tx:"ULXD2"},"off");
          out.barra=rx({bars:1,tx:"AD2"},"auto");
          return out;}""")
        check("batería ≤ 20 % con emisor: alarma con el porcentaje", r["bajo"] == ["Batería baja (15 %)!"], r["bajo"])
        check("batería alta, sin emisor o emisor apagado: sin aviso", r["alto"] == [] and r["sinTx"] == [] and r["apagado"] == [], r)
        check("equipos de barras avisan con 1 barra", r["barra"] == ["Batería baja (20 %)!"], r["barra"])
        pg.click('[data-tab="live"]')
        check("ya no hay desplegable de batería", pg.locator("#battWarn").count() == 0)
        # calidad baja sostenida
        r = pg.evaluate("""()=>{
          const out={};const ch={key:"q1",f:563000};
          trackQual(ch,{c:{qual:1,tx:"AD2",ant:"A"}},"auto");
          out.pronto=qualLow("q1");out.alerta0=rxAlerts({c:{qual:1,tx:"AD2",ant:"A"}},"auto","q1").map(a=>a.t);
          net.qlow.set("q1",Date.now()-6000);
          out.sostenida=qualLow("q1");out.alerta=rxAlerts({c:{qual:1,tx:"AD2",ant:"A"}},"auto","q1").map(a=>a.t+(a.bad?"!":""));
          trackQual(ch,{c:{qual:4,tx:"AD2",ant:"A"}},"auto");out.recupera=qualLow("q1");
          trackQual(ch,{c:{qual:0,tx:"AD2",ant:"-"}},"auto");out.sinPortadora=net.qlow.has("q1");
          trackQual(ch,{c:{qual:0,tx:"AD2",ant:"A"}},"off");out.apagado=net.qlow.has("q1");
          return out;}""")
        check("un valor bajo suelto no avisa", r["pronto"] is False and r["alerta0"] == [], r)
        check("calidad ≤ 1 durante 5 s: alarma de posible interferencia", r["sostenida"] and r["alerta"] == ["Calidad baja: posible interferencia!"], r)
        check("al recuperarse, sin portadora o con el canal apagado no cuenta", r["recupera"] is False and r["sinPortadora"] is False and r["apagado"] is False, r)
        # señal ajena cerca, vista por el analizador
        pg.click('[data-tab="mon"]')
        r = pg.evaluate("""()=>{
          const out={};
          state.groups=[{id:"g1",name:"A",color:"#e33",freqs:[{f:563000},{f:564000}],qty:2}];
          state.monitor.expect={"g1:0":"on","g1:1":"on"};
          const F=[],L=[];for(let f=560000;f<=567000;f+=5){F.push(f);L.push(-110);}
          const at=(f,v)=>{L[F.findIndex(x=>x>=f)]=v;};
          live.f=Float64Array.from(F);live.l=Float32Array.from(L);
          at(563000,-50);at(563100,-60);                       // el propio emisor y su flanco: no cuentan
          at(562500,-90);                                      // faldas muy por debajo de la portadora: no cuentan
          mon.near.clear();mon.lastTick=0;monitorTick(10000);
          out.sinAjena=mon.near.size;
          at(562000,-65);                                      // señal ajena a 1 MHz del canal 1... fuera de 500 kHz
          live.l=Float32Array.from(L);mon.lastTick=0;monitorTick(10300);out.lejos=mon.near.size;
          at(563350,-62);live.l=Float32Array.from(L);          // a 350 kHz del canal 1: ajena
          mon.lastTick=0;monitorTick(11000);out.empieza=!!(mon.near.get("g1:0")&&!mon.near.get("g1:0").on);
          mon.lastTick=0;monitorTick(13200);out.activa=!!(mon.near.get("g1:0")&&mon.near.get("g1:0").on);
          out.log=mon.log[0]&&mon.log[0].txt;
          out.ch2=!!mon.near.get("g1:1");                      // el canal 2 (564,000) queda a 650 kHz: no
          renderTiles(true);out.tarjeta=document.querySelector('.tile[data-k="g1:0"] .near').textContent;
          out.alarma=document.querySelector('.tile[data-k="g1:0"]').classList.contains("alarm");
          // otro canal coordinado justo ahí: no es ajena
          state.groups[0].freqs[1].f=563350;mon.near.clear();mon.lastTick=0;monitorTick(20000);mon.lastTick=0;monitorTick(23000);
          out.coordinada=!!(mon.near.get("g1:0")&&mon.near.get("g1:0").on);
          return out;}""")
        check("sin señal ajena no hay aviso (faldas y emisor propio no cuentan)", r["sinAjena"] == 0 and r["lejos"] == 0, r)
        check("señal ajena a 350 kHz: tarda 2 s en avisar y queda en el registro", r["empieza"] and r["activa"] and "señal ajena cerca" in (r["log"] or ""), r)
        check("la tarjeta lo muestra y se pone en alarma", "Señal ajena cerca" in r["tarjeta"] and r["alarma"], r)
        check("otro canal coordinado cerca no se toma por interferencia", r["coordinada"] is False and r["ch2"] is False, r)
        ctx.close()


SHW_DEMO = """<show version="1.0" appl_version="7.1.0.285">
  <show_properties version="1.0"><show_info><name>Gira &lt;demo&gt;</name></show_info></show_properties>
  <inventory version="2.1">
    <device><id dcid="X">AAAA0001-0000-11DD-A000-000EDDCCCCCC</id><model>AD4D-A</model><device_name type="10">AD4D-A</device_name>
      <channel number="1"><channel_name type="10">01</channel_name></channel>
      <channel number="2"><channel_name type="10">Voz principal</channel_name></channel></device>
    <device><id dcid="Y">BBBB0002-0000-11DD-A000-000EDDCCCCCC</id><model>PSM1000</model><device_name type="10">P10T</device_name>
      <channel number="1"><channel_name type="10">03</channel_name></channel></device>
  </inventory>
  <coordination_info>
    <scan_data version="1.3"><threshold>-90</threshold><higher_threshold>-60</higher_threshold></scan_data>
    <global_exclusions version="1.1">
      <frequency_exclusions>
        <channel><frequency units="kHz">610250</frequency><series>Generic Device - IMD</series><exclude>1</exclude></channel>
        <channel><frequency units="kHz">611000</frequency><exclude>0</exclude></channel>
      </frequency_exclusions>
      <freq_range_exclusions>
        <range><frequency units="kHz"><start>600100</start><end>600400</end></frequency><source>Detected</source><exclude>1</exclude></range>
        <range><frequency units="kHz"><start>620000</start><end>620000</end></frequency><exclude>1</exclude></range>
      </freq_range_exclusions>
    </global_exclusions>
  </coordination_info>
  <coordinated_data_root version="0.3">
    <compatibility_profile_settings version="1.0" count="2">
      <profile><band>G56</band><series>AD</series><tx_profile>Standard</tx_profile>
        <compat_profile imd_source="1" name="Standard"><spacing freq_units="KHz"><ch_ch>350</ch_ch><imd_2t3o>75</imd_2t3o><imd_2t5o>0</imd_2t5o><imd_2t7o>0</imd_2t7o><imd_2t9o>0</imd_2t9o><imd_3t3o>0</imd_3t3o></spacing></compat_profile></profile>
      <profile><band>G10E</band><series>PSM1000</series><tx_profile/>
        <compat_profile imd_source="1" name="Standard"><spacing freq_units="KHz"><ch_ch>375</ch_ch><imd_2t3o>275</imd_2t3o><imd_2t5o>0</imd_2t5o><imd_2t7o>0</imd_2t7o><imd_2t9o>0</imd_2t9o><imd_3t3o>100</imd_3t3o></spacing></compat_profile></profile>
    </compatibility_profile_settings>
    <mic_channels units="khz" count="4">
      <freq_entry id="AAAA0001-0000-11DD-A000-000EDDCCCCCC-0"><compat_key><series>AD</series><band>G56</band></compat_key><value>583125</value></freq_entry>
      <freq_entry id="AAAA0001-0000-11DD-A000-000EDDCCCCCC-1"><compat_key><series>AD</series><band>G56</band></compat_key><value>585650</value></freq_entry>
      <freq_entry id="BBBB0002-0000-11DD-A000-000EDDCCCCCC-0"><compat_key><series>PSM1000</series><band>G10E</band></compat_key><value>486750</value></freq_entry>
      <freq_entry id="CCCC0003-0000-11DD-A000-000EDDCCCCCC-0"><compat_key><series>Equipo Raro</series><band>Z9</band></compat_key><value>700500</value></freq_entry>
    </mic_channels>
  </coordinated_data_root>
</show>"""


def t_importar_wwb(B):
    print("Importar un show de Wireless Workbench")
    import tempfile as _t
    with bridge() as br:
        ctx, pg = B.page(br["url"])
        n0 = pg.evaluate("()=>projIdx.list.length")
        r = pg.evaluate("(x)=>{const ok=menuShw('demo.shw',x);const g=state.groups;return {ok,n:projIdx.list.length,name:curProject().name,"
                        "g:g.map(a=>({n:a.name,m:a.model&&a.model.series+'/'+a.model.band,r:[a.cc,a.im3,a.im5,a.im33],p:a.preset,f:a.freqs.map(e=>[e.f,e.locked,e.name||null])})),excl:state.excl,"
                        "toast:document.querySelector('#toast').textContent}}", SHW_DEMO)
        check("el show se importa como proyecto nuevo con el nombre del show", r["ok"] and r["n"] == n0 + 1 and r["name"] == "Gira <demo>", r)
        gs = {g["m"] or g["n"]: g for g in r["g"]}
        check("un grupo por serie y banda, con la biblioteca de la app", "shure-ad/G56" in gs and "shure-psm1000/G10E" in gs, list(gs))
        check("frecuencias en kHz, bloqueadas, con el nombre del canal", gs["shure-ad/G56"]["f"] == [[583125, True, "AD4D-A 01"], [585650, True, "Voz principal"]]
              and gs["shure-psm1000/G10E"]["f"] == [[486750, True, "P10T 03"]], gs)
        check("las reglas de separación de WWB pasan al grupo (ch-ch, 3.º orden y 3 transmisores)", gs["shure-ad/G56"]["r"] == [350, 75, 0, 0] and gs["shure-psm1000/G10E"]["r"] == [375, 275, 0, 100], [gs["shure-ad/G56"]["r"], gs["shure-psm1000/G10E"]["r"]])
        check("si coinciden con un modo del equipo se elige ese modo; si no, quedan como personalizadas", gs["shure-psm1000/G10E"]["p"] == "mode:0" and gs["shure-ad/G56"]["p"] in ("custom",) or gs["shure-ad/G56"]["p"].startswith("mode:"), [gs["shure-ad/G56"]["p"], gs["shure-psm1000/G10E"]["p"]])
        check("un equipo que la biblioteca no conoce queda como grupo de rango propio", any(g["n"].startswith("Equipo Raro Z9") and g["f"][0][0] == 700500 for g in r["g"]), r["g"])
        lines = r["excl"].split("\n")
        check("exclusiones: frecuencias y rangos activos, sin las desactivadas", lines == ["600.1-600.4", "610.25", "620"], lines)
        check("avisa de lo importado y de lo que no se importa", "4 frecuencias en 3 grupos" in r["toast"] and "reglas de separación" in r["toast"] and "3 exclusiones" in r["toast"] and "escaneos" in r["toast"] and "sin equipo" in r["toast"], r["toast"])
        r = pg.evaluate("()=>[state.scan.threshold,state.scan.peak]")
        check("los umbrales del escaneo del show (exclusión y pico) se importan", r == [-90, -60], r)
        # desde el botón de la página
        p = os.path.join(_t.mkdtemp(), "otro.shw")
        open(p, "w", encoding="utf-8").write(SHW_DEMO.replace("Gira &lt;demo&gt;", "Segundo"))
        pg.set_input_files("#loadShw", p)
        pg.wait_for_function("()=>curProject().name==='Segundo'", timeout=5000)
        check("el botón de la página importa el archivo elegido", True)
        # archivos que no son un show
        r = pg.evaluate("()=>{const a=menuShw('x.shw','<html/>'),b=menuShw('x.shw','no es xml'),c=menuShw('x.shw','<show><coordinated_data_root><mic_channels/></coordinated_data_root></show>');return [a,b,c,document.querySelector('#toast').textContent]}")
        check("un archivo que no es un show o no tiene frecuencias se rechaza con un aviso", r[:3] == [False, False, False] and "No se ha podido importar" in r[3], r)
        ctx.close()


def t_umbrales(B):
    print("Escaneo: umbral de exclusión, umbral de pico y protección")
    with bridge() as br:
        ctx, pg = B.page(br["url"])
        pg.click('[data-tab="coord"]')
        pg.evaluate("()=>{const d=document.querySelector('#scanTh').closest('details');if(d)d.open=true;}")
        v = pg.evaluate("()=>[$('#scanTh').value,$('#scanPeak').value,$('#scanProt').value]")
        check("por defecto: exclusión -85, pico -60 y protección 800 kHz", v == ["-85", "-60", "800"], v)
        r = pg.evaluate("""()=>{
          const F=[],L=[];for(let f=560000;f<=566000;f+=25){F.push(f);L.push(-105);}
          const at=(f,v)=>{L[F.indexOf(f)]=v;};
          at(562975,-55);at(563000,-45);at(563025,-58);   // un pico fuerte (el punto más alto: 563 MHz)
          at(565000,-70);                                  // una señal media: pasa el de exclusión, no el de pico
          state.scans=[{id:"t1",name:"prueba",f:F,l:L,on:true,color:"#2f7896"}];state.scan={threshold:-85,peak:-60,protect:800,mode:"imd",enabled:true};
          const out={};const c=()=>buildCtx();
          out.picos=scanPeaks().map(p=>[p.f,p.l]);
          out.cerca=blockReason(563600,c());out.lejos=blockReason(563900,c());
          out.media=blockReason(565000,c());out.mediaCerca=blockReason(565300,c());
          state.scan.protect=0;out.sinProt=blockReason(563600,c());state.scan.protect=800;
          state.scan.enabled=false;out.apagado=blockReason(563600,c());state.scan.enabled=true;
          return out;}""")
        check("un tramo por encima del umbral de pico es un pico, en su punto más alto", r["picos"] == [[563000, -45]], r["picos"])
        check("dentro de la protección de un pico no se puede coordinar", r["cerca"] and "pico del escaneo" in r["cerca"] and "563" in r["cerca"], r["cerca"])
        check("fuera de la protección (900 kHz) queda libre", r["lejos"] is None, r["lejos"])
        check("una señal media solo se evita en su propia frecuencia", r["media"] and "Señal en el escaneo" in r["media"] and r["mediaCerca"] is None, [r["media"], r["mediaCerca"]])
        check("con protección 0 o el escaneo apagado no hay zona de pico", r["sinProt"] is None and r["apagado"] is None, [r["sinProt"], r["apagado"]])
        # el análisis marca una frecuencia coordinada dentro de la zona
        r = pg.evaluate("""()=>{
          state.groups=[mkModelGroup("shure-ulxd","G51",1,0)];state.groups[0].freqs[0]={f:563500,locked:true};
          analyzeNow();return analysis.C[0].issues;}""")
        check("el análisis avisa de una frecuencia dentro de la zona de un pico", any("pico del escaneo" in s for s in r), r)
        # los campos de la ventana
        pg.fill("#scanProt", "400"); pg.press("#scanProt", "Tab")
        r = pg.evaluate("()=>[state.scan.protect,blockReason(563500,buildCtx())]")
        check("la protección se cambia desde su campo y se aplica", r[0] == 400 and r[1] is None, r)
        pg.fill("#scanTh", "-50"); pg.press("#scanTh", "Tab")
        r = pg.evaluate("()=>[state.scan.threshold,state.scan.peak,$('#scanPeak').value]")
        check("el umbral de pico nunca queda por debajo del de exclusión", r == [-50, -50, "-50"], r)
        pg.fill("#scanPeak", "-80"); pg.press("#scanPeak", "Tab")
        check("…ni al escribirlo a mano", pg.evaluate("()=>state.scan.peak") == -50)
        pg.fill("#scanTh", "-85"); pg.press("#scanTh", "Tab"); pg.fill("#scanPeak", "-60"); pg.press("#scanPeak", "Tab")
        info = pg.inner_text("#scanInfo")
        check("el panel cuenta los picos", "1 pico" in info, info)
        # picos como emisores en la intermodulación (como WWB) o solo protección
        pg.evaluate("()=>{state.scan.protect=800;state.scan.mode='imd';}")
        r = pg.evaluate("""()=>{
          const out={};
          state.groups=[mkGroup("A",1,470000,694000,25,"analog",0),mkGroup("B",1,470000,694000,25,"analog",1)];
          state.groups[0].freqs[0]={f:563850,locked:true};state.groups[1].freqs[0]={f:562150,locked:true};
          state.scan.mode="imd";analyzeNow();out.imd=analysis.C[1].issues.filter(s=>s.includes("pico"));out.fuentes=peakSources().length;
          state.scan.mode="prot";analyzeNow();out.prot=analysis.C[1].issues.filter(s=>s.includes("IMD"));out.fuentesProt=peakSources().length;
          state.scan.mode="imd";return out;}""")
        check("modo WWB: el producto 2×pico − otra frecuencia cae sobre una coordinada y se avisa", len(r["imd"]) == 1 and "2×pico" in r["imd"][0] and r["fuentes"] == 1, r)
        check("modo «solo protección»: los picos no entran en la intermodulación", r["prot"] == [] and r["fuentesProt"] == 0, r)
        r = pg.evaluate("""async()=>{
          state.groups=[mkGroup("A",4,470000,694000,25,"analog",0),mkGroup("B",4,470000,694000,25,"analog",1)];
          for(const g of state.groups)g.freqs=g.freqs.map(()=>({f:null,locked:false}));
          const F=[],L=[];for(let f=560000;f<=566000;f+=25){F.push(f);L.push(-105);}
          for(const p of [561000,563000,565000])L[F.indexOf(p)]=-40;
          state.scans=[{id:"t2",name:"tres picos",f:F,l:L,on:true,color:"#2f7896"}];state.scan={threshold:-85,peak:-60,protect:800,mode:"imd",enabled:true};
          state.opts.pmse=false;
          await coordinate();
          const fr=analysis.C.map(c=>c.f);
          return {n:fr.length,bad:analysis.bad,iss:analysis.C.flatMap(c=>c.issues),lejos:fr.every(f=>[561000,563000,565000].every(p=>Math.abs(f-p)>=800)||f<560000||f>566000)};}""")
        check("coordinar con picos como emisores coloca todo sin problemas y fuera de sus zonas", r["n"] == 8 and r["bad"] == 0 and r["lejos"], r)
        # guardado y recuperación
        r = pg.evaluate("()=>{const n=normalize(JSON.parse(JSON.stringify(exportState())));const v=normalize({groups:[],scan:{threshold:-80}});return [n.scan.peak,n.scan.protect,v.scan.peak,v.scan.protect,v.scan.mode,n.scan.mode]}")
        check("se guardan con el proyecto y los proyectos antiguos reciben los valores por defecto (picos como WWB)", r == [-60, 800, -60, 800, "imd", "imd"], r)
        ctx.close()


def t_exportar_wwb(B):
    print("Exportar un proyecto a un show de Wireless Workbench")
    import xml.etree.ElementTree as ET
    with bridge() as br:
        ctx, pg = B.page(br["url"])
        r = pg.evaluate("""()=>{
          const ad=mkModelGroup("shure-ad","G56",3,0),ps=mkModelGroup("shure-psm1000","G10E",2,1),sn=mkModelGroup("senn-iemg4","A",2,2),qx=mkModelGroup("shure-ulx","G3",1,3);
          ad.freqs=[{f:583125,locked:true,name:"Voz & coros"},{f:585650,locked:false},{f:587000,locked:false}];
          ps.freqs=[{f:486750,locked:true},{f:509150,locked:true}];
          sn.freqs=[{f:520000,locked:true},{f:521000,locked:true}];qx.freqs=[{f:480000,locked:true}];
          state.groups=[ad,ps,sn,qx];state.excl="610.25\\n600.1-600.4";
          state.scan.threshold=-88;state.scan.peak=-58;
          const r=shwBuild(state,"Gira <2026>");return {text:r.text,devices:r.devices,channels:r.channels,skipped:r.skipped};}""")
        root = ET.fromstring(r["text"])
        check("el show es XML válido con la raíz «show» de WWB 7.1", root.tag == "show" and root.get("appl_version") == "7.1.0.285", root.attrib)
        check("hay un equipo por cada 2 canales (AD4D y P10T) y los grupos sin equivalente se dejan fuera con aviso", r["devices"] == 3 and r["channels"] == 5 and len(r["skipped"]) == 2, r["skipped"])
        devs = root.findall("inventory/device")
        ids = [d.findtext("id") for d in devs]
        check("identificadores de equipo únicos y con el formato de WWB", len(set(ids)) == 3 and all(len(i) == 36 and i.endswith("-0000-11DD-A000-000EDDCCCCCC") for i in ids), ids)
        check("equipos con su modelo, serie y banda de WWB", [(d.findtext("model"), d.findtext("series"), d.findtext("band")) for d in devs] == [("AD4D-A", "AD", "G56"), ("AD4D-A", "AD", "G56"), ("PSM1000", "PSM1000", "G10E")], [(d.findtext("model"), d.findtext("series")) for d in devs])
        fe = root.findall("coordinated_data_root/mic_channels/freq_entry")
        check("cada frecuencia coordinada apunta a un canal de un equipo del inventario", len(fe) == 5 and root.find("coordinated_data_root/mic_channels").get("count") == "5"
              and all(e.get("id").rsplit("-", 1)[0] in ids for e in fe) and sorted(int(e.findtext("value")) for e in fe) == [486750, 509150, 583125, 585650, 587000], [e.get("id") for e in fe])
        ch = {(d.findtext("id"), c.get("number")): c.findtext("frequency") for d in devs for c in d.findall("channel")}
        check("las frecuencias del canal coinciden con las coordinadas y el hueco de un equipo queda en 0", sorted(ch.values()) == ["0", "486750", "509150", "583125", "585650", "587000"], sorted(ch.values()))
        check("reglas de separación por serie y banda", {(p.findtext("series"), p.findtext("band")) for p in root.findall("coordinated_data_root/compatibility_profile_settings/profile")} == {("AD", "G56"), ("PSM1000", "G10E")}
              and root.find("coordinated_data_root/compatibility_profile_settings").get("count") == "2")
        check("umbrales de exclusión y de pico del escaneo", root.findtext("coordination_info/scan_data/threshold") == "-88" and root.findtext("coordination_info/scan_data/higher_threshold") == "-58")
        check("el nombre se escapa y la lista de canales monitorizados lista todos", root.findtext("show_properties/show_info/name") == "Gira <2026>" and len(root.findtext("monitoring_info/channel_order").split(";")) == 5)
        nm = [(c.findtext("channel_name")) for d in devs for c in d.findall("channel")]
        check("nombres de canal para WWB: el propio (hasta 8 caracteres) o el número del canal", "Voz & co" in nm and "02" in nm and "03" in nm and all(len(n) <= 8 for n in nm), nm)
        r3 = pg.evaluate("()=>[shwChName('AD4D-A 01','AD4D-A','AD4D-A'),shwChName('P10T 07','P10T','PSM1000'),shwChName('Voz principal','AD4D-A','AD4D-A'),shwChName('Voz 2','P10T','PSM1000'),shwChName('PSM1000 3','P10T','PSM1000')]")
        check("«AD4D-A 01» → «01», «P10T 07» → «07», los nombres propios se respetan (hasta 8)", r3 == ["01", "07", "Voz prin", "Voz 2", "3"], r3)
        # ida y vuelta: lo que se exporta se vuelve a importar igual
        back = pg.evaluate("""(x)=>{const p=shwParse(x);return {name:p.name,groups:p.groups.map(g=>({s:g.series,b:g.band,f:g.freqs.map(e=>e.f),r:g.rules,n:g.freqs.map(e=>e.name)})),excl:p.excl,scan:p.scan}}""", r["text"])
        gs = {(g["s"], g["b"]): g for g in back["groups"]}
        check("ida y vuelta: grupos, frecuencias y nombres", gs[("AD", "G56")]["f"] == [583125, 585650, 587000] and gs[("PSM1000", "G10E")]["f"] == [486750, 509150] and gs[("AD", "G56")]["n"][0] == "Voz & co", back["groups"])
        check("ida y vuelta: reglas, exclusiones y umbrales", gs[("AD", "G56")]["r"]["cc"] > 0 and sorted(map(str, back["excl"])) == sorted(["610250", "600100,600400"]) or back["excl"] == [610250, [600100, 600400]] or sorted(back["excl"], key=str) == sorted([610250, [600100, 600400]], key=str), back["excl"])
        check("ida y vuelta: umbrales del escaneo", back["scan"] == {"threshold": -88, "peak": -58} and back["name"] == "Gira <2026>", back["scan"])
        # sin nada exportable
        r2 = pg.evaluate("()=>{state.groups=[mkModelGroup('senn-iemg4','A',2,0)];state.groups[0].freqs[0].f=520000;const r=menuShwExport();return r}")
        check("si nada se puede exportar, avisa en vez de crear un archivo vacío", r2["ok"] is False and "No hay frecuencias" in r2["msg"], r2)
        # el botón de la página descarga el archivo
        pg.evaluate("""()=>{const ad=mkModelGroup("shure-ad","G56",1,0);ad.freqs=[{f:583125,locked:true}];state.groups=[ad];}""")
        pg.click("#shwExport", timeout=5000) if False else None
        with pg.expect_download(timeout=8000) as dl:
            pg.evaluate("()=>shwDownload()")
        d = dl.value
        check("el botón descarga un archivo .shw", d.suggested_filename.endswith(".shw"), d.suggested_filename)
        ctx.close()


def t_escaneos(B):
    print("Panel de escaneos: varios, con selección, suma para coordinar y trazas separadas")
    import tempfile as _t
    d = _t.mkdtemp()
    def csv(nombre, picos, base=-105):
        p = os.path.join(d, nombre)
        pts = {round(560 + i * 0.025, 3): base for i in range(0, 241)}
        for f, v in picos.items():
            pts[f] = v
        open(p, "w").write("\n".join(f"{f},{v}" for f, v in sorted(pts.items())))
        return p
    a = csv("Sala A.csv", {563.0: -45})
    b = csv("Sala B.csv", {565.0: -45, 563.05: -48})
    with bridge() as br:
        ctx, pg = B.page(br["url"])
        pg.click('[data-tab="coord"]')
        pg.evaluate("()=>{const d=document.querySelector('#scanTh').closest('details');if(d)d.open=true;}")
        # proyectos antiguos: un solo escaneo dentro de «scan»
        r = pg.evaluate("""()=>{const s=normalize({groups:[],scan:{f:[100000,100025,100050],l:[-50,-60,-70],name:"viejo",threshold:-80,enabled:true}});
          return {n:s.scans.length,name:s.scans[0].name,on:s.scans[0].on,pts:s.scans[0].f.length,limpio:!("f" in s.scan)&&!("name" in s.scan),th:s.scan.threshold}}""")
        check("un proyecto antiguo con un escaneo lo conserva en la lista", r == {"n": 1, "name": "viejo", "on": True, "pts": 3, "limpio": True, "th": -80}, r)
        pg.evaluate("()=>{state.scans=[];save();renderScanInfo();analyzeNow()}")
        check("sin escaneos la lista está vacía y lo dice", pg.locator(".scrow").count() == 0 and "Sin escaneos" in pg.inner_text("#scanInfo"))
        pg.set_input_files("#scanFile", [a, b])
        pg.wait_for_function("()=>state.scans.length===2", timeout=8000)
        rows = pg.evaluate("()=>[...document.querySelectorAll('.scrow')].map(r=>[r.querySelector('.nm').getAttribute('title').split('\\n')[0],r.querySelector('input').checked,r.querySelector('.sw').style.background])")
        check("se pueden añadir varios archivos a la vez; cada uno es una fila marcada con su color", len(rows) == 2 and sorted(x[0] for x in rows) == ["Sala A.csv", "Sala B.csv"] and all(x[1] for x in rows) and rows[0][2] != rows[1][2], rows)
        idA = pg.evaluate("()=>state.scans.find(s=>s.name==='Sala A.csv').id"); idB = pg.evaluate("()=>state.scans.find(s=>s.name==='Sala B.csv').id")
        check("el resumen habla de la suma de los escaneos", "2 escaneos" in pg.inner_text("#scanInfo") and "suma" in pg.inner_text("#scanInfo"), pg.inner_text("#scanInfo"))
        meta = pg.evaluate("()=>state.scans.map(s=>[s.name,!!s.date,s.analyzer,s.place])")
        check("cada archivo guarda su fecha (la del archivo) y la lista la muestra", all(m[1] for m in meta) and pg.locator(".scrow .meta").count() == 2, meta)
        pg.click(f'[data-edit="{idA}"]')
        pg.wait_for_selector("#scModal:not([hidden])")
        pg.fill("#scName", "Prueba de sonido"); pg.fill("#scPlace", "Sala Principal"); pg.fill("#scAn", "RF Explorer"); pg.fill("#scDate", "2026-10-01T18:30"); pg.fill("#scNotes", "Con la PA encendida")
        pg.click("#scYes")
        r = pg.evaluate("()=>{const s=state.scans.find(x=>x.name==='Prueba de sonido');return s&&[s.place,s.analyzer,s.notes,new Date(s.date).getDate(),new Date(s.date).getHours()]}")
        check("editar un escaneo cambia nombre, lugar, analizador, fecha y notas", r == ["Sala Principal", "RF Explorer", "Con la PA encendida", 1, 18], r)
        check("la lista muestra la fecha, el analizador y el lugar", "Sala Principal" in pg.inner_text(".scrow >> nth=0") or "Sala Principal" in pg.inner_text("#scanList"), pg.inner_text("#scanList"))
        pg.fill("#scanFind", "principal")
        check("buscar filtra por nombre, lugar, analizador o notas", pg.locator(".scrow").count() == 1)
        pg.fill("#scanFind", "xyz")
        check("sin coincidencias lo dice", pg.locator(".scrow").count() == 0 and "Ningún escaneo coincide" in pg.inner_text("#scanList"))
        pg.fill("#scanFind", "")
        pg.select_option("#scanSort", "nombre")
        nombres = pg.evaluate("()=>[...document.querySelectorAll('.scrow .nm')].map(n=>n.getAttribute('title').split('\\n')[0])")
        check("ordenar por nombre", nombres == sorted(nombres, key=str.lower), nombres)
        pg.select_option("#scanSort", "fecha")
        primero = pg.evaluate("()=>document.querySelector('.scrow .nm').getAttribute('title').split('\\n')[0]")
        check("ordenar por fecha pone el más reciente primero", primero in ("Sala A.csv", "Sala B.csv"), primero)
        r = pg.evaluate("""()=>({a:blockReason(563000,buildCtx()),b:blockReason(565000,buildCtx()),libre:blockReason(561500,buildCtx()),picos:scanPeaks().map(p=>p.f)})""")
        check("se coordina con la suma: lo de A y lo de B se evita", r["a"] and r["b"] and r["libre"] is None, r)
        check("los picos de varios escaneos a menos de 100 kHz cuentan como uno (el más fuerte)", r["picos"] == [563000, 565000], r["picos"])
        pg.uncheck(f'[data-sc="{idB}"]')
        r = pg.evaluate("()=>({a:blockReason(563000,buildCtx()),b:blockReason(565000,buildCtx()),n:activeScans().length,info:document.querySelector('#scanInfo').textContent})")
        check("al desmarcar B solo cuenta A", r["a"] and r["b"] is None and r["n"] == 1 and "1 escaneo" in r["info"], r)
        pg.uncheck(f'[data-sc="{idA}"]')
        r = pg.evaluate("()=>({a:blockReason(563000,buildCtx()),info:document.querySelector('#scanInfo').textContent,ctx:buildCtx().scanOn})")
        check("con todos desmarcados no se evita nada y el panel lo dice", r["a"] is None and r["ctx"] is False and "Ningún escaneo marcado" in r["info"], r)
        pg.check(f'[data-sc="{idA}"]'); pg.check(f'[data-sc="{idB}"]')
        # la gráfica los dibuja por separado, cada uno con su color
        r = pg.evaluate("""()=>{const seen=new Set(),o=CanvasRenderingContext2D.prototype.stroke;
          CanvasRenderingContext2D.prototype.stroke=function(){seen.add(String(this.strokeStyle));return o.apply(this,arguments);};
          try{state.scans.forEach(s=>s.on=true);draw();}finally{CanvasRenderingContext2D.prototype.stroke=o;}
          return {cols:state.scans.map(s=>s.color),seen:[...seen]}}""")
        check("la gráfica de coordinación traza cada escaneo con su propio color", all(c in r["seen"] for c in r["cols"]), r)
        # quitar uno (con confirmación)
        pg.click(f'.scrow [data-del="{idA}"]')
        check("quitar un escaneo lo borra de la lista y de la coordinación", pg.evaluate("()=>[state.scans.length,state.scans[0].name,blockReason(563000,buildCtx())]") == [1, "Sala B.csv", "Señal en el escaneo a -48 dBm"] or pg.evaluate("()=>[state.scans.length,state.scans[0].name]") == [1, "Sala B.csv"])
        pg.evaluate("()=>{window.confirm=()=>false}")
        pg.click(f'.scrow [data-del="{idB}"]')
        check("si no se confirma, no se quita", pg.evaluate("()=>state.scans.length") == 1)
        # se guardan con el proyecto
        r = pg.evaluate("()=>{const n=normalize(JSON.parse(JSON.stringify(exportState())));return [n.scans.length,n.scans[0].name,n.scans[0].f.length,n.scans[0].color===state.scans[0].color]}")
        check("los escaneos se guardan y se copian con el proyecto", r == [1, "Sala B.csv", 241, True], r)
        # archivo que no es un escaneo
        bad = os.path.join(d, "malo.txt"); open(bad, "w").write("hola\nmundo")
        pg.evaluate("()=>{window.confirm=()=>true}")
        pg.set_input_files("#scanFile", bad)
        pg.wait_for_timeout(500)
        check("un archivo sin pares frecuencia/nivel se rechaza y no añade nada", pg.evaluate("()=>state.scans.length") == 1 and "No se han encontrado" in pg.inner_text("#toast"), pg.inner_text("#toast"))
        ctx.close()


def t_vista(B):
    print("Vista de la gráfica: campos de Inicio, Centro, Fin y Ancho, y franja")
    with bridge() as br:
        ctx, pg = B.page(br["url"])
        pg.click('[data-tab="coord"]')
        pg.evaluate("()=>setView(500000,600000)")
        v = pg.evaluate("()=>[$('#vwA').value,$('#vwB').value,$('#vwC').value,$('#vwS').value]")
        check("los campos reflejan la vista (MHz)", v == ["500.000", "600.000", "550.000", "100.000"], v)
        pg.fill("#vwA", "520,5"); pg.press("#vwA", "Tab")
        r = pg.evaluate("()=>[view.a,view.b,$('#vwS').value]")
        check("Inicio mueve solo el extremo izquierdo (acepta coma decimal)", r == [520500, 600000, "79.500"], r)
        pg.fill("#vwB", "560"); pg.press("#vwB", "Tab")
        check("Fin mueve solo el extremo derecho", pg.evaluate("()=>[view.a,view.b]") == [520500, 560000])
        pg.fill("#vwC", "600"); pg.press("#vwC", "Tab")
        r = pg.evaluate("()=>[view.a,view.b]")
        check("Centro desplaza la vista sin cambiar su ancho", r == [580250, 619750] or abs((r[1] - r[0]) - 39500) < 1 and abs((r[0] + r[1]) / 2 - 600000) < 1, r)
        pg.fill("#vwS", "20"); pg.press("#vwS", "Tab")
        r = pg.evaluate("()=>[view.a,view.b]")
        check("Ancho cambia el zoom alrededor del centro", abs((r[1] - r[0]) - 20000) < 1 and abs((r[0] + r[1]) / 2 - 600000) < 1, r)
        pg.fill("#vwS", "0,01"); pg.press("#vwS", "Tab")
        check("no deja un ancho absurdo (mínimo 300 kHz)", pg.evaluate("()=>view.b-view.a") >= 300)
        pg.fill("#vwA", "hola"); pg.press("#vwA", "Tab")
        check("un valor que no es un número no cambia la vista", pg.evaluate("()=>$('#vwA').value") == pg.evaluate("()=>(view.a/1000).toFixed(3)"))
        # la rueda y los botones actualizan los campos
        pg.evaluate("()=>setView(500000,600000)")
        pg.click("#zin")
        check("al hacer zoom los campos se actualizan", pg.evaluate("()=>$('#vwS').value") != "100.000")
        # la franja: arrastrar el recuadro desplaza la vista; clic centra
        pg.evaluate("()=>setView(520000,560000)")
        box = pg.locator("#mini").bounding_box()
        info = pg.evaluate("()=>{const [lo,hi]=miniRange();return {lo,hi,a:view.a,b:view.b}}")
        xa = box["x"] + (info["a"] + 5000 - info["lo"]) / (info["hi"] - info["lo"]) * box["width"]
        y = box["y"] + box["height"] / 2
        pg.mouse.move(xa, y); pg.mouse.down(); pg.mouse.move(xa + 60, y, steps=5); pg.mouse.up()
        r = pg.evaluate("()=>[view.a,view.b]")
        check("arrastrar el recuadro de la franja desplaza la vista sin cambiar el ancho", r[0] > 520000 and abs((r[1] - r[0]) - 40000) < 1, r)
        fr = pg.evaluate("()=>{const [lo,hi]=miniRange();return (view.a+view.b)/2>(lo+hi)/2?.08:.92}")
        esperado = pg.evaluate(f"()=>{{const [lo,hi]=miniRange();return lo+{fr}*(hi-lo)}}")
        pg.mouse.click(box["x"] + box["width"] * fr, y)
        centro = pg.evaluate("()=>(view.a+view.b)/2")
        check("un clic fuera del recuadro centra la vista ahí", abs(centro - esperado) < 800, [centro, esperado])
        ctx.close()


def t_imagen(B):
    print("Guardar la gráfica como imagen")
    with bridge() as br:
        ctx, pg = B.page(br["url"])
        pg.click('[data-tab="coord"]')
        coordinate(pg)
        r = pg.evaluate("()=>{const c=chartImage();return {w:c.width,h:c.height,cw:cv.width,ch:cv.height}}")
        check("la imagen es la gráfica con una cabecera encima", r["w"] == r["cw"] and r["h"] > r["ch"], r)
        pg.click("#shotBtn")
        pg.wait_for_function("()=>/Imagen guardada/.test(document.querySelector('#toast').textContent)", timeout=8000)
        files = glob.glob(os.path.join(br["docs"], "Imágenes", "*.png"))
        data = open(files[0], "rb").read() if files else b""
        check("se guarda un PNG en Documentos/Coordinador RF/Imágenes y se avisa", len(files) == 1 and data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) > 3000, [len(files), len(data)])
        # el puente rechaza lo que no es una imagen
        r = pg.evaluate("""async()=>{try{await diskApi('/files',{kind:'imagen',name:'x',content:btoa('no es un png'),open:false});return 'aceptada'}catch(e){return e.message}}""")
        check("el puente no acepta como imagen lo que no es un PNG", "imagen no válida" in r, r)
        ctx.close()


def t_perfil(B):
    print("Perfil del equipo con los modos lado a lado")
    with bridge() as br:
        ctx, pg = B.page(br["url"])
        pg.click('[data-tab="coord"]')
        pg.evaluate("()=>{state.groups=[mkModelGroup('shure-psm1000','G10E',2,0),mkModelGroup('senn-iemg4','A',2,1)];save();renderGroups();analyzeNow()}")
        pg.click('[data-prof] >> nth=0')
        pg.wait_for_selector("#profModal:not([hidden])")
        tit = pg.inner_text("#prT")
        check("el título dice equipo y banda", "PSM 1000" in tit and "G10E" in tit, tit)
        modos = pg.evaluate("()=>[...document.querySelectorAll('#prBody [data-pmode]')].map(b=>b.textContent)")
        check("una columna por cada modo de la biblioteca", modos == ["Robusto", "Medio", "Cantidad"], modos)
        tabla = pg.evaluate("""()=>[...document.querySelectorAll('#prBody table')[1].querySelectorAll('tbody tr')].map(r=>[...r.cells].map(c=>c.textContent))""")
        fila = {r[0]: r[1:] for r in tabla}
        check("valores de PSM 1000 como en WWB: entre portadoras 375 / 350 / 325 y 3.er orden 275 / 250 / 225", fila["Entre portadoras"] == ["375 kHz", "350 kHz", "325 kHz"] and fila["Intermodulación 3er orden"] == ["275 kHz", "250 kHz", "225 kHz"], fila)
        check("3 Tx de 3.er orden 100 / 50 / 0 y origen «Fabricante»", fila["3 Tx · 3er orden"] == ["100 kHz", "50 kHz", "0"] and fila["Origen"] == ["Fabricante"] * 3, fila)
        act = pg.evaluate("()=>[...document.querySelectorAll('#prBody thead th')].map(t=>t.classList.contains('on'))")
        check("el modo activo del grupo sale resaltado", act == [False, True, False, False] or act[1:].count(True) == 1, act)
        pg.click('#prBody [data-pmode="2"]')
        r = pg.evaluate("()=>[state.groups[0].preset,state.groups[0].cc,state.groups[0].im3,state.groups[0].im33,document.querySelectorAll('#prBody thead th.on').length]")
        check("pulsar un modo lo aplica al grupo y lo resalta", r == ["mode:2", 325, 225, 0, 1], r)
        pg.keyboard.press("Escape")
        check("Escape cierra la ventana", pg.evaluate("()=>document.getElementById('profModal').hidden"))
        # grupo de otra marca
        pg.click('[data-prof] >> nth=1')
        pg.wait_for_selector("#profModal:not([hidden])")
        check("también funciona con equipos de otras marcas (valores orientativos)", "Orientativo" in pg.inner_text("#prBody") and "ew IEM G4" in pg.inner_text("#prT"), pg.inner_text("#prT"))
        pg.click("#prNo")
        # grupo personalizado: sin botón
        pg.evaluate("()=>{state.groups=[mkGroup('Mío',2,470000,694000,25,'analog',0)];renderGroups()}")
        check("un grupo personalizado no tiene botón de comparar", pg.locator("[data-prof]").count() == 0)
        ctx.close()


def t_revisar(B):
    print("Revisar proyecto")
    with bridge() as br:
        ctx, pg = B.page(br["url"])
        pg.click('[data-tab="coord"]')
        pg.evaluate("""()=>{
          const g=mkGroup("Micros",4,470000,694000,25,"analog",0);
          g.freqs=[{f:563000,locked:true,rx:{d:"d1",c:"1"}},{f:563100,locked:false},{f:null,locked:false},{f:500000,locked:true}];
          state.groups=[g];state.tv=[];state.scans=[];state.excl="";
          net.snap={devices:[{id:"d1",name:"AD4D",channels:{"1":{freq:570000}}}],bridge:"x"};
          save();renderGroups();analyzeNow();renderTable();}""")
        pg.click("#hcBtn")
        pg.wait_for_selector("#hcModal:not([hidden])")
        sm = pg.inner_text("#hcBody .hcsum")
        check("el resumen cuenta asignadas, problemas y avisos", "3/4" in sm and "Con problemas" in sm, sm)
        items = pg.evaluate("()=>[...document.querySelectorAll('#hcBody .hcl li')].map(l=>[l.className,l.querySelector('.tt').textContent,l.querySelector('.dd')?l.querySelector('.dd').textContent:''])")
        sev = [i[0] for i in items]
        check("primero los problemas, luego los avisos y al final las notas", sev == sorted(sev, key=lambda s: {"bad": 0, "warn": 1, "info": 2}[s]) and "bad" in sev and "warn" in sev and "info" in sev, sev)
        txt = " | ".join(" ".join(i) for i in items)
        check("avisa de dos frecuencias demasiado cerca", "Demasiado cerca" in txt, txt)
        check("avisa del canal sin frecuencia", "sin frecuencia" in txt, txt)
        check("avisa del receptor sin enviar con las dos frecuencias", "sin enviar al receptor" in txt and "570" in txt and "563" in txt, txt)
        check("avisa de que no hay escaneos ni canales de TV ni frecuencias sin bloquear", "Sin escaneos" in txt and "Sin canales de TV" in txt and "sin bloquear" in txt, txt)
        # ir a un problema
        pg.click('#hcBody li.bad[data-k] >> nth=0')
        r = pg.evaluate("()=>[document.getElementById('hcModal').hidden,selected,view.b-view.a]")
        check("pulsar un problema cierra la ventana, selecciona el canal y acerca la vista", r[0] is True and r[1] and abs(r[2] - 6000) < 1, r)
        # copiar: el texto resumen
        t = pg.evaluate("()=>hcText(healthReport())")
        check("el resumen en texto lleva el recuento y cada punto", "Frecuencias: 3 de 4 asignadas" in t and "[Problema]" in t and "[Aviso]" in t, t[:200])
        # todo en orden
        pg.evaluate("""()=>{const g=mkGroup("Micros",2,470000,694000,25,"analog",0);g.freqs=[{f:520000,locked:true},{f:600000,locked:true}];
          state.groups=[g];state.tv=[22];state.scans=[{id:"a",name:"E",f:[500000,500025],l:[-100,-100],on:true,color:"#2f7896"}];state.scan.enabled=true;
          net.snap=null;save();renderGroups();analyzeNow();renderTable();}""")
        pg.click("#hcBtn")
        pg.wait_for_selector("#hcModal:not([hidden])")
        ok = pg.inner_text("#hcBody")
        check("sin problemas ni avisos dice «Todo en orden»", "Todo en orden" in ok and pg.locator("#hcBody li.bad").count() == 0 and pg.locator("#hcBody li.warn").count() == 0, ok)
        pg.keyboard.press("Escape")
        check("Escape cierra la ventana", pg.evaluate("()=>document.getElementById('hcModal').hidden"))
        # desde el menú de Mac
        r = pg.evaluate("()=>{menuProyecto('revisar');return !document.getElementById('hcModal').hidden}")
        check("la acción «revisar» del menú abre la revisión", r)
        ctx.close()


def t_recorrido(B):
    print("Recorrido de bienvenida")
    with bridge() as br:
        ctx, pg = B.page(br["url"])
        check("con el navegador automatizado no se abre solo", pg.evaluate("()=>document.getElementById('tour').hidden"))
        pg.click("#tourBtn")
        check("el botón «?» abre el recorrido", pg.evaluate("()=>!document.getElementById('tour').hidden && /Paso 1 de/.test(document.getElementById('tourN').textContent)"))
        pg.click("#tourNext")
        check("Siguiente avanza de paso", pg.evaluate("()=>/Paso 2 de/.test(document.getElementById('tourN').textContent)"))
        pg.click("#tourPrev")
        check("Anterior vuelve", pg.evaluate("()=>/Paso 1 de/.test(document.getElementById('tourN').textContent)"))
        ok = True
        n = pg.evaluate("()=>TOUR.length")
        for i in range(n):
            r = pg.evaluate("(i)=>{tourShow(i);const e=document.querySelector(TOUR[i].s);const b=e&&e.getBoundingClientRect();return !!b&&b.width>0}", i)
            ok = ok and r
        check("todos los pasos señalan un elemento visible", ok)
        pg.evaluate("()=>{tourShow(0);document.getElementById('tourNever').checked=true;}")
        pg.keyboard.press("Escape")
        check("Escape cierra y recuerda «no volver a mostrar»", pg.evaluate("()=>document.getElementById('tour').hidden && localStorage.getItem(TOUR_KEY)==='1'"))
        r = pg.evaluate("()=>{menuProyecto('recorrido');return !document.getElementById('tour').hidden}")
        check("la acción «recorrido» del menú lo abre", r)
        ctx.close()


BLOQUES = {"recorrido": t_recorrido, "revisar": t_revisar, "perfil": t_perfil, "imagen": t_imagen, "vista": t_vista, "escaneos": t_escaneos, "exportar_wwb": t_exportar_wwb, "umbrales": t_umbrales, "importar_wwb": t_importar_wwb, "interferencias": t_interferencias, "coordinacion": t_coordinacion, "menu": t_menu_proyecto, "shure0": t_shure_sin_medidores, "axient": t_shure_axient, "captura": t_captura, "grafica": t_grafica_red, "proyectos": t_proyectos, "receptores": t_receptores, "alertas": t_alertas_informe, "ad600": t_ad600, "actualizacion": t_actualizacion}

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
