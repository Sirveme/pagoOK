-- ============================================================================
-- zWeb-11/12 — Zombies y alarmas con fecha (detalle en "Estado de dispositivos")
-- Fecha: 2026-11-17
--
-- CÓMO CORRERLO: Railway → PGAdmin, pega TODO el archivo (trae su BEGIN/COMMIT)
-- y revisa las consultas de verificación del final. Se corre ANTES del push.
--
-- Solo CREA una tabla nueva y su índice. No toca ninguna tabla ni dato
-- existente. Idempotente: se puede correr varias veces. Si ya se había corrido
-- la versión de zWeb-11 (sin las columnas del lector y la recuperación), las
-- agrega con ADD COLUMN IF NOT EXISTS.
--
-- ¿El código depende de esto? Sí, el detalle de zombies/alarmas y su conteo en
-- /admin/testers-metricas. Si se hace el push antes, el heartbeat NO se rompe
-- (el registro va aislado y solo deja un error en el log) y el tablero muestra
-- "sin datos"; el panel lateral del detalle sí respondería con error.
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS dispositivo_incidente (
    id                     SERIAL PRIMARY KEY,
    dispositivo_id         INTEGER NOT NULL REFERENCES dispositivo(id) ON DELETE CASCADE,
    empresa_id             INTEGER NOT NULL REFERENCES empresa(id) ON DELETE CASCADE,
    tipo                   VARCHAR(20)  NOT NULL,          -- 'zombie' | 'alarma'
    causa                  VARCHAR(30),                    -- 'lector_desconectado' | 'sin_permiso' | NULL
    mensaje                VARCHAR(300) NOT NULL,
    cantidad               INTEGER      NOT NULL DEFAULT 1,
    app_version            VARCHAR(30),
    listener_conectado     BOOLEAN,                        -- lector al inicio (NULL = app vieja)
    permiso_notificaciones BOOLEAN,
    rebind_exitoso_inicio  INTEGER,
    recuperacion           VARCHAR(20),                    -- 'sola' | 'intervencion' | 'sin_determinar'
    recuperacion_detalle   VARCHAR(150),
    ocurrido_en            TIMESTAMPTZ  NOT NULL DEFAULT now(),
    activa                 BOOLEAN      NOT NULL DEFAULT true,
    cerrada_en             TIMESTAMPTZ,
    revisada_en            TIMESTAMPTZ,                    -- auditoría de "Marcar como revisada"
    revisada_por           VARCHAR(150),
    revisada_ip            VARCHAR(64)
);

-- Columnas nuevas de zWeb-12 (por si ya existía la tabla de zWeb-11).
ALTER TABLE dispositivo_incidente ADD COLUMN IF NOT EXISTS listener_conectado     BOOLEAN;
ALTER TABLE dispositivo_incidente ADD COLUMN IF NOT EXISTS permiso_notificaciones BOOLEAN;
ALTER TABLE dispositivo_incidente ADD COLUMN IF NOT EXISTS rebind_exitoso_inicio  INTEGER;
ALTER TABLE dispositivo_incidente ADD COLUMN IF NOT EXISTS recuperacion           VARCHAR(20);
ALTER TABLE dispositivo_incidente ADD COLUMN IF NOT EXISTS recuperacion_detalle   VARCHAR(150);

CREATE INDEX IF NOT EXISTS ix_dispositivo_incidente_disp_fecha
    ON dispositivo_incidente (dispositivo_id, ocurrido_en);

COMMIT;

-- ============================================================================
-- VERIFICACIÓN (una fila por columna; deben salir las 19, todas con "ok")
-- ============================================================================
SELECT c.columna,
       CASE WHEN i.column_name IS NOT NULL THEN 'ok' ELSE 'FALTA' END AS estado,
       i.data_type
  FROM (VALUES ('id'), ('dispositivo_id'), ('empresa_id'), ('tipo'), ('causa'), ('mensaje'), ('cantidad'),
               ('app_version'), ('listener_conectado'), ('permiso_notificaciones'), ('rebind_exitoso_inicio'),
               ('recuperacion'), ('recuperacion_detalle'), ('ocurrido_en'), ('activa'), ('cerrada_en'),
               ('revisada_en'), ('revisada_por'), ('revisada_ip')) AS c(columna)
  LEFT JOIN information_schema.columns i
         ON i.table_name = 'dispositivo_incidente' AND i.column_name = c.columna
 ORDER BY estado DESC, c.columna;

-- El índice (debe devolver 1 fila):
SELECT indexname FROM pg_indexes
 WHERE tablename = 'dispositivo_incidente' AND indexname = 'ix_dispositivo_incidente_disp_fecha';
