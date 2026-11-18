-- ============================================================================
-- zWeb-12 — Rubro de la empresa (para agrupar el tablero de testers)
-- Fecha: 2026-11-17
--
-- CÓMO CORRERLO: Railway → PGAdmin, pega TODO el archivo (trae su BEGIN/COMMIT).
-- Solo AGREGA una columna opcional (NULL = sin rubro). No toca datos.
-- Idempotente.
--
-- ¿El código depende de esto? NO. /admin/testers-metricas lee el rubro solo si
-- la columna existe; si no, agrupa todo como "Sin rubro". El modelo del ORM no
-- la mapea, así que ninguna otra pantalla la necesita.
--
-- Valores sugeridos (texto libre, en minúsculas): bodega, restaurante, farmacia,
-- clinica, educacion, servicios, comercio, transporte, otro.
-- Ejemplo para cargarlo después (NO se corre aquí):
--   UPDATE empresa SET rubro = 'servicios' WHERE slug = 'mita';
-- ============================================================================

BEGIN;

ALTER TABLE empresa ADD COLUMN IF NOT EXISTS rubro VARCHAR(40);

COMMIT;

-- VERIFICACIÓN (debe devolver 1 fila: rubro | character varying | 40):
SELECT column_name, data_type, character_maximum_length
  FROM information_schema.columns
 WHERE table_name = 'empresa' AND column_name = 'rubro';
