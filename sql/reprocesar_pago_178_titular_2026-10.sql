-- =============================================================
-- PagoOK — Completar el titular del pago 178 (quedó vacío por el guion)
-- =============================================================
-- El aviso real fue "Pedro De-* te envió un pago por S/ 35...". El parser no
-- aceptaba el guion en los nombres y el titular quedó ''. Ya corregido en
-- app/api/parser.py; esto repara el único pago afectado (178).
--
-- CÓMO CORRERLO en PGAdmin (Tools > Query Tool):
--   1. Pega TODO este archivo y ejecútalo (F5). Abre una transacción (BEGIN).
--   2. Mira el SELECT de verificación: debe decir titular='Pedro De-*',
--      titular_corto='P. De-*', monto=35.00.
--   3. Si está bien:  COMMIT;   (escríbelo a mano y ejecútalo).
--      Si algo no cuadra:  ROLLBACK;   (no cambia nada).
--
-- A propósito SIN COMMIT: tú decides confirmarlo tras ver la verificación.
-- =============================================================

BEGIN;

UPDATE pago_detectado
SET titular = 'Pedro De-*',
    titular_corto = 'P. De-*'
WHERE id = 178
  AND (titular IS NULL OR titular = '');   -- solo si sigue vacío (idempotente/seguro)

-- Verificación (revisa esta fila antes de COMMIT):
SELECT id, monto, titular, titular_corto
FROM pago_detectado
WHERE id = 178;

-- COMMIT;   -- <- descomenta/escribe y ejecuta si la verificación está OK
-- ROLLBACK; -- <- si no, deshaz sin cambios
