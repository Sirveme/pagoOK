-- ============================================================================
-- zWeb-12 — Desactivar la cuenta API 5 ("MITA" vieja, empresa perusistemas)
-- Fecha: 2026-11-17 · PREPARADO, NO CORRIDO.
--
-- CÓMO CORRERLO: Railway → PGAdmin, pega TODO el archivo (trae su BEGIN/COMMIT).
--
-- Por qué (leído el 2026-11-17 en solo lectura):
--   - Último uso: 2026-11-03 21:20 UTC (5 marcados de "Mita.pe"); 0 usos después.
--   - MITA consulta desde el 04-11 con su cuenta propia id=6 (pk_live_mYhUYp).
--   - La demo PSP usa la cuenta 4 ("Demo PSP"), no la 5.
--   Las consultas a /pagos/consultar no dejan registro por cuenta en la base: si
--   algún sistema solo consultara con esta clave sin marcar pagos, empezaría a
--   recibir 401 al desactivarla. Se revierte con: UPDATE cuenta_api SET activa = TRUE WHERE id = 5;
--
-- DESACTIVA, no borra (los pagos ya marcados con ella conservan su historial).
-- Idempotente. Solo actúa si la fila coincide EXACTO en id, nombre, prefijo y empresa.
-- ¿El código depende de esto? No.
-- ============================================================================

BEGIN;

UPDATE cuenta_api c
   SET activa = FALSE
  FROM empresa e
 WHERE e.id = c.empresa_id
   AND c.id = 5 AND c.nombre = 'MITA' AND c.prefijo = 'pk_live_MURMct'
   AND e.slug = 'perusistemas'
   AND c.activa;

COMMIT;

-- VERIFICACIÓN (esperado: id 5 activa = false; id 6 de mita sigue activa = true):
SELECT c.id, e.slug, c.nombre, c.prefijo, c.activa
  FROM cuenta_api c JOIN empresa e ON e.id = c.empresa_id
 WHERE c.id IN (5, 6)
 ORDER BY c.id;
