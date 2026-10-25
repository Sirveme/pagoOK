-- =============================================================
-- PagoOK — Contrato de eventos v1 (webhooks salientes firmados)
-- =============================================================
-- Regla SQL-primero: BD VIVA en Railway, NO se usa create_all ni Alembic.
-- Aplicar a mano desde PGAdmin:
--   Tools > Query Tool > pegar este archivo > F5
-- (o:  psql $DATABASE_URL -f sql/eventos_webhook.sql)
--
-- Todo es ADITIVO e idempotente (IF NOT EXISTS): no toca tablas existentes.
-- Mientras este DDL no se aplique, la ingesta sigue funcionando igual: la
-- emisión de eventos falla en silencio (se loguea) y no afecta al pago.
-- =============================================================

BEGIN;

-- -------------------------------------------------------------
-- 1) Suscripciones: a qué URL se notifica cada empresa y qué tipos
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS suscripcion_webhook (
    id               SERIAL PRIMARY KEY,
    empresa_id       INT NOT NULL REFERENCES empresa(id) ON DELETE CASCADE,
    nombre           VARCHAR(150),                 -- ej. "QueVendi - tienda Lince"
    url_destino      TEXT NOT NULL,                -- HTTPS del sistema receptor
    secreto_hmac     VARCHAR(128) NOT NULL UNIQUE, -- en claro: se necesita para firmar
    tipos_suscritos  TEXT[] NOT NULL DEFAULT ARRAY['pago.confirmado']::TEXT[],
    activo           BOOLEAN NOT NULL DEFAULT true,
    creado_en        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_suscripcion_webhook_empresa
    ON suscripcion_webhook(empresa_id) WHERE activo;

-- -------------------------------------------------------------
-- 2) Log de entregas (una fila por evento x suscripción)
--    Base para reintentos, auditoría y el futuro "reenviar eventos".
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS entrega_evento (
    id                    BIGSERIAL PRIMARY KEY,
    evento_id             UUID NOT NULL,
    suscripcion_id        INT NOT NULL REFERENCES suscripcion_webhook(id) ON DELETE CASCADE,
    tipo                  VARCHAR(60) NOT NULL,
    payload               JSONB NOT NULL,          -- el sobre completo
    intentos              INT NOT NULL DEFAULT 0,
    ultimo_estado         VARCHAR(20) NOT NULL DEFAULT 'pendiente'
                          CHECK (ultimo_estado IN ('pendiente', 'entregado', 'fallido')),
    ultimo_codigo_http    INT,
    ultimo_error          TEXT,                    -- diagnóstico del último intento fallido
    proximo_reintento_en  TIMESTAMPTZ,             -- NULL = ya no se reintenta
    creado_en             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    entregado_en          TIMESTAMPTZ
);

-- No duplicar la entrega de un mismo evento a una misma suscripción.
CREATE UNIQUE INDEX IF NOT EXISTS uq_entrega_evento_evento_suscripcion
    ON entrega_evento(evento_id, suscripcion_id);

-- Lo que recorre el worker: entregas vivas con reintento vencido.
CREATE INDEX IF NOT EXISTS idx_entrega_evento_pendientes
    ON entrega_evento(proximo_reintento_en)
    WHERE ultimo_estado IN ('pendiente', 'fallido') AND proximo_reintento_en IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_entrega_evento_suscripcion
    ON entrega_evento(suscripcion_id, creado_en DESC);

COMMIT;

-- -------------------------------------------------------------
-- 3) Alta de una suscripción (PASO MANUAL, ejemplo)
-- -------------------------------------------------------------
-- Genera el secreto con:  python scripts/gestion_webhooks.py crear ...
-- (imprime el secreto UNA vez y el INSERT listo). O a mano:
--
-- INSERT INTO suscripcion_webhook (empresa_id, nombre, url_destino, secreto_hmac, tipos_suscritos)
-- VALUES (1, 'QueVendi', 'https://quevendi.pro/webhooks/pagook',
--         'whsec_REEMPLAZAR_POR_SECRETO_ALEATORIO', ARRAY['pago.confirmado']);
--
-- Consultas útiles:
--   SELECT id, tipo, ultimo_estado, intentos, ultimo_codigo_http, proximo_reintento_en
--   FROM entrega_evento ORDER BY id DESC LIMIT 50;
