-- 02_topologia.sql
-- Convierte los tendidos en una topología de red con pgRouting.
--
-- 1) Nodado: se cortan las líneas en cada cruce o contacto. Los shapefiles de
--    Yafún tienen tramos que se unen en un vértice interior de otro, y
--    pgr_createTopology solo une extremos: sin este paso la red queda mucho más
--    fragmentada (ver informe/hallazgos.md).
--    Se usa ST_UnaryUnion con grilla de precisión (GEOS, nodado robusto) en vez de
--    pgr_nodeNetwork: en pgRouting 3.8 pgr_nodeNetwork falla con estos datos
--    ("Splitter line has linear intersection with input") porque hay tramos que
--    comparten recorrido. ST_UnaryUnion además fusiona los trozos duplicados.
-- 2) Cada trozo hereda la capa del tendido original que lo cubre, con prioridad
--    fibra > postacion > camino > ferry (si una calle tiene fibra, es fibra).
-- 3) pgr_createTopology: asigna source/target y crea la tabla de vértices.
--
-- {{tolerancia_nodado}} y {{tolerancia_topologia}} vienen de main.py (grados, SRID 4326).

DROP TABLE IF EXISTS infraestructura_noded CASCADE;
DROP TABLE IF EXISTS infraestructura_noded_vertices_pgr CASCADE;

CREATE INDEX IF NOT EXISTS infraestructura_geom_idx ON infraestructura USING gist (geom);

-- 1) Nodado
CREATE TABLE infraestructura_noded AS
SELECT row_number() OVER ()::bigint AS id,
       d.geom::geometry(LineString, 4326) AS geom
  FROM (SELECT ST_UnaryUnion(ST_Collect(geom), {{tolerancia_nodado}}) AS g FROM infraestructura) u
 CROSS JOIN LATERAL ST_Dump(u.g) d
 WHERE GeometryType(d.geom) = 'LINESTRING';

ALTER TABLE infraestructura_noded
    ADD PRIMARY KEY (id),
    ADD COLUMN source bigint,
    ADD COLUMN target bigint,
    ADD COLUMN old_id integer,
    ADD COLUMN capa text,
    ADD COLUMN longitud_km double precision;

-- 2) Capa de origen (por el punto medio de cada trozo)
UPDATE infraestructura_noded n
   SET old_id = s.gid, capa = s.capa
  FROM (SELECT n2.id, c.gid, c.capa
          FROM infraestructura_noded n2
         CROSS JOIN LATERAL (
               SELECT i.gid, i.capa
                 FROM infraestructura i
                WHERE ST_DWithin(i.geom, ST_LineInterpolatePoint(n2.geom, 0.5), 0.00001)
                ORDER BY CASE i.capa WHEN 'fibra' THEN 1 WHEN 'postacion' THEN 2
                                     WHEN 'camino' THEN 3 ELSE 4 END,
                         ST_Distance(i.geom, ST_LineInterpolatePoint(n2.geom, 0.5))
                LIMIT 1) c) s
 WHERE s.id = n.id;

UPDATE infraestructura_noded SET longitud_km = ST_Length(geom::geography) / 1000.0;

DELETE FROM infraestructura_noded WHERE capa IS NULL OR ST_Length(geom) = 0;

-- 3) Topología
SELECT pgr_createTopology('infraestructura_noded', {{tolerancia_topologia}}, 'geom', 'id',
                          'source', 'target', clean := true);

CREATE INDEX ON infraestructura_noded USING gist (geom);
CREATE INDEX ON infraestructura_noded_vertices_pgr USING gist (the_geom);
