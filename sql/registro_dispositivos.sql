-- =============================================================
-- pagoOK — Auto-registro de dispositivos + campos de telemetría
-- =============================================================
-- SQL-PRIMERO: aplicar A MANO en PGAdmin (Railway) ANTES del deploy.
-- Todo IDEMPOTENTE (IF NOT EXISTS). Aditivo.
-- =============================================================

BEGIN;

-- -------------------------------------------------------------
-- 1) Columnas de identidad/telemetría en `dispositivo` (Tarea 3.2)
-- -------------------------------------------------------------
ALTER TABLE dispositivo
    ADD COLUMN IF NOT EXISTS instalacion_id  VARCHAR(64) DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS manufacturer    VARCHAR(50) DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS modelo          VARCHAR(80) DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS android_version VARCHAR(20) DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS app_version     VARCHAR(30) DEFAULT NULL;

-- Idempotencia del registro por (empresa, instalacion_id).
CREATE INDEX IF NOT EXISTS ix_dispositivo_instalacion
    ON dispositivo(empresa_id, instalacion_id)
    WHERE instalacion_id IS NOT NULL;

-- -------------------------------------------------------------
-- 2) Config de empresa: ¿acepta auto-registro sin código? (nuevo)
--    No existía en el esquema. Default FALSE (seguro): sin este flag,
--    una empresa exige código de invitación para registrar dispositivos.
-- -------------------------------------------------------------
ALTER TABLE empresa
    ADD COLUMN IF NOT EXISTS acepta_autoregistro BOOLEAN NOT NULL DEFAULT false;

-- -------------------------------------------------------------
-- 3) Campos rebind en device_estado_actual (Tarea 1)
--    (el prompt indica que ya se ejecutó el ALTER; se repite por
--     idempotencia/referencia — es no-op si ya existen)
-- -------------------------------------------------------------
ALTER TABLE device_estado_actual
    ADD COLUMN IF NOT EXISTS veces_rebind_intentado INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS veces_rebind_exitoso   INTEGER DEFAULT 0;

-- -------------------------------------------------------------
-- 4) Campos consolidados en device_estado_historico (Tarea 2)
--    (referencia; el prompt indica que la tabla real ya los tiene)
-- -------------------------------------------------------------
ALTER TABLE device_estado_historico
    ADD COLUMN IF NOT EXISTS heartbeats_recibidos    INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS minutos_verde           INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS minutos_amarillo        INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS minutos_rojo            INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS pings_guardian_perdidos INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS alarmas_disparadas      INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS rebinds_intentados      INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS rebinds_exitosos        INTEGER DEFAULT 0;

COMMIT;
