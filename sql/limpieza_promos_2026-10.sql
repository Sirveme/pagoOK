-- =============================================================
-- PagoOK — Limpieza: promociones de Yape registradas como pagos
-- =============================================================
-- Borra los pagos 115, 116, 120 y 123 de pago_detectado. Eran promociones
-- ("Gana hasta S/3,000…", "participa por S/5,000. T&C.") que el parser guardó
-- como ingreso dudoso (tipo_incierto). El parser corregido ya no las crea.
--
-- Uso en PGAdmin (Query Tool), PASO A PASO:
--   1) Ejecuta desde BEGIN hasta el SELECT de "VERIFICACIÓN PREVIA" y revisa.
--   2) Ejecuta el bloque DO: si algo no coincide, lanza un error y aborta.
--      Si ves un ERROR en cualquier paso -> ejecuta ROLLBACK; y avísame.
--   3) Ejecuta DELETE y UPDATE y compara con los conteos esperados.
--   4) Ejecuta la VERIFICACIÓN POSTERIOR. Si todo coincide, escribe y ejecuta
--      COMMIT; si no, ROLLBACK;  (el archivo NO trae COMMIT activo).
-- =============================================================

BEGIN;

-- -------------------------------------------------------------
-- VERIFICACIÓN PREVIA (esperado: 4 filas, usos = 0, entregas = 0)
-- -------------------------------------------------------------
SELECT p.id, p.monto, p.recibido_en, p.tipo, p.tipo_incierto, p.consumido, p.reclamado_por,
       (SELECT count(*) FROM pago_uso u WHERE u.pago_id = p.id)                         AS usos,
       (SELECT count(*) FROM entrega_evento e
         WHERE e.payload -> 'datos' ->> 'pago_id' = p.id::text)                       AS entregas,
       p.notificacion_raw_id,
       left(n.texto, 60)                                                              AS texto
FROM pago_detectado p
JOIN notificacion_raw n ON n.id = p.notificacion_raw_id
WHERE p.id IN (115, 116, 120, 123)
ORDER BY p.id;

-- -------------------------------------------------------------
-- GUARDAS: si algo no coincide, ERROR y la transacción queda abortada.
-- -------------------------------------------------------------
DO $$
DECLARE
    n_pagos    int;
    n_usos     int;
    n_entregas int;
BEGIN
    SELECT count(*) INTO n_pagos
    FROM pago_detectado
    WHERE id IN (115, 116, 120, 123)
      AND tipo_incierto = true
      AND consumido = false
      AND reclamado_por IS NULL
      AND notificacion_raw_id IN (350, 352, 360, 367);
    IF n_pagos <> 4 THEN
        RAISE EXCEPTION 'Se esperaban 4 pagos promocionales intactos y hay %. NO continuar: ROLLBACK.', n_pagos;
    END IF;

    SELECT count(*) INTO n_usos FROM pago_uso WHERE pago_id IN (115, 116, 120, 123);
    IF n_usos <> 0 THEN
        RAISE EXCEPTION 'Hay % registros en pago_uso para estos pagos. NO continuar: ROLLBACK.', n_usos;
    END IF;

    SELECT count(*) INTO n_entregas FROM entrega_evento
    WHERE payload -> 'datos' ->> 'pago_id' IN ('115', '116', '120', '123');
    IF n_entregas <> 0 THEN
        RAISE EXCEPTION 'Hay % entregas de webhook para estos pagos. NO continuar: ROLLBACK.', n_entregas;
    END IF;

    RAISE NOTICE 'Guardas OK: 4 pagos, 0 usos, 0 entregas de webhook.';
END $$;

-- -------------------------------------------------------------
-- BORRADO (esperado: DELETE 4)
-- -------------------------------------------------------------
DELETE FROM pago_detectado
WHERE id IN (115, 116, 120, 123)
  AND tipo_incierto = true;

-- Las notificaciones originales se CONSERVAN (auditoría), marcadas como
-- ignoradas con el motivo. (esperado: UPDATE 4)
UPDATE notificacion_raw
SET estado_parseo   = 'ignorada',
    metodo_detectado = NULL,
    error_parseo    = 'Promocion de Yape: sin verbo de ingreso (limpieza 2026-10)'
WHERE id IN (350, 352, 360, 367);

-- -------------------------------------------------------------
-- VERIFICACIÓN POSTERIOR
-- -------------------------------------------------------------
-- Esperado: 0
SELECT count(*) AS pagos_promo_restantes FROM pago_detectado WHERE id IN (115, 116, 120, 123);
-- Esperado: 0 (ya no queda ningún pago dudoso)
SELECT count(*) AS pagos_tipo_incierto FROM pago_detectado WHERE tipo_incierto = true;
-- Esperado: 4 filas 'ignorada'
SELECT id, estado_parseo, error_parseo FROM notificacion_raw WHERE id IN (350, 352, 360, 367);

-- La transacción queda ABIERTA a propósito (así un F5 del archivo completo
-- nunca confirma solo). Revisa los tres resultados de arriba y ejecuta a mano:
--   COMMIT;     si todo coincide
--   ROLLBACK;   si algo no coincide
