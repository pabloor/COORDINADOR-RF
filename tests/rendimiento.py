"""Mide el coste del espectro en vivo (simulador): milisegundos por barrido, por dibujo y por fila de cascada.
Uso: python3 tests/rendimiento.py [puntos] [segundos]   (en un Chromium sin GPU los números son pesimistas; sirve para comparar)"""
import json, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import e2e

PUNTOS = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
SINTETICO = PUNTOS > 3000   # más de 3000 puntos: barridos sintéticos a 4 por segundo, como un AD600
SEG = float(sys.argv[2]) if len(sys.argv) > 2 else 8

INSTR = """()=>{
  window.__m={sweep:[],draw:[],water:[],frames:0};
  const wrap=(name,key)=>{const f=window[name];window[name]=function(...a){const t=performance.now();const r=f.apply(this,a);window.__m[key].push(performance.now()-t);return r;}};
  wrap('onSweep','sweep');wrap('drawLive','draw');wrap('addWaterRow','water');
  const raf=()=>{window.__m.frames++;requestAnimationFrame(raf)};requestAnimationFrame(raf);
  window.__long=0;try{new PerformanceObserver(l=>{for(const e of l.getEntries())window.__long+=e.duration}).observe({entryTypes:['longtask']})}catch(e){}
}"""

B = e2e.Browser()
with e2e.bridge() as br:
    ctx = B.b.new_context(viewport={"width": 1500, "height": 950}, device_scale_factor=2)
    pg = ctx.new_page()
    pg.on("pageerror", lambda e: print("ERROR JS:", e))
    pg.goto(br["url"]); pg.wait_for_timeout(1000)
    for tab, extra in (("live", ""),):
        pg.evaluate("(n)=>{state.live.points=Math.min(n,3000);state.live.start=470000;state.live.stop=694000;state.live.maxOn=true;state.live.avgOn=true;save();}", PUNTOS)
        pg.click('[data-tab="live"]'); pg.select_option("#lvSrc", "sim")
        pg.evaluate(INSTR)
        if SINTETICO:
            pg.evaluate("""(n)=>{const fk=new Float64Array(n);for(let i=0;i<n;i++)fk[i]=470000+i*(224000/(n-1));
                window.__iv=setInterval(()=>{const lv=new Float32Array(n);for(let i=0;i<n;i++)lv[i]=-100+Math.random()*6+(i%700<8?50:0);onSweep(fk,lv)},250)}""", PUNTOS)
            pg.wait_for_timeout(1200)
            pg.evaluate("()=>{window.__m.sweep.length=0;window.__m.draw.length=0;window.__m.water.length=0;window.__m.frames=0}")
        else:
            pg.click("#lvConn")
        pg.wait_for_timeout(int(SEG * 1000))
        r = pg.evaluate("()=>{const m=window.__m,s=a=>a.reduce((x,y)=>x+y,0),p=(a,q)=>{const b=[...a].sort((x,y)=>x-y);return b.length?b[Math.min(b.length-1,Math.floor(b.length*q))]:0};return {n:m.sweep.length,sweep_ms:s(m.sweep),sweep_p95:p(m.sweep,.95),draws:m.draw.length,draw_ms:s(m.draw),draw_p95:p(m.draw,.95),waters:m.water.length,water_ms:s(m.water),water_p95:p(m.water,.95),frames:m.frames,long:window.__long}}")
        tot = r["sweep_ms"]
        print(f"puntos={PUNTOS}  durante {SEG:.0f} s")
        print(f"  barridos recibidos: {r['n']}  (onSweep entero: {r['sweep_ms']:.0f} ms en total, p95 {r['sweep_p95']:.1f} ms)")
        print(f"  dibujos del espectro: {r['draws']}  ({r['draw_ms']:.0f} ms en total, p95 {r['draw_p95']:.1f} ms)")
        print(f"  filas de cascada: {r['waters']}  ({r['water_ms']:.0f} ms en total, p95 {r['water_p95']:.1f} ms)")
        busy = (r["sweep_ms"] + r["draw_ms"]) / (SEG * 1000) * 100
        print(f"  hilo principal ocupado por el espectro: {busy:.0f} %   cuadros/s: {r['frames']/SEG:.0f}   tareas largas (>50 ms): {r['long']:.0f} ms")
    ctx.close()
B.close()
