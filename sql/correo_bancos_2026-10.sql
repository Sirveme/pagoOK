-- =============================================================
-- PagoOK — Canal de correo bancario + administración de bancos (zWeb-06)
-- =============================================================
-- Aplicar en PGAdmin DESPUÉS de sql/egresos_privacidad_2026-10.sql.
-- Idempotente. NO borra ni modifica pagos existentes (solo agrega columnas
-- con valores por defecto). No emite webhooks.
-- El correo de Hostinger (@pagook.pro) no se toca: el canal usa el subdominio
-- bancos.pagook.pro (Cloudflare Email Routing). La columna antigua
-- empresa.email_notif_bancos se mantiene, sin usar.
-- =============================================================

BEGIN;

-- -------------------------------------------------------------
-- 1) Origen de cada pago/egreso (celular o correo) y destino del envío
-- -------------------------------------------------------------
ALTER TABLE pago_detectado
    ADD COLUMN IF NOT EXISTS origen  VARCHAR(12) NOT NULL DEFAULT 'notificacion',
    ADD COLUMN IF NOT EXISTS destino VARCHAR(40);          -- ej. Yape, Interbank, Plin

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_pago_origen') THEN
        ALTER TABLE pago_detectado ADD CONSTRAINT ck_pago_origen CHECK (origen IN ('notificacion', 'correo'));
    END IF;
END $$;

-- -------------------------------------------------------------
-- 2) Dirección de correo por empresa: egresos+<codigo>@bancos.pagook.pro
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS correo_empresa (
    id            SERIAL PRIMARY KEY,
    empresa_id    INT NOT NULL REFERENCES empresa(id) ON DELETE CASCADE,
    codigo        VARCHAR(16) NOT NULL UNIQUE,     -- 8 caracteres aleatorios, nunca el RUC
    activo        BOOLEAN NOT NULL DEFAULT true,
    creado_en     TIMESTAMPTZ NOT NULL DEFAULT now(),
    regenerado_en TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_correo_empresa_empresa ON correo_empresa(empresa_id) WHERE activo;

-- -------------------------------------------------------------
-- 3) Correos recibidos (texto purgado a los 90 días)
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS correo_raw (
    id                  SERIAL PRIMARY KEY,
    empresa_id          INT REFERENCES empresa(id) ON DELETE CASCADE,   -- NULL si se descartó
    destinatario        VARCHAR(200),
    remitente           VARCHAR(200),
    asunto              TEXT,
    cuerpo_texto        TEXT,
    mime                BYTEA,                    -- mensaje original (para re-verificar DKIM); se purga
    message_id          VARCHAR(300),
    recibido_en         TIMESTAMPTZ NOT NULL DEFAULT now(),
    dkim_ok             BOOLEAN NOT NULL DEFAULT false,
    dkim_dominios       VARCHAR(300),             -- dominios con firma DKIM válida
    banco_clave         VARCHAR(30),
    estado              VARCHAR(20) NOT NULL
                        CHECK (estado IN ('procesado', 'sin_formato', 'rechazado', 'descartado', 'confirmacion_gmail')),
    motivo              TEXT,
    codigo_confirmacion VARCHAR(20),              -- confirmación de reenvío de Gmail
    pago_id             INT REFERENCES pago_detectado(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_correo_raw_empresa ON correo_raw(empresa_id, recibido_en DESC);
CREATE INDEX IF NOT EXISTS idx_correo_raw_estado ON correo_raw(estado, recibido_en DESC);
CREATE INDEX IF NOT EXISTS idx_correo_raw_msgid ON correo_raw(empresa_id, message_id);

-- -------------------------------------------------------------
-- 4) Catálogo de bancos: datos técnicos y tipo "caja"
-- -------------------------------------------------------------
ALTER TABLE banco_config
    ADD COLUMN IF NOT EXISTS paquetes_android JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS dominios_correo  JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS guia_alertas     TEXT,
    ADD COLUMN IF NOT EXISTS color            VARCHAR(9);

DO $$
BEGIN
    -- El CHECK original (billetera | banco) se reemplaza por uno que admite cajas.
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'banco_config_tipo_check') THEN
        ALTER TABLE banco_config DROP CONSTRAINT banco_config_tipo_check;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_banco_config_tipo') THEN
        ALTER TABLE banco_config ADD CONSTRAINT ck_banco_config_tipo CHECK (tipo IN ('billetera', 'banco', 'caja'));
    END IF;
END $$;

-- Nuevos del catálogo de Perú (sin datos técnicos inventados: vacíos para completar en /admin/bancos).
INSERT INTO banco_config (pais, clave, nombre, tipo, orden) VALUES
    ('PE', 'ligo',           'Ligo',           'billetera', 5),
    ('PE', 'mibanco',        'Mibanco',        'banco',     8),
    ('PE', 'caja_maynas',    'Caja Maynas',    'caja',      1),
    ('PE', 'caja_arequipa',  'Caja Arequipa',  'caja',      2),
    ('PE', 'caja_huancayo',  'Caja Huancayo',  'caja',      3),
    ('PE', 'caja_piura',     'Caja Piura',     'caja',      4),
    ('PE', 'caja_trujillo',  'Caja Trujillo',  'caja',      5)
ON CONFLICT (pais, clave) DO NOTHING;

-- Nombres técnicos de las apps que la app PagoOK YA escucha hoy (lista de
-- NotificationService.kt). Solo se completan si están vacíos.
UPDATE banco_config b SET paquetes_android = v.paquetes
FROM (VALUES
    ('yape',         '["com.bcp.innovacxion.yapeapp"]'::jsonb),
    ('tunki',        '["pe.com.interbank.tunki"]'::jsonb),
    ('bcp',          '["pe.com.bcp.bcppersonas"]'::jsonb),
    ('interbank',    '["pe.com.interbank.mobilebanking"]'::jsonb),
    ('bbva',         '["com.bbva.bbvacontigo"]'::jsonb),
    ('scotiabank',   '["com.scotiabank.consumer.peru"]'::jsonb),
    ('banco_nacion', '["com.bn.bnap.bnportal.app"]'::jsonb),
    ('pichincha',    '["pe.com.pichincha.bancamovil"]'::jsonb),
    ('banbif',       '["pe.banbif.mobilebanking"]'::jsonb)
) AS v(clave, paquetes)
WHERE b.pais = 'PE' AND b.clave = v.clave AND b.paquetes_android = '[]'::jsonb;

-- -------------------------------------------------------------
-- 5) Pedidos de bancos que todavía no están ("Pídelo" en la app)
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS solicitud_banco (
    id                 SERIAL PRIMARY KEY,
    pais               VARCHAR(2)   NOT NULL,
    nombre_banco       VARCHAR(80)  NOT NULL,
    nombre_normalizado VARCHAR(80)  NOT NULL,
    dispositivo_id     INT REFERENCES dispositivo(id) ON DELETE SET NULL,
    empresa_id         INT REFERENCES empresa(id) ON DELETE SET NULL,
    ip_hash            VARCHAR(64)  NOT NULL,
    creado_en          TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_solicitud_banco_ip ON solicitud_banco(ip_hash, creado_en DESC);
CREATE INDEX IF NOT EXISTS idx_solicitud_banco_nombre ON solicitud_banco(pais, nombre_normalizado);

-- -------------------------------------------------------------
-- 6) Empresa demo para el revisor de Google Play (no es perusistemas)
--    Se usa solo si la variable CODIGO_REVISION_PLAY tiene valor.
-- -------------------------------------------------------------
INSERT INTO empresa (pais_codigo, id_fiscal, id_fiscal_tipo, razon_social, nombre_comercial, slug,
                     activa, visible_en_testers, acepta_autoregistro, estado_cuenta, prueba_inicio, prueba_fin)
SELECT 'PE', 'DEMO-PLAY', 'RUC', 'PAGOOK DEMO REVISION', 'PagoOK Demo', 'demo-revision',
       true, false, false, 'activa', NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM empresa WHERE slug = 'demo-revision');

COMMIT;

-- =============================================================
-- VERIFICACIÓN POSTERIOR (solo lectura)
-- =============================================================
-- a) Los pagos existentes quedaron con origen 'notificacion' (esperado: 1 fila, notificacion).
SELECT origen, count(*) FROM pago_detectado GROUP BY origen;

-- b) Tablas nuevas vacías (esperado: 0, 0, 0).
SELECT (SELECT count(*) FROM correo_empresa) AS correos_empresa,
       (SELECT count(*) FROM correo_raw)     AS correos,
       (SELECT count(*) FROM solicitud_banco) AS solicitudes_banco;

-- c) Catálogo de Perú: 18 entradas; cuáles no tienen app ni dominio de correo todavía.
SELECT tipo, clave, nombre, paquetes_android, dominios_correo
FROM banco_config WHERE pais = 'PE'
ORDER BY CASE tipo WHEN 'billetera' THEN 0 WHEN 'banco' THEN 1 ELSE 2 END, orden;

-- d) Empresa demo de revisión (esperado: 1 fila, activa, no visible en testers).
SELECT id, slug, razon_social, estado_cuenta, visible_en_testers FROM empresa WHERE slug = 'demo-revision';
