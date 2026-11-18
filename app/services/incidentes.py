"""Zombies y alarmas de un dispositivo con fecha y causa (zWeb-11/12).

Qué es cada cosa (lenguaje simple):
  - ZOMBIE: lo cuenta la APP, no el servidor. Es la app viva (manda heartbeat)
    pero sin poder leer notificaciones (Android le desconectó el lector). La app
    envía su contador acumulado `veces_zombie_total` en cada heartbeat; cuando
    sube respecto del anterior, aquí se registra un incidente 'zombie'.
  - ALARMA: la cuenta el SERVIDOR. Es cada vez que el equipo pasa a reportar
    'rojo' viniendo de otro estado (router_device.heartbeat).

Se cierran solos: un incidente activo queda cerrado cuando llega un heartbeat
sano (alarma: deja de reportar rojo; zombie: verde y con el lector conectado o
sin dato del lector). Al cerrarse se clasifica cómo se recuperó:
  - 'intervencion': entre el inicio y el cierre cambió la versión de la app
    (se actualizó o reinstaló) o se volvió a dar el permiso de notificaciones.
  - 'sola': la app informó una reconexión exitosa del lector (sube
    veces_rebind_exitoso) sin ninguno de esos cambios.
  - 'sin_determinar': se normalizó sin ninguna señal; pudo ser Android o una
    persona reactivando el acceso desde Ajustes (la app no lo distingue).
No se inventa historia: solo hay incidentes desde que existe la tabla.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.api.models_incidente import DispositivoIncidente

logger = logging.getLogger("pagook")

LIMA = timezone(timedelta(hours=-5))   # Perú no tiene horario de verano

# Qué hacer, por causa conocida. Texto visible: español neutro peruano, tuteo.
ACCIONES = {
    "lector_desconectado": "Abre PagoOK y revisa el acceso a notificaciones "
                           "(Ajustes → Notificaciones → Acceso a notificaciones → PagoOK).",
    "sin_permiso": "Abre PagoOK y activa el permiso de notificaciones que te pide.",
}

MENSAJES_CAUSA = {
    "lector_desconectado": "Android desconectó el lector de notificaciones",
    "sin_permiso": "PagoOK no tiene permiso de notificaciones",
}

DETALLE_CAUSA_ALARMA = {
    "lector_desconectado": "lector de notificaciones desconectado",
    "sin_permiso": "sin permiso de notificaciones",
}

RECUPERACION_TEXTO = {
    "sola": "Se recuperó sola",
    "intervencion": "Con intervención",
    "sin_determinar": "Sin determinar",
}


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def _entero(valor) -> int:
    try:
        return int(valor or 0)
    except (TypeError, ValueError):
        return 0


def _bool_o_none(valor) -> Optional[bool]:
    return None if valor is None else bool(valor)


def causa_desde_heartbeat(data: dict) -> Optional[str]:
    if data.get("permiso_notificaciones") is False:
        return "sin_permiso"
    if data.get("listener_conectado") is False:
        return "lector_desconectado"
    return None


def clasificar_recuperacion(inc: DispositivoIncidente, data: dict) -> tuple[str, str]:
    """(recuperacion, detalle) al cerrar `inc` con el heartbeat `data`."""
    version_ahora = str(data.get("app_version") or "")[:30] or None
    if inc.app_version and version_ahora and version_ahora != inc.app_version:
        return "intervencion", f"Se actualizó o reinstaló la app ({inc.app_version} → {version_ahora})"
    if inc.permiso_notificaciones is False and data.get("permiso_notificaciones") is True:
        return "intervencion", "Se volvió a dar el permiso de notificaciones"
    if inc.rebind_exitoso_inicio is not None and _entero(data.get("veces_rebind_exitoso")) > inc.rebind_exitoso_inicio:
        return "sola", "La app se reconectó sola al lector de notificaciones"
    return "sin_determinar", "Se normalizó sin señales: pudo ser Android o alguien desde Ajustes"


def _cerrar(inc: DispositivoIncidente, data: dict, cuando: datetime) -> None:
    inc.activa = False
    inc.cerrada_en = cuando
    inc.recuperacion, inc.recuperacion_detalle = clasificar_recuperacion(inc, data)


def registrar_desde_heartbeat(db: Session, dispositivo, data: dict, *,
                              zombies_antes: Optional[int], estado_antes: Optional[str],
                              rebind_antes: Optional[int] = None) -> None:
    """Crea/cierra incidentes según el heartbeat recién guardado.

    `*_antes` son los valores ANTES de este heartbeat (None si es el primero del
    equipo: los contadores con que llega son anteriores al registro y quedan
    como "sin fecha")."""
    estado = str(data.get("estado")).lower()
    zombies = _entero(data.get("veces_zombie_total"))
    causa = causa_desde_heartbeat(data)
    ahora = _ahora()
    lector_ok = data.get("listener_conectado") is not False and data.get("permiso_notificaciones") is not False
    sano_zombie = estado == "verde" and lector_ok

    activos = (
        db.query(DispositivoIncidente)
        .filter(DispositivoIncidente.dispositivo_id == dispositivo.id,
                DispositivoIncidente.activa == True)  # noqa: E712
        .all()
    )
    # 1) Cierre automático de lo que ya se resolvió.
    for inc in activos:
        if (estado != "rojo") if inc.tipo == "alarma" else sano_zombie:
            _cerrar(inc, data, ahora)

    def _nuevo(tipo: str, mensaje: str, cantidad: int) -> DispositivoIncidente:
        return DispositivoIncidente(
            dispositivo_id=dispositivo.id, empresa_id=dispositivo.empresa_id, tipo=tipo, causa=causa,
            cantidad=cantidad, app_version=str(data.get("app_version") or "")[:30] or None,
            listener_conectado=_bool_o_none(data.get("listener_conectado")),
            permiso_notificaciones=_bool_o_none(data.get("permiso_notificaciones")),
            rebind_exitoso_inicio=rebind_antes, ocurrido_en=ahora, mensaje=mensaje[:300], activa=True,
        )

    # 2) Zombies nuevos: el contador de la app subió. Si bajó, la app se
    #    reinstaló o borró sus datos: no es un zombie.
    if zombies_antes is not None and zombies > zombies_antes:
        delta = zombies - zombies_antes
        base = MENSAJES_CAUSA.get(causa, "La app detectó que no podía leer notificaciones")
        inc = _nuevo("zombie", base + (f" ({delta} veces desde el heartbeat anterior)" if delta > 1 else ""), delta)
        if sano_zombie:          # ya se recuperó entre dos heartbeats: nace cerrado
            _cerrar(inc, data, ahora)
        db.add(inc)

    # 3) Alarma nueva: misma regla que el contador (transición a rojo).
    if estado == "rojo" and estado_antes is not None and estado_antes != "rojo":
        detalles = []
        if causa:
            detalles.append(DETALLE_CAUSA_ALARMA[causa])
        pings = _entero(data.get("pings_fallidos_consecutivos"))
        if pings:
            detalles.append(f"{pings} ping{'s' if pings != 1 else ''} del guardián fallido{'s' if pings != 1 else ''}")
        db.add(_nuevo("alarma", "El equipo reportó estado rojo" + (": " + "; ".join(detalles) if detalles else ""), 1))


def _a_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def fmt_lima(dt: Optional[datetime]) -> Optional[str]:
    """'17/11/2026 12:40' en hora de Lima, o None."""
    dt = _a_utc(dt)
    return dt.astimezone(LIMA).strftime("%d/%m/%Y %H:%M") if dt else None


def fmt_duracion(segundos: float) -> str:
    """'menos de 1 min', '12 min', '3 h 5 min', '2 d 4 h'."""
    minutos = int(segundos // 60)
    if minutos < 1:
        return "menos de 1 min"
    if minutos < 60:
        return f"{minutos} min"
    horas, m = divmod(minutos, 60)
    if horas < 24:
        return f"{horas} h {m} min" if m else f"{horas} h"
    dias, h = divmod(horas, 24)
    return f"{dias} d {h} h" if h else f"{dias} d"


def serializar(inc: DispositivoIncidente, ahora: Optional[datetime] = None) -> dict:
    inicio, fin = _a_utc(inc.ocurrido_en), _a_utc(inc.cerrada_en)
    if inc.activa or fin is None:
        duracion = "Sigue activo" + (f" (hace {fmt_duracion(((ahora or _ahora()) - inicio).total_seconds())})"
                                     if inicio else "")
    else:
        duracion = fmt_duracion((fin - inicio).total_seconds()) if inicio else None
    return {
        "id": inc.id,
        "tipo": inc.tipo,
        "causa": inc.causa,
        "mensaje": inc.mensaje,
        "accion": ACCIONES.get(inc.causa or ""),
        "cantidad": inc.cantidad or 1,
        "app_version": inc.app_version,
        "listener_conectado": inc.listener_conectado,
        "permiso_notificaciones": inc.permiso_notificaciones,
        "ocurrido_lima": fmt_lima(inc.ocurrido_en),
        "activa": bool(inc.activa),
        "duracion": duracion,
        "cerrada_lima": fmt_lima(inc.cerrada_en),
        "recuperacion": inc.recuperacion,
        "recuperacion_texto": RECUPERACION_TEXTO.get(inc.recuperacion or ""),
        "recuperacion_detalle": inc.recuperacion_detalle,
        "revisada": inc.revisada_en is not None,
        "revisada_lima": fmt_lima(inc.revisada_en),
        "revisada_por": inc.revisada_por,
    }


def listar(db: Session, dispositivo_id: int, limite: int = 200) -> list[DispositivoIncidente]:
    return (
        db.query(DispositivoIncidente)
        .filter(DispositivoIncidente.dispositivo_id == dispositivo_id)
        .order_by(DispositivoIncidente.ocurrido_en.desc(), DispositivoIncidente.id.desc())
        .limit(limite)
        .all()
    )


def marcar_revisada(db: Session, incidente: DispositivoIncidente, *, por: str, ip: Optional[str]) -> bool:
    """Deja la auditoría de quién y cuándo. Idempotente: si ya estaba revisada
    conserva la primera revisión (no la pisa) y devuelve False."""
    if incidente.revisada_en is not None:
        return False
    incidente.revisada_en = _ahora()
    incidente.revisada_por = por[:150]
    incidente.revisada_ip = (ip or "")[:64] or None
    return True
