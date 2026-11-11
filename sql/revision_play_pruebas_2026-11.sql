-- ============================================================================
-- zWeb-09 — Pagos de prueba, cuenta de revisión de Google Play y datos de ejemplo
-- Fecha: 2026-11
--
-- CÓMO CORRERLO (Railway → PGAdmin, SQL-primero, ANTES del push):
--   BEGIN;
--     \i revision_play_pruebas_2026-11.sql    -- o pega el contenido
--   -- revisa la salida de la consulta de verificación del final
--   COMMIT;   (o ROLLBACK; si algo no cuadra)
--
-- Es IDEMPOTENTE: se puede correr más de una vez sin duplicar nada.
--
-- Variables de Railway que deben quedar coherentes con este seed (solo nombres):
--   EMPRESA_REVISION_PLAY=revision-play      (o dejar que la bandera la resuelva)
--   CODIGO_REVISION_PLAY=<código de vinculación del revisor, 6+ caracteres>
--   REVISION_PANEL_CELULAR=+51900000111      (EXACTO al celular sembrado abajo)
--   REVISION_PANEL_CODIGO_HASH=<sha256 hex del código fijo del panel>
--       Generar:  python -c "import hashlib;print(hashlib.sha256(b'EL-CODIGO').hexdigest())"
-- ============================================================================

-- 1) Columnas nuevas (aditivas, con default seguro) -------------------------
ALTER TABLE pago_detectado ADD COLUMN IF NOT EXISTS es_prueba BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE empresa        ADD COLUMN IF NOT EXISTS es_cuenta_revision BOOLEAN NOT NULL DEFAULT false;

-- 2) Empresa de revisión de Google Play -------------------------------------
--    No vence (estado 'activa', prueba_inicio/fin NULL), no visible en /testers,
--    sin auto-registro, marcada con la bandera es_cuenta_revision.
INSERT INTO empresa (pais_codigo, id_fiscal, id_fiscal_tipo, razon_social, nombre_comercial, slug,
                     activa, visible_en_testers, acepta_autoregistro, es_cuenta_revision,
                     estado_cuenta, prueba_inicio, prueba_fin)
SELECT 'PE', 'REVISION-PLAY', 'RUC', 'REVISION GOOGLE PLAY', 'Revisión Google Play', 'revision-play',
       true, false, false, true, 'activa', NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM empresa WHERE slug = 'revision-play');

-- Si ya existía (p. ej. corridas previas), asegura la bandera y el no-vencimiento.
UPDATE empresa
   SET es_cuenta_revision = true, estado_cuenta = 'activa', prueba_inicio = NULL, prueba_fin = NULL,
       visible_en_testers = false, acepta_autoregistro = false
 WHERE slug = 'revision-play';

-- 3) Acceso del revisor al PANEL: usuario ficticio + membresía contador -------
--    El celular DEBE coincidir con REVISION_PANEL_CELULAR (normalizado E.164).
--    El rol 'contador' es solo lectura + reportes (no marca usado, no invita).
INSERT INTO usuario (nombre, celular, activo)
SELECT 'Revisor Google Play', '+51900000111', true
WHERE NOT EXISTS (SELECT 1 FROM usuario WHERE celular = '+51900000111');

INSERT INTO membresia (usuario_id, empresa_id, rol, activo)
SELECT u.id, e.id, 'contador', true
  FROM usuario u, empresa e
 WHERE u.celular = '+51900000111' AND e.slug = 'revision-play'
   AND NOT EXISTS (
       SELECT 1 FROM membresia m WHERE m.usuario_id = u.id AND m.empresa_id = e.id
   );

-- 4) 10 pagos de prueba de ejemplo (es_prueba=true) en los últimos 7 días -----
--    Montos y nombres ficticios. codigo_operacion 'PRUEBA-REV-01..10' sirve de
--    clave de idempotencia. recibido_en escalonado hacia atrás desde ahora.
INSERT INTO pago_detectado
    (empresa_id, metodo, monto, moneda, titular, titular_corto, codigo_operacion,
     banco, tipo, origen, es_prueba, recibido_en)
SELECT e.id, v.metodo, v.monto, 'PEN', v.titular, v.titular_corto, v.codigo_operacion,
       v.banco, 'ingreso', 'notificacion', true, (now() AT TIME ZONE 'utc') - v.atras
  FROM empresa e
  JOIN (VALUES
      ('yape',          12.50, 'ANA MARIA QUISPE',     'A. Quispe',  'PRUEBA-REV-01', 'Yape',        (interval '2 hour')),
      ('plin',          35.00, 'LUIS GONZALES ROJAS',  'L. Gonzales','PRUEBA-REV-02', 'Plin',        (interval '8 hour')),
      ('transferencia', 120.00,'MARIA FERNANDEZ DIAZ', 'M. Fernandez','PRUEBA-REV-03','Interbank',   (interval '1 day')),
      ('yape',          8.00,  'JOSE RAMIREZ TORRES',  'J. Ramirez', 'PRUEBA-REV-04', 'Yape',        (interval '1 day 5 hour')),
      ('yape',          50.00, 'CARMEN VARGAS LEON',   'C. Vargas',  'PRUEBA-REV-05', 'Yape',        (interval '2 day')),
      ('plin',          22.90, 'PEDRO CASTILLO MEZA',  'P. Castillo','PRUEBA-REV-06', 'Plin',        (interval '3 day')),
      ('transferencia', 200.00,'ROSA HUAMAN SILVA',    'R. Huaman',  'PRUEBA-REV-07', 'BCP',         (interval '4 day')),
      ('yape',          15.00, 'JORGE FLORES RIOS',    'J. Flores',  'PRUEBA-REV-08', 'Yape',        (interval '5 day')),
      ('plin',          9.50,  'LUCIA MENDOZA PAREDES','L. Mendoza', 'PRUEBA-REV-09', 'Plin',        (interval '6 day')),
      ('yape',          75.00, 'MIGUEL SALAZAR CRUZ',  'M. Salazar', 'PRUEBA-REV-10', 'Yape',        (interval '6 day 20 hour'))
  ) AS v(metodo, monto, titular, titular_corto, codigo_operacion, banco, atras) ON true
 WHERE e.slug = 'revision-play'
   AND NOT EXISTS (
       SELECT 1 FROM pago_detectado p
        WHERE p.empresa_id = e.id AND p.codigo_operacion = v.codigo_operacion AND p.es_prueba = true
   );

-- ============================================================================
-- VERIFICACIÓN (correr tras el COMMIT; debe mostrar la empresa, el acceso y 10 pagos)
-- ============================================================================
-- SELECT slug, es_cuenta_revision, estado_cuenta, prueba_fin, visible_en_testers
--   FROM empresa WHERE slug = 'revision-play';
-- SELECT u.celular, m.rol, m.activo
--   FROM usuario u JOIN membresia m ON m.usuario_id = u.id
--   JOIN empresa e ON e.id = m.empresa_id
--  WHERE e.slug = 'revision-play';
-- SELECT count(*) AS pagos_prueba, min(recibido_en) AS mas_viejo, max(recibido_en) AS mas_nuevo
--   FROM pago_detectado p JOIN empresa e ON e.id = p.empresa_id
--  WHERE e.slug = 'revision-play' AND p.es_prueba = true;
-- Esperado: es_cuenta_revision=t, estado_cuenta='activa', prueba_fin=NULL,
--           1 membresía 'contador' activa, pagos_prueba=10.
