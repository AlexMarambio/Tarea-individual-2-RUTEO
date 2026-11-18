"""Carga de datos en la BD: infraestructura (shapefiles), nodos clave y sismo (GeoJSON)."""
import json
from datetime import datetime, timezone

import shapefile
from psycopg2.extras import execute_values

from .config import CUT_EXCLUIDOS, DATA
from .db import ejecutar_sql

CAPAS = {
    "fibra": "fibra_optica_detectada",
    "postacion": "postacion_electrica_detectada",
    "camino": "camino_sin_postacion_detectado",
    "ferry": "trayecto_en_ferry",
}

ARCHIVOS_NODOS = ("comunas.geojson", "datacenters.geojson", "ixp.geojson", "aterrizajes_cables.geojson")


def _wkt_linea(puntos) -> str | None:
    limpios = [p for i, p in enumerate(puntos) if i == 0 or p != puntos[i - 1]]
    if len(limpios) < 2:
        return None
    return "LINESTRING(" + ",".join(f"{x} {y}" for x, y in limpios) + ")"


def cargar_infraestructura(con) -> dict:
    conteo = {}
    filas = []
    for capa, nombre in CAPAS.items():
        r = shapefile.Reader(str(DATA / "infraestructura" / nombre), encoding="latin-1")
        n = 0
        for rec, shp in zip(r.records(), r.shapes()):
            wkt = _wkt_linea([tuple(p) for p in shp.points])
            if wkt:
                filas.append((capa, str(rec[0]), wkt))
                n += 1
        conteo[capa] = n
    with con.cursor() as cur:
        execute_values(cur, "INSERT INTO infraestructura (capa, id_original, geom) VALUES %s", filas,
                       template="(%s, %s, ST_SetSRID(ST_GeomFromText(%s), 4326))", page_size=500)
    con.commit()
    return conteo


def cargar_nodos(con) -> dict:
    filas = []
    for archivo in ARCHIVOS_NODOS:
        fc = json.loads((DATA / "nodos" / archivo).read_text(encoding="utf-8"))
        for f in fc["features"]:
            p = f["properties"]
            lon, lat = f["geometry"]["coordinates"][:2]
            motivo = CUT_EXCLUIDOS.get(p.get("cut"))
            filas.append((p["rol"], p["tipo"], p["nombre"], p.get("cut"), p.get("fuente"),
                          bool(p.get("ubicacion_aproximada")), motivo is None, motivo, lon, lat))
    with con.cursor() as cur:
        execute_values(cur, """INSERT INTO nodos_clave (rol, tipo, nombre, cut, fuente,
                       ubicacion_aproximada, incluido, motivo_exclusion, geom) VALUES %s""", filas,
                       template="(%s,%s,%s,%s,%s,%s,%s,%s, ST_SetSRID(ST_MakePoint(%s,%s),4326))")
        cur.execute("SELECT tipo, count(*) FILTER (WHERE incluido), count(*) FROM nodos_clave GROUP BY tipo")
        res = {t: (inc, tot) for t, inc, tot in cur.fetchall()}
    con.commit()
    return res


def cargar_sismo(con) -> dict:
    fc = json.loads((DATA / "sismo" / "epicentro_maule_2010_usgs.geojson").read_text(encoding="utf-8"))
    f = max(fc["features"], key=lambda x: x["properties"]["mag"])
    p = f["properties"]
    lon, lat, prof = f["geometry"]["coordinates"]
    fecha = datetime.fromtimestamp(p["time"] / 1000, tz=timezone.utc)
    with con.cursor() as cur:
        cur.execute("""INSERT INTO sismo (id, magnitud, tipo_magnitud, lugar, fecha_utc, profundidad_km,
                       geojson, geom) VALUES (%s,%s,%s,%s,%s,%s,%s, ST_SetSRID(ST_MakePoint(%s,%s),4326))""",
                    (f["id"], p["mag"], p["magType"], p["place"], fecha, prof, json.dumps(f), lon, lat))
        for parametro in ("mmi", "pga", "pgv"):
            cont = json.loads((DATA / "sismo" / f"cont_{parametro}.json").read_text(encoding="utf-8"))
            for c in cont["features"]:
                cur.execute("""INSERT INTO sismo_shakemap (parametro, valor, unidades, geom)
                               VALUES (%s, %s, %s, ST_Multi(ST_Force2D(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326))))""",
                            (parametro, c["properties"]["value"], c["properties"].get("units"),
                             json.dumps(c["geometry"])))
        rup = json.loads((DATA / "sismo" / "rupture.json").read_text(encoding="utf-8"))
        for r in rup["features"]:
            cur.execute("""INSERT INTO sismo_ruptura (geom)
                           VALUES (ST_Multi(ST_Force2D(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326))))""",
                        (json.dumps(r["geometry"]),))
    con.commit()
    return {"id": f["id"], "magnitud": p["mag"], "lugar": p["place"], "fecha_utc": fecha.isoformat(),
            "lon": lon, "lat": lat, "profundidad_km": prof}


def cargar_todo(con) -> dict:
    ejecutar_sql(con, "01_esquema.sql")
    return {
        "infraestructura": cargar_infraestructura(con),
        "nodos_clave": cargar_nodos(con),
        "sismo": cargar_sismo(con),
    }
