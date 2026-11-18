"""ATTR (Average Two-Terminal Reliability) de los nodos clave, antes y después del sismo.

ATTR = (n sobre 2)^-1 * sum_{i<j} Z_ij        (Boettcher et al., 2018, ec. 8)

Z_ij = 1 si existe un camino entre los nodos clave i y j. Se calcula por
componentes conexas: sum_c n_c (n_c - 1) / 2  /  n (n - 1) / 2.

Después del sismo cada enlace e falla con probabilidad p_falla(e) y el ATTR pasa
a ser una variable aleatoria. Se informan tres cifras:
  * E[ATTR] con fallas independientes (Monte Carlo): cota pesimista.
  * E[ATTR] con fallas totalmente correlacionadas (comonótonas): cota optimista.
  * ATTR del escenario determinista p_falla >= 0,5 (SQL, pgr_connectedComponents).
"""
from __future__ import annotations

import math
import time

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components

from . import config as C
from .db import consultar

MODELOS = {
    "p_falla": "Gaussiano epicentral (principal)",
    "p_falla_lineal": "Lineal epicentral",
    "p_falla_ruptura": "Gaussiano a la ruptura",
}


class Grafo:
    def __init__(self, con):
        # Tramos del grafo (para la conectividad) y enlaces (unidad que falla).
        tramos = consultar(con, "SELECT source, target, tipo, enlace_id FROM grafo ORDER BY id")
        enl = consultar(con, """SELECT enlace_id, dist_epicentro_km, p_falla, p_falla_lineal, p_falla_ruptura
                                  FROM probabilidad_falla ORDER BY enlace_id""")
        nodos = consultar(con, """SELECT id, rol, tipo, nombre, cut FROM nodos_clave
                                  WHERE incluido ORDER BY id""")
        src = np.array([f[0] for f in tramos], dtype=np.int64)
        dst = np.array([f[1] for f in tramos], dtype=np.int64)
        self.tipo = np.array([f[2] for f in tramos])
        pos = {e[0]: i for i, e in enumerate(enl)}
        self.enlace = np.array([pos[f[3]] for f in tramos])          # índice de enlace por tramo
        self.n_enlaces = len(enl)
        self.dist = np.array([e[1] for e in enl], dtype=float)       # por enlace
        self.p = {m: np.array([e[2 + i] for e in enl], dtype=float) for i, m in enumerate(MODELOS)}
        self.nodos = nodos
        self.rol = np.array([n[1] for n in nodos])
        self.tipo_nodo = np.array([n[2] for n in nodos])
        clave = np.array([C.OFFSET_NODO + n[0] for n in nodos], dtype=np.int64)
        todos, inv = np.unique(np.concatenate([src, dst, clave]), return_inverse=True)
        m = len(src)
        self.s, self.t, self.k = inv[:m], inv[m:2 * m], inv[2 * m:]
        self.nv = len(todos)
        self.base = {"completa": np.ones(m, bool), "fibra": np.isin(self.tipo, C.TIPOS_FIBRA)}
        self.es_comuna = self.rol == "recibe"
        self.es_genera = self.rol == "genera"
        self.es_intercambia = self.rol == "intercambia"

    def etiquetas(self, vivos: np.ndarray) -> np.ndarray:
        """Componente conexa de cada nodo clave con los enlaces vivos."""
        s, t = self.s[vivos], self.t[vivos]
        a = csr_matrix((np.ones(len(s), np.int8), (s, t)), shape=(self.nv, self.nv))
        _, lab = connected_components(a, directed=False)
        return lab[self.k]

    @staticmethod
    def attr(lab: np.ndarray) -> float:
        n = len(lab)
        c = np.bincount(lab).astype(float)
        return float((c * (c - 1)).sum() / (n * (n - 1)))

    def acceso(self, lab: np.ndarray) -> np.ndarray:
        """Comunas cuya componente tiene al menos un data center y un IXP/aterrizaje."""
        con_gen = np.zeros(lab.max() + 1, bool)
        con_int = np.zeros(lab.max() + 1, bool)
        con_gen[lab[self.es_genera]] = True
        con_int[lab[self.es_intercambia]] = True
        lc = lab[self.es_comuna]
        return con_gen[lc] & con_int[lc]


def _resumen(x: np.ndarray) -> dict:
    return {"media": float(x.mean()), "desv": float(x.std(ddof=1)),
            "p2_5": float(np.percentile(x, 2.5)), "p97_5": float(np.percentile(x, 97.5))}


def monte_carlo(g: Grafo, red: str, p: np.ndarray, n: int, rng) -> dict:
    """p: probabilidad de falla por enlace. Cada enlace falla de forma independiente
    y arrastra a todos sus tramos."""
    base = g.base[red]
    attrs = np.empty(n)
    accs = np.empty(n)
    acc_comuna = np.zeros(g.es_comuna.sum())
    for i in range(n):
        vivos = base & (rng.random(g.n_enlaces) >= p)[g.enlace]
        lab = g.etiquetas(vivos)
        attrs[i] = g.attr(lab)
        a = g.acceso(lab)
        accs[i] = a.mean()
        acc_comuna += a
    return {"attr": _resumen(attrs), "acceso_comunas": _resumen(accs),
            "p_acceso_por_comuna": acc_comuna / n}


def correlacionado(g: Grafo, red: str, p: np.ndarray, puntos: int = 400) -> dict:
    """Fallas totalmente correlacionadas (comonótonas): un solo U ~ Uniforme(0,1);
    falla todo enlace con p >= U, es decir, si falla un enlace también fallan
    todos los más cercanos al epicentro. Cada enlace conserva su probabilidad
    marginal p, pero las fallas quedan ligadas espacialmente, como en un terremoto
    real. E[ATTR] = integral de ATTR(u) en [0, 1].
    u = 0,5 corresponde al escenario determinista p_falla >= 0,5."""
    u = (np.arange(puntos) + 0.5) / puntos
    curva = np.array([g.attr(g.etiquetas(g.base[red] & (p < x)[g.enlace])) for x in u])
    return {"attr_esperado": float(curva.mean()),
            "curva": [{"u": round(float(x), 5), "attr": round(float(a), 5)} for x, a in zip(u, curva)]}


def criticos(con, g: Grafo, red: str = "completa", top: int = 15) -> list[dict]:
    """Enlaces puente (pgr_bridges) ordenados por riesgo = p_falla * pérdida de ATTR
    si ese enlace falla solo."""
    # Va dentro del literal SQL de pgr_bridges: comillas duplicadas.
    filtro = "" if red == "completa" else "WHERE tipo IN (''fibra'', ''acceso'', ''urbano_supuesto'')"
    puentes = consultar(con, f"""
        SELECT DISTINCT g.enlace_id
          FROM pgr_bridges('SELECT id, source, target, 1::float8 AS cost, 1::float8 AS reverse_cost
                              FROM grafo {filtro}') b
          JOIN grafo g ON g.id = b.edge""")
    ids = consultar(con, "SELECT enlace_id FROM probabilidad_falla ORDER BY enlace_id")
    pos = {e[0]: i for i, e in enumerate(ids)}
    base = g.base[red]
    a0 = g.attr(g.etiquetas(base))
    filas = []
    for (eid,) in puentes:
        vivos = base & (g.enlace != pos[eid])
        perdida = a0 - g.attr(g.etiquetas(vivos))
        if perdida > 1e-9:
            p = float(g.p["p_falla"][pos[eid]])
            filas.append({"enlace_id": eid, "perdida_attr": perdida, "p_falla": p, "riesgo": p * perdida})
    filas.sort(key=lambda f: f["riesgo"], reverse=True)
    if filas:
        info = {r[0]: r[1:] for r in consultar(con, """
            SELECT enlace_id, tipo, longitud_km, dist_epicentro_km,
                   ST_Y(ST_Centroid(geom)), ST_X(ST_Centroid(geom))
              FROM probabilidad_falla WHERE enlace_id = ANY(%s)""", ([f["enlace_id"] for f in filas],))}
        for f in filas:
            t, l, d, la, lo = info[f["enlace_id"]]
            f.update(tipo=t, longitud_km=l, dist_epicentro_km=d, lat=la, lon=lo)
    return {"n_puentes": len(puentes), "n_puentes_con_perdida": len(filas), "top": filas[:top]}


def calcular(con, simulaciones: int = C.SIMULACIONES, semilla: int = C.SEMILLA,
             sensibilidad: bool = True) -> dict:
    t0 = time.time()
    g = Grafo(con)
    rng = np.random.default_rng(semilla)
    n = len(g.k)
    res = {"nodos_clave": {"total": n, "genera": int(g.es_genera.sum()),
                           "intercambia": int(g.es_intercambia.sum()), "recibe": int(g.es_comuna.sum()),
                           "pares": n * (n - 1) // 2},
           "simulaciones": simulaciones, "semilla": semilla, "redes": {}}
    por_comuna = {}
    for red in C.REDES:
        lab0 = g.etiquetas(g.base[red])
        acc0 = g.acceso(lab0)
        sql_antes = consultar(con, "SELECT attr FROM attr_red(%s)", (red,))[0][0]
        r = {"descripcion": C.REDES[red],
             "antes": {"attr": g.attr(lab0), "attr_sql_pgrouting": sql_antes,
                       "componentes_con_nodos_clave": int(len(np.unique(lab0))),
                       "acceso_comunas": float(acc0.mean())},
             "despues": {}}
        por_comuna[red] = {"antes": acc0}
        for modelo, nombre in MODELOS.items():
            mc = monte_carlo(g, red, g.p[modelo], simulaciones, rng)
            det = consultar(con, "SELECT attr FROM attr_red(%s, 0.5, %s)", (red, modelo))[0][0]
            cor = correlacionado(g, red, g.p[modelo])
            r["despues"][modelo] = {"nombre": nombre, "attr_esperado": mc["attr"],
                                    "acceso_comunas": mc["acceso_comunas"],
                                    "attr_determinista_p05": det,
                                    "attr_esperado_correlacionado": cor["attr_esperado"],
                                    "curva_correlacionado": cor["curva"]}
            if modelo == "p_falla":
                por_comuna[red]["despues"] = mc["p_acceso_por_comuna"]
        res["redes"][red] = r

    if sensibilidad:
        sig0 = consultar(con, "SELECT sigma_km FROM parametros_modelo")[0][0]
        sens = []
        for sigma in sorted({100, 150, 200, round(sig0, 1), 300, 400}):
            p = np.exp(-np.minimum(g.dist ** 2 / (2 * sigma ** 2), 700))
            fila = {"sigma_km": sigma, "d50_km": sigma * math.sqrt(2 * math.log(2))}
            for red in C.REDES:
                fila[red] = monte_carlo(g, red, p, max(200, simulaciones // 4), rng)["attr"]["media"]
            sens.append(fila)
        res["sensibilidad_sigma"] = sens

    res["enlaces_criticos"] = {red: criticos(con, g, red) for red in C.REDES}
    _guardar_por_comuna(con, g, por_comuna)
    res["segundos"] = round(time.time() - t0, 1)
    return res


def _guardar_por_comuna(con, g: Grafo, por_comuna: dict) -> None:
    ids = [n[0] for n, es in zip(g.nodos, g.es_comuna) if es]
    with con.cursor() as cur:
        cur.execute("""DROP TABLE IF EXISTS acceso_comunas;
                       CREATE TABLE acceso_comunas (
                           nodo_id integer PRIMARY KEY REFERENCES nodos_clave(id),
                           acceso_antes_completa boolean, p_acceso_despues_completa double precision,
                           acceso_antes_fibra boolean, p_acceso_despues_fibra double precision)""")
        filas = list(zip(ids,
                         map(bool, por_comuna["completa"]["antes"]), map(float, por_comuna["completa"]["despues"]),
                         map(bool, por_comuna["fibra"]["antes"]), map(float, por_comuna["fibra"]["despues"])))
        cur.executemany("INSERT INTO acceso_comunas VALUES (%s,%s,%s,%s,%s)", filas)
    con.commit()
