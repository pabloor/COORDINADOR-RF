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
        pg.evaluate("()=>{state.scan.f=[100000,100025,900000];state.scan.l=[-50,-50,-60];state.scan.enabled=false;save()}")
        pg.click('[data-tab="live"]')
        pg.select_option("#lvSrc", "sim")
        pg.click("#lvConn")
        pg.wait_for_function("()=>live.f&&live.f.length>10&&live.n>3", timeout=20000)
        pg.select_option("#lvCapDur", "0")
        pg.click("#lvSave")
        r = pg.evaluate("()=>({n:state.scan.f.length,on:state.scan.enabled,th:state.scan.threshold,name:state.scan.name,keep:state.scan.f.includes(100000)&&state.scan.f.includes(900000),sorted:state.scan.f.every((f,i,a)=>!i||a[i-1]<=f)})")
        check("capturar guarda el barrido, activa «evitar» y propone un umbral", r["n"] > 50 and r["on"] and -110 <= r["th"] <= -40, r)
        check("fundir: lo que estaba fuera del rango capturado se conserva y todo queda ordenado", r["keep"] and r["sorted"], r)
        check("el aviso resume ruido, umbral y zonas", "umbral" in pg.inner_text("#toast") and "zona" in pg.inner_text("#toast"), pg.inner_text("#toast"))
        check("los controles del escaneo reflejan el cambio", pg.evaluate("()=>document.getElementById('scanOn').checked"))
        pl = pg.evaluate("""()=>{const f=()=>lctx.getImageData(0,0,1,1);f();const t=performance.now();for(let i=0;i<5;i++){drawLive();f()}return (performance.now()-t)/5}""")
        check("dibujar el espectro en vivo es rápido (< 300 ms; antes más de 900 ms)", pl < 300, pl)
        pw_ = pg.evaluate("""()=>{const n=12000,fk=new Float64Array(n),lv=new Float32Array(n);for(let i=0;i<n;i++){fk[i]=470000+i*224000/(n-1);lv[i]=-100+Math.random()*6}
            onSweep(fk,lv);const f=()=>lctx.getImageData(0,0,1,1);f();const t=performance.now();for(let i=0;i<5;i++){drawLive();f()}return (performance.now()-t)/5}""")
        check("con 12 000 puntos (como un AD600) el dibujo también es rápido", pw_ < 300, pw_)
        n0 = pg.evaluate("()=>state.scan.f.length")
        pg.select_option("#lvCapDur", "10")
        pg.click("#lvSave")
        check("la captura temporizada muestra la cuenta atrás", "Capturando" in pg.inner_text("#lvSave"), pg.inner_text("#lvSave"))
        pg.wait_for_function("()=>!/Capturando/.test(document.getElementById('lvSave').textContent)", timeout=20000)
        check("al terminar guarda el máximo del periodo", "máximo de 10 s" in pg.evaluate("()=>state.scan.name") and pg.evaluate("()=>state.scan.f.length")>=n0 - 5)
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
        pg.wait_for_selector(".tile .rx .leds", timeout=8000)
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
            return "a;b" if "menuTexto" in code else None
        def create_file_dialog(self, *a, **k):
            return [dlg_file[0]]
    wv = types.SimpleNamespace(windows=[W()], FileDialog=types.SimpleNamespace(OPEN=1))
    class Menu:
        def __init__(self, title, items): self.title, self.items = title, items
    class Action:
        def __init__(self, title, function): self.title, self.function = title, function
    class Sep: pass
    mm = types.SimpleNamespace(Menu=Menu, MenuAction=Action, MenuSeparator=Sep)
    pr.copy_clipboard = lambda t: copied.append(t) or True
    m = pr.project_menu(wv, mm)
    acts = {i.title: i.function for i in m[0].items if isinstance(i, Action)}
    check("el menú «Proyecto» tiene las opciones esperadas", m[0].title == "Proyecto" and len(acts) == 12 and "Cambiar de proyecto…" in acts and "Borrar proyecto…" in acts, list(acts))
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
    acts["Cargar archivo como proyecto nuevo…"]()
    check("cargar lee el archivo elegido y se lo pasa a la página", calls[-1].startswith('menuCargar("mi proyecto.json",') and '{\\"groups\\"' in calls[-1].replace('\\"', '\\\\"') or "groups" in calls[-1], calls[-1])


BLOQUES = {"coordinacion": t_coordinacion, "menu": t_menu_proyecto, "shure0": t_shure_sin_medidores, "axient": t_shure_axient, "captura": t_captura, "grafica": t_grafica_red, "proyectos": t_proyectos, "receptores": t_receptores, "alertas": t_alertas_informe, "ad600": t_ad600, "actualizacion": t_actualizacion}

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
