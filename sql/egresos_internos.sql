-- =============================================================
-- pagoOK — Sprint EGRESOS + CUENTAS PROPIAS — DDL aditivo
-- =============================================================
-- Regla SQL-PRIMERO: la BD vive en Railway y NO se usa create_all ni Alembic.
-- Aplicar A MANO en PGAdmin:  Tools > Query Tool > pegar > F5
-- (o:  psql $DATABASE_URL -f sql/egresos_internos.sql)
--
-- IMPORTANTE: aplicar ESTE SQL ANTES de desplegar el código nuevo, porque los
-- modelos SQLAlchemy ya esperan las columnas/tablas de abajo.
--
-- Todo aquí es ADITIVO e IDEMPOTENTE: se puede correr más de una vez sin daño.
-- =============================================================

BEGIN;

-- -------------------------------------------------------------
-- 1) Cuentas propias del dueño (para detectar transferencias internas)
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS persona_cuenta (
    id                  SERIAL PRIMARY KEY,
    persona_id          INTEGER NOT NULL REFERENCES persona(id) ON DELETE CASCADE,
    empresa_id          INTEGER NOT NULL REFERENCES empresa(id) ON DELETE CASCADE,
    nombre_en_push      VARCHAR(200) NOT NULL,
    nombre_normalizado  VARCHAR(200) NOT NULL,
    billetera           VARCHAR(20)  NOT NULL,
    banco               VARCHAR(50)  DEFAULT NULL,
    telefono            VARCHAR(20)  DEFAULT NULL,
    activa              BOOLEAN      DEFAULT true,
    creada_en           TIMESTAMPTZ  DEFAULT NOW(),
    actualizada_en      TIMESTAMPTZ  DEFAULT NOW()
);

-- Acelera es_posible_interno(): busca por empresa + nombre normalizado.
CREATE INDEX IF NOT EXISTS ix_persona_cuenta_empresa_norm
    ON persona_cuenta(empresa_id, nombre_normalizado)
    WHERE activa = true;

-- Valores de billetera admitidos (referencia, no se fuerza con CHECK para que
-- el usuario pueda ampliar sin migración):
--   'yape' | 'plin' | 'bcp' | 'bbva' | 'interbank' | 'scotiabank' |
--   'pichincha' | 'banbif' | 'tunki' | 'banco_nacion' | 'otro'

-- -------------------------------------------------------------
-- 2) Titulares confirmados como EXTERNOS (para no volver a sugerir)
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS titular_confirmado_externo (
    id                   SERIAL PRIMARY KEY,
    empresa_id           INTEGER NOT NULL REFERENCES empresa(id) ON DELETE CASCADE,
    nombre_normalizado   VARCHAR(200) NOT NULL,
    veces_confirmado     INTEGER DEFAULT 1,
    ultima_confirmacion  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (empresa_id, nombre_normalizado)
);

-- -------------------------------------------------------------
-- 3) Columnas nuevas en pago_detectado (aditivas)
-- -------------------------------------------------------------
ALTER TABLE pago_detectado
    ADD COLUMN IF NOT EXISTS posible_interno       BOOLEAN DEFAULT false,
    ADD COLUMN IF NOT EXISTS confirmacion_usuario  VARCHAR(20) DEFAULT NULL,
    -- Flag del parser: no pudo clasificar ingreso/egreso con certeza (revisar).
    ADD COLUMN IF NOT EXISTS tipo_incierto         BOOLEAN DEFAULT false;

-- Valores de confirmacion_usuario:
--   NULL                  = no revisado
--   'confirmado_interno'  = el usuario confirmó que es cuenta propia
--   'confirmado_externo'  = el usuario confirmó que NO es cuenta propia

-- Cola de revisión: posibles internos sin confirmar, por empresa.
CREATE INDEX IF NOT EXISTS ix_pago_detectado_posible_interno
    ON pago_detectado(empresa_id, posible_interno)
    WHERE posible_interno = true AND confirmacion_usuario IS NULL;

-- Se amplían los valores admitidos de `tipo` (decisión: agregar 'interno' y
-- 'externo_confirmado' para compatibilidad futura). El flujo actual NO setea
-- 'interno' automáticamente: la exclusión del negocio se hace con
-- confirmacion_usuario, preservando la dirección ingreso/egreso.
ALTER TABLE pago_detectado DROP CONSTRAINT IF EXISTS pago_detectado_tipo_check;
ALTER TABLE pago_detectado
    ADD CONSTRAINT pago_detectado_tipo_check
    CHECK (tipo IN ('ingreso', 'egreso', 'interno', 'externo_confirmado'));

-- -------------------------------------------------------------
-- 4) Seeding de cuentas propias — empresa_id = 1 (Perú Sistemas Pro)
--    Idempotente: solo inserta si aún no existe ese nombre normalizado.
-- -------------------------------------------------------------
INSERT INTO persona_cuenta
    (persona_id, empresa_id, nombre_en_push, nombre_normalizado, billetera, banco)
SELECT v.persona_id, v.empresa_id, v.nombre_en_push, v.nombre_normalizado, v.billetera, v.banco
FROM (VALUES
    (1, 1, 'DUILIO RESTUCCIA',               'duilio restuccia',               'yape', 'bcp'),
    (1, 1, 'Duilio Cesar Restuccia Eslava',  'duilio cesar restuccia eslava',  'plin', 'interbank'),
    (1, 1, 'Duilio C Restuccia E',           'duilio c restuccia e',           'plin', 'interbank'),
    (1, 1, 'D. RESTUCCIA',                    'd restuccia',                    'yape', 'bcp'),
    (1, 1, 'D. C. Restuccia E.',              'd c restuccia e',                'plin', 'interbank')
) AS v(persona_id, empresa_id, nombre_en_push, nombre_normalizado, billetera, banco)
WHERE NOT EXISTS (
    SELECT 1 FROM persona_cuenta pc
    WHERE pc.empresa_id = v.empresa_id
      AND pc.nombre_normalizado = v.nombre_normalizado
);

COMMIT;
