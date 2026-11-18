"""Logs de avisos SIN su contenido (zWeb-12).

La política de privacidad promete que el texto de los avisos se guarda 90 días
en la base y luego se borra. Los logs de Railway no se purgan con esa regla,
así que ningún log puede llevar título, texto ni datos sacados del aviso
(titular, monto). Solo metadatos: dispositivo, empresa, paquete, estado y
longitud del texto.
"""
from __future__ import annotations

import os
import re
import traceback

_RE_PAQUETE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$")


def paquete_seguro(package: str | None) -> str:
    """El paquete solo se registra si TIENE FORMATO de paquete Android
    (com.banco.app). Si un cliente manda otra cosa en ese campo (por ejemplo,
    texto del aviso), en el log queda 'invalido'."""
    p = (package or "").strip()
    if not p:
        return "-"
    return p[:100] if _RE_PAQUETE.match(p) else "invalido"


def log_aviso(evento: str, dispositivo, package: str | None, estado: str | None,
              texto: str | None, **extra) -> str:
    partes = [
        f"{evento}:",
        f"dispositivo={getattr(dispositivo, 'id', None)}",
        f"empresa={getattr(dispositivo, 'empresa_id', None)}",
        f"paquete={paquete_seguro(package)}",
        f"estado={estado or '-'}",
        f"longitud_texto={len(texto or '')}",
    ]
    partes += [f"{k}={v}" for k, v in extra.items()]
    return " ".join(partes)


def donde_fallo(exc: BaseException) -> str:
    """Tipo de error y archivo:línea donde ocurrió, SIN el mensaje (que puede
    contener el texto que se estaba procesando)."""
    marcos = traceback.extract_tb(exc.__traceback__) if exc.__traceback__ else []
    if marcos:
        m = marcos[-1]
        return f"{type(exc).__name__}@{os.path.basename(m.filename)}:{m.lineno}"
    return type(exc).__name__
