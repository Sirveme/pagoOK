-- =============================================================
-- pagoOK Admin — Migración v001 — Esquema inicial
-- =============================================================
-- Ejecutar: psql $DATABASE_URL -f migrations/v001_init.sql
-- O desde PGAdmin: Tools > Query Tool > pegar contenido > F5
-- =============================================================

BEGIN;

-- Control de migraciones
CREATE TABLE IF NOT EXISTS migracion (
    id SERIAL PRIMARY KEY,
    version VARCHAR(20) UNIQUE NOT NULL,
    aplicada_en TIMESTAMP DEFAULT NOW()
);

-- =============================================================
-- PAÍSES Y TRIBUTOS
-- =============================================================

CREATE TABLE pais (
    codigo VARCHAR(3) PRIMARY KEY,
    nombre VARCHAR(100) NOT NULL,
    moneda_codigo VARCHAR(3) NOT NULL,
    moneda_simbolo VARCHAR(5) NOT NULL,
    nombre_id_fiscal VARCHAR(30) NOT NULL,
    longitud_id_fiscal_min INT,
    longitud_id_fiscal_max INT,
    nombre_id_persona VARCHAR(30),
    longitud_id_persona_min INT,
    longitud_id_persona_max INT,
    activo BOOLEAN DEFAULT true,
    orden INT DEFAULT 0
);

CREATE TABLE tributo (
    id SERIAL PRIMARY KEY,
    pais_codigo VARCHAR(3) REFERENCES pais(codigo) ON DELETE CASCADE,
    codigo VARCHAR(20) NOT NULL,
    nombre VARCHAR(100) NOT NULL,
    tasa NUMERIC(7,4) NOT NULL,
    es_porcentaje BOOLEAN DEFAULT true,
    activo_default BOOLEAN DEFAULT true,
    UNIQUE (pais_codigo, codigo)
);

CREATE TABLE regimen_tributario (
    id SERIAL PRIMARY KEY,
    pais_codigo VARCHAR(3) REFERENCES pais(codigo) ON DELETE CASCADE,
    codigo VARCHAR(20) NOT NULL,
    nombre VARCHAR(100) NOT NULL,
    descripcion TEXT,
    orden INT DEFAULT 0,
    UNIQUE (pais_codigo, codigo)
);

-- =============================================================
-- PERSONAS (dueños + vendedores)
-- =============================================================

CREATE TABLE persona (
    id SERIAL PRIMARY KEY,
    pais_codigo VARCHAR(3) REFERENCES pais(codigo) NOT NULL,
    id_fiscal VARCHAR(20) NOT NULL,
    nombre_completo VARCHAR(200) NOT NULL,
    email VARCHAR(150),
    telefono VARCHAR(30),
    es_duenio BOOLEAN DEFAULT false,
    password_hash VARCHAR(255),
    pin_hash VARCHAR(255),
    activa BOOLEAN DEFAULT true,
    bloqueada BOOLEAN DEFAULT false,
    intentos_fallidos INT DEFAULT 0,
    ultimo_login TIMESTAMP,
    creada_en TIMESTAMP DEFAULT NOW(),
    actualizada_en TIMESTAMP DEFAULT NOW(),
    UNIQUE (pais_codigo, id_fiscal)
);

CREATE INDEX idx_persona_id_fiscal ON persona(id_fiscal);
CREATE INDEX idx_persona_email ON persona(email);

-- =============================================================
-- EMPRESAS
-- =============================================================

CREATE TABLE empresa (
    id SERIAL PRIMARY KEY,
    pais_codigo VARCHAR(3) REFERENCES pais(codigo) NOT NULL,
    id_fiscal VARCHAR(20) NOT NULL,
    razon_social VARCHAR(200) NOT NULL,
    nombre_comercial VARCHAR(200),
    domicilio_fiscal TEXT,
    ciudad VARCHAR(100),
    regimen_tributario_id INT REFERENCES regimen_tributario(id),
    forma_pago_default VARCHAR(20) DEFAULT 'CONTADO',
    es_zona_especial BOOLEAN DEFAULT false,
    zona_especial_nombre VARCHAR(100),
    logo_url TEXT,
    icono_sectorial VARCHAR(50),
    slug VARCHAR(60) UNIQUE NOT NULL,
    email_notif_bancos VARCHAR(150) UNIQUE,
    duenio_id INT REFERENCES persona(id),
    activa BOOLEAN DEFAULT true,
    creada_en TIMESTAMP DEFAULT NOW(),
    actualizada_en TIMESTAMP DEFAULT NOW(),
    UNIQUE (pais_codigo, id_fiscal)
);

CREATE INDEX idx_empresa_id_fiscal ON empresa(id_fiscal);
CREATE INDEX idx_empresa_slug ON empresa(slug);

CREATE TABLE empresa_tributo (
    empresa_id INT REFERENCES empresa(id) ON DELETE CASCADE,
    tributo_id INT REFERENCES tributo(id),
    PRIMARY KEY (empresa_id, tributo_id)
);

-- =============================================================
-- LOCALES Y VENDEDORES
-- =============================================================

CREATE TABLE local (
    id SERIAL PRIMARY KEY,
    empresa_id INT REFERENCES empresa(id) ON DELETE CASCADE,
    nombre VARCHAR(150) NOT NULL,
    direccion TEXT,
    es_anexo BOOLEAN DEFAULT false,
    codigo_anexo VARCHAR(10),
    serie_base INT,
    activo BOOLEAN DEFAULT true,
    creado_en TIMESTAMP DEFAULT NOW()
);

CREATE TABLE vendedor_local (
    id SERIAL PRIMARY KEY,
    persona_id INT REFERENCES persona(id),
    local_id INT REFERENCES local(id) ON DELETE CASCADE,
    serie_b VARCHAR(10) NOT NULL,
    serie_f VARCHAR(10) NOT NULL,
    serie_p VARCHAR(10),
    activo BOOLEAN DEFAULT true,
    creado_en TIMESTAMP DEFAULT NOW(),
    UNIQUE (local_id, serie_b),
    UNIQUE (local_id, serie_f)
);

CREATE INDEX idx_vendedor_persona ON vendedor_local(persona_id);
CREATE INDEX idx_vendedor_local ON vendedor_local(local_id);

-- =============================================================
-- MÉTODOS DE PAGO
-- =============================================================

CREATE TABLE metodo_pago (
    id SERIAL PRIMARY KEY,
    empresa_id INT REFERENCES empresa(id) ON DELETE CASCADE,
    tipo VARCHAR(20) NOT NULL,
    celular VARCHAR(20),
    titular VARCHAR(200),
    qr_url TEXT,
    voucher_obligatorio BOOLEAN DEFAULT false,
    activo BOOLEAN DEFAULT true,
    orden INT DEFAULT 0,
    UNIQUE (empresa_id, tipo)
);

CREATE TABLE cuenta_bancaria (
    id SERIAL PRIMARY KEY,
    empresa_id INT REFERENCES empresa(id) ON DELETE CASCADE,
    banco VARCHAR(50) NOT NULL,
    tipo_cuenta VARCHAR(50),
    numero VARCHAR(50) NOT NULL,
    cci VARCHAR(50),
    titular VARCHAR(200),
    activa BOOLEAN DEFAULT true,
    orden INT DEFAULT 0,
    creada_en TIMESTAMP DEFAULT NOW()
);

-- =============================================================
-- CONFIGURACIÓN DE IMPRESIÓN
-- =============================================================

CREATE TABLE config_impresion (
    empresa_id INT PRIMARY KEY REFERENCES empresa(id) ON DELETE CASCADE,
    formato_default VARCHAR(10) DEFAULT '58mm',
    comprobante_default VARCHAR(20) DEFAULT 'boleta',
    imprime_boleta BOOLEAN DEFAULT true,
    imprime_factura BOOLEAN DEFAULT true,
    imprime_proforma BOOLEAN DEFAULT true,
    imprime_nota_credito BOOLEAN DEFAULT false,
    imprime_nota_debito BOOLEAN DEFAULT false,
    imprime_guia_remision BOOLEAN DEFAULT false,
    imprime_ticket_simple BOOLEAN DEFAULT false
);

-- =============================================================
-- SESIONES
-- =============================================================

CREATE TABLE sesion_admin (
    token VARCHAR(64) PRIMARY KEY,
    persona_id INT REFERENCES persona(id) ON DELETE CASCADE,
    empresa_id INT REFERENCES empresa(id),
    creada_en TIMESTAMP DEFAULT NOW(),
    expira_en TIMESTAMP NOT NULL,
    ip VARCHAR(50),
    user_agent TEXT
);

CREATE INDEX idx_sesion_persona ON sesion_admin(persona_id);
CREATE INDEX idx_sesion_expira ON sesion_admin(expira_en);

-- =============================================================
-- DATOS SEMILLA
-- =============================================================

INSERT INTO pais (codigo, nombre, moneda_codigo, moneda_simbolo, nombre_id_fiscal, longitud_id_fiscal_min, longitud_id_fiscal_max, nombre_id_persona, longitud_id_persona_min, longitud_id_persona_max, orden) VALUES
  ('PE',  'Perú',       'PEN', 'S/',   'RUC',  11, 11, 'DNI',  8,  8,  1),
  ('MX',  'México',     'MXN', '$',    'RFC',  12, 13, 'CURP', 18, 18, 2),
  ('CO',  'Colombia',   'COP', '$',    'NIT',  9,  10, 'CC',   6,  10, 3),
  ('CL',  'Chile',      'CLP', '$',    'RUT',  8,  10, 'RUT',  8,  10, 4),
  ('EC',  'Ecuador',    'USD', '$',    'RUC',  13, 13, 'CI',   10, 10, 5),
  ('BO',  'Bolivia',    'BOB', 'Bs',   'NIT',  7,  12, 'CI',   6,  10, 6),
  ('AR',  'Argentina',  'ARS', '$',    'CUIT', 11, 11, 'DNI',  7,  8,  7),
  ('VE',  'Venezuela',  'VES', 'Bs.S', 'RIF',  9,  10, 'CI',   6,  9,  8),
  ('ES',  'España',     'EUR', '€',    'NIF',  9,  9,  'DNI',  9,  9,  9);

INSERT INTO tributo (pais_codigo, codigo, nombre, tasa, activo_default) VALUES
  ('PE', 'IGV',    'Impuesto General a las Ventas',  18.00, true),
  ('PE', 'ICBPER', 'Impuesto a bolsas plásticas',     0.50, false),
  ('MX', 'IVA',    'Impuesto al Valor Agregado',     16.00, true),
  ('MX', 'IEPS',   'Imp. Especial sobre Producción y Servicios', 8.00, false),
  ('CO', 'IVA',    'Impuesto al Valor Agregado',     19.00, true),
  ('CO', 'INC',    'Impuesto Nacional al Consumo',    8.00, false),
  ('CL', 'IVA',    'Impuesto al Valor Agregado',     19.00, true),
  ('EC', 'IVA',    'Impuesto al Valor Agregado',     12.00, true),
  ('BO', 'IVA',    'Impuesto al Valor Agregado',     13.00, true),
  ('AR', 'IVA',    'Impuesto al Valor Agregado',     21.00, true),
  ('VE', 'IVA',    'Impuesto al Valor Agregado',     16.00, true),
  ('ES', 'IVA',    'Impuesto al Valor Añadido',      21.00, true);

INSERT INTO regimen_tributario (pais_codigo, codigo, nombre, descripcion, orden) VALUES
  ('PE', 'NRUS', 'Nuevo Régimen Único Simplificado', 'Para pequeños negocios. Pago mensual fijo según ingresos.', 1),
  ('PE', 'RER',  'Régimen Especial de Renta',         'Tasa fija 1.5% sobre ingresos netos mensuales.', 2),
  ('PE', 'RMT',  'Régimen MYPE Tributario',           'Para micro y pequeñas empresas hasta 1700 UIT anuales.', 3),
  ('PE', 'RG',   'Régimen General',                   'Para medianas y grandes empresas. Renta neta + IGV.', 4);

INSERT INTO migracion (version) VALUES ('v001');

COMMIT;

-- =============================================================
-- Para verificar instalación, ejecuta:
--   SELECT * FROM pais;
--   SELECT * FROM tributo;
--   SELECT version FROM migracion;
-- =============================================================
