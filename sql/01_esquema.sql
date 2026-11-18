-- 01_esquema.sql
-- Tablas base. Los datos los inserta main.py (tarea/carga.py) desde data/.

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgrouting;

-- Tendidos documentados por el proyecto Yafún (NIC Labs 2018, entregados por Subtel).
--   fibra     : fibra_optica_detectada
--   postacion : postacion_electrica_detectada (postes sin fibra visible)
--   camino    : camino_sin_postacion_detectado
--   ferry     : trayecto_en_ferry
DROP TABLE IF EXISTS infraestructura CASCADE;
CREATE TABLE infraestructura (
    gid          serial PRIMARY KEY,
    capa         text NOT NULL,
    id_original  text,
    geom         geometry(LineString, 4326) NOT NULL
);

-- Nodos clave: generan (data centers), intercambian (IXPs y aterrizajes de
-- cables submarinos) y reciben información (comunas).
DROP TABLE IF EXISTS nodos_clave CASCADE;
CREATE TABLE nodos_clave (
    id                    serial PRIMARY KEY,
    rol                   text NOT NULL CHECK (rol IN ('genera', 'intercambia', 'recibe')),
    tipo                  text NOT NULL,
    nombre                text NOT NULL,
    cut                   text,
    fuente                text,
    ubicacion_aproximada  boolean DEFAULT false,
    incluido              boolean DEFAULT true,
    motivo_exclusion      text,
    geom                  geometry(Point, 4326) NOT NULL
);

-- Sismo 8.8 (USGS, GeoJSON). Se guarda el Feature completo además de la geometría.
DROP TABLE IF EXISTS sismo CASCADE;
CREATE TABLE sismo (
    id              text PRIMARY KEY,
    magnitud        double precision,
    tipo_magnitud   text,
    lugar           text,
    fecha_utc       timestamptz,
    profundidad_km  double precision,
    geojson         jsonb,
    geom            geometry(Point, 4326)
);

-- ShakeMap oficial del USGS: isosistas (MMI), PGA y PGV.
DROP TABLE IF EXISTS sismo_shakemap CASCADE;
CREATE TABLE sismo_shakemap (
    id         serial PRIMARY KEY,
    parametro  text,
    valor      double precision,
    unidades   text,
    geom       geometry(MultiLineString, 4326)
);

-- Proyección en superficie del plano de ruptura (ShakeMap rupture.json).
DROP TABLE IF EXISTS sismo_ruptura CASCADE;
CREATE TABLE sismo_ruptura (
    id    serial PRIMARY KEY,
    geom  geometry(MultiPolygon, 4326)
);
