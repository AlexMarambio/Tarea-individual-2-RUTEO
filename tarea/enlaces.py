"""Agrupa los tramos del grafo en enlaces (contracción de cadenas de grado 2).

Un tramo de la topología es un trozo de levantamiento (en promedio unos 5 km), no
un enlace de red. Si cada tramo fallara de forma independiente, un corredor de
300 km formado por 60 tramos en serie se cortaría casi siempre aunque cada tramo
tuviera una probabilidad de falla baja, y el resultado dependería de cómo se
digitalizaron las líneas. Por eso, siguiendo a Agarwal et al. (2013), donde
un enlace es la conexión física entre dos nodos de la red, se contraen las
cadenas de tramos del mismo tipo que pasan por vértices de grado 2. Cada enlace
resultante une dos nodos de grado distinto de 2 (cruces, extremos o puntos de acceso).
"""
from collections import defaultdict

from psycopg2.extras import execute_values

from .config import OFFSET_NODO
from .db import consultar


def contraer(con) -> dict:
    filas = consultar(con, "SELECT id, source, target, tipo FROM grafo ORDER BY id")
    padre = {f[0]: f[0] for f in filas}

    def raiz(x):
        while padre[x] != x:
            padre[x] = padre[padre[x]]
            x = padre[x]
        return x

    incidentes = defaultdict(list)
    for i, s, t, tipo in filas:
        incidentes[s].append((i, tipo))
        incidentes[t].append((i, tipo))

    for v, lst in incidentes.items():
        if v >= OFFSET_NODO or len(lst) != 2:
            continue
        (a, ta), (b, tb) = lst
        if a != b and ta == tb:
            padre[raiz(a)] = raiz(b)

    # Numerar enlaces en orden de su primer tramo.
    ids, enlace_de = {}, {}
    for i, *_ in filas:
        r = raiz(i)
        if r not in ids:
            ids[r] = len(ids) + 1
        enlace_de[i] = ids[r]

    # Extremos de cada enlace: vértices que aparecen una sola vez entre sus tramos.
    cuenta = defaultdict(lambda: defaultdict(int))
    for i, s, t, _ in filas:
        cuenta[enlace_de[i]][s] += 1
        cuenta[enlace_de[i]][t] += 1
    extremos = {}
    for e, c in cuenta.items():
        ext = sorted(v for v, n in c.items() if n == 1)
        extremos[e] = (ext[0], ext[-1]) if ext else (min(c), min(c))  # anillo cerrado

    with con.cursor() as cur:
        cur.execute("ALTER TABLE grafo ADD COLUMN IF NOT EXISTS enlace_id integer")
        cur.execute("CREATE TEMP TABLE tmp_enlace (tramo integer, enlace integer) ON COMMIT DROP")
        execute_values(cur, "INSERT INTO tmp_enlace VALUES %s", list(enlace_de.items()), page_size=2000)
        cur.execute("UPDATE grafo g SET enlace_id = t.enlace FROM tmp_enlace t WHERE t.tramo = g.id")
        cur.execute("""
            DROP TABLE IF EXISTS enlaces CASCADE;
            CREATE TABLE enlaces AS
            SELECT enlace_id AS id,
                   min(tipo) AS tipo,
                   count(*)::integer AS n_tramos,
                   sum(longitud_km) AS longitud_km,
                   NULL::bigint AS source,
                   NULL::bigint AS target,
                   ST_Multi(ST_CollectionExtract(ST_LineMerge(ST_Collect(geom)), 2))
                       ::geometry(MultiLineString, 4326) AS geom
              FROM grafo GROUP BY enlace_id;
            UPDATE enlaces e SET geom = s.g
              FROM (SELECT enlace_id, ST_Multi(ST_Collect(geom))::geometry(MultiLineString, 4326) AS g
                      FROM grafo GROUP BY enlace_id) s
             WHERE s.enlace_id = e.id AND ST_IsEmpty(e.geom);
            ALTER TABLE enlaces ADD PRIMARY KEY (id);
            CREATE INDEX ON enlaces USING gist (geom);
            CREATE INDEX ON grafo (enlace_id);""")
        cur.execute("CREATE TEMP TABLE tmp_ext (id integer, s bigint, t bigint) ON COMMIT DROP")
        execute_values(cur, "INSERT INTO tmp_ext VALUES %s",
                       [(e, s, t) for e, (s, t) in extremos.items()], page_size=2000)
        cur.execute("UPDATE enlaces e SET source = x.s, target = x.t FROM tmp_ext x WHERE x.id = e.id")
    con.commit()
    return {"tramos": len(filas), "enlaces": len(ids)}
