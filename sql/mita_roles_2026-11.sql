-- ============================================================================
-- zWeb-12 — Duilio pasa a rol CONTADOR en la empresa MITA
-- Fecha: 2026-11-17 · PREPARADO, NO CORRIDO.
--
-- CÓMO CORRERLO: Railway → PGAdmin, pega TODO el archivo (trae su BEGIN/COMMIT).
-- Lee el mensaje (NOTICE) en la pestaña "Messages" y la verificación del final.
--
-- Regla: SOLO si en 'mita' existe OTRO dueño activo (membresía activa y usuario
-- activo). Si no existe, no cambia nada y lo informa: una empresa nunca se
-- queda sin dueño.
-- Idempotente: si Duilio ya es contador, no hace nada y lo informa.
-- ¿El código depende de esto? No.
--
-- Estado leído el 2026-11-17 (solo lectura): mita tiene 2 membresías, ambas
-- 'dueno' y activas: +519*****760 y +519*****946 (Duilio). Hoy SÍ se aplicaría.
-- ============================================================================

BEGIN;

DO $$
DECLARE
    v_empresa  INTEGER;
    v_memb     INTEGER;
    v_rol      TEXT;
    v_otros    INTEGER;
BEGIN
    SELECT id INTO v_empresa FROM empresa WHERE slug = 'mita';
    IF v_empresa IS NULL THEN
        RAISE NOTICE 'No existe la empresa mita: no se hizo nada.';
        RETURN;
    END IF;

    SELECT m.id, m.rol INTO v_memb, v_rol
      FROM membresia m JOIN usuario u ON u.id = m.usuario_id
     WHERE m.empresa_id = v_empresa AND u.celular = '+51967317946';
    IF v_memb IS NULL THEN
        RAISE NOTICE 'Duilio (+519*****946) no tiene membresía en mita: no se hizo nada.';
        RETURN;
    END IF;
    IF v_rol = 'contador' THEN
        RAISE NOTICE 'Duilio ya es contador en mita: no se hizo nada.';
        RETURN;
    END IF;

    SELECT count(*) INTO v_otros
      FROM membresia m JOIN usuario u ON u.id = m.usuario_id
     WHERE m.empresa_id = v_empresa AND m.id <> v_memb
       AND m.rol = 'dueno' AND m.activo AND u.activo;
    IF v_otros = 0 THEN
        RAISE NOTICE 'NO se cambió: no hay otro dueño activo en mita (la empresa se quedaría sin dueño).';
        RETURN;
    END IF;

    UPDATE membresia SET rol = 'contador' WHERE id = v_memb;
    RAISE NOTICE 'Listo: Duilio pasó de % a contador en mita (quedan % dueño(s) activo(s)).', v_rol, v_otros;
END $$;

COMMIT;

-- VERIFICACIÓN: membresías de mita (celular enmascarado). Esperado: Duilio
-- 'contador' activo y al menos un 'dueno' activo.
SELECT m.id, left(u.celular, 4) || '*****' || right(u.celular, 3) AS celular, m.rol, m.activo, u.activo AS usuario_activo
  FROM membresia m JOIN usuario u ON u.id = m.usuario_id JOIN empresa e ON e.id = m.empresa_id
 WHERE e.slug = 'mita'
 ORDER BY m.id;
