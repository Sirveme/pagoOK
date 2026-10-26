-- =============================================================
-- PagoOK — Registro de visitas a propuestas comerciales (zWeb-07, octubre 2026)
-- =============================================================
-- Aplicar en PGAdmin ANTES del push. Idempotente. No toca otras tablas.
-- Sin IP en claro: solo su hash (HMAC con el pepper del servidor).
-- =============================================================

BEGIN;

CREATE TABLE IF NOT EXISTS propuesta_visita (
    id            SERIAL PRIMARY KEY,
    codigo        VARCHAR(80)  NOT NULL,
    visto_en      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    user_agent    VARCHAR(60),                       -- resumido: "Android · Chrome"
    ip_hash       VARCHAR(64)  NOT NULL,
    vista_previa  BOOLEAN      NOT NULL DEFAULT FALSE -- robot de WhatsApp/Facebook armando la tarjeta
);

CREATE INDEX IF NOT EXISTS idx_propuesta_visita_codigo ON propuesta_visita(codigo, visto_en DESC);

COMMIT;

-- Verificación:
-- SELECT codigo, count(*) FILTER (WHERE NOT vista_previa) AS aperturas, max(visto_en)
--   FROM propuesta_visita GROUP BY codigo;
