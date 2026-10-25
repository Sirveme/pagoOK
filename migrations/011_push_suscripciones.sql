-- Migración 011: tablas para Web Push Notifications
-- Fecha: 2026-06-11

CREATE TABLE IF NOT EXISTS codigos_invitacion (
    id SERIAL PRIMARY KEY,
    empresa_id INTEGER NOT NULL REFERENCES empresa(id) ON DELETE CASCADE,
    codigo VARCHAR(20) NOT NULL UNIQUE,
    descripcion VARCHAR(100),
    creado_por VARCHAR(100),
    creado_en TIMESTAMP NOT NULL DEFAULT NOW(),
    expira_en TIMESTAMP NOT NULL,
    max_usos INTEGER NOT NULL DEFAULT 50,
    usos_actuales INTEGER NOT NULL DEFAULT 0,
    activo BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_codigos_invit_empresa
    ON codigos_invitacion (empresa_id);
CREATE INDEX IF NOT EXISTS idx_codigos_invit_codigo
    ON codigos_invitacion (codigo);

CREATE TABLE IF NOT EXISTS push_suscripciones (
    id SERIAL PRIMARY KEY,
    empresa_id INTEGER NOT NULL REFERENCES empresa(id) ON DELETE CASCADE,
    codigo_invitacion_id INTEGER REFERENCES codigos_invitacion(id) ON DELETE SET NULL,
    nombre_receptor VARCHAR(100),
    rol VARCHAR(50) DEFAULT 'general',
    endpoint TEXT NOT NULL UNIQUE,
    p256dh_key TEXT NOT NULL,
    auth_key TEXT NOT NULL,
    user_agent VARCHAR(255),
    creado_en TIMESTAMP NOT NULL DEFAULT NOW(),
    ultimo_envio_en TIMESTAMP,
    envios_exitosos INTEGER NOT NULL DEFAULT 0,
    envios_fallidos INTEGER NOT NULL DEFAULT 0,
    activo BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_push_susc_empresa_activo
    ON push_suscripciones (empresa_id, activo);
CREATE INDEX IF NOT EXISTS idx_push_susc_endpoint
    ON push_suscripciones (endpoint);
