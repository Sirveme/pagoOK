-- ============================================================================
-- zWeb-14 — Entrar con el número desde la app y autorregistro de independientes
-- Fecha: 2026-11-22
--
-- CÓMO CORRERLO: Railway → PGAdmin, pega TODO el archivo (trae su BEGIN/COMMIT).
-- Se corre ANTES del push: el código nuevo mapea empresa.tipo_empresa y la
-- tabla ticket_acceso; sin este SQL, cualquier consulta a empresa fallaría.
-- Idempotente: se puede correr dos veces sin error ni cambios extra.
--
-- Qué hace (NO cambia datos de las empresas actuales):
--   1) empresa.id_fiscal pasa a aceptar NULL, SOLO para tipo 'independiente'.
--      Las 10 empresas actuales tienen id_fiscal y quedan como 'negocio'; el
--      CHECK ck_empresa_id_fiscal_negocio les sigue exigiendo id_fiscal.
--      El UNIQUE (pais_codigo, id_fiscal) no cambia: en Postgres varios NULL no
--      chocan entre sí.
--   2) empresa.tipo_empresa ('negocio' por defecto | 'independiente').
--   3) empresa.rubro: ya existe (sql/empresa_rubro_2026-11.sql); se asegura.
--   4) otp.proposito acepta 'app' (código pedido desde la app).
--   5) Tabla ticket_acceso (tickets de registro y de elegir empresa).
--   6) solicitud_eliminacion acepta el CELULAR en vez del id fiscal (formulario
--      público /eliminar-cuenta para independientes). Las solicitudes actuales
--      tienen id_fiscal y no cambian.
-- ============================================================================

BEGIN;

-- 1) y 2) Empresa independiente sin identificador fiscal
ALTER TABLE empresa ADD COLUMN IF NOT EXISTS tipo_empresa VARCHAR(15) NOT NULL DEFAULT 'negocio';
ALTER TABLE empresa ADD COLUMN IF NOT EXISTS rubro VARCHAR(40);
ALTER TABLE empresa ALTER COLUMN id_fiscal DROP NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_empresa_tipo_empresa') THEN
        ALTER TABLE empresa ADD CONSTRAINT ck_empresa_tipo_empresa
            CHECK (tipo_empresa IN ('negocio', 'independiente'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_empresa_id_fiscal_negocio') THEN
        ALTER TABLE empresa ADD CONSTRAINT ck_empresa_id_fiscal_negocio
            CHECK (id_fiscal IS NOT NULL OR tipo_empresa = 'independiente');
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_empresa_tipo_empresa ON empresa(tipo_empresa, creada_en DESC);

-- 4) OTP pedido desde la app (separado de 'login' del panel)
ALTER TABLE otp DROP CONSTRAINT IF EXISTS otp_proposito_check;
ALTER TABLE otp ADD CONSTRAINT otp_proposito_check
    CHECK (proposito IN ('login', 'invitacion', 'app'));

-- 5) Tickets de un solo uso tras el OTP (se guarda el SHA-256, nunca el ticket)
CREATE TABLE IF NOT EXISTS ticket_acceso (
    id            SERIAL PRIMARY KEY,
    tipo          VARCHAR(10) NOT NULL CHECK (tipo IN ('registro', 'elegir')),
    celular       VARCHAR(20) NOT NULL,                 -- E.164 verificado con el OTP
    pais          VARCHAR(2)  NOT NULL,
    token_hash    VARCHAR(64) NOT NULL UNIQUE,
    datos_equipo  JSONB,                                -- marca, modelo, versión, consentimiento
    creado_en     TIMESTAMPTZ NOT NULL DEFAULT now(),
    expira_en     TIMESTAMPTZ NOT NULL,
    usado_en      TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_ticket_acceso_celular ON ticket_acceso(celular, creado_en DESC);

-- 6) Eliminar cuenta en la web con el celular (independientes sin id fiscal)
ALTER TABLE solicitud_eliminacion ADD COLUMN IF NOT EXISTS celular VARCHAR(20);
ALTER TABLE solicitud_eliminacion ALTER COLUMN id_fiscal DROP NOT NULL;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_solicitud_eliminacion_identificador') THEN
        ALTER TABLE solicitud_eliminacion ADD CONSTRAINT ck_solicitud_eliminacion_identificador
            CHECK (id_fiscal IS NOT NULL OR celular IS NOT NULL OR estado = 'ejecutada');
    END IF;
END $$;

COMMIT;

-- ============================================================================
-- VERIFICACIÓN (correr después del COMMIT)
-- ============================================================================

-- a) Columnas de empresa. Esperado: 3 filas
--    id_fiscal    | YES | character varying | NULL
--    rubro        | YES | character varying | NULL
--    tipo_empresa | NO  | character varying | 'negocio'::character varying
SELECT column_name, is_nullable, data_type, column_default
  FROM information_schema.columns
 WHERE table_name = 'empresa' AND column_name IN ('id_fiscal', 'tipo_empresa', 'rubro')
 ORDER BY column_name;

-- b) CHECKs de empresa y de otp. Esperado: 3 filas (ck_empresa_id_fiscal_negocio,
--    ck_empresa_tipo_empresa, otp_proposito_check con 'app').
SELECT conname, pg_get_constraintdef(oid)
  FROM pg_constraint
 WHERE conname IN ('ck_empresa_tipo_empresa', 'ck_empresa_id_fiscal_negocio', 'otp_proposito_check')
 ORDER BY conname;

-- c) Empresas actuales intactas. Esperado: todas 'negocio', 0 sin id_fiscal.
SELECT tipo_empresa, count(*) AS empresas, count(*) FILTER (WHERE id_fiscal IS NULL) AS sin_id_fiscal
  FROM empresa GROUP BY tipo_empresa;

-- d) Tabla ticket_acceso. Esperado: 9 columnas en este orden.
SELECT column_name, is_nullable, data_type
  FROM information_schema.columns
 WHERE table_name = 'ticket_acceso'
 ORDER BY ordinal_position;

-- e) Índices nuevos. Esperado: 2 filas.
SELECT indexname FROM pg_indexes
 WHERE indexname IN ('idx_empresa_tipo_empresa', 'idx_ticket_acceso_celular');

-- f) solicitud_eliminacion. Esperado: 2 filas
--    celular   | YES | character varying
--    id_fiscal | YES | character varying
--    y 1 fila del CHECK ck_solicitud_eliminacion_identificador.
SELECT column_name, is_nullable, data_type
  FROM information_schema.columns
 WHERE table_name = 'solicitud_eliminacion' AND column_name IN ('id_fiscal', 'celular')
 ORDER BY column_name;
SELECT conname, pg_get_constraintdef(oid)
  FROM pg_constraint WHERE conname = 'ck_solicitud_eliminacion_identificador';

-- g) Países del autorregistro en la tabla pais (llave foránea). Esperado: 6 filas.
SELECT codigo, nombre FROM pais WHERE codigo IN ('PE', 'BO', 'CO', 'EC', 'MX', 'CL') ORDER BY codigo;
