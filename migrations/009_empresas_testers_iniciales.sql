-- =============================================================
-- pagoOK Admin — Migración 009 — Empresas iniciales beta
-- =============================================================
-- Fecha: 2026-05-24
-- Propósito: crea 3 empresas iniciales para etapa beta
-- IMPORTANTE: requiere que exista pais 'PE' (insertado por v001_init.sql).
--   El campo regimen_tributario_id queda NULL (la empresa lo completará después).
-- Idempotente: usa INSERT...SELECT...WHERE NOT EXISTS, puede correrse N veces.
-- =============================================================

BEGIN;

-- 1) Multiservicios Shevalche (sin slug alias, slug = RUC)
INSERT INTO empresa (
    pais_codigo, id_fiscal, razon_social, nombre_comercial,
    slug, forma_pago_default, es_zona_especial,
    activa, visible_en_testers, creada_en, actualizada_en
)
SELECT
    'PE', '20615643735', 'MULTISERVICIOS SHEVALCHE EIRL', 'MULTISERVICIOS SHEVALCHE',
    '20615643735', 'CONTADO', false,
    true, true, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM empresa WHERE id_fiscal = '20615643735');

-- 2) Colegio de Contadores Públicos de Loreto (slug alias 'ccpl')
INSERT INTO empresa (
    pais_codigo, id_fiscal, razon_social, nombre_comercial,
    slug, forma_pago_default, es_zona_especial,
    activa, visible_en_testers, creada_en, actualizada_en
)
SELECT
    'PE', '20103830991', 'COLEGIO DE CONTADORES PUBLICOS DE LORETO', 'CCPL',
    'ccpl', 'CONTADO', false,
    true, true, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM empresa WHERE id_fiscal = '20103830991');

-- 3) Pollería Bolognesi (sin slug alias, slug = RUC)
INSERT INTO empresa (
    pais_codigo, id_fiscal, razon_social, nombre_comercial,
    slug, forma_pago_default, es_zona_especial,
    activa, visible_en_testers, creada_en, actualizada_en
)
SELECT
    'PE', '10462121496', 'POLLERIA BOLOGNESI', 'POLLERIA BOLOGNESI',
    '10462121496', 'CONTADO', false,
    true, true, NOW(), NOW()
WHERE NOT EXISTS (SELECT 1 FROM empresa WHERE id_fiscal = '10462121496');

INSERT INTO migracion (version) VALUES ('009')
ON CONFLICT (version) DO NOTHING;

COMMIT;
