-- 03_grafo.sql
-- Arma el grafo sobre el que se calcula el ATTR:
--   * tramos de la topología (fibra, postación, camino, ferry)
--   * enlaces de acceso entre cada nodo clave y la red
--   * enlaces urbanos supuestos del Gran Santiago (supuesto S2 del estudio NIC 2018)
-- Los nodos clave usan id de vértice {{offset}} + nodos_clave.id para no chocar
-- con los vértices de pgr_createTopology.
--
-- Parámetros (main.py): {{offset}}, {{r_acceso_km}}, {{r_max_km}},
-- {{r_urbano_km}}, {{largo_max_urbano_km}}

DROP TABLE IF EXISTS grafo CASCADE;
CREATE TABLE grafo (
    id           serial PRIMARY KEY,
    source       bigint NOT NULL,
    target       bigint NOT NULL,
    tipo         text NOT NULL,   -- fibra | postacion | camino | ferry | acceso | urbano_supuesto
    origen_id    bigint,          -- id en infraestructura_noded (tramos de la topología)
    longitud_km  double precision,
    geom         geometry(LineString, 4326) NOT NULL
);

-- 1) Tramos de la topología (sin lazos).
INSERT INTO grafo (source, target, tipo, origen_id, longitud_km, geom)
SELECT source, target, capa, id, longitud_km, geom
  FROM infraestructura_noded
 WHERE source IS NOT NULL AND target IS NOT NULL AND source <> target;

-- Vértices que tocan al menos un tramo de fibra.
DROP TABLE IF EXISTS tmp_v_fibra;
CREATE TEMP TABLE tmp_v_fibra AS
SELECT DISTINCT v.id, v.the_geom
  FROM infraestructura_noded_vertices_pgr v
  JOIN grafo g ON g.tipo = 'fibra' AND (g.source = v.id OR g.target = v.id);
CREATE INDEX ON tmp_v_fibra USING gist ((the_geom::geography));

-- Comunas del Gran Santiago (supuesto S2): provincia de Santiago (CUT 131xx)
-- + Puente Alto, Pirque, San Bernardo y Padre Hurtado.
DROP TABLE IF EXISTS tmp_gs;
CREATE TEMP TABLE tmp_gs AS
SELECT id, geom FROM nodos_clave
 WHERE tipo = 'comuna' AND incluido
   AND (cut LIKE '131%' OR cut IN ('13201', '13202', '13401', '13604'));

-- 2a) Acceso: cada nodo clave se une a todos los vértices a menos de r_acceso_km.
--     Representa el POP del pueblo, donde llegan los tendidos de los caminos
--     (supuesto S1 del estudio: los tendidos se extienden hasta la plaza de armas).
INSERT INTO grafo (source, target, tipo, longitud_km, geom)
SELECT {{offset}} + k.id, v.id, 'acceso',
       ST_Distance(k.geom::geography, v.the_geom::geography) / 1000.0,
       ST_MakeLine(k.geom, v.the_geom)
  FROM nodos_clave k
  JOIN infraestructura_noded_vertices_pgr v
    ON ST_DWithin(k.geom::geography, v.the_geom::geography, {{r_acceso_km}} * 1000)
 WHERE k.incluido;

-- 2b) Data centers, IXPs y aterrizajes de cables se unen al centro de la comuna
--     más cercana (red urbana de la ciudad), hasta r_urbano_km.
INSERT INTO grafo (source, target, tipo, longitud_km, geom)
SELECT {{offset}} + k.id, {{offset}} + c.id, 'acceso', c.d / 1000.0, ST_MakeLine(k.geom, c.geom)
  FROM nodos_clave k
  CROSS JOIN LATERAL (
        SELECT m.id, m.geom, ST_Distance(k.geom::geography, m.geom::geography) AS d
          FROM nodos_clave m
         WHERE m.tipo = 'comuna' AND m.incluido
         ORDER BY k.geom <-> m.geom
         LIMIT 1) c
 WHERE k.incluido AND k.tipo <> 'comuna'
   AND c.d <= {{r_urbano_km}} * 1000;

-- 2c) Gran Santiago (supuesto S2 del estudio): Yafún no recorrió la ciudad, así que
--     se supone fibra entre comunas contiguas. La contigüidad se aproxima con una
--     triangulación de Delaunay de los centros comunales, descartando aristas largas.
WITH aristas AS (
    SELECT (ST_Dump(ST_DelaunayTriangles(ST_Collect(geom), 0, 1))).geom AS g FROM tmp_gs
)
INSERT INTO grafo (source, target, tipo, longitud_km, geom)
SELECT {{offset}} + a.id, {{offset}} + b.id, 'urbano_supuesto',
       ST_Length(ar.g::geography) / 1000.0, ar.g
  FROM aristas ar
  JOIN tmp_gs a ON ST_DWithin(ST_StartPoint(ar.g), a.geom, 1e-9)
  JOIN tmp_gs b ON ST_DWithin(ST_EndPoint(ar.g), b.geom, 1e-9)
 WHERE ST_Length(ar.g::geography) <= {{largo_max_urbano_km}} * 1000;

-- Respaldo (2d y 2e) solo para comunas fuera del Gran Santiago y nodos que no
-- quedaron unidos a un centro comunal. Las comunas del Gran Santiago ya están en la
-- malla urbana; si se les aplicara, todas se unirían al mismo vértice al norte de
-- la ciudad, formando una estrella artificial.
DROP TABLE IF EXISTS tmp_respaldo;
CREATE TEMP TABLE tmp_respaldo AS
SELECT k.id, k.geom FROM nodos_clave k
 WHERE k.incluido
   AND k.id NOT IN (SELECT id FROM tmp_gs)
   AND NOT (k.tipo <> 'comuna' AND EXISTS (
            SELECT 1 FROM grafo g WHERE g.source = {{offset}} + k.id AND g.target >= {{offset}}));

-- 2d) Si no quedó unido a ningún vértice con fibra, se une al vértice con fibra
--     más cercano (hasta r_max_km).
INSERT INTO grafo (source, target, tipo, longitud_km, geom)
SELECT {{offset}} + k.id, c.id, 'acceso', c.d / 1000.0, ST_MakeLine(k.geom, c.the_geom)
  FROM tmp_respaldo k
  CROSS JOIN LATERAL (
        SELECT f.id, f.the_geom, ST_Distance(k.geom::geography, f.the_geom::geography) AS d
          FROM tmp_v_fibra f
         ORDER BY k.geom <-> f.the_geom
         LIMIT 1) c
 WHERE c.d <= {{r_max_km}} * 1000
   AND NOT EXISTS (SELECT 1 FROM grafo g JOIN tmp_v_fibra f ON f.id = g.target
                    WHERE g.tipo = 'acceso' AND g.source = {{offset}} + k.id);

-- 2e) Si no quedó unido a nada, se une al vértice más cercano de cualquier tipo.
INSERT INTO grafo (source, target, tipo, longitud_km, geom)
SELECT {{offset}} + k.id, c.id, 'acceso', c.d / 1000.0, ST_MakeLine(k.geom, c.the_geom)
  FROM tmp_respaldo k
  CROSS JOIN LATERAL (
        SELECT v.id, v.the_geom, ST_Distance(k.geom::geography, v.the_geom::geography) AS d
          FROM infraestructura_noded_vertices_pgr v
         ORDER BY k.geom <-> v.the_geom
         LIMIT 1) c
 WHERE c.d <= {{r_max_km}} * 1000
   AND NOT EXISTS (SELECT 1 FROM grafo g WHERE g.tipo = 'acceso' AND g.source = {{offset}} + k.id);

CREATE INDEX ON grafo USING gist (geom);
CREATE INDEX ON grafo (source);
CREATE INDEX ON grafo (target);

-- ATTR con pgRouting (pgr_connectedComponents).
--   ATTR = (n sobre 2)^-1 * sum_{i<j} Z_ij, con Z_ij = 1 si hay camino entre los
--   nodos clave i y j (Boettcher et al., 2018, ec. 8). Se calcula por componentes:
--   sum_c n_c (n_c - 1) / 2 sobre n (n - 1) / 2.
-- p_red    : 'completa' (todas las capas) o 'fibra' (solo fibra + acceso + urbano)
-- p_umbral : NULL = antes del sismo; si no, sobreviven los enlaces con
--            p_columna < p_umbral (escenario determinista después del sismo).
CREATE OR REPLACE FUNCTION attr_red(p_red text DEFAULT 'completa',
                                    p_umbral double precision DEFAULT NULL,
                                    p_columna text DEFAULT 'p_falla')
RETURNS TABLE (red text, escenario text, nodos bigint, pares_conectados numeric,
               pares_totales numeric, attr double precision)
LANGUAGE plpgsql AS $$
DECLARE
    q text := 'SELECT g.id, g.source, g.target, 1.0::float8 AS cost, 1.0::float8 AS reverse_cost '
              'FROM grafo g WHERE true';
BEGIN
    IF p_red = 'fibra' THEN
        q := q || ' AND g.tipo IN (''fibra'', ''acceso'', ''urbano_supuesto'')';
    END IF;
    IF p_umbral IS NOT NULL THEN
        q := q || format(' AND g.enlace_id IN (SELECT enlace_id FROM probabilidad_falla WHERE %I < %s)',
                         p_columna, p_umbral);
    END IF;
    RETURN QUERY
    WITH cc AS (
        SELECT c.node, c.component FROM pgr_connectedComponents(q) c
    ), k AS (
        SELECT nk.id, COALESCE(cc.component, -nk.id) AS comp   -- aislado = componente propia
          FROM nodos_clave nk
          LEFT JOIN cc ON cc.node = {{offset}} + nk.id
         WHERE nk.incluido
    ), t AS (
        SELECT comp, count(*)::numeric AS n FROM k GROUP BY comp
    ), tot AS (
        SELECT count(*)::numeric AS n FROM k
    )
    SELECT p_red,
           CASE WHEN p_umbral IS NULL THEN 'antes del sismo'
                ELSE format('después del sismo (%s < %s)', p_columna, p_umbral) END,
           tot.n::bigint,
           sum(t.n * (t.n - 1) / 2),
           tot.n * (tot.n - 1) / 2,
           (sum(t.n * (t.n - 1) / 2) / (tot.n * (tot.n - 1) / 2))::double precision
      FROM t, tot
     GROUP BY tot.n;
END $$;
