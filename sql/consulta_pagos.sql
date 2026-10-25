-- =============================================================
-- PagoOK — API de consulta de pagos (pull) + registro de uso
-- =============================================================
-- Regla SQL-primero: BD VIVA en Railway, NO se usa create_all ni Alembic.
-- Aplicar a mano desde PGAdmin:
--   Tools > Query Tool > pegar este archivo > F5
-- (o:  psql $DATABASE_URL -f sql/consulta_pagos.sql)
--
-- Requiere sql/api_v1.sql (tabla cuenta_api) ya aplicado.
-- Todo es ADITIVO e idempotente: pago_detectado NO cambia (ya tiene monto,
-- metodo, banco, titular, codigo_operacion, recibido_en y empresa_id).
-- =============================================================

BEGIN;

-- -------------------------------------------------------------
-- Registro de "pago usado": un pago confirma UNA sola cosa.
-- UNIQUE(pago_id) es la garantía contra el doble cobro: aunque dos
-- sistemas (o dos pedidos del mismo sistema) intenten usar el mismo
-- pago a la vez, solo una fila puede existir.
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pago_uso (
    id                  SERIAL PRIMARY KEY,
    pago_id             INT NOT NULL UNIQUE REFERENCES pago_detectado(id) ON DELETE CASCADE,
    empresa_id          INT NOT NULL REFERENCES empresa(id) ON DELETE CASCADE,
    cuenta_api_id       INT REFERENCES cuenta_api(id) ON DELETE SET NULL,  -- credencial que lo usó
    sistema_que_uso     VARCHAR(150) NOT NULL,   -- ej. "Facturalo", "QueVendi"
    referencia_externa  VARCHAR(150) NOT NULL,   -- ej. id de suscripción o pedido
    usado_en            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pago_uso_empresa
    ON pago_uso(empresa_id, usado_en DESC);
CREATE INDEX IF NOT EXISTS idx_pago_uso_referencia
    ON pago_uso(cuenta_api_id, referencia_externa);

-- La consulta busca por empresa + monto + ventana (idx_pago_monto, ya existe)
-- y por empresa + codigo_operacion (idx_pago_codigo, ya existe). Por si una BD
-- antigua no los tuviera:
CREATE INDEX IF NOT EXISTS idx_pago_monto  ON pago_detectado(empresa_id, monto, recibido_en DESC);
CREATE INDEX IF NOT EXISTS idx_pago_codigo ON pago_detectado(empresa_id, codigo_operacion);

COMMIT;

-- Consultas útiles:
--   SELECT u.*, p.monto, p.titular FROM pago_uso u JOIN pago_detectado p ON p.id = u.pago_id
--   ORDER BY u.usado_en DESC LIMIT 50;
