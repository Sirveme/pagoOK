-- =============================================================
-- PagoOK — Egresos + eliminación de datos (zWeb-05, octubre 2026)
-- =============================================================
-- Aplicar en PGAdmin DESPUÉS de sql/multiempresa_2026-10.sql.
-- Idempotente. NO borra ni modifica pagos existentes. No emite webhooks.
--
-- Decisión: la dirección del dinero YA existe en pago_detectado.tipo
-- ('ingreso' | 'egreso', default 'ingreso', migración 010). No se agrega una
-- columna `direccion` duplicada: tipo = dirección. Aquí solo se agrega el
-- detalle del egreso.
-- =============================================================

BEGIN;

-- -------------------------------------------------------------
-- 1) Detalle de egresos en pago_detectado
-- -------------------------------------------------------------
ALTER TABLE pago_detectado
    ADD COLUMN IF NOT EXISTS tipo_egreso      VARCHAR(25),
    ADD COLUMN IF NOT EXISTS tarjeta_ultimos4 VARCHAR(4);    -- nunca el número completo

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_pago_tipo_egreso') THEN
        ALTER TABLE pago_detectado ADD CONSTRAINT ck_pago_tipo_egreso CHECK (
            tipo_egreso IS NULL OR tipo_egreso IN (
                'envio_billetera', 'transferencia_enviada', 'consumo_tarjeta', 'retiro_cajero',
                'pago_servicio', 'cargo_recurrente', 'otro_egreso'));
    END IF;
END $$;

-- La página /{slug}/egresos filtra por empresa + tipo + fecha.
CREATE INDEX IF NOT EXISTS idx_pago_empresa_tipo_fecha
    ON pago_detectado(empresa_id, tipo, recibido_en DESC);

-- -------------------------------------------------------------
-- 2) Solicitudes de eliminación (formulario público /eliminar-cuenta)
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS solicitud_eliminacion (
    id          SERIAL PRIMARY KEY,
    pais        VARCHAR(2)   NOT NULL,
    id_fiscal   VARCHAR(20)  NOT NULL,
    nombre      VARCHAR(150),
    contacto    VARCHAR(150),                 -- celular o correo
    motivo      TEXT,
    ip_hash     VARCHAR(64)  NOT NULL,        -- HMAC de la IP (límite 5/día), nunca la IP
    estado      VARCHAR(12)  NOT NULL DEFAULT 'pendiente'
                CHECK (estado IN ('pendiente', 'ejecutada', 'rechazada')),
    creado_en   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    atendida_en TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_solicitud_elim_ip ON solicitud_eliminacion(ip_hash, creado_en DESC);
CREATE INDEX IF NOT EXISTS idx_solicitud_elim_estado ON solicitud_eliminacion(estado, creado_en DESC);

-- -------------------------------------------------------------
-- 3) Auditoría mínima de eliminaciones (sin nombres ni datos de pagos)
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS eliminacion_auditoria (
    id             SERIAL PRIMARY KEY,
    fecha          TIMESTAMPTZ NOT NULL DEFAULT now(),
    id_fiscal_hash VARCHAR(64) NOT NULL,      -- HMAC de pais|id_fiscal
    conteos        JSONB       NOT NULL,      -- filas borradas por tabla
    canal          VARCHAR(12) NOT NULL CHECK (canal IN ('autoservicio', 'solicitud'))
);

COMMIT;

-- =============================================================
-- VERIFICACIÓN POSTERIOR (solo lectura)
-- =============================================================
-- a) Columnas nuevas (esperado: 2 filas)
SELECT column_name, data_type FROM information_schema.columns
WHERE table_name = 'pago_detectado' AND column_name IN ('tipo_egreso', 'tarjeta_ultimos4');

-- b) Los pagos no cambian: conteo por empresa y tipo (comparar con el de antes)
SELECT empresa_id, tipo, count(*) FROM pago_detectado GROUP BY 1, 2 ORDER BY 1, 2;

-- c) Tablas nuevas vacías (esperado: 0 y 0)
SELECT (SELECT count(*) FROM solicitud_eliminacion) AS solicitudes,
       (SELECT count(*) FROM eliminacion_auditoria) AS auditorias;
