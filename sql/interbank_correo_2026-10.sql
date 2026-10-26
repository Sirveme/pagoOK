-- =============================================================
-- PagoOK — Interbank: dominio de correo para las constancias
-- =============================================================
-- Registra netinterbank.com.pe en banco_config.dominios_correo de Interbank
-- (remitente real: servicioalcliente@netinterbank.com.pe). Sin esto, los
-- correos de Interbank quedan 'rechazado' aunque su firma DKIM sea válida.
-- Idempotente: si el dominio ya está, no cambia nada. No toca otros bancos.
--
-- Uso en PGAdmin (Query Tool): ejecuta todo el archivo. Si ves un ERROR,
-- ejecuta ROLLBACK; y avísame.
-- =============================================================

BEGIN;

UPDATE banco_config
SET dominios_correo = COALESCE(dominios_correo, '[]'::jsonb) || '["netinterbank.com.pe"]'::jsonb
WHERE pais = 'PE' AND clave = 'interbank'
  AND NOT COALESCE(dominios_correo, '[]'::jsonb) ? 'netinterbank.com.pe';

COMMIT;

-- =============================================================
-- VERIFICACIÓN (solo lectura)
-- =============================================================
-- Esperado: 1 fila, Interbank, activo = true, dominios_correo = ["netinterbank.com.pe"].
SELECT clave, nombre, activo, paquetes_android, dominios_correo
FROM banco_config WHERE pais = 'PE' AND clave = 'interbank';
