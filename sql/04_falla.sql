-- 04_falla.sql
-- Probabilidad de falla de cada enlace del grafo por el sismo Mw 8.8 del Maule (2010).
--
-- Modelo principal: falla geográfica probabilística de Agarwal et al. (2013),
-- "The Resilience of WDM Networks to Probabilistic Geographical Failures",
-- IEEE/ACM ToN 21(5). La probabilidad de que un componente q falle por un evento
-- en p es una función no creciente de la distancia d(q, p). Variante gaussiana:
--
--     p_falla(e) = beta * exp( -d(e, epicentro)^2 / (2 * sigma^2) )
--
-- d(e, epicentro) es la distancia geodésica mínima entre el enlace y el epicentro.
-- Los enlaces fallan de forma independiente entre sí.
-- beta = 1 (falla segura en el epicentro). sigma se calibra con el ShakeMap del USGS:
-- p_falla = 0,5 a la distancia mediana de la isosista MMI {{mmi_ref}} (sacudida
-- "muy fuerte", inicio de daño), es decir, sigma = d50 / sqrt(2 ln 2).
--
-- Variantes para sensibilidad:
--   p_falla_lineal  : max(0, 1 - d / (2 * d50))   (modelo lineal de Agarwal et al.)
--   p_falla_ruptura : gaussiano con la distancia al plano de ruptura en vez del
--                     epicentro, calibrado igual con la isosista MMI {{mmi_ref}}.
--
-- {{sigma_km}} = 0 -> calibración automática; > 0 -> sigma fijo.

DROP TABLE IF EXISTS parametros_modelo CASCADE;
CREATE TABLE parametros_modelo AS
WITH iso AS (
    SELECT (ST_DumpPoints(geom)).geom AS g
      FROM sismo_shakemap
     WHERE parametro = 'mmi' AND valor = {{mmi_ref}}
), d AS (
    SELECT percentile_cont(0.5) WITHIN GROUP (
               ORDER BY ST_Distance(iso.g::geography, s.geom::geography)) / 1000.0 AS d50_epi,
           percentile_cont(0.5) WITHIN GROUP (
               ORDER BY ST_Distance(iso.g::geography, r.geom::geography)) / 1000.0 AS d50_rup
      FROM iso, sismo s, sismo_ruptura r
)
SELECT 'gaussiano (Agarwal et al. 2013)'::text AS modelo,
       1.0::double precision AS beta,
       {{mmi_ref}}::double precision AS mmi_referencia,
       d50_epi AS d50_epicentro_km,
       CASE WHEN {{sigma_km}} > 0 THEN {{sigma_km}}::double precision
            ELSE d50_epi / sqrt(2 * ln(2)) END AS sigma_km,
       d50_rup AS d50_ruptura_km,
       d50_rup / sqrt(2 * ln(2)) AS sigma_ruptura_km
  FROM d;

-- Un enlace es una cadena de tramos entre dos nodos de grado distinto de 2
-- (tabla enlaces, ver tarea/enlaces.py). Todos sus tramos comparten la misma suerte.
DROP TABLE IF EXISTS probabilidad_falla CASCADE;
CREATE TABLE probabilidad_falla AS
SELECT e.id AS enlace_id,
       e.source,
       e.target,
       e.tipo,
       e.n_tramos,
       e.longitud_km,
       ST_Distance(e.geom::geography, s.geom::geography) / 1000.0 AS dist_epicentro_km,
       ST_Distance(e.geom::geography, r.geom::geography) / 1000.0 AS dist_ruptura_km,
       NULL::double precision AS p_falla,
       NULL::double precision AS p_falla_lineal,
       NULL::double precision AS p_falla_ruptura,
       e.geom
  FROM enlaces e, sismo s, sismo_ruptura r;

UPDATE probabilidad_falla pf
   -- least(..., 700): exp() de PostgreSQL lanza "underflow" en vez de devolver 0.
   SET p_falla         = m.beta * exp(-least((pf.dist_epicentro_km ^ 2) / (2 * m.sigma_km ^ 2), 700)),
       p_falla_lineal  = greatest(0, 1 - pf.dist_epicentro_km / (2 * m.d50_epicentro_km)),
       p_falla_ruptura = m.beta * exp(-least((pf.dist_ruptura_km ^ 2) / (2 * m.sigma_ruptura_km ^ 2), 700))
  FROM parametros_modelo m;

ALTER TABLE probabilidad_falla ADD PRIMARY KEY (enlace_id);
CREATE INDEX ON probabilidad_falla USING gist (geom);
