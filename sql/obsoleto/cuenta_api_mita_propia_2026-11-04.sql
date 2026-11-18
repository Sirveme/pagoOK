-- #############################################################
-- OBSOLETO — NO CORRER. (Nota del 2026-11-17)
-- MITA ya consulta en producción con su propia clave, creada por script el
-- 2026-11-04: cuenta_api id=6, prefijo pk_live_mYhUYp, empresa 'mita'
-- (marcados de uso de "Mita.pe" hasta el 2026-11-16, al menos).
-- La cuenta de este archivo (pk_live_AkYr-4) no existe en cuenta_api y ningún
-- sistema la usa: correrlo crearía una segunda cuenta activa en 'mita' sin dueño.
-- Se conserva solo como registro.
-- #############################################################

-- =============================================================
-- PagoOK — Cuenta API "MITA (propia)" en la empresa nueva de MITA (04/11/2026)
-- =============================================================
-- CORRER SOLO DESPUÉS de crear la empresa de MITA con slug = 'mita'
-- (scripts/gestion_empresas.py crear ... --slug mita --aplicar).
--
-- Si la empresa 'mita' todavía no existe, el INSERT inserta 0 filas y no pasa
-- nada (revisa el SELECT de verificación: debe devolver 1 fila).
--
-- Solo se guarda el hash SHA-256 de la clave; la clave en claro NO está aquí
-- (se mostró una sola vez al generarla y va directo a Railway).
-- Idempotente: volver a correrlo no duplica la cuenta.
--
-- La clave "MITA" actual vive en la empresa 'perusistemas' y sigue activa:
-- no se toca aquí. Se desactivará cuando el sistema de MITA use esta nueva.
-- =============================================================

BEGIN;

INSERT INTO cuenta_api (empresa_id, nombre, api_key_hash, prefijo, activa)
SELECT e.id, 'MITA (propia)',
       '701b18f6d499428cda480093746b7735bc5fdaac61e2b9f3a4bf7610bbc11e14',
       'pk_live_AkYr-4', TRUE
  FROM empresa e
 WHERE e.slug = 'mita'
   AND NOT EXISTS (
       SELECT 1 FROM cuenta_api
        WHERE api_key_hash = '701b18f6d499428cda480093746b7735bc5fdaac61e2b9f3a4bf7610bbc11e14'
   );

COMMIT;

-- Verificación (debe devolver 1 fila, activa = true, empresa_id = la de 'mita'):
SELECT c.id, c.empresa_id, e.slug, c.nombre, c.prefijo, c.activa, c.creada_en
  FROM cuenta_api c
  JOIN empresa e ON e.id = c.empresa_id
 WHERE c.prefijo = 'pk_live_AkYr-4';
