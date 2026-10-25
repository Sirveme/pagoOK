-- =============================================================
-- PagoOK — Reproceso de los 6 egresos históricos de Interbank (perusistemas)
-- =============================================================
-- Equivale a:  python scripts/reprocesar_egresos.py --empresa perusistemas --desde 2026-05-01 --aplicar
-- (simulado contra producción en solo lectura el 23/10/2026: exactamente estos 6).
--
--   raw  fecha        tipo_egreso        monto
--   61   2026-05-16   cargo_recurrente   USD 26.19
--   75   2026-05-22   envio_billetera    PEN  1.00
--   76   2026-05-22   envio_billetera    PEN  1.00
--   84   2026-05-26   consumo_tarjeta    PEN 50.00
--   91   2026-05-28   consumo_tarjeta    PEN 42.00
--   94   2026-05-28   consumo_tarjeta    PEN 70.70
--
-- Requiere sql/egresos_privacidad_2026-10.sql (columnas tipo_egreso y
-- tarjeta_ultimos4), que ya está aplicado.
--
-- Garantías:
--   * Idempotente: no inserta si el raw ya tiene un registro en pago_detectado.
--     Una 2da ejecución da INSERT 0 0 / UPDATE 0.
--   * Solo raws de perusistemas. No toca ingresos ni pagos existentes.
--   * No emite webhooks (un INSERT no dispara nada: no hay triggers, y un
--     egreso nunca genera pago.confirmado).
--   * Sin nombres de personas en este archivo: el titular/comercio se toma del
--     propio texto del aviso, igual que el parser (verificado: idéntico en los 6).
--   * Conserva la fecha original del aviso (recibido_en = timestamp del raw).
--
-- Uso en PGAdmin: ejecuta el archivo completo (F5). La transacción queda
-- ABIERTA: revisa la verificación del final y escribe tú COMMIT; o ROLLBACK;
-- =============================================================

BEGIN;

-- -------------------------------------------------------------
-- 1) Insertar los egresos (esperado: INSERT 0 6; si ya se aplicó, INSERT 0 0)
-- -------------------------------------------------------------
WITH datos (raw_id, metodo, monto, moneda, tipo_egreso, patron) AS (
    VALUES
        (61, 'tarjeta', 26.19::numeric, 'USD', 'cargo_recurrente', '\men\s+(.+?)\s+con\s+tu\s+tarjeta'),
        (75, 'plin',     1.00::numeric, 'PEN', 'envio_billetera',  'plineaste\s+S/\.?\s*[0-9.,]+\s+a\s+(.+)$'),
        (76, 'plin',     1.00::numeric, 'PEN', 'envio_billetera',  'plineaste\s+S/\.?\s*[0-9.,]+\s+a\s+(.+)$'),
        (84, 'tarjeta', 50.00::numeric, 'PEN', 'consumo_tarjeta',  '\men\s+(.+?)\s+con\s+tu\s+tarjeta'),
        (91, 'tarjeta', 42.00::numeric, 'PEN', 'consumo_tarjeta',  '\men\s+(.+?)\s+con\s+tu\s+tarjeta'),
        (94, 'tarjeta', 70.70::numeric, 'PEN', 'consumo_tarjeta',  '\men\s+(.+?)\s+con\s+tu\s+tarjeta')
)
INSERT INTO pago_detectado (
    empresa_id, notificacion_raw_id, metodo, monto, moneda, titular, titular_corto,
    codigo_operacion, banco, tipo, tipo_egreso, tarjeta_ultimos4,
    posible_interno, confirmacion_usuario, tipo_incierto, consumido, recibido_en
)
SELECT n.empresa_id, n.id, d.metodo, d.monto, d.moneda, t.titular, left(t.titular, 50),
       NULL, NULL, 'egreso', d.tipo_egreso, NULL,
       false, NULL, false, false, n.timestamp_servidor
FROM datos d
JOIN notificacion_raw n ON n.id = d.raw_id
JOIN empresa e          ON e.id = n.empresa_id AND e.slug = 'perusistemas'
CROSS JOIN LATERAL (
    SELECT btrim(rtrim(btrim((regexp_match(n.texto, d.patron, 'i'))[1]), '.,')) AS titular
) t
WHERE n.texto IS NOT NULL
  AND t.titular IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM pago_detectado p WHERE p.notificacion_raw_id = n.id);

-- -------------------------------------------------------------
-- 2) Marcar esas notificaciones como procesadas (esperado: UPDATE 6; luego 0)
-- -------------------------------------------------------------
UPDATE notificacion_raw n
SET estado_parseo    = 'parseada',
    metodo_detectado = p.metodo
FROM pago_detectado p
WHERE p.notificacion_raw_id = n.id
  AND p.tipo = 'egreso'
  AND n.id IN (61, 75, 76, 84, 91, 94)
  AND n.estado_parseo <> 'parseada';

-- -------------------------------------------------------------
-- 3) VERIFICACIÓN (antes de decidir COMMIT o ROLLBACK)
-- -------------------------------------------------------------
-- a) Los 6 egresos, uno por raw (esperado: 6 filas, sin repetidos).
SELECT p.notificacion_raw_id AS raw, p.id AS pago_id, p.tipo, p.tipo_egreso, p.moneda, p.monto,
       p.recibido_en, n.estado_parseo
FROM pago_detectado p
JOIN notificacion_raw n ON n.id = p.notificacion_raw_id
WHERE p.notificacion_raw_id IN (61, 75, 76, 84, 91, 94)
ORDER BY p.notificacion_raw_id;

-- b) Conteo por tipo en perusistemas (esperado: ingresos igual que antes; egresos = 6).
SELECT p.tipo, count(*) FROM pago_detectado p JOIN empresa e ON e.id = p.empresa_id
WHERE e.slug = 'perusistemas' GROUP BY p.tipo ORDER BY p.tipo;

-- c) Ningún raw con más de un registro (esperado: 0 filas).
SELECT notificacion_raw_id, count(*) FROM pago_detectado
WHERE notificacion_raw_id IN (61, 75, 76, 84, 91, 94)
GROUP BY notificacion_raw_id HAVING count(*) > 1;

-- Si todo coincide, escribe y ejecuta:   COMMIT;
-- Si algo no coincide:                    ROLLBACK;
