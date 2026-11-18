"""Genera main.sql con las probabilidades de falla de cada enlace."""
from datetime import date

from .config import RAIZ
from .db import consultar

COLUMNAS = ("enlace_id integer PRIMARY KEY",
            "source bigint",
            "target bigint",
            "tipo text",
            "n_tramos integer",
            "longitud_km double precision",
            "dist_epicentro_km double precision",
            "dist_ruptura_km double precision",
            "p_falla double precision",
            "p_falla_lineal double precision",
            "p_falla_ruptura double precision",
            "geom geometry(MultiLineString, 4326)")


def _lit(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, str):
        return "'" + v.replace("'", "''") + "'"
    if isinstance(v, float):
        return repr(round(v, 6))
    return str(v)


def main_sql(con, ruta=None) -> dict:
    ruta = ruta or RAIZ / "main.sql"
    (modelo, beta, mmi, d50, sigma, d50r, sigmar), = consultar(con, "SELECT * FROM parametros_modelo")
    (sid, mag, lugar, fecha, lon, lat), = consultar(
        con, "SELECT id, magnitud, lugar, fecha_utc, ST_X(geom), ST_Y(geom) FROM sismo")
    filas = consultar(con, """
        SELECT enlace_id, source, target, tipo, n_tramos, longitud_km, dist_epicentro_km,
               dist_ruptura_km, p_falla, p_falla_lineal, p_falla_ruptura, ST_AsEWKT(geom, 6)
          FROM probabilidad_falla ORDER BY enlace_id""")
    cab = f"""-- main.sql
-- Probabilidad de falla de cada enlace de la infraestructura chilena (Yafún, NIC 2018)
-- frente al terremoto {lugar} (USGS {sid}), Mw {mag}, {fecha:%Y-%m-%d %H:%M} UTC,
-- epicentro ({lat:.3f}, {lon:.3f}).
-- Generado por main.py el {date.today().isoformat()}.
--
-- Modelo: {modelo}
--   p_falla(e) = beta * exp(-d(e, epicentro)^2 / (2 sigma^2))
--   beta = {beta:g}; sigma = {sigma:.1f} km. Calibrado para que p_falla = 0,5 a la distancia
--   mediana ({d50:.1f} km) de la isosista MMI {mmi:g} del ShakeMap del USGS.
--   p_falla_lineal  = max(0, 1 - d / (2 * {d50:.1f}))
--   p_falla_ruptura = exp(-d(e, ruptura)^2 / (2 * {sigmar:.1f}^2))   (d50 = {d50r:.1f} km)
--
-- Un enlace es una cadena de tramos entre dos nodos de grado distinto de 2 (cruces,
-- extremos o puntos de acceso de un nodo clave). source/target son los ids de vértice del
-- grafo: los menores a 1.000.000 vienen de pgr_createTopology y los mayores son
-- 1.000.000 + id del nodo clave.
-- tipo: fibra | postacion | camino | ferry | acceso | urbano_supuesto
-- Unos pocos enlaces de acceso tienen largo 0 (nodo clave sobre el mismo punto que su
-- vértice); su geometría no es válida para PostGIS pero se conservan por conectividad.
--
-- Carga:  psql -d <base> -f main.sql   (requiere PostGIS)

BEGIN;
CREATE EXTENSION IF NOT EXISTS postgis;
DROP TABLE IF EXISTS probabilidad_falla CASCADE;
CREATE TABLE probabilidad_falla (
    {(','+chr(10)+'    ').join(COLUMNAS)}
);
"""
    partes = [cab]
    for i in range(0, len(filas), 500):
        bloque = filas[i:i + 500]
        partes.append("INSERT INTO probabilidad_falla VALUES\n" + ",\n".join(
            "(" + ", ".join(_lit(v) for v in f) + ")" for f in bloque) + ";\n")
    partes.append("CREATE INDEX ON probabilidad_falla USING gist (geom);\nCOMMIT;\n")
    ruta.write_text("".join(partes), encoding="utf-8", newline="\n")
    return {"archivo": str(ruta.name), "enlaces": len(filas), "kb": round(ruta.stat().st_size / 1024)}
