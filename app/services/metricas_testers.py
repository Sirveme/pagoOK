"""Métricas de testers por dispositivo y agrupadas (zWeb-12), SOLO LECTURA.

Una fila por dispositivo activo de empresas reales (sin la cuenta de revisión
de Google Play ni las empresas demo) y agrupaciones por marca, modelo, versión
de Android y rubro. Cada métrica dice "sin datos" (None) cuando no se puede
calcular con lo que hoy existe; nunca se inventa un valor.

Fuentes:
  - Pagos capturados: pago_detectado (ingresos, sin pruebas ni dudosos) unido a
    notificacion_raw para saber de qué equipo vino. Los pagos que entraron por
    correo del banco no tienen equipo y no cuentan aquí.
  - Latencia: notificacion_raw.timestamp_servidor - timestamp_celular, últimos
    30 días (se descartan valores imposibles por reloj del celular desfasado).
  - Tiempo en verde: device_estado_historico, 1 fila por hora con el estado que
    el equipo REPORTÓ al cerrar la hora (aproximación: los minutos exactos no se
    guardan). Una hora sin fila = sin heartbeat = no estuvo en verde.
  - Zombies y alarmas en 7 días: dispositivo_incidente (existe desde zWeb-11;
    los contadores anteriores no tienen fecha y no se usan).
"""
from __future__ import annotations

import logging
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import case, func, text
from sqlalchemy.orm import Session

from app.admin.models import Empresa
from app.api.models_device import DeviceEstadoActual, DeviceEstadoHistorico
from app.api.models_webhook import Dispositivo, NotificacionRaw, PagoDetectado

logger = logging.getLogger("pagook")

DIAS_LATENCIA = 30
LATENCIA_MIN_S, LATENCIA_MAX_S = -120, 86400       # fuera de esto: reloj del celular desfasado
VERSION_SIN_LECTOR = (1, 6, 0)                    # menores no informan el lector de notificaciones

# Umbrales de los semáforos (texto visible en la página).
UMBRALES = {
    "verde_pct": {"verde": 95, "amarillo": 80},           # % de horas en verde
    "latencia_p90_s": {"verde": 10, "amarillo": 60},      # segundos
    "incidentes_7d": {"verde": 0, "amarillo": 2},         # zombies + alarmas
}


# ---------------------------------------------------------------- utilidades

def _utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def percentil(valores: list[float], p: float) -> Optional[float]:
    """Interpolación lineal, igual que percentile_cont de Postgres."""
    if not valores:
        return None
    v = sorted(valores)
    k = (len(v) - 1) * p
    i = int(k)
    return v[i] if i + 1 >= len(v) else v[i] + (v[i + 1] - v[i]) * (k - i)


def version_tupla(texto: Optional[str]) -> Optional[tuple[int, int, int]]:
    """'1.7.1' -> (1, 7, 1); '1.4-beta-fix3' -> (1, 4, 0); None si no hay número."""
    nums = [int(n) for n in re.findall(r"\d+", str(texto or "").split("-")[0])[:3]]
    return tuple(nums + [0] * (3 - len(nums))) if nums else None  # type: ignore[return-value]


def semaforo(valor: Optional[float], metrica: str, mayor_es_mejor: bool) -> str:
    if valor is None:
        return "gris"
    u = UMBRALES[metrica]
    if mayor_es_mejor:
        return "verde" if valor >= u["verde"] else ("amarillo" if valor >= u["amarillo"] else "rojo")
    return "verde" if valor <= u["verde"] else ("amarillo" if valor <= u["amarillo"] else "rojo")


def slugs_excluidos() -> set[str]:
    from app.api.router_demo import slugs_demo   # import local: evita ciclo
    revision = os.getenv("EMPRESA_REVISION_PLAY", "demo-revision").strip().lower()
    return slugs_demo() | {"revision-play", "demo-revision", revision}


def empresa_excluida(emp: Empresa) -> bool:
    return bool(emp.es_cuenta_revision) or (emp.slug or "").lower() in slugs_excluidos()


def _rubros(db: Session) -> dict[int, Optional[str]]:
    """empresa.rubro si la columna existe (sql/empresa_rubro_2026-11.sql); si no, {}."""
    try:
        filas = db.execute(text("SELECT id, rubro FROM empresa")).all()
        return {i: (r or None) for i, r in filas}
    except Exception:
        db.rollback()
        return {}


def _incidentes_7d(db: Session, ids: list[int], desde: datetime) -> Optional[dict[int, tuple[int, int]]]:
    """{dispositivo_id: (zombies, alarmas)} en 7 días; None si la tabla aún no existe."""
    from app.api.models_incidente import DispositivoIncidente as I
    try:
        filas = (
            db.query(I.dispositivo_id,
                     func.coalesce(func.sum(case((I.tipo == "zombie", I.cantidad), else_=0)), 0),
                     func.coalesce(func.sum(case((I.tipo == "alarma", 1), else_=0)), 0))
            .filter(I.dispositivo_id.in_(ids), I.ocurrido_en >= desde)
            .group_by(I.dispositivo_id).all()
        ) if ids else []
        return {d: (int(z), int(a)) for d, z, a in filas}
    except Exception:
        db.rollback()
        logger.warning("Métricas de testers: dispositivo_incidente no disponible (¿falta correr el SQL?)")
        return None


# ---------------------------------------------------------------- cálculo

def calcular(db: Session, ahora: Optional[datetime] = None) -> dict:
    ahora = _utc(ahora) or datetime.now(timezone.utc)
    ahora_naive = ahora.replace(tzinfo=None)                     # columnas naive en UTC
    desde_7d, desde_24h = ahora - timedelta(days=7), ahora - timedelta(hours=24)

    filas = (
        db.query(Dispositivo, Empresa, DeviceEstadoActual)
        .join(Empresa, Empresa.id == Dispositivo.empresa_id)
        .outerjoin(DeviceEstadoActual, DeviceEstadoActual.dispositivo_id == Dispositivo.id)
        .filter(Dispositivo.activo == True)  # noqa: E712
        .all()
    )
    excluidas = sorted({emp.slug for _, emp, _ in filas if empresa_excluida(emp)})
    filas = [(d, e, a) for d, e, a in filas if not empresa_excluida(e)]
    ids = [d.id for d, _, _ in filas]

    # 1) Pagos capturados por equipo (ingresos reales: sin pruebas ni dudosos).
    pagos: dict[int, tuple[int, int]] = {}
    if ids:
        for did, total, ult7 in (
            db.query(NotificacionRaw.dispositivo_id, func.count(PagoDetectado.id),
                     func.coalesce(func.sum(case((PagoDetectado.recibido_en >= desde_7d.replace(tzinfo=None), 1),
                                                 else_=0)), 0))
            .join(NotificacionRaw, NotificacionRaw.id == PagoDetectado.notificacion_raw_id)
            .filter(NotificacionRaw.dispositivo_id.in_(ids), PagoDetectado.tipo == "ingreso",
                    PagoDetectado.es_prueba == False, PagoDetectado.tipo_incierto == False)  # noqa: E712
            .group_by(NotificacionRaw.dispositivo_id).all()
        ):
            pagos[did] = (int(total), int(ult7))

    # 2) Latencia (segundos) por equipo, últimos 30 días.
    latencias: dict[int, list[float]] = defaultdict(list)
    if ids:
        desde_lat = ahora_naive - timedelta(days=DIAS_LATENCIA)
        for did, ts_srv, ts_cel in (
            db.query(NotificacionRaw.dispositivo_id, NotificacionRaw.timestamp_servidor,
                     NotificacionRaw.timestamp_celular)
            .filter(NotificacionRaw.dispositivo_id.in_(ids), NotificacionRaw.timestamp_servidor >= desde_lat,
                    NotificacionRaw.timestamp_celular > 1_000_000_000_000).all()
        ):
            lat = _utc(ts_srv).timestamp() - ts_cel / 1000.0
            if LATENCIA_MIN_S <= lat <= LATENCIA_MAX_S:
                latencias[did].append(lat)

    # 3) Horas en verde (histórico por hora). Se filtra en Python para no
    #    depender de cómo guarda la zona horaria cada motor.
    horas_verde: dict[int, dict[str, int]] = defaultdict(lambda: {"24h": 0, "7d": 0})
    if ids:
        for did, hora, est in (
            db.query(DeviceEstadoHistorico.dispositivo_id, DeviceEstadoHistorico.hora_inicio,
                     DeviceEstadoHistorico.estado_predominante)
            .filter(DeviceEstadoHistorico.dispositivo_id.in_(ids),
                    DeviceEstadoHistorico.hora_inicio >= desde_7d - timedelta(days=1)).all()
        ):
            h = _utc(hora)
            if est != "verde" or h is None or h >= ahora:
                continue
            if h >= desde_7d:
                horas_verde[did]["7d"] += 1
            if h >= desde_24h:
                horas_verde[did]["24h"] += 1

    rubros = _rubros(db)
    incid = _incidentes_7d(db, ids, desde_7d)

    versiones = [v for v in (version_tupla((a.app_version if a else None) or d.app_version) for d, _, a in filas) if v]
    referencia = max(versiones) if versiones else None

    from app.api.router_device import estado_conexion
    from app.services.incidentes import fmt_lima

    dispositivos = []
    for d, e, a in filas:
        creado = _utc(d.creado_en) or (ahora - timedelta(days=365))

        def pct(ventana: timedelta, clave: str) -> Optional[float]:
            horas = min(ventana.total_seconds(), (ahora - creado).total_seconds()) / 3600
            if horas < 1:
                return None                               # equipo con menos de 1 h: sin datos
            return round(min(100.0, 100.0 * horas_verde[d.id][clave] / int(horas)), 1) if int(horas) else None

        app_version = (a.app_version if a else None) or d.app_version
        vt = version_tupla(app_version)
        if vt is None:
            estado_version = "gris"
        elif vt < VERSION_SIN_LECTOR:
            estado_version = "rojo"
        elif referencia and vt < referencia:
            estado_version = "amarillo"
        else:
            estado_version = "verde"

        uh = _utc(a.ultimo_heartbeat) if a else None
        minutos = (ahora - uh).total_seconds() / 60 if uh else None
        lat = latencias.get(d.id, [])
        z, al = (incid.get(d.id, (0, 0)) if incid is not None else (None, None))
        p_total, p_7d = pagos.get(d.id, (0, 0))
        v24, v7 = pct(timedelta(hours=24), "24h"), pct(timedelta(days=7), "7d")
        p50, p90 = percentil(lat, 0.5), percentil(lat, 0.9)
        incidentes = None if z is None else z + al
        dispositivos.append({
            "dispositivo_id": d.id,
            "empresa": e.nombre_comercial or e.razon_social,
            "empresa_slug": e.slug,
            "rubro": rubros.get(e.id),
            "marca": ((a.manufacturer if a else None) or d.manufacturer or "").strip().capitalize() or None,
            "modelo": (a.modelo if a else None) or d.modelo or None,
            "android": (a.android_version if a else None) or d.android_version or None,
            "app_version": app_version,
            "semaforo_version": estado_version,
            "pagos_total": p_total,
            "pagos_7d": p_7d,
            "latencia_p50_s": round(p50, 1) if p50 is not None else None,
            "latencia_p90_s": round(p90, 1) if p90 is not None else None,
            "latencia_muestras": len(lat),
            "semaforo_latencia": semaforo(p90, "latencia_p90_s", mayor_es_mejor=False),
            "verde_24h_pct": v24,
            "verde_7d_pct": v7,
            "semaforo_verde_24h": semaforo(v24, "verde_pct", mayor_es_mejor=True),
            "semaforo_verde_7d": semaforo(v7, "verde_pct", mayor_es_mejor=True),
            "zombies_7d": z,
            "alarmas_7d": al,
            "semaforo_incidentes": semaforo(incidentes, "incidentes_7d", mayor_es_mejor=False),
            "ultimo_pago_lima": fmt_lima(d.ultimo_pago_capturado),
            "ultimo_heartbeat_lima": fmt_lima(uh),
            "semaforo_heartbeat": estado_conexion(minutos) if uh else "rojo",
            "_latencias": lat,
        })
    dispositivos.sort(key=lambda x: ((x["empresa"] or "").casefold(), x["dispositivo_id"]))

    grupos = {clave: _agrupar(dispositivos, clave) for clave in ("marca", "modelo", "android", "rubro")}
    for x in dispositivos:
        del x["_latencias"]
    return {
        "generado_lima": fmt_lima(ahora),
        "dispositivos": dispositivos,
        "grupos": grupos,
        "excluidas": excluidas,
        "version_referencia": ".".join(map(str, referencia)) if referencia else None,
        "incidentes_disponibles": incid is not None,
        "rubro_disponible": bool(rubros),
        "umbrales": UMBRALES,
        "dias_latencia": DIAS_LATENCIA,
    }


def _agrupar(dispositivos: list[dict], clave: str) -> list[dict]:
    por: dict[str, list[dict]] = defaultdict(list)
    vacio = {"rubro": "Sin rubro"}.get(clave, "Sin dato")
    for d in dispositivos:
        por[d[clave] or vacio].append(d)
    salida = []
    for nombre, lista in por.items():
        lat = [v for d in lista for v in d["_latencias"]]
        p50, p90 = percentil(lat, 0.5), percentil(lat, 0.9)

        def prom(campo: str) -> Optional[float]:
            vals = [d[campo] for d in lista if d[campo] is not None]
            return round(sum(vals) / len(vals), 1) if vals else None

        def suma(campo: str) -> Optional[int]:
            vals = [d[campo] for d in lista if d[campo] is not None]
            return sum(vals) if vals else None

        v24, v7 = prom("verde_24h_pct"), prom("verde_7d_pct")
        z, a = suma("zombies_7d"), suma("alarmas_7d")
        inc = None if z is None and a is None else (z or 0) + (a or 0)
        salida.append({
            "grupo": nombre,
            "dispositivos": len(lista),
            "pagos_total": sum(d["pagos_total"] for d in lista),
            "pagos_7d": sum(d["pagos_7d"] for d in lista),
            "latencia_p50_s": round(p50, 1) if p50 is not None else None,
            "latencia_p90_s": round(p90, 1) if p90 is not None else None,
            "semaforo_latencia": semaforo(p90, "latencia_p90_s", mayor_es_mejor=False),
            "verde_24h_pct": v24, "semaforo_verde_24h": semaforo(v24, "verde_pct", mayor_es_mejor=True),
            "verde_7d_pct": v7, "semaforo_verde_7d": semaforo(v7, "verde_pct", mayor_es_mejor=True),
            "zombies_7d": z, "alarmas_7d": a,
            "semaforo_incidentes": semaforo(inc, "incidentes_7d", mayor_es_mejor=False),
            "por_actualizar": sum(1 for d in lista if d["semaforo_version"] in ("amarillo", "rojo")),
        })
    salida.sort(key=lambda g: (-g["dispositivos"], g["grupo"].casefold()))
    return salida


# ---------------------------------------------------------------- CSV

COLUMNAS_CSV = [
    ("empresa", "Empresa"), ("rubro", "Rubro"), ("dispositivo_id", "Dispositivo"), ("marca", "Marca"),
    ("modelo", "Modelo"), ("android", "Android"), ("app_version", "Versión app"),
    ("semaforo_version", "Estado versión"), ("pagos_total", "Pagos total"), ("pagos_7d", "Pagos 7 días"),
    ("latencia_p50_s", "Latencia mediana (s)"), ("latencia_p90_s", "Latencia p90 (s)"),
    ("verde_24h_pct", "% verde 24 h"), ("verde_7d_pct", "% verde 7 días"),
    ("zombies_7d", "Zombies 7 días"), ("alarmas_7d", "Alarmas 7 días"),
    ("ultimo_pago_lima", "Último pago (Lima)"), ("ultimo_heartbeat_lima", "Último heartbeat (Lima)"),
]


def _celda_csv(valor) -> str:
    if valor is None:
        return "sin datos"
    s = str(valor)
    # Evita que Excel interprete una celda como fórmula (inyección CSV).
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def a_csv(datos: dict) -> str:
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([titulo for _, titulo in COLUMNAS_CSV])
    for d in datos["dispositivos"]:
        w.writerow([_celda_csv(d.get(c)) for c, _ in COLUMNAS_CSV])
    return "﻿" + buf.getvalue()       # BOM: Excel abre bien las tildes
