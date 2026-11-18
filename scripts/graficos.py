"""Gráficos para el informe a partir de salida/resultados.json y la BD.

    python scripts/graficos.py      (requiere matplotlib)

Salida: informe/graficos/*.png
"""
import json
import math
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tarea.config import RAIZ, SALIDA  # noqa: E402
from tarea.db import conectar, consultar  # noqa: E402

# Paleta validada (dataviz, slots 1-2, modo claro) y tinta de texto.
SUP, TXT, TXT2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
SERIE = {"completa": "#2a78d6", "fibra": "#eb6834"}
NOMBRE = {"completa": "Red completa", "fibra": "Solo fibra"}
OUT = RAIZ / "informe" / "graficos"

plt.rcParams.update({
    "figure.facecolor": SUP, "axes.facecolor": SUP, "savefig.facecolor": SUP,
    "axes.edgecolor": GRID, "axes.labelcolor": TXT2, "xtick.color": TXT2, "ytick.color": TXT2,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8, "axes.axisbelow": True,
    "axes.spines.top": False, "axes.spines.right": False, "font.size": 10,
    "axes.titlesize": 12, "axes.titleweight": "bold", "axes.titlecolor": TXT, "axes.titlelocation": "left",
    "legend.frameon": False, "legend.labelcolor": TXT, "lines.linewidth": 2,
})


def coma(x, d=2):
    return f"{x:.{d}f}".replace(".", ",")


def guardar(fig, nombre):
    OUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT / nombre, dpi=160, bbox_inches="tight")
    plt.close(fig)
    print("  ", (OUT / nombre).relative_to(RAIZ))


def modelo_falla(con):
    (_, beta, mmi, d50, sigma, _, _), = consultar(con, "SELECT * FROM parametros_modelo")
    d = [i for i in range(0, 1201, 5)]
    gauss = [beta * math.exp(-(x ** 2) / (2 * sigma ** 2)) for x in d]
    lineal = [max(0.0, 1 - x / (2 * d50)) for x in d]
    fig, ax = plt.subplots(figsize=(8, 4.2))
    ax.plot(d, gauss, color=SERIE["completa"], label=f"Gaussiano (σ = {coma(sigma, 0)} km) · principal")
    ax.plot(d, lineal, color=SERIE["fibra"], label="Lineal (variante)")
    ax.axvline(d50, color=TXT2, lw=1, ls="--")
    ax.annotate(f"p = 0,5 a {coma(d50, 0)} km\n(mediana isosista MMI {coma(mmi, 0)})",
                xy=(d50, 0.5), xytext=(d50 + 40, 0.72), color=TXT, fontsize=9,
                arrowprops={"arrowstyle": "-", "color": TXT2, "lw": 0.8})
    ax.set_xlabel("Distancia mínima del enlace al epicentro (km)")
    ax.set_ylabel("Probabilidad de falla")
    ax.set_ylim(0, 1.02)
    ax.set_xlim(0, 1200)
    ax.set_title("Modelo de falla geográfica (Agarwal et al., 2013) calibrado con el ShakeMap")
    ax.legend(loc="upper right")
    guardar(fig, "08_modelo_falla.png")


def resumen(res):
    etapas = [("Antes", lambda r: r["antes"]["attr"]),
              ("Después:\nfallas independientes", lambda r: r["despues"]["p_falla"]["attr_esperado"]["media"]),
              ("Después:\nescenario p ≥ 0,5", lambda r: r["despues"]["p_falla"]["attr_determinista_p05"]),
              ("Después:\nfallas correlacionadas", lambda r: r["despues"]["p_falla"]["attr_esperado_correlacionado"])]
    fig, ax = plt.subplots(figsize=(8.5, 4.4))
    ancho = 0.36
    for j, red in enumerate(("completa", "fibra")):
        xs = [i + (j - 0.5) * (ancho + 0.03) for i in range(len(etapas))]
        vals = [f(res["redes"][red]) for _, f in etapas]
        ax.bar(xs, vals, width=ancho, color=SERIE[red], label=NOMBRE[red], edgecolor=SUP, linewidth=2)
        ia = res["redes"][red]["despues"]["p_falla"]["attr_esperado"]
        for i, (x, v) in enumerate(zip(xs, vals)):
            y = (ia["p97_5"] if i == 1 and red == "completa" else v) + 0.015   # sobre la barra de error
            ax.text(x, y, coma(v, 3), ha="center", va="bottom", fontsize=8.5, color=TXT)
    ia = res["redes"]["completa"]["despues"]["p_falla"]["attr_esperado"]
    ax.errorbar([1 - 0.5 * (ancho + 0.03)], [ia["media"]],
                yerr=[[ia["media"] - ia["p2_5"]], [ia["p97_5"] - ia["media"]]], fmt="none", ecolor=TXT2,
                capsize=3, lw=1)
    ax.set_xticks(range(len(etapas)), [e for e, _ in etapas])
    ax.set_ylabel("ATTR de los nodos clave")
    ax.set_ylim(0, 1.08)
    ax.grid(axis="x", visible=False)
    ax.set_title("ATTR antes y después del terremoto Mw 8.8")
    ax.legend(loc="upper right")
    guardar(fig, "09_attr_antes_despues.png")


def sensibilidad(res):
    s = res["sensibilidad_sigma"]
    sig0 = consultar(conectar(), "SELECT sigma_km FROM parametros_modelo")[0][0]
    fig, ax = plt.subplots(figsize=(8, 4.2))
    for red in ("completa", "fibra"):
        xs = [f["sigma_km"] for f in s]
        ys = [f[red] for f in s]
        ax.plot(xs, ys, color=SERIE[red], marker="o", ms=6, label=NOMBRE[red])
        ax.annotate(NOMBRE[red], xy=(xs[0], ys[0]), xytext=(-6, 0), textcoords="offset points",
                    ha="right", va="center", color=TXT, fontsize=9)
    ax.axvline(sig0, color=TXT2, lw=1, ls="--")
    ax.text(sig0 + 5, 0.33, f"σ calibrado\n{coma(sig0, 0)} km", color=TXT, fontsize=9, va="top")
    ax.set_xlabel("σ del modelo gaussiano (km)")
    ax.set_ylabel("E[ATTR] después (fallas independientes)")
    ax.set_xlim(40, 420)
    ax.set_ylim(0, None)
    ax.set_title("Sensibilidad del ATTR al alcance del daño (σ)")
    ax.legend(loc="upper right")
    guardar(fig, "10_sensibilidad_sigma.png")


def correlacionado(res, con):
    (sigma,), = consultar(con, "SELECT sigma_km FROM parametros_modelo")
    fig, ax = plt.subplots(figsize=(8, 4.2))
    for red in ("completa", "fibra"):
        c = res["redes"][red]["despues"]["p_falla"]["curva_correlacionado"]
        u = [p["u"] for p in c]
        # u = umbral de probabilidad -> distancia: fallan los enlaces con d < d(u)
        d = [sigma * math.sqrt(-2 * math.log(x)) for x in u]
        ax.step(d, [p["attr"] for p in c], where="post", color=SERIE[red], label=NOMBRE[red])
    ax.axhline(res["redes"]["completa"]["antes"]["attr"], color=TXT2, lw=1, ls=":")
    ax.text(690, res["redes"]["completa"]["antes"]["attr"] - 0.05, "ATTR antes (red completa)", color=TXT2,
            fontsize=8.5, ha="right")
    ax.set_xlim(0, 700)
    ax.set_ylim(0, 1.02)
    ax.set_xlabel("Radio de daño: fallan todos los enlaces a menos de esta distancia del epicentro (km)")
    ax.set_ylabel("ATTR")
    ax.set_title("ATTR si fallan todos los enlaces dentro de un radio (fallas correlacionadas)")
    ax.legend(loc="center right")
    guardar(fig, "11_attr_vs_radio_de_dano.png")


if __name__ == "__main__":
    con = conectar()
    res = json.loads((SALIDA / "resultados.json").read_text(encoding="utf-8"))
    modelo_falla(con)
    resumen(res)
    sensibilidad(res)
    correlacionado(res, con)
