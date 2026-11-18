"""Descarga y normaliza los datos externos usados en la tarea.

No es necesario ejecutarlo: los archivos resultantes ya están en data/.
Se deja para documentar el origen de cada archivo y poder regenerarlos.

    python scripts/descargar_datos.py

Fuentes:
  - USGS FDSN Event Web Service (sismo Maule 2010, Mw 8.8) y ShakeMap oficial.
  - OpenStreetMap / Overpass API: centros de las comunas (nodo admin_centre).
  - PeeringDB: data centers (fac) e IXPs (ix, ixfac) de Chile.
  - TeleGeography Submarine Cable Map: puntos de aterrizaje y cables submarinos.
"""
from __future__ import annotations

import json
import math
import statistics
import urllib.parse
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DATA = RAIZ / "data"
UA = {"User-Agent": "TareaRuteo/1.0 (script educativo)"}

USGS_EVENTO = "official20100227063411530_30"
USGS_BUSQUEDA = ("https://earthquake.usgs.gov/fdsnws/event/1/query?format=geojson"
                 "&starttime=2010-02-27&endtime=2010-02-28&minmagnitude=8.5")
SHAKEMAP = ("https://earthquake.usgs.gov/product/shakemap/official20100227063411530_30/"
            "atlas/1594161725779/download/")
OVERPASS = "https://overpass-api.de/api/interpreter"
PEERINGDB = "https://www.peeringdb.com/api/"
CABLEMAP = "https://www.submarinecablemap.com/api/v3/"


def bajar(url: str, data: bytes | None = None) -> bytes:
    req = urllib.request.Request(url, data=data, headers=UA)
    with urllib.request.urlopen(req, timeout=300) as r:
        return r.read()


def bajar_json(url: str, data: bytes | None = None):
    return json.loads(bajar(url, data))


def guardar(ruta: Path, obj) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    n = len(obj.get("features", [])) if isinstance(obj, dict) else "-"
    print(f"  {ruta.relative_to(RAIZ)} ({n} features)")


def punto(lon, lat, props):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": props}


def fc(features):
    return {"type": "FeatureCollection", "features": features}


def sismo() -> None:
    print("USGS")
    guardar(DATA / "sismo" / "epicentro_maule_2010_usgs.geojson", bajar_json(USGS_BUSQUEDA))
    for f in ("cont_mmi.json", "cont_pga.json", "cont_pgv.json", "rupture.json", "info.json"):
        guardar(DATA / "sismo" / f, bajar_json(SHAKEMAP + f))


def comunas() -> list[dict]:
    print("OpenStreetMap (Overpass)")
    q = """[out:json][timeout:300];area["ISO3166-1"="CL"][admin_level=2]->.cl;
rel(area.cl)["boundary"="administrative"]["admin_level"="8"]->.r;
.r out body;
node(r.r:"admin_centre")->.ac; .ac out;
node(r.r:"label")->.lb; .lb out;"""
    els = bajar_json(OVERPASS, urllib.parse.urlencode({"data": q}).encode())["elements"]
    nodos = {e["id"]: e for e in els if e["type"] == "node"}
    feats = []
    for r in els:
        if r["type"] != "relation":
            continue
        roles = {m["role"]: m["ref"] for m in r["members"] if m["type"] == "node"}
        nid = roles.get("admin_centre") or roles.get("label")
        n = nodos[nid]
        t = r["tags"]
        pob = t.get("population", "")
        feats.append(punto(n["lon"], n["lat"], {
            "nombre": t.get("name"), "tipo": "comuna", "rol": "recibe",
            "cut": t.get("dpachile:id"),
            "poblacion_osm": int(pob) if pob.isdigit() else None,
            "fuente": "OpenStreetMap admin_centre" if roles.get("admin_centre") == nid
                      else "OpenStreetMap label",
            "ubicacion_aproximada": False}))
    feats.sort(key=lambda f: f["properties"]["cut"] or "")
    guardar(DATA / "nodos" / "comunas.geojson", fc(feats))
    return feats


def _norm(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return s.replace("santiago de chile", "santiago").strip()


def peeringdb(comunas_feats: list[dict]) -> None:
    """Data centers e IXPs. Los que no tienen coordenadas se ubican en el centro
    de la comuna de su ciudad (ubicacion_aproximada = true)."""
    print("PeeringDB")
    centro = {_norm(f["properties"]["nombre"]): f["geometry"]["coordinates"] for f in comunas_feats}
    fac = bajar_json(PEERINGDB + "fac?country=CL")["data"]
    ix = bajar_json(PEERINGDB + "ix?country=CL")["data"]
    ixfac = bajar_json(PEERINGDB + "ixfac?ix_id__in=" + ",".join(str(i["id"]) for i in ix))["data"]

    def ubicar(f):
        if f.get("latitude") is not None:
            return [f["longitude"], f["latitude"]], False
        c = centro.get(_norm(f.get("city")))
        return (c, True) if c else (None, True)

    dcs, fac_xy = [], {}
    for f in fac:
        xy, aprox = ubicar(f)
        if xy is None:
            print("   sin ubicación:", f["name"], f.get("city"))
            continue
        fac_xy[f["id"]] = (xy, f)
        dcs.append(punto(*xy, {"nombre": f["name"], "tipo": "data_center", "rol": "genera",
                               "ciudad": f.get("city"), "peeringdb_fac": f["id"],
                               "fuente": "PeeringDB", "ubicacion_aproximada": aprox}))
    guardar(DATA / "nodos" / "datacenters.geojson", fc(dcs))

    ixps = []
    for i in ix:
        sedes = [fac_xy[r["fac_id"]] for r in ixfac if r["ix_id"] == i["id"] and r["fac_id"] in fac_xy]
        # Preferir sedes en la ciudad del IXP; entre ellas, el medoide.
        en_ciudad = [s for s in sedes if _norm(s[1].get("city")) == _norm(i["city"])] or sedes
        if en_ciudad:
            def costo(s):
                return sum(math.dist(s[0], o[0]) for o in en_ciudad)
            xy, f = min(en_ciudad, key=costo)
            aprox, sede = False, f["name"]
        else:
            xy, aprox, sede = centro.get(_norm(i["city"])), True, None
        if xy is None:
            print("   sin ubicación:", i["name"])
            continue
        ixps.append(punto(*xy, {"nombre": i["name"], "tipo": "ixp", "rol": "intercambia",
                                "ciudad": i["city"], "sede": sede, "peeringdb_ix": i["id"],
                                "fuente": "PeeringDB", "ubicacion_aproximada": aprox}))
    guardar(DATA / "nodos" / "ixp.geojson", fc(ixps))


def cables() -> None:
    print("TeleGeography Submarine Cable Map")
    lp = bajar_json(CABLEMAP + "landing-point/landing-point-geo.json")
    cl = [f for f in lp["features"] if f["properties"]["name"].endswith(", Chile")]
    guardar(DATA / "nodos" / "aterrizajes_cables.geojson", fc([
        punto(*f["geometry"]["coordinates"], {
            "nombre": f["properties"]["name"].replace(", Chile", ""),
            "tipo": "aterrizaje_cable", "rol": "intercambia",
            "fuente": "TeleGeography", "ubicacion_aproximada": False}) for f in cl]))
    xy = [f["geometry"]["coordinates"] for f in cl]
    cab = bajar_json(CABLEMAP + "cable/cable-geo.json")

    def coords(g):
        if g["type"] == "LineString":
            yield from g["coordinates"]
        else:
            for linea in g["coordinates"]:
                yield from linea

    sel = [f for f in cab["features"]
           if any(abs(x - a) < 0.05 and abs(y - b) < 0.05 for x, y in coords(f["geometry"]) for a, b in xy)]
    guardar(DATA / "contexto" / "cables_submarinos_cl.geojson", fc(sel))


if __name__ == "__main__":
    sismo()
    c = comunas()
    peeringdb(c)
    cables()
