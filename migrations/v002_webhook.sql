-- =============================================================
-- pagoOK Admin — Migración v002 — Webhook de notificaciones
-- =============================================================
-- Ejecutar: psql $DATABASE_URL -f migrations/v002_webhook.sql
-- O desde PGAdmin: Tools > Query Tool > pegar contenido > F5
-- =============================================================

BEGIN;

-- =============================================================
-- DISPOSITIVOS (celulares que envían notificaciones)
-- =============================================================

CREATE TABLE dispositivo (
    id SERIAL PRIMARY KEY,
    empresa_id INT REFERENCES empresa(id) ON DELETE CASCADE NOT NULL,
    token VARCHAR(64) UNIQUE NOT NULL,
    nombre VARCHAR(100) NOT NULL,
    modelo VARCHAR(100),
    activo BOOLEAN DEFAULT true,
    ultimo_ping TIMESTAMP,
    total_notificaciones INT DEFAULT 0,
    creado_en TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_dispositivo_token ON dispositivo(token);
CREATE INDEX idx_dispositivo_empresa ON dispositivo(empresa_id);

-- =============================================================
-- NOTIFICACIONES RAW (todo lo que llega del celular)
-- =============================================================

CREATE TABLE notificacion_raw (
    id SERIAL PRIMARY KEY,
    empresa_id INT REFERENCES empresa(id) ON DELETE CASCADE NOT NULL,
    dispositivo_id INT REFERENCES dispositivo(id),
    package_app VARCHAR(200),
    titulo TEXT,
    texto TEXT,
    sbn_key VARCHAR(200),
    timestamp_celular BIGINT,
    timestamp_servidor TIMESTAMP DEFAULT NOW(),
    estado_parseo VARCHAR(20) DEFAULT 'pendiente',
    metodo_detectado VARCHAR(20),
    error_parseo TEXT
);

CREATE INDEX idx_notif_empresa_ts ON notificacion_raw(empresa_id, timestamp_servidor DESC);
CREATE INDEX idx_notif_sbn_key ON notificacion_raw(sbn_key);
CREATE INDEX idx_notif_estado ON notificacion_raw(estado_parseo);

-- =============================================================
-- PAGOS DETECTADOS (resultado del parseo exitoso)
-- =============================================================

CREATE TABLE pago_detectado (
    id SERIAL PRIMARY KEY,
    empresa_id INT REFERENCES empresa(id) ON DELETE CASCADE NOT NULL,
    notificacion_raw_id INT REFERENCES notificacion_raw(id) ON DELETE CASCADE,
    metodo VARCHAR(20) NOT NULL,
    monto NUMERIC(12, 2) NOT NULL,
    moneda VARCHAR(3) DEFAULT 'PEN',
    titular TEXT,
    titular_corto VARCHAR(50),
    codigo_operacion VARCHAR(30),
    banco VARCHAR(50),
    recibido_en TIMESTAMP DEFAULT NOW(),
    consumido BOOLEAN DEFAULT false,
    consumido_en TIMESTAMP,
    venta_id INT
);

CREATE INDEX idx_pago_empresa_recibido ON pago_detectado(empresa_id, recibido_en DESC);
CREATE INDEX idx_pago_consumido ON pago_detectado(empresa_id, consumido, recibido_en DESC);
CREATE INDEX idx_pago_monto ON pago_detectado(empresa_id, monto, recibido_en DESC);
CREATE INDEX idx_pago_codigo ON pago_detectado(empresa_id, codigo_operacion);

INSERT INTO migracion (version) VALUES ('v002');

COMMIT;
