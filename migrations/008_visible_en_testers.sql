-- =============================================================
-- pagoOK Admin — Migración 008 — visible_en_testers
-- =============================================================
-- Fecha: 2026-05-24
-- Propósito: filtrar qué empresas aparecen en la grilla pública /testers
-- Ejecutar: psql $DATABASE_URL -f migrations/008_visible_en_testers.sql
-- O desde PGAdmin: Tools > Query Tool > pegar contenido > F5
-- =============================================================

BEGIN;

ALTER TABLE empresa
ADD COLUMN IF NOT EXISTS visible_en_testers BOOLEAN NOT NULL DEFAULT TRUE;

-- Actualizar empresa existente (Perú Sistemas) para que aparezca
UPDATE empresa
SET visible_en_testers = TRUE
WHERE slug = 'perusistemas';

INSERT INTO migracion (version) VALUES ('008')
ON CONFLICT (version) DO NOTHING;

COMMIT;
