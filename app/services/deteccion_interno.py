"""Detección de transferencias internas (cuentas propias del dueño).

Al recibir un pago se compara el titular extraído contra las cuentas propias
registradas de la empresa. Si hay coincidencia razonable se marca
`posible_interno=true` como SUGERENCIA — NUNCA se cambia el `tipo`
automáticamente. El usuario confirma manualmente en la interfaz.

Cuidado especial con familiares (padre, hijo, hermanos) de apellido parecido:
por eso, una vez que el usuario confirma un nombre como EXTERNO, ese nombre se
guarda en `titular_confirmado_externo` y no se vuelve a sugerir.
"""
from __future__ import annotations

import difflib
import re
import unicodedata

from sqlalchemy.orm import Session

from app.admin.models import Empresa
from app.api.models_internos import PersonaCuenta, TitularConfirmadoExterno

# Umbral de similitud global (difflib) para considerar match parcial.
UMBRAL_SIMILITUD = 0.85
# Longitud mínima de un token para tratarlo como apellido compartido.
LONGITUD_APELLIDO = 4


def normalizar(nombre: str) -> str:
    """Normaliza un nombre para comparar: minúsculas, sin tildes, sin puntos.

    Ejemplos:
      "D. C. Restuccia E."            -> "d c restuccia e"
      "Duilio Cesar Restuccia Eslava" -> "duilio cesar restuccia eslava"
    """
    if not nombre:
        return ""
    # Quitar tildes/diacríticos.
    sin_tildes = "".join(
        c for c in unicodedata.normalize("NFKD", nombre)
        if not unicodedata.combining(c)
    )
    texto = sin_tildes.lower()
    # Quitar puntos y comas.
    texto = texto.replace(".", " ").replace(",", " ")
    # Colapsar espacios múltiples y trim.
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto


def _match_parcial(norm: str, referencia: str) -> bool:
    """True si `norm` se parece a `referencia` (similitud global o inicial+apellido)."""
    if not referencia:
        return False
    if difflib.SequenceMatcher(None, norm, referencia).ratio() >= UMBRAL_SIMILITUD:
        return True
    tokens = norm.split()
    ref_tokens = referencia.split()
    if not tokens or not ref_tokens:
        return False
    # Misma inicial del primer nombre...
    if tokens[0][:1] != ref_tokens[0][:1]:
        return False
    # ...y un apellido (token largo) en común.
    apellidos_norm = {t for t in tokens if len(t) >= LONGITUD_APELLIDO}
    apellidos_ref = {t for t in ref_tokens if len(t) >= LONGITUD_APELLIDO}
    return bool(apellidos_norm & apellidos_ref)


def es_posible_interno(empresa_id: int, titular_extraido: str, session: Session) -> bool:
    """Devuelve True si el titular parece una cuenta propia del dueño.

    Reglas:
      1. Si el nombre ya fue confirmado como EXTERNO antes -> False (no sugerir).
      2. Match EXACTO contra persona_cuenta.nombre_normalizado (empresa, activa) -> True.
      3. Match PARCIAL (similitud >= 85% o inicial+apellido compartido) contra las
         cuentas propias o el nombre del dueño -> True.
      4. Si no -> False.

    NUNCA marca como interno automáticamente: solo sugiere `posible_interno`.
    """
    if not titular_extraido or not titular_extraido.strip():
        return False

    norm = normalizar(titular_extraido)
    if not norm:
        return False

    # 1) Ya confirmado externo -> nunca volver a sugerir.
    externo = (
        session.query(TitularConfirmadoExterno)
        .filter(
            TitularConfirmadoExterno.empresa_id == empresa_id,
            TitularConfirmadoExterno.nombre_normalizado == norm,
        )
        .first()
    )
    if externo:
        return False

    # 2) Referencias: cuentas propias activas + nombre del dueño.
    cuentas = (
        session.query(PersonaCuenta)
        .filter(
            PersonaCuenta.empresa_id == empresa_id,
            PersonaCuenta.activa == True,  # noqa: E712
        )
        .all()
    )
    referencias = [c.nombre_normalizado for c in cuentas if c.nombre_normalizado]

    empresa = session.query(Empresa).filter(Empresa.id == empresa_id).first()
    if empresa and empresa.duenio and empresa.duenio.nombre_completo:
        referencias.append(normalizar(empresa.duenio.nombre_completo))

    # Match exacto.
    if norm in referencias:
        return True

    # Match parcial (ojo con familiares: solo SUGIERE, el usuario decide).
    return any(_match_parcial(norm, ref) for ref in referencias)
