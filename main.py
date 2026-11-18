"""Resiliencia de la infraestructura chilena de Internet frente al terremoto Mw 8.8 (Maule 2010).

Uso:
    docker compose up -d            # PostgreSQL + PostGIS + pgRouting 3.8
    python main.py                  # todo: carga, topología, falla, ATTR, main.sql y mapa
    python main.py --abrir          # además abre el mapa en el navegador

Pasos sueltos (en este orden):
    python main.py cargar           # shapefiles Yafún + nodos clave + sismo GeoJSON -> BD
    python main.py topologia        # nodado + pgr_createTopology
    python main.py grafo            # nodos clave, accesos, Gran Santiago y enlaces
    python main.py falla            # probabilidad de falla por enlace + main.sql
    python main.py attr             # ATTR antes y después
    python main.py mapa [--abrir]   # salida/mapa.html (Leaflet)
"""
import argparse
import json
import sys
import time
import webbrowser

from tarea import config as C
from tarea.attr import calcular
from tarea.carga import cargar_todo
from tarea.db import conectar, consultar, ejecutar_sql
from tarea.enlaces import contraer
from tarea.exportar import main_sql
from tarea.mapa import generar

RESULTADOS = C.SALIDA / "resultados.json"


def paso(titulo):
    print(f"\n== {titulo}", flush=True)


def cmd_cargar(con, a):
    paso("Carga de datos")
    r = cargar_todo(con)
    for capa, n in r["infraestructura"].items():
        print(f"  infraestructura/{capa:<10} {n:>5} tramos")
    for tipo, (inc, tot) in r["nodos_clave"].items():
        print(f"  nodos_clave/{tipo:<17} {inc:>4} incluidos de {tot}")
    s = r["sismo"]
    print(f"  sismo: {s['lugar']} Mw {s['magnitud']} ({s['lat']}, {s['lon']}), {s['fecha_utc']}")


def cmd_topologia(con, a):
    paso("Topología (nodado + pgr_createTopology)")
    t = ejecutar_sql(con, "02_topologia.sql", tolerancia_nodado=C.TOLERANCIA_NODADO,
                     tolerancia_topologia=C.TOLERANCIA_TOPOLOGIA)
    for capa, n, km in consultar(con, """SELECT capa, count(*), sum(longitud_km)
                                       FROM infraestructura_noded GROUP BY capa ORDER BY capa"""):
        print(f"  {capa:<10} {n:>5} tramos {km:>9,.0f} km")
    nv = consultar(con, "SELECT count(*) FROM infraestructura_noded_vertices_pgr")[0][0]
    print(f"  vértices: {nv}  ({t:.1f} s)")


def cmd_grafo(con, a):
    paso("Grafo con nodos clave y enlaces")
    ejecutar_sql(con, "03_grafo.sql", offset=C.OFFSET_NODO, r_acceso_km=C.R_ACCESO_KM,
                 r_max_km=C.R_MAX_KM, r_urbano_km=C.R_URBANO_KM,
                 largo_max_urbano_km=C.LARGO_MAX_URBANO_KM)
    r = contraer(con)
    print(f"  {r['tramos']} tramos agrupados en {r['enlaces']} enlaces")
    for tipo, n, km in consultar(con, "SELECT tipo, count(*), sum(longitud_km) FROM enlaces GROUP BY 1 ORDER BY 1"):
        print(f"  {tipo:<16} {n:>5} enlaces {km:>9,.0f} km")


def cmd_falla(con, a):
    paso("Probabilidad de falla por enlace")
    ejecutar_sql(con, "04_falla.sql", mmi_ref=C.MMI_REFERENCIA, sigma_km=a.sigma)
    (_, beta, mmi, d50, sigma, d50r, sigmar), = consultar(con, "SELECT * FROM parametros_modelo")
    print(f"  p = {beta:g}·exp(-d²/(2σ²)), σ = {sigma:.1f} km (p = 0,5 a {d50:.1f} km, isosista MMI {mmi:g})")
    for b, n, km in consultar(con, """SELECT width_bucket(p_falla, 0, 1.0000001, 5), count(*), sum(longitud_km)
                                    FROM probabilidad_falla GROUP BY 1 ORDER BY 1"""):
        print(f"  p_falla en [{(b - 1) / 5:.1f}, {b / 5:.1f}): {n:>5} enlaces {km:>9,.0f} km")
    r = main_sql(con)
    print(f"  {r['archivo']}: {r['enlaces']} enlaces ({r['kb']} KB)")


def imprimir(res):
    print(f"  Nodos clave: {res['nodos_clave']['total']} ({res['nodos_clave']['genera']} generan, "
          f"{res['nodos_clave']['intercambia']} intercambian, {res['nodos_clave']['recibe']} reciben)")
    print(f"  {'red':<9} {'antes':>7} {'E[ATTR] indep (IC95)':>27} {'E[ATTR] correl':>15} {'p>=0,5':>8}")
    for red, r in res["redes"].items():
        d = r["despues"]["p_falla"]
        a = d["attr_esperado"]
        ic = f"{a['media']:.4f} [{a['p2_5']:.4f}-{a['p97_5']:.4f}]"
        print(f"  {red:<9} {r['antes']['attr']:>7.4f} {ic:>27} {d['attr_esperado_correlacionado']:>15.4f} "
              f"{d['attr_determinista_p05']:>8.4f}")
        print(f"  {'':<9} comunas con acceso a data center + IXP: {100 * r['antes']['acceso_comunas']:.1f}% -> "
              f"{100 * d['acceso_comunas']['media']:.1f}%")


def cmd_attr(con, a):
    paso(f"ATTR antes y después ({a.simulaciones} simulaciones)")
    res = calcular(con, simulaciones=a.simulaciones)
    C.SALIDA.mkdir(exist_ok=True)
    RESULTADOS.write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    imprimir(res)
    print(f"  -> {RESULTADOS.relative_to(C.RAIZ)} ({res['segundos']} s)")
    return res


def cmd_mapa(con, a):
    paso("Mapa Leaflet")
    if not RESULTADOS.exists():
        cmd_attr(con, a)
    res = json.loads(RESULTADOS.read_text(encoding="utf-8"))
    ruta = generar(con, res)
    print(f"  -> {ruta}")
    if a.abrir:
        webbrowser.open(f"file:///{ruta}")


PASOS = {"cargar": cmd_cargar, "topologia": cmd_topologia, "grafo": cmd_grafo,
         "falla": cmd_falla, "attr": cmd_attr, "mapa": cmd_mapa}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("paso", nargs="?", default="todo", choices=["todo", *PASOS])
    p.add_argument("--simulaciones", type=int, default=C.SIMULACIONES)
    p.add_argument("--sigma", type=float, default=C.SIGMA_KM,
                   help="sigma del modelo en km (0 = calibrar con el ShakeMap)")
    p.add_argument("--abrir", action="store_true", help="abrir el mapa en el navegador")
    a = p.parse_args()
    t0 = time.time()
    con = conectar()
    for nombre in (PASOS if a.paso == "todo" else [a.paso]):
        PASOS[nombre](con, a)
    print(f"\nListo en {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
