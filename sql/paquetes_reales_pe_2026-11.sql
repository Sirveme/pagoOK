-- =============================================================
-- PagoOK — Paquetes reales de bancos peruanos (Google Play)
-- =============================================================
-- La app suma a su lista fija los paquetes_android de /api/v1/bancos-config.
-- 7 de los 9 paquetes de esa lista no coinciden con la app publicada en Play;
-- aquí se agregan los reales. NO se quita ningún paquete antiguo.
--
-- Tunki queda PENDIENTE y no se toca: pe.com.interbank.mpay.customer figura en
-- Play como "Interbank Negocios" y no hay avisos reales que lo confirmen.
--
-- Idempotente: si un paquete ya está, no cambia nada. No toca otros bancos.
-- Uso en PGAdmin (Query Tool): ejecuta todo el archivo. Si ves un ERROR,
-- ejecuta ROLLBACK; y avísame.
-- =============================================================

BEGIN;

UPDATE banco_config
SET paquetes_android = COALESCE(paquetes_android, '[]'::jsonb) || '["com.bcp.bank.bcp"]'::jsonb
WHERE pais = 'PE' AND clave = 'bcp'
  AND NOT COALESCE(paquetes_android, '[]'::jsonb) ? 'com.bcp.bank.bcp';

UPDATE banco_config
SET paquetes_android = COALESCE(paquetes_android, '[]'::jsonb) || '["com.bbva.nxt_peru"]'::jsonb
WHERE pais = 'PE' AND clave = 'bbva'
  AND NOT COALESCE(paquetes_android, '[]'::jsonb) ? 'com.bbva.nxt_peru';

UPDATE banco_config
SET paquetes_android = COALESCE(paquetes_android, '[]'::jsonb) || '["pe.com.scotiabank.blpm.android.client"]'::jsonb
WHERE pais = 'PE' AND clave = 'scotiabank'
  AND NOT COALESCE(paquetes_android, '[]'::jsonb) ? 'pe.com.scotiabank.blpm.android.client';

UPDATE banco_config
SET paquetes_android = COALESCE(paquetes_android, '[]'::jsonb) || '["pe.com.bn.app.bancodelanacion"]'::jsonb
WHERE pais = 'PE' AND clave = 'banco_nacion'
  AND NOT COALESCE(paquetes_android, '[]'::jsonb) ? 'pe.com.bn.app.bancodelanacion';

UPDATE banco_config
SET paquetes_android = COALESCE(paquetes_android, '[]'::jsonb) || '["pe.com.banbif.pnappmobile"]'::jsonb
WHERE pais = 'PE' AND clave = 'banbif'
  AND NOT COALESCE(paquetes_android, '[]'::jsonb) ? 'pe.com.banbif.pnappmobile';

UPDATE banco_config
SET paquetes_android = COALESCE(paquetes_android, '[]'::jsonb) || '["pe.com.banbif.pjappmobile"]'::jsonb
WHERE pais = 'PE' AND clave = 'banbif'
  AND NOT COALESCE(paquetes_android, '[]'::jsonb) ? 'pe.com.banbif.pjappmobile';

UPDATE banco_config
SET paquetes_android = COALESCE(paquetes_android, '[]'::jsonb) || '["pe.pichincha.bm"]'::jsonb
WHERE pais = 'PE' AND clave = 'pichincha'
  AND NOT COALESCE(paquetes_android, '[]'::jsonb) ? 'pe.pichincha.bm';

COMMIT;

-- =============================================================
-- VERIFICACIÓN (solo lectura)
-- =============================================================
-- Esperado: 7 filas, todas con presente = true.
SELECT v.clave, v.paquete, b.paquetes_android ? v.paquete AS presente, b.paquetes_android
FROM (VALUES
    ('bcp', 'com.bcp.bank.bcp'),
    ('bbva', 'com.bbva.nxt_peru'),
    ('scotiabank', 'pe.com.scotiabank.blpm.android.client'),
    ('banco_nacion', 'pe.com.bn.app.bancodelanacion'),
    ('banbif', 'pe.com.banbif.pnappmobile'),
    ('banbif', 'pe.com.banbif.pjappmobile'),
    ('pichincha', 'pe.pichincha.bm')
) AS v(clave, paquete)
JOIN banco_config b ON b.pais = 'PE' AND b.clave = v.clave
ORDER BY v.clave, v.paquete;
