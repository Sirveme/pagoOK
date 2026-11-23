"""Configuración por país del acceso y autorregistro desde la app (zWeb-14).

La app NO trae estas reglas: las pide a GET /api/v1/app/registro/config, así que
agregar un país es agregar una entrada en PAISES (y que exista en la tabla
`pais`, por la llave foránea de empresa.pais_codigo). Hoy los 6 ya existen.

No se pide documento de identidad: la identidad es el número de WhatsApp
verificado con el OTP. La validación del celular es BÁSICA (largo y primer
dígito): solo evita errores de tipeo, no prueba que el número exista.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ConfigPais:
    codigo: str
    nombre: str
    prefijo: str          # sin "+", ej. "51"
    digitos: int          # largo del número local (sin prefijo)
    empieza_con: str      # "" = cualquier dígito
    ejemplo: str          # número local de ejemplo para el placeholder

    @property
    def patron(self) -> str:
        primero = self.empieza_con or r"\d"
        return rf"^{primero}\d{{{self.digitos - 1}}}$"

    def publico(self) -> dict:
        return {
            "pais": self.codigo,
            "nombre": self.nombre,
            "prefijo": f"+{self.prefijo}",
            "celular": {
                "digitos": self.digitos,
                "empieza_con": self.empieza_con or None,
                "patron": self.patron,
                "ejemplo": self.ejemplo,
            },
        }


PAISES: dict[str, ConfigPais] = {
    c.codigo: c for c in (
        ConfigPais("PE", "Perú", "51", 9, "9", "987654321"),
        ConfigPais("BO", "Bolivia", "591", 8, "", "71234567"),
        ConfigPais("CO", "Colombia", "57", 10, "3", "3001234567"),
        ConfigPais("EC", "Ecuador", "593", 9, "9", "991234567"),
        ConfigPais("MX", "México", "52", 10, "", "5512345678"),
        ConfigPais("CL", "Chile", "56", 9, "9", "912345678"),
    )
}

@dataclass(frozen=True)
class Rubro:
    clave: str        # lo que se guarda en empresa.rubro (no cambiar una vez publicado)
    nombre: str       # texto visible
    incluye: str      # ayuda bajo el nombre, para que la persona se ubique
    icono: str        # clave de ícono: nombre de Material Symbols (la app lo mapea)
    color: str        # color del ícono, hex #RRGGBB

    def publico(self) -> dict:
        return {"clave": self.clave, "nombre": self.nombre, "incluye": self.incluye,
                "icono": self.icono, "color": self.color}


# Los mismos para todos los países, en este orden.
RUBROS: list[Rubro] = [
    Rubro("transporte_personas", "Transporte de personas", "Taxi, mototaxi, transporte público",
          "local_taxi", "#F59E0B"),
    Rubro("tecnico_profesional", "Técnico o profesional", "Técnicos de servicios, profesionales independientes",
          "handyman", "#2563EB"),
    Rubro("peluqueria_barberia", "Peluquería o barbería", "Peluquería, barbería, estética",
          "content_cut", "#DB2777"),
    Rubro("salud", "Salud", "Profesionales de la salud, consultorios",
          "medical_services", "#059669"),
    Rubro("bodega_tienda", "Bodega o tienda", "Bodega, minimarket, tienda de barrio",
          "storefront", "#EA580C"),
    Rubro("restaurante_comida", "Restaurante o comida", "Restaurante, venta de comida, delivery",
          "restaurant", "#DC2626"),
    Rubro("comercio", "Comercio", "Venta de productos, emprendimientos",
          "shopping_bag", "#7C3AED"),
    Rubro("otro", "Otro", "Cualquier otro trabajo independiente",
          "more_horiz", "#6B7280"),
]
CLAVES_RUBRO = {r.clave for r in RUBROS}
NOMBRE_RUBRO = {r.clave: r.nombre for r in RUBROS}


def pais(codigo: str | None) -> ConfigPais | None:
    return PAISES.get((codigo or "").strip().upper())


def rubros_publicos() -> list[dict]:
    return [r.publico() for r in RUBROS]


def candidato_e164(cfg: ConfigPais, celular: str | None) -> str | None:
    """'+51987654321' a partir de lo que escribió la persona, SIN validar el
    formato del país (sirve para reconocer el celular ficticio del revisor).

    Acepta el número local ('987 654 321') o con el prefijo del país
    ('51987654321', '+51 987-654-321'). None si no hay dígitos."""
    digitos = re.sub(r"\D", "", celular or "")
    if not digitos:
        return None
    # Primero el número local: un local mexicano que empieza en "52" no se
    # confunde con el prefijo.
    if len(digitos) == cfg.digitos:
        return f"+{cfg.prefijo}{digitos}"
    if digitos.startswith(cfg.prefijo):
        return f"+{digitos}"
    return f"+{cfg.prefijo}{digitos}"


def normalizar(cfg: ConfigPais, celular: str | None) -> str | None:
    """E.164 válido para ese país, o None si no cumple la validación básica."""
    e164 = candidato_e164(cfg, celular)
    if e164 is None or not e164.startswith(f"+{cfg.prefijo}"):
        return None
    local = e164[len(cfg.prefijo) + 1:]
    return e164 if re.fullmatch(cfg.patron, local) else None


def mensaje_formato(cfg: ConfigPais) -> str:
    inicio = f" y empezar con {cfg.empieza_con}" if cfg.empieza_con else ""
    return (f"Revisa el número: en {cfg.nombre} debe tener {cfg.digitos} dígitos{inicio} "
            f"(ej. {cfg.ejemplo}), sin el +{cfg.prefijo}.")
