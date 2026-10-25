-- =============================================================
-- pagoOK — API pública v1 — DDL aditivo
-- =============================================================
-- Regla SQL-primero: este backend corre en Railway con BD VIVA y NO usa
-- create_all. NO ejecutar automáticamente. Aplicar a mano desde PGAdmin:
--   Tools > Query Tool > pegar este archivo > F5
-- (o:  psql $DATABASE_URL -f sql/api_v1.sql)
--
-- Todo aquí es ADITIVO: no altera tablas de ingesta Android ni borra datos.
-- =============================================================

BEGIN;

-- -------------------------------------------------------------
-- 1) Credenciales de la API pública v1 (consumidores externos)
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cuenta_api (
    id            SERIAL PRIMARY KEY,
    empresa_id    INT NOT NULL REFERENCES empresa(id) ON DELETE CASCADE,
    nombre        VARCHAR(150) NOT NULL,
    api_key_hash  VARCHAR(64) UNIQUE NOT NULL,   -- SHA-256 hex; nunca el valor en claro
    prefijo       VARCHAR(20),                   -- parte no secreta, para identificar
    activa        BOOLEAN NOT NULL DEFAULT true,
    creada_en     TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cuenta_api_hash    ON cuenta_api(api_key_hash);
CREATE INDEX IF NOT EXISTS idx_cuenta_api_empresa ON cuenta_api(empresa_id);

-- -------------------------------------------------------------
-- 2) Columnas de reclamo en pago_detectado (aditivas)
--    Independientes de `consumido` (uso interno de la PWA Caja).
-- -------------------------------------------------------------
ALTER TABLE pago_detectado
    ADD COLUMN IF NOT EXISTS reclamado_por INT,
    ADD COLUMN IF NOT EXISTS reclamado_en  TIMESTAMP;

-- FK hacia cuenta_api (SET NULL si se borra la cuenta). Idempotente.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_pago_reclamado_por'
    ) THEN
        ALTER TABLE pago_detectado
            ADD CONSTRAINT fk_pago_reclamado_por
            FOREIGN KEY (reclamado_por) REFERENCES cuenta_api(id) ON DELETE SET NULL;
    END IF;
END $$;

-- Acelera GET /api/v1/pagos (filtro por empresa + no-reclamados + ventana).
CREATE INDEX IF NOT EXISTS idx_pago_empresa_reclamo
    ON pago_detectado(empresa_id, reclamado_por, recibido_en);

-- -------------------------------------------------------------
-- 3) Cuenta para el producto "pagos" (primer consumidor)
-- -------------------------------------------------------------
-- El hash de abajo corresponde a la api_key entregada a Duilio por separado
-- (mostrada UNA sola vez; guardarla cifrada en integraciones de pagos).
--
-- PASO MANUAL: reemplazar :EMPRESA_ID por el id de la empresa cuyos pagos
-- consumirá el producto "pagos". Para verlos:
--     SELECT id, nombre_comercial, razon_social FROM empresa ORDER BY id;
--
-- Luego descomentar y ejecutar el INSERT:
--
-- INSERT INTO cuenta_api (empresa_id, nombre, api_key_hash, prefijo, activa)
-- VALUES (
--     :EMPRESA_ID,
--     'Producto pagos',
--     '40d01debe618cf831ac43fc40d35df60d47f33a28c2b5f3f3cf174df76ba9205',
--     'pk_live_SzBGsk',
--     true
-- );

COMMIT;
