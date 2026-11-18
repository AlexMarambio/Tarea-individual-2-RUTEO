"""Genera vistas del mapa y las captura con un navegador Chromium sin ventana (Edge o Chrome).

    python scripts/capturas_mapa.py

Requiere la BD cargada (python main.py) y salida/resultados.json.
Las imágenes quedan en informe/capturas/.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tarea.config import RAIZ, SALIDA  # noqa: E402
from tarea.db import conectar  # noqa: E402
from tarea.mapa import generar  # noqa: E402

NAVEGADORES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "msedge", "google-chrome", "chromium", "chrome",
]

VISTAS = [
    # nombre, centro, zoom, capas extra, panel abierto, tamaño
    ("01_mapa_general", (-36.0, -71.0), 5, (), True, (1500, 1500)),
    ("02_mapa_zona_epicentro", (-36.0, -72.2), 8, (), False, (1600, 1100)),
    ("03_mapa_santiago_nodos", (-33.45, -70.68), 10, (), False, (1600, 1100)),
    ("04_mapa_santiago_accesos_urbanos", (-33.47, -70.66), 11,
     ("Accesos y enlaces urbanos supuestos",), False, (1600, 1100)),
    ("06_mapa_zona_maule", (-35.6, -71.6), 9, ("Caminos sin postación",), False, (1600, 1100)),
    ("07_mapa_enlaces_criticos_santiago_norte", (-33.1, -70.9), 9, (), False, (1600, 1100)),
]


def navegador() -> str:
    for n in NAVEGADORES:
        ruta = shutil.which(n) or (n if Path(n).exists() else None)
        if ruta:
            return ruta
    raise SystemExit("No encontré Edge ni Chrome")


def main():
    exe = navegador()
    con = conectar()
    res = json.loads((SALIDA / "resultados.json").read_text(encoding="utf-8"))
    tmp = SALIDA / "vistas"
    tmp.mkdir(exist_ok=True)
    destino = RAIZ / "informe" / "capturas"
    destino.mkdir(parents=True, exist_ok=True)
    for nombre, centro, zoom, extra, panel, (w, h) in VISTAS:
        html = Path(generar(con, res, tmp / f"{nombre}.html", centro, zoom, extra, panel))
        png = destino / f"{nombre}.png"
        subprocess.run([exe, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                        f"--window-size={w},{h}", "--virtual-time-budget=25000",
                        f"--screenshot={png}", html.as_uri()],
                       check=True, capture_output=True)
        print("  ", png.relative_to(RAIZ))
    shutil.rmtree(tmp)


if __name__ == "__main__":
    main()
