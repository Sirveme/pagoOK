-- =============================================================
-- PagoOK — Multi-empresa (zWeb-04, octubre 2026)
-- =============================================================
-- Aplicar en PGAdmin (Tools > Query Tool > pegar > F5), DESPUÉS de los
-- SQL anteriores (api_v1, eventos_webhook, consulta_pagos).
--
-- Reglas:
--   * Idempotente: se puede correr más de una vez (IF NOT EXISTS,
--     ON CONFLICT DO NOTHING, guardas con DO $$ ... $$).
--   * NO borra ni modifica pagos (pago_detectado / notificacion_raw / pago_uso
--     no se tocan).
--   * No emite webhooks (solo DDL y datos de configuración).
--
-- Decisiones:
--   * empresa.pais = la columna existente pais_codigo (ISO alfa-2, FK a pais).
--     No se duplica. UNIQUE (pais_codigo, id_fiscal) ya existe.
--   * Las empresas que YA existen quedan 'activa' (comportamiento actual).
--     Solo las empresas NUEVAS nacen en 'prueba' por 7 días.
-- =============================================================

BEGIN;

-- -------------------------------------------------------------
-- 1) EMPRESA: tipo de id fiscal + estado de la cuenta
-- -------------------------------------------------------------
ALTER TABLE empresa
    ADD COLUMN IF NOT EXISTS id_fiscal_tipo  VARCHAR(10),
    ADD COLUMN IF NOT EXISTS estado_cuenta   VARCHAR(12) NOT NULL DEFAULT 'activa',
    ADD COLUMN IF NOT EXISTS prueba_inicio   TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS prueba_fin      TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS prueba_ampliada BOOLEAN NOT NULL DEFAULT false;

-- id_fiscal_tipo desde el catálogo de países (RUC, RFC, NIT, RUT, CUIT...).
UPDATE empresa e
SET id_fiscal_tipo = p.nombre_id_fiscal
FROM pais p
WHERE p.codigo = e.pais_codigo
  AND e.id_fiscal_tipo IS NULL;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_empresa_estado_cuenta') THEN
        ALTER TABLE empresa ADD CONSTRAINT ck_empresa_estado_cuenta
            CHECK (estado_cuenta IN ('prueba', 'activa', 'vencida', 'suspendida'));
    END IF;
END $$;

-- Las existentes ya quedaron 'activa' por el DEFAULT de arriba. A partir de
-- aquí, una empresa NUEVA nace en prueba de 7 días (el código también lo fija).
ALTER TABLE empresa ALTER COLUMN estado_cuenta SET DEFAULT 'prueba';
ALTER TABLE empresa ALTER COLUMN prueba_inicio SET DEFAULT now();
ALTER TABLE empresa ALTER COLUMN prueba_fin    SET DEFAULT now() + interval '7 days';

-- perusistemas: siempre activa, sin fin de prueba, datos fiscales completos.
UPDATE empresa
SET estado_cuenta  = 'activa',
    prueba_inicio  = NULL,
    prueba_fin     = NULL,
    pais_codigo    = 'PE',
    id_fiscal_tipo = 'RUC',
    id_fiscal      = '20615446565',
    razon_social   = 'PERU SISTEMAS PRO E.I.R.L.'
WHERE slug = 'perusistemas';

-- -------------------------------------------------------------
-- 2) USUARIOS DEL PANEL (sin DNI ni documento de identidad)
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS usuario (
    id        SERIAL PRIMARY KEY,
    nombre    VARCHAR(150) NOT NULL,
    celular   VARCHAR(20)  NOT NULL UNIQUE,        -- E.164, ej. +51987654321
    email     VARCHAR(150),
    creado_en TIMESTAMPTZ  NOT NULL DEFAULT now(),
    activo    BOOLEAN      NOT NULL DEFAULT true
);

CREATE TABLE IF NOT EXISTS membresia (
    id          SERIAL PRIMARY KEY,
    usuario_id  INT NOT NULL REFERENCES usuario(id) ON DELETE CASCADE,
    empresa_id  INT NOT NULL REFERENCES empresa(id) ON DELETE CASCADE,
    rol         VARCHAR(12) NOT NULL CHECK (rol IN ('dueno', 'cajero', 'almacen', 'contador')),
    activo      BOOLEAN NOT NULL DEFAULT true,
    creado_en   TIMESTAMPTZ NOT NULL DEFAULT now(),
    revocado_en TIMESTAMPTZ,
    CONSTRAINT uq_membresia_usuario_empresa UNIQUE (usuario_id, empresa_id)
);
CREATE INDEX IF NOT EXISTS idx_membresia_empresa ON membresia(empresa_id) WHERE activo;

CREATE TABLE IF NOT EXISTS sesion (
    id          SERIAL PRIMARY KEY,
    usuario_id  INT NOT NULL REFERENCES usuario(id) ON DELETE CASCADE,
    empresa_id  INT NOT NULL REFERENCES empresa(id) ON DELETE CASCADE,
    token_hash  VARCHAR(64) NOT NULL UNIQUE,        -- SHA-256; nunca el token en claro
    creado_en   TIMESTAMPTZ NOT NULL DEFAULT now(),
    expira_en   TIMESTAMPTZ NOT NULL,
    revocada_en TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_sesion_usuario_empresa ON sesion(usuario_id, empresa_id);

CREATE TABLE IF NOT EXISTS otp (
    id          SERIAL PRIMARY KEY,
    celular     VARCHAR(20) NOT NULL,
    codigo_hash VARCHAR(64) NOT NULL,               -- HMAC-SHA256 con pepper
    proposito   VARCHAR(12) NOT NULL CHECK (proposito IN ('login', 'invitacion')),
    creado_en   TIMESTAMPTZ NOT NULL DEFAULT now(),
    expira_en   TIMESTAMPTZ NOT NULL,                -- 5 minutos
    intentos    INT NOT NULL DEFAULT 0,              -- máx. 5
    usado_en    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_otp_celular ON otp(celular, proposito, creado_en DESC);

-- -------------------------------------------------------------
-- 3) VINCULACIÓN DE CELULARES E INVITACIONES
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS codigo_vinculacion (
    id             SERIAL PRIMARY KEY,
    empresa_id     INT NOT NULL REFERENCES empresa(id) ON DELETE CASCADE,
    codigo_hash    VARCHAR(64) NOT NULL UNIQUE,
    creado_por     INT REFERENCES usuario(id) ON DELETE SET NULL,
    creado_en      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expira_en      TIMESTAMPTZ NOT NULL,             -- 10 minutos
    usado_en       TIMESTAMPTZ,
    dispositivo_id INT REFERENCES dispositivo(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS invitacion (
    id          SERIAL PRIMARY KEY,
    empresa_id  INT NOT NULL REFERENCES empresa(id) ON DELETE CASCADE,
    token_hash  VARCHAR(64) NOT NULL UNIQUE,
    rol         VARCHAR(12) NOT NULL CHECK (rol IN ('dueno', 'cajero', 'almacen', 'contador')),
    creado_por  INT REFERENCES usuario(id) ON DELETE SET NULL,
    creado_en   TIMESTAMPTZ NOT NULL DEFAULT now(),
    expira_en   TIMESTAMPTZ NOT NULL,                -- 24 horas
    usado_en    TIMESTAMPTZ,
    usuario_id  INT REFERENCES usuario(id) ON DELETE SET NULL
);

-- -------------------------------------------------------------
-- 4) DISPOSITIVO: campos nuevos (todos opcionales)
-- -------------------------------------------------------------
ALTER TABLE dispositivo
    ADD COLUMN IF NOT EXISTS consentimiento_version VARCHAR(20),
    ADD COLUMN IF NOT EXISTS consentimiento_fecha   TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS pais                   VARCHAR(2),
    ADD COLUMN IF NOT EXISTS bancos_declarados      JSONB,
    ADD COLUMN IF NOT EXISTS nivel_equipo           VARCHAR(20),
    ADD COLUMN IF NOT EXISTS egresos_activo         BOOLEAN;

-- Backfill: dispositivos sin empresa -> perusistemas (hoy no hay ninguno;
-- empresa_id ya es NOT NULL, se deja por si una BD antigua lo permitiera).
UPDATE dispositivo
SET empresa_id = (SELECT id FROM empresa WHERE slug = 'perusistemas')
WHERE empresa_id IS NULL;

-- -------------------------------------------------------------
-- 5) CATÁLOGO DE BANCOS POR PAÍS
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS banco_config (
    id     SERIAL PRIMARY KEY,
    pais   VARCHAR(2)  NOT NULL,
    clave  VARCHAR(30) NOT NULL,
    nombre VARCHAR(80) NOT NULL,
    tipo   VARCHAR(10) NOT NULL CHECK (tipo IN ('billetera', 'banco')),
    activo BOOLEAN NOT NULL DEFAULT true,
    orden  INT NOT NULL DEFAULT 0,
    CONSTRAINT uq_banco_config_pais_clave UNIQUE (pais, clave)
);

-- Perú: el catálogo que ya reconoce el parser + plin y tunki.
INSERT INTO banco_config (pais, clave, nombre, tipo, orden) VALUES
    ('PE', 'yape',         'Yape',              'billetera', 1),
    ('PE', 'plin',         'Plin',              'billetera', 2),
    ('PE', 'tunki',        'Tunki',             'billetera', 3),
    ('PE', 'bim',          'BIM',               'billetera', 4),
    ('PE', 'bcp',          'BCP',               'banco',     1),
    ('PE', 'interbank',    'Interbank',         'banco',     2),
    ('PE', 'bbva',         'BBVA',              'banco',     3),
    ('PE', 'scotiabank',   'Scotiabank',        'banco',     4),
    ('PE', 'banco_nacion', 'Banco de la Nación','banco',     5),
    ('PE', 'pichincha',    'Banco Pichincha',   'banco',     6),
    ('PE', 'banbif',       'BanBif',            'banco',     7)
ON CONFLICT (pais, clave) DO NOTHING;

COMMIT;

-- =============================================================
-- VERIFICACIÓN POSTERIOR (solo lectura; ejecutar después del COMMIT)
-- =============================================================

-- a) Estado de cada empresa (perusistemas: activa, sin prueba_fin, RUC).
SELECT id, slug, pais_codigo AS pais, id_fiscal_tipo, id_fiscal, razon_social,
       estado_cuenta, prueba_fin, prueba_ampliada
FROM empresa ORDER BY id;

-- b) Conteos por empresa: dispositivos y pagos (los pagos NO deben cambiar).
SELECT e.slug,
       (SELECT count(*) FROM dispositivo d WHERE d.empresa_id = e.id)     AS dispositivos,
       (SELECT count(*) FROM pago_detectado p WHERE p.empresa_id = e.id)  AS pagos
FROM empresa e ORDER BY e.id;

-- c) Dispositivos sin empresa (esperado: 0).
SELECT count(*) AS dispositivos_sin_empresa FROM dispositivo WHERE empresa_id IS NULL;

-- d) Catálogo de bancos de Perú (esperado: 11 filas, billeteras primero).
SELECT tipo, clave, nombre FROM banco_config WHERE pais = 'PE' AND activo
ORDER BY CASE tipo WHEN 'billetera' THEN 0 ELSE 1 END, orden, nombre;

-- e) Tablas nuevas vacías (esperado: 0 en todas al estrenar).
SELECT (SELECT count(*) FROM usuario) AS usuarios, (SELECT count(*) FROM membresia) AS membresias,
       (SELECT count(*) FROM sesion) AS sesiones, (SELECT count(*) FROM otp) AS otps,
       (SELECT count(*) FROM codigo_vinculacion) AS codigos, (SELECT count(*) FROM invitacion) AS invitaciones;
