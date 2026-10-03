"""Pruebas de extremo a extremo de Coordinador RF: puente real + interfaz en Chromium (Playwright).
Cada bloque arranca su propio puente con una carpeta de usuario temporal, así que no se afectan entre sí.
Uso:  python3 tests/e2e.py [bloque ...]      (sin argumentos, todos).   PW_CHROMIUM=/ruta/al/chromium si hace falta."""
import contextlib, glob, json, os, re, socket, subprocess, sys, tempfile, time, urllib.request
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
def bridge(kind="demo"):
    home, port = tempfile.mkdtemp(prefix="crf-"), free_port()
    log = open(os.path.join(home, "bridge.log"), "w")
    cmd = [sys.executable, os.path.join(ROOT, "puente-rf.py"), "--sin-navegador", "--puerto", str(port)] + (["--demo"] if kind == "demo" else [])
    if kind == "ad600":
        cmd = [sys.executable, os.path.join(ROOT, "tests", "ad600_harness.py"), str(port)]
    pr = subprocess.Popen(cmd, env=dict(os.environ, HOME=home, AD600_ENGINE_SCRATCH=os.path.join(home, "ad600")), stdout=log, stderr=subprocess.STDOUT)
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


BLOQUES = {"coordinacion": t_coordinacion, "proyectos": t_proyectos, "receptores": t_receptores, "alertas": t_alertas_informe, "ad600": t_ad600}

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
