"""Parámetros de la tarea. La conexión se puede cambiar con las variables PG* estándar."""
import os
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DATA = RAIZ / "data"
SQL = RAIZ / "sql"
SALIDA = RAIZ / "salida"

DB = {
    "host": os.getenv("PGHOST", "localhost"),
    "port": int(os.getenv("PGPORT", "5434")),
    "dbname": os.getenv("PGDATABASE", "ruteo"),
    "user": os.getenv("PGUSER", "ruteo"),
    "password": os.getenv("PGPASSWORD", "ruteo"),
}

# Topología
TOLERANCIA_NODADO = 0.000001    # grilla de ST_UnaryUnion (≈ 0,1 m)
TOLERANCIA_TOPOLOGIA = 0.0001   # pgr_createTopology (≈ 11 m): une extremos cercanos

# Grafo
OFFSET_NODO = 1_000_000         # id de vértice de un nodo clave = OFFSET + nodos_clave.id
R_ACCESO_KM = 2.0               # nodo clave -> todos los vértices a esta distancia
R_MAX_KM = 25.0                 # distancia máxima para el vértice más cercano
R_URBANO_KM = 15.0              # data center / IXP / aterrizaje -> centro comunal
LARGO_MAX_URBANO_KM = 15.0      # aristas Delaunay del Gran Santiago

# Comunas insulares sin enlace físico al continente en los datos (solo satélite).
CUT_EXCLUIDOS = {
    "05201": "Isla de Pascua: insular, sin enlace físico al continente en los datos",
    "05104": "Juan Fernández: insular, sin enlace físico al continente en los datos",
}

# Modelo de falla
MMI_REFERENCIA = 7.0            # isosista usada para calibrar sigma (p = 0,5)
SIGMA_KM = 0                    # 0 = calibración automática con el ShakeMap

# ATTR después del sismo (Monte Carlo)
SIMULACIONES = 2000
SEMILLA = 42

REDES = {
    "completa": "Todas las capas de Yafún (fibra, postación, caminos, ferry)",
    "fibra": "Solo fibra óptica detectada",
}
TIPOS_FIBRA = ("fibra", "acceso", "urbano_supuesto")
