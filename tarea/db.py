"""Conexión a PostgreSQL y ejecución de los scripts de sql/."""
import time

import psycopg2

from .config import DB, SQL


def conectar():
    try:
        return psycopg2.connect(**DB)
    except psycopg2.OperationalError as e:
        raise SystemExit(
            f"No se pudo conectar a PostgreSQL en {DB['host']}:{DB['port']}.\n"
            "Levanta la base con:  docker compose up -d\n"
            f"Detalle: {e}") from e


def ejecutar_sql(con, archivo: str, **parametros) -> float:
    """Ejecuta sql/<archivo> reemplazando {{parametro}} por su valor."""
    texto = (SQL / archivo).read_text(encoding="utf-8")
    for k, v in parametros.items():
        texto = texto.replace("{{" + k + "}}", str(v))
    if "{{" in texto:
        faltan = sorted({t.split("}}")[0] for t in texto.split("{{")[1:]})
        raise ValueError(f"{archivo}: faltan parámetros {faltan}")
    t0 = time.time()
    with con.cursor() as cur:
        cur.execute(texto)
    con.commit()
    return time.time() - t0


def consultar(con, sql: str, params=None) -> list[tuple]:
    with con.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def escalar(con, sql: str, params=None):
    filas = consultar(con, sql, params)
    return filas[0][0] if filas else None
