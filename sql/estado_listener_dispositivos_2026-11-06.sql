-- =============================================================
-- PagoOK — Estado del lector de notificaciones + último pago capturado
-- Incidente 06-11-2026 (dispositivo 18, empresa 10 / MITA):
--   heartbeat "verde" pero el NotificationListener estaba desconectado por
--   Android, así que un Yape de S/ 1 llegó al teléfono y NO se envió a PagoOK.
-- =============================================================
-- Regla SQL-primero: BD VIVA en Railway, NO se usa create_all ni Alembic.
-- Aplicar a mano ANTES de desplegar:
--   Tools > Query Tool (PGAdmin) > pegar este archivo > F5
--   (o:  psql $DATABASE_URL -f sql/estado_listener_dispositivos_2026-11-06.sql)
--
-- Aditivo e idempotente. Todas las columnas quedan NULL en los equipos que
-- todavía no reportan (apps < 1.6.x): NULL = "desconocido", no fuerza rojo.
--
--   device_estado_actual.listener_conectado      -> ¿el lector está conectado?
--   device_estado_actual.permiso_notificaciones  -> ¿sigue concedido el permiso
--                                                    de acceso a notificaciones?
--   dispositivo.ultimo_pago_capturado            -> último pago que el servidor
--                                                    capturó de este equipo.
--
-- Este es el ÚNICO SQL del lote 06-11. El resto de los cambios NO tocan el
-- esquema:
--   * app vieja (listener NULL) -> amarillo "actualiza la app": usa la columna
--     listener_conectado de arriba.
--   * avisos "no_reconocido" (POST /api/v1/device/no-reconocido): se guardan en
--     notificacion_raw.estado_parseo = 'no_reconocido' (columna ya existente).
--   * paneles /admin/bancos, /admin/eliminaciones, /admin/propuestas a sesión
--     de superadmin: solo código, sin esquema.
-- =============================================================

BEGIN;

ALTER TABLE device_estado_actual
    ADD COLUMN IF NOT EXISTS listener_conectado     BOOLEAN,
    ADD COLUMN IF NOT EXISTS permiso_notificaciones BOOLEAN;

ALTER TABLE dispositivo
    ADD COLUMN IF NOT EXISTS ultimo_pago_capturado TIMESTAMP;

COMMIT;

-- Verificación (deben aparecer las 3 columnas nuevas):
SELECT table_name, column_name, data_type
  FROM information_schema.columns
 WHERE (table_name = 'device_estado_actual'
        AND column_name IN ('listener_conectado', 'permiso_notificaciones'))
    OR (table_name = 'dispositivo' AND column_name = 'ultimo_pago_capturado')
 ORDER BY table_name, column_name;
