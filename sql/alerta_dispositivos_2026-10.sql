-- =============================================================
-- PagoOK — Aviso por WhatsApp de celular desconectado
-- =============================================================
-- Regla SQL-primero: BD VIVA en Railway, NO se usa create_all ni Alembic.
-- Aplicar a mano desde PGAdmin ANTES de desplegar:
--   Tools > Query Tool > pegar este archivo > F5
--   (o:  psql $DATABASE_URL -f sql/alerta_dispositivos_2026-10.sql)
--
-- Aditivo e idempotente. Una sola columna en `dispositivo`:
--   alerta_caida_en = cuándo se avisó que el celular dejó de conectar.
--   NULL  -> no hay caída avisada (estado normal).
--   fecha -> ya se mandó el aviso de caída; se limpia al volver a conectarse.
-- Garantiza "un solo aviso por caída" y sobrevive a los reinicios del server.
-- =============================================================

BEGIN;

ALTER TABLE dispositivo
    ADD COLUMN IF NOT EXISTS alerta_caida_en TIMESTAMP;

COMMIT;
