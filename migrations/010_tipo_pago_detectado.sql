-- Migración 010: agrega columna 'tipo' a pago_detectado
-- Fecha: 2026-06-11
-- Propósito: distinguir ingresos de egresos para Flujo de Caja Semanal
--
-- NOTA: el modelo real usa `recibido_en` (no `fecha_evento`) como timestamp
-- del pago, y `metodo` (no `metodo_pago`). El índice se ajusta a eso.

ALTER TABLE pago_detectado
ADD COLUMN IF NOT EXISTS tipo VARCHAR(10) NOT NULL DEFAULT 'ingreso';

-- Constraint para validar valores
ALTER TABLE pago_detectado
DROP CONSTRAINT IF EXISTS pago_detectado_tipo_check;

ALTER TABLE pago_detectado
ADD CONSTRAINT pago_detectado_tipo_check
CHECK (tipo IN ('ingreso', 'egreso'));

-- Índice para queries de Flujo de Caja (empresa + tipo + fecha)
CREATE INDEX IF NOT EXISTS idx_pago_detectado_empresa_tipo_fecha
ON pago_detectado (empresa_id, tipo, recibido_en);

-- Todos los registros existentes son ingresos (no parseábamos egresos antes)
UPDATE pago_detectado SET tipo = 'ingreso' WHERE tipo IS NULL OR tipo = '';
