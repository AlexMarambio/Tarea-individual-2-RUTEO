"""Mapa Leaflet (folium): infraestructura, daño del sismo y ATTR antes/después."""
import json

import folium
from branca.colormap import LinearColormap
from folium.plugins import Fullscreen

from .config import DATA, SALIDA
from .db import consultar

COLOR_P = LinearColormap(["#1a9850", "#a6d96a", "#fee08b", "#f46d43", "#a50026"],
                         index=[0, 0.25, 0.5, 0.75, 1], vmin=0, vmax=1,
                         caption="Probabilidad de falla del enlace (modelo gaussiano epicentral)")
COLOR_ACCESO = LinearColormap(["#a50026", "#fdae61", "#1a9850"], index=[0, 0.5, 1], vmin=0, vmax=1,
                              caption="P(comuna con acceso a data center e IXP después del sismo)")

CAPAS_ENLACES = [
    ("Fibra óptica detectada", ("fibra",), True, 3.0, None),
    ("Postación eléctrica sin fibra", ("postacion",), True, 1.6, "6 4"),
    ("Caminos sin postación", ("camino",), False, 1.2, "2 4"),
    ("Rutas de ferry", ("ferry",), False, 1.2, "8 6"),
    ("Accesos y enlaces urbanos supuestos", ("acceso", "urbano_supuesto"), False, 1.0, "1 3"),
]

COLOR_ROL = {"genera": "#6a3d9a", "intercambia": "#1f78b4"}


def _fmt(x, d=3):
    return "—" if x is None else f"{x:.{d}f}".replace(".", ",")


def _enlaces(con, tipos):
    filas = consultar(con, """
        SELECT enlace_id, tipo, n_tramos, longitud_km, dist_epicentro_km, p_falla,
               ST_AsGeoJSON(ST_SimplifyPreserveTopology(geom, 0.0005), 5)
          FROM probabilidad_falla WHERE tipo = ANY(%s)""", (list(tipos),))
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": json.loads(g),
         "properties": {"enlace": i, "tipo": t, "tramos": n, "km": round(l, 1),
                        "dist_km": round(d, 0), "p_falla": round(p, 3), "color": COLOR_P(p)}}
        for i, t, n, l, d, p, g in filas]}


def _panel(res: dict, sismo: tuple, modelo: tuple) -> str:
    sid, mag, lugar, fecha = sismo
    sigma, d50 = modelo
    filas = ""
    for red, nombre in (("completa", "Red completa"), ("fibra", "Solo fibra")):
        r = res["redes"][red]
        d = r["despues"]["p_falla"]
        a = d["attr_esperado"]
        filas += f"""<tr><td><b>{nombre}</b></td><td>{_fmt(r['antes']['attr'])}</td>
            <td>{_fmt(a['media'])}<br><small>[{_fmt(a['p2_5'])}–{_fmt(a['p97_5'])}]</small></td>
            <td>{_fmt(d['attr_esperado_correlacionado'])}</td><td>{_fmt(d['attr_determinista_p05'])}</td></tr>
            <tr class="sub"><td>comunas con acceso</td><td>{_fmt(100 * r['antes']['acceso_comunas'], 0)}%</td>
            <td>{_fmt(100 * d['acceso_comunas']['media'], 0)}%</td><td colspan="2"></td></tr>"""
    n = res["nodos_clave"]
    return f"""
<div id="panel-attr">
  <details open>
  <summary>ATTR de la infraestructura crítica</summary>
  <p class="meta">{lugar} · Mw {mag} · {fecha:%d-%m-%Y}<br>
  {n['total']} nodos clave: {n['genera']} generan (data centers), {n['intercambia']} intercambian
  (IXPs y cables submarinos), {n['recibe']} reciben (comunas).</p>
  <table>
    <tr><th></th><th>Antes</th><th>E[ATTR]<br>independiente</th><th>E[ATTR]<br>correlac.</th><th>p ≥ 0,5</th></tr>
    {filas}
  </table>
  <p class="meta">Falla gaussiana (Agarwal et al., 2013), σ = {_fmt(sigma, 0)} km: p = 0,5 a {_fmt(d50, 0)} km,
  la distancia mediana de la isosista MMI VII del USGS. Independiente: Monte Carlo con
  {res['simulaciones']} simulaciones (IC 95 %). Correlacionado: fallas comonótonas.
  p ≥ 0,5: fallan los enlaces con probabilidad ≥ 0,5.</p>
  </details>
</div>
<style>
#panel-attr {{position:absolute; left:10px; bottom:28px; z-index:1000; max-width:420px;
  background:rgba(255,255,255,.95); color:#222; padding:8px 12px; border-radius:6px;
  box-shadow:0 1px 6px rgba(0,0,0,.35); font:12px/1.35 system-ui, sans-serif;}}
#panel-attr summary {{font-weight:600; font-size:13px; cursor:pointer;}}
#panel-attr table {{border-collapse:collapse; width:100%; margin:6px 0;}}
#panel-attr th, #panel-attr td {{padding:2px 5px; text-align:right; border-bottom:1px solid #ddd;
  font-variant-numeric:tabular-nums;}}
#panel-attr th:first-child, #panel-attr td:first-child {{text-align:left;}}
#panel-attr tr.sub td {{color:#555; font-size:11px;}}
#panel-attr .meta {{color:#555; margin:4px 0; font-size:11px;}}
</style>"""


def generar(con, res: dict, ruta=None, centro=(-35.5, -71.5), zoom=6,
            capas_extra=(), panel_abierto=True) -> str:
    """capas_extra: nombres de CAPAS_ENLACES que se muestran aunque estén ocultas por defecto."""
    ruta = ruta or SALIDA / "mapa.html"
    ruta.parent.mkdir(parents=True, exist_ok=True)
    m = folium.Map(location=list(centro), zoom_start=zoom, tiles=None, control_scale=True,
                   prefer_canvas=True)
    folium.TileLayer(
        "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}",
        attr="Tiles &copy; Esri &mdash; Esri, DeLorme, NAVTEQ", name="Mapa base gris (Esri)", max_zoom=16,
    ).add_to(m)
    folium.TileLayer("OpenStreetMap", name="OpenStreetMap", show=False).add_to(m)

    # Enlaces coloreados por probabilidad de falla
    for nombre, tipos, visible, peso, guion in CAPAS_ENLACES:
        fc = _enlaces(con, tipos)
        folium.GeoJson(
            fc, name=f"{nombre} ({len(fc['features'])})", show=visible or nombre in capas_extra,
            style_function=lambda f, w=peso, g=guion: {
                "color": f["properties"]["color"], "weight": w, "opacity": 0.9, "dashArray": g},
            highlight_function=lambda f: {"weight": 6, "opacity": 1},
            tooltip=folium.GeoJsonTooltip(
                fields=["tipo", "enlace", "km", "tramos", "dist_km", "p_falla"],
                aliases=["Tipo", "Enlace", "Largo (km)", "Tramos", "Dist. epicentro (km)", "P(falla)"]),
        ).add_to(m)

    # Enlaces críticos: puentes (pgr_bridges) con mayor riesgo = p_falla * pérdida de ATTR
    top = res.get("enlaces_criticos", {}).get("completa", {}).get("top", [])[:10]
    if top:
        capa_crit = folium.FeatureGroup(name="Enlaces críticos: 10 puentes de mayor riesgo", show=True)
        geoms = dict(consultar(con, """SELECT enlace_id, ST_AsGeoJSON(geom, 5) FROM probabilidad_falla
                                      WHERE enlace_id = ANY(%s)""", ([t["enlace_id"] for t in top],)))
        for rank, t in enumerate(top, 1):
            texto = (f"<b>#{rank} enlace crítico</b> (id {t['enlace_id']}, {t['tipo']}, {t['longitud_km']:.1f} km)"
                     f"<br>Si falla solo, el ATTR baja {_fmt(t['perdida_attr'])}"
                     f"<br>P(falla) = {_fmt(t['p_falla'])} · riesgo = {_fmt(t['riesgo'], 4)}")
            folium.GeoJson(json.loads(geoms[t["enlace_id"]]),
                           style_function=lambda f: {"color": "#000", "weight": 9, "opacity": 0.55},
                           tooltip=folium.Tooltip(texto)).add_to(capa_crit)
            folium.Marker([t["lat"], t["lon"]], icon=folium.DivIcon(
                html=f'<div style="font:600 11px system-ui;background:#000;color:#fff;border-radius:9px;'
                     f'width:18px;height:18px;line-height:18px;text-align:center">{rank}</div>',
                icon_size=(18, 18), icon_anchor=(9, 9)), tooltip=folium.Tooltip(texto)).add_to(capa_crit)
        capa_crit.add_to(m)

    # Sismo: epicentro, ruptura, isosistas y radio p = 0,5
    (sid, mag, lugar, fecha, prof, lon, lat), = consultar(
        con, "SELECT id, magnitud, lugar, fecha_utc, profundidad_km, ST_X(geom), ST_Y(geom) FROM sismo")
    (sigma, d50), = consultar(con, "SELECT sigma_km, d50_epicentro_km FROM parametros_modelo")
    capa_sismo = folium.FeatureGroup(name="Sismo Mw 8.8: epicentro, ruptura e isosistas", show=True)
    for (g,) in consultar(con, "SELECT ST_AsGeoJSON(geom, 4) FROM sismo_ruptura"):
        folium.GeoJson(json.loads(g), style_function=lambda f: {
            "color": "#7f0000", "weight": 2, "fillColor": "#d7301f", "fillOpacity": 0.12, "dashArray": "6 3"},
            tooltip="Plano de ruptura (ShakeMap USGS)").add_to(capa_sismo)
    for valor, g in consultar(con, """SELECT valor, ST_AsGeoJSON(ST_Simplify(geom, 0.01), 3)
                                     FROM sismo_shakemap WHERE parametro = 'mmi' AND valor >= 5
                                     ORDER BY valor"""):
        folium.GeoJson(json.loads(g), style_function=lambda f, v=valor: {
            "color": "#b2182b" if v >= 7 else "#ef8a62", "weight": 1.2, "opacity": 0.8},
            tooltip=f"Isosista MMI {valor:g}".replace(".", ",")).add_to(capa_sismo)
    folium.Circle([lat, lon], radius=d50 * 1000, color="#7f0000", weight=1.5, fill=False,
                  dash_array="10 6", tooltip=f"p_falla = 0,5 (d = {d50:.0f} km)").add_to(capa_sismo)
    folium.Marker([lat, lon], icon=folium.Icon(color="red", icon="certificate", prefix="fa"),
                  tooltip=f"Epicentro Mw {mag}",
                  popup=folium.Popup(f"<b>{lugar}</b><br>USGS {sid}<br>Mw {mag}, {fecha:%Y-%m-%d %H:%M} UTC"
                                     f"<br>Profundidad {prof:.1f} km<br>({lat:.3f}, {lon:.3f})",
                                     max_width=260)).add_to(capa_sismo)
    capa_sismo.add_to(m)

    # Nodos clave
    nodos = consultar(con, """
        SELECT k.id, k.rol, k.tipo, k.nombre, k.cut, k.incluido, k.motivo_exclusion,
               k.ubicacion_aproximada, ST_X(k.geom), ST_Y(k.geom),
               a.acceso_antes_completa, a.p_acceso_despues_completa,
               a.acceso_antes_fibra, a.p_acceso_despues_fibra
          FROM nodos_clave k LEFT JOIN acceso_comunas a ON a.nodo_id = k.id
         ORDER BY k.rol DESC, k.id""")
    grupos = {
        "recibe": folium.FeatureGroup(name="Reciben: comunas (color = P(acceso) después)", show=True),
        "genera": folium.FeatureGroup(name="Generan: data centers", show=True),
        "intercambia": folium.FeatureGroup(name="Intercambian: IXPs y aterrizajes de cables", show=True),
    }
    for (i, rol, tipo, nombre, cut, incl, motivo, aprox, x, y, ac, pc, af, pf) in nodos:
        if not incl:
            texto = f"<b>{nombre}</b> (excluida)<br>{motivo}"
            folium.CircleMarker([y, x], radius=4, color="#777", fill=True, fill_opacity=0.5,
                                tooltip=f"{nombre} (excluida)", popup=texto).add_to(grupos[rol])
            continue
        if rol == "recibe":
            texto = (f"<b>{nombre}</b> (CUT {cut})<br>"
                     f"<u>Red completa</u>: acceso antes {'sí' if ac else 'no'}; "
                     f"P(acceso) después {_fmt(pc, 2)}<br>"
                     f"<u>Solo fibra</u>: acceso antes {'sí' if af else 'no'}; "
                     f"P(acceso) después {_fmt(pf, 2)}")
            folium.CircleMarker([y, x], radius=4, color="#333", weight=0.6, fill=True,
                                fill_color=COLOR_ACCESO(pc if pc is not None else 0), fill_opacity=0.95,
                                tooltip=nombre, popup=folium.Popup(texto, max_width=280)).add_to(grupos[rol])
        else:
            texto = (f"<b>{nombre}</b><br>{tipo.replace('_', ' ')} · {rol}"
                     + ("<br><i>ubicación aproximada (centro comunal)</i>" if aprox else ""))
            # DivIcon con CSS (RegularPolygonMarker de leaflet-dvf falla con prefer_canvas).
            forma = (f"width:9px;height:9px;background:{COLOR_ROL[rol]};border:1px solid #fff;"
                     if rol == "genera" else
                     f"width:0;height:0;border-left:6px solid transparent;border-right:6px solid transparent;"
                     f"border-bottom:11px solid {COLOR_ROL[rol]};")
            folium.Marker([y, x], icon=folium.DivIcon(html=f'<div style="{forma}"></div>',
                                                      icon_size=(12, 12), icon_anchor=(6, 6)),
                          tooltip=nombre, popup=folium.Popup(texto, max_width=260)).add_to(grupos[rol])
    for g in grupos.values():
        g.add_to(m)

    # Contexto: cables submarinos
    cables = json.loads((DATA / "contexto" / "cables_submarinos_cl.geojson").read_text(encoding="utf-8"))
    folium.GeoJson(cables, name="Cables submarinos (TeleGeography, contexto)", show=False,
                   style_function=lambda f: {"color": "#08519c", "weight": 1.5, "opacity": 0.6},
                   tooltip=folium.GeoJsonTooltip(fields=["name"], aliases=["Cable"])).add_to(m)

    COLOR_P.add_to(m)
    COLOR_ACCESO.add_to(m)
    folium.LayerControl(collapsed=True).add_to(m)
    Fullscreen().add_to(m)
    panel = _panel(res, (sid, mag, lugar, fecha), (sigma, d50))
    if not panel_abierto:
        panel = panel.replace("<details open>", "<details>")
    m.get_root().html.add_child(folium.Element(panel))
    m.get_root().header.add_child(folium.Element("<title>Sismo Maule 8.8 · ATTR red chilena</title>"))
    m.save(str(ruta))
    return str(ruta)
