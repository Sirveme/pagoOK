from sqlalchemy import Column, Integer, String, Boolean, Text, ForeignKey, Numeric, DateTime
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.admin.db import Base


class Pais(Base):
    __tablename__ = "pais"
    codigo = Column(String(3), primary_key=True)
    nombre = Column(String(100), nullable=False)
    moneda_codigo = Column(String(3), nullable=False)
    moneda_simbolo = Column(String(5), nullable=False)
    nombre_id_fiscal = Column(String(30), nullable=False)
    longitud_id_fiscal_min = Column(Integer)
    longitud_id_fiscal_max = Column(Integer)
    nombre_id_persona = Column(String(30))
    longitud_id_persona_min = Column(Integer)
    longitud_id_persona_max = Column(Integer)
    activo = Column(Boolean, default=True)
    orden = Column(Integer, default=0)

    tributos = relationship("Tributo", back_populates="pais")
    regimenes = relationship("RegimenTributario", back_populates="pais")


class Tributo(Base):
    __tablename__ = "tributo"
    id = Column(Integer, primary_key=True)
    pais_codigo = Column(String(3), ForeignKey("pais.codigo", ondelete="CASCADE"))
    codigo = Column(String(20), nullable=False)
    nombre = Column(String(100), nullable=False)
    tasa = Column(Numeric(7, 4), nullable=False)
    es_porcentaje = Column(Boolean, default=True)
    activo_default = Column(Boolean, default=True)

    pais = relationship("Pais", back_populates="tributos")


class RegimenTributario(Base):
    __tablename__ = "regimen_tributario"
    id = Column(Integer, primary_key=True)
    pais_codigo = Column(String(3), ForeignKey("pais.codigo", ondelete="CASCADE"))
    codigo = Column(String(20), nullable=False)
    nombre = Column(String(100), nullable=False)
    descripcion = Column(Text)
    orden = Column(Integer, default=0)

    pais = relationship("Pais", back_populates="regimenes")


class Persona(Base):
    __tablename__ = "persona"
    id = Column(Integer, primary_key=True)
    pais_codigo = Column(String(3), ForeignKey("pais.codigo"), nullable=False)
    id_fiscal = Column(String(20), nullable=False)
    nombre_completo = Column(String(200), nullable=False)
    email = Column(String(150))
    telefono = Column(String(30))
    es_duenio = Column(Boolean, default=False)
    password_hash = Column(String(255))
    pin_hash = Column(String(255))
    activa = Column(Boolean, default=True)
    bloqueada = Column(Boolean, default=False)
    intentos_fallidos = Column(Integer, default=0)
    ultimo_login = Column(DateTime)
    creada_en = Column(DateTime, server_default=func.now())
    actualizada_en = Column(DateTime, server_default=func.now())


class Empresa(Base):
    __tablename__ = "empresa"
    id = Column(Integer, primary_key=True)
    pais_codigo = Column(String(3), ForeignKey("pais.codigo"), nullable=False)
    id_fiscal = Column(String(20), nullable=False)
    razon_social = Column(String(200), nullable=False)
    nombre_comercial = Column(String(200))
    domicilio_fiscal = Column(Text)
    ciudad = Column(String(100))
    regimen_tributario_id = Column(Integer, ForeignKey("regimen_tributario.id"))
    forma_pago_default = Column(String(20), default="CONTADO")
    es_zona_especial = Column(Boolean, default=False)
    zona_especial_nombre = Column(String(100))
    logo_url = Column(Text)
    icono_sectorial = Column(String(50))
    slug = Column(String(60), unique=True, nullable=False)
    email_notif_bancos = Column(String(150), unique=True)
    duenio_id = Column(Integer, ForeignKey("persona.id"))
    activa = Column(Boolean, default=True)
    creada_en = Column(DateTime, server_default=func.now())
    actualizada_en = Column(DateTime, server_default=func.now())

    pais = relationship("Pais")
    regimen = relationship("RegimenTributario")
    duenio = relationship("Persona")
    locales = relationship("Local", back_populates="empresa", cascade="all, delete-orphan")
    metodos_pago = relationship("MetodoPago", back_populates="empresa", cascade="all, delete-orphan")
    cuentas_bancarias = relationship("CuentaBancaria", back_populates="empresa", cascade="all, delete-orphan")
    config_impresion = relationship("ConfigImpresion", back_populates="empresa", uselist=False, cascade="all, delete-orphan")


class Local(Base):
    __tablename__ = "local"
    id = Column(Integer, primary_key=True)
    empresa_id = Column(Integer, ForeignKey("empresa.id", ondelete="CASCADE"))
    nombre = Column(String(150), nullable=False)
    direccion = Column(Text)
    es_anexo = Column(Boolean, default=False)
    codigo_anexo = Column(String(10))
    serie_base = Column(Integer)
    activo = Column(Boolean, default=True)
    creado_en = Column(DateTime, server_default=func.now())

    empresa = relationship("Empresa", back_populates="locales")
    vendedores = relationship("VendedorLocal", back_populates="local", cascade="all, delete-orphan")


class VendedorLocal(Base):
    __tablename__ = "vendedor_local"
    id = Column(Integer, primary_key=True)
    persona_id = Column(Integer, ForeignKey("persona.id"))
    local_id = Column(Integer, ForeignKey("local.id", ondelete="CASCADE"))
    serie_b = Column(String(10), nullable=False)
    serie_f = Column(String(10), nullable=False)
    serie_p = Column(String(10))
    activo = Column(Boolean, default=True)
    creado_en = Column(DateTime, server_default=func.now())

    persona = relationship("Persona")
    local = relationship("Local", back_populates="vendedores")


class MetodoPago(Base):
    __tablename__ = "metodo_pago"
    id = Column(Integer, primary_key=True)
    empresa_id = Column(Integer, ForeignKey("empresa.id", ondelete="CASCADE"))
    tipo = Column(String(20), nullable=False)
    celular = Column(String(20))
    titular = Column(String(200))
    qr_url = Column(Text)
    voucher_obligatorio = Column(Boolean, default=False)
    activo = Column(Boolean, default=True)
    orden = Column(Integer, default=0)

    empresa = relationship("Empresa", back_populates="metodos_pago")


class CuentaBancaria(Base):
    __tablename__ = "cuenta_bancaria"
    id = Column(Integer, primary_key=True)
    empresa_id = Column(Integer, ForeignKey("empresa.id", ondelete="CASCADE"))
    banco = Column(String(50), nullable=False)
    tipo_cuenta = Column(String(50))
    numero = Column(String(50), nullable=False)
    cci = Column(String(50))
    titular = Column(String(200))
    activa = Column(Boolean, default=True)
    orden = Column(Integer, default=0)
    creada_en = Column(DateTime, server_default=func.now())

    empresa = relationship("Empresa", back_populates="cuentas_bancarias")


class ConfigImpresion(Base):
    __tablename__ = "config_impresion"
    empresa_id = Column(Integer, ForeignKey("empresa.id", ondelete="CASCADE"), primary_key=True)
    formato_default = Column(String(10), default="58mm")
    comprobante_default = Column(String(20), default="boleta")
    imprime_boleta = Column(Boolean, default=True)
    imprime_factura = Column(Boolean, default=True)
    imprime_proforma = Column(Boolean, default=True)
    imprime_nota_credito = Column(Boolean, default=False)
    imprime_nota_debito = Column(Boolean, default=False)
    imprime_guia_remision = Column(Boolean, default=False)
    imprime_ticket_simple = Column(Boolean, default=False)

    empresa = relationship("Empresa", back_populates="config_impresion")


class SesionAdmin(Base):
    __tablename__ = "sesion_admin"
    token = Column(String(64), primary_key=True)
    persona_id = Column(Integer, ForeignKey("persona.id", ondelete="CASCADE"))
    empresa_id = Column(Integer, ForeignKey("empresa.id"))
    creada_en = Column(DateTime, server_default=func.now())
    expira_en = Column(DateTime, nullable=False)
    ip = Column(String(50))
    user_agent = Column(Text)


class Migracion(Base):
    __tablename__ = "migracion"
    id = Column(Integer, primary_key=True)
    version = Column(String(20), unique=True, nullable=False)
    aplicada_en = Column(DateTime, server_default=func.now())
