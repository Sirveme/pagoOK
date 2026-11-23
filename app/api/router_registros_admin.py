"""Autorregistros de independientes desde la app (zWeb-14), SOLO LECTURA.

  GET /admin/registros                HTML (sesión de superadmin por cookie)
  GET /api/v1/admin/registros         JSON (cookie o X-Admin-Token)
  GET /admin/registros.csv            CSV  (cookie o X-Admin-Token)

Filtros (query): pais, rubro, desde, hasta (AAAA-MM-DD, hora de Lima).
Sin avisos externos: los registros solo se ven aquí.
"""
from __future__ import annotations

import csv
import io
from datetime import date, datetime, timedelta, timezone
from html import escape
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.admin import superadmin
from app.admin.db import get_db
from app.admin.estilo_oscuro import CSS_BASE
from app.admin.models import Empresa
from app.api.models_device import DeviceEstadoActual
from app.api.models_multiempresa import Membresia, Usuario
from app.api.models_webhook import Dispositivo
from app.services import cuenta_empresa, registro_paises

router = APIRouter(tags=["admin-registros"])

LIMA = timezone(timedelta(hours=-5))
LIMITE_FILAS = 1000
COLUMNAS = [
    ("creada_lima", "Fecha (Lima)"), ("nombre", "Nombre"), ("nombre_comercial", "Nombre comercial"),
    ("pais", "País"), ("rubro_nombre", "Rubro"), ("celular", "Celular"), ("dispositivo", "Dispositivo"),
    ("app_version", "Versión app"), ("estado_cuenta", "Estado"), ("dias_restantes", "Días de prueba"),
    ("prueba_fin_lima", "Prueba hasta"), ("prueba_ampliada", "Ampliada"),
    ("ultimo_pago_lima", "Último pago capturado"),
]


def _fecha(valor: Optional[str]) -> Optional[date]:
    try:
        return date.fromisoformat(valor) if valor else None
    except ValueError:
        return None


def _utc_naive(d: date) -> datetime:
    """Medianoche de Lima de ese día, como datetime UTC sin zona (creada_en es naive UTC)."""
    return datetime(d.year, d.month, d.day, tzinfo=LIMA).astimezone(timezone.utc).replace(tzinfo=None)


def _lima(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    dt = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(LIMA).strftime("%Y-%m-%d %H:%M")


def calcular(db: Session, pais: Optional[str] = None, rubro: Optional[str] = None,
             desde: Optional[str] = None, hasta: Optional[str] = None) -> dict:
    q = db.query(Empresa).filter(Empresa.tipo_empresa == "independiente")
    filtros = {"pais": (pais or "").strip().upper() or None, "rubro": (rubro or "").strip().lower() or None,
               "desde": _fecha(desde), "hasta": _fecha(hasta)}
    if filtros["pais"]:
        q = q.filter(Empresa.pais_codigo == filtros["pais"])
    if filtros["rubro"]:
        q = q.filter(Empresa.rubro == filtros["rubro"])
    if filtros["desde"]:
        q = q.filter(Empresa.creada_en >= _utc_naive(filtros["desde"]))
    if filtros["hasta"]:
        q = q.filter(Empresa.creada_en < _utc_naive(filtros["hasta"] + timedelta(days=1)))
    filas = []
    for e in q.order_by(Empresa.creada_en.desc(), Empresa.id.desc()).limit(LIMITE_FILAS).all():
        dueno = (
            db.query(Usuario)
            .join(Membresia, Membresia.usuario_id == Usuario.id)
            .filter(Membresia.empresa_id == e.id, Membresia.rol == "dueno")
            .order_by(Membresia.id)
            .first()
        )
        disp = db.query(Dispositivo).filter(Dispositivo.empresa_id == e.id).order_by(Dispositivo.id).first()
        estado_disp = db.get(DeviceEstadoActual, disp.id) if disp else None
        ultimo_pago = (db.query(func.max(Dispositivo.ultimo_pago_capturado))
                       .filter(Dispositivo.empresa_id == e.id).scalar())
        # Sin db: no se escribe nada (la vista es de solo lectura).
        estado = cuenta_empresa.refrescar_estado(None, e)
        filas.append({
            "empresa_id": e.id,
            "slug": e.slug,
            "creada_lima": _lima(e.creada_en),
            "nombre": dueno.nombre if dueno else e.razon_social,
            "nombre_comercial": e.nombre_comercial if e.nombre_comercial != (dueno.nombre if dueno else None) else "",
            "pais": e.pais_codigo,
            "rubro": e.rubro,
            "rubro_nombre": registro_paises.NOMBRE_RUBRO.get(e.rubro or "", e.rubro or ""),
            "celular": dueno.celular if dueno else None,
            "dispositivo": " ".join(x for x in ((disp.manufacturer if disp else None),
                                                (disp.modelo if disp else None)) if x) or None,
            "app_version": (estado_disp.app_version if estado_disp else None) or (disp.app_version if disp else None),
            "estado_cuenta": estado,
            "dias_restantes": cuenta_empresa.dias_restantes(e),
            "prueba_fin_lima": _lima(e.prueba_fin),
            "prueba_ampliada": bool(e.prueba_ampliada),
            "ultimo_pago_lima": _lima(ultimo_pago),
        })
    return {
        "filtros": {k: (v.isoformat() if isinstance(v, date) else v) for k, v in filtros.items()},
        "total": len(filas),
        "registros": filas,
        "paises": [c.codigo for c in registro_paises.PAISES.values()],
        "rubros": registro_paises.rubros_publicos(),
    }


def _a_csv(datos: dict) -> str:
    salida = io.StringIO()
    w = csv.writer(salida)
    w.writerow([titulo for _, titulo in COLUMNAS])
    for f in datos["registros"]:
        w.writerow(["sí" if f[c] is True else "no" if f[c] is False else ("" if f[c] is None else f[c])
                    for c, _ in COLUMNAS])
    return "﻿" + salida.getvalue()  # BOM: Excel abre bien las tildes


def _autorizar(request: Request, x_admin_token: Optional[str]) -> None:
    if not superadmin.autorizado(request, x_admin_token):
        raise HTTPException(401, "No autorizado")


@router.get("/api/v1/admin/registros")
def registros_json(request: Request, pais: Optional[str] = None, rubro: Optional[str] = None,
                   desde: Optional[str] = None, hasta: Optional[str] = None,
                   x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
                   db: Session = Depends(get_db)):
    _autorizar(request, x_admin_token)
    return calcular(db, pais, rubro, desde, hasta)


@router.get("/admin/registros.csv")
def registros_csv(request: Request, pais: Optional[str] = None, rubro: Optional[str] = None,
                  desde: Optional[str] = None, hasta: Optional[str] = None,
                  x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
                  db: Session = Depends(get_db)):
    _autorizar(request, x_admin_token)
    nombre = f"pagook_registros_{datetime.now(timezone.utc):%Y%m%d}.csv"
    return Response(_a_csv(calcular(db, pais, rubro, desde, hasta)), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{nombre}"'})


@router.get("/admin/registros", response_class=HTMLResponse)
def registros_html(request: Request, pais: Optional[str] = None, rubro: Optional[str] = None,
                   desde: Optional[str] = None, hasta: Optional[str] = None, db: Session = Depends(get_db)):
    if not superadmin.sesion_valida(request):
        return HTMLResponse(superadmin.html_login("/admin/registros", "Autorregistros"), status_code=401)
    datos = calcular(db, pais, rubro, desde, hasta)
    f = datos["filtros"]
    qs = urlencode({k: v for k, v in f.items() if v})

    def opciones(valores, actual):
        return "".join(f'<option value="{escape(v)}"{" selected" if v == actual else ""}>{escape(n)}</option>'
                       for v, n in valores)

    def celda(fila, clave):
        v = fila[clave]
        if v is None or v == "":
            return '<td class="mute">—</td>'
        if isinstance(v, bool):
            return f"<td>{'sí' if v else 'no'}</td>"
        return f"<td>{escape(str(v))}</td>"

    cuerpo = "".join("<tr>" + "".join(celda(fila, c) for c, _ in COLUMNAS) + "</tr>" for fila in datos["registros"])
    if not cuerpo:
        cuerpo = f'<tr><td colspan="{len(COLUMNAS)}" class="mute">Sin autorregistros con estos filtros.</td></tr>'
    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Autorregistros · pagoOK</title>
<style>
{CSS_BASE}
  .cont {{ max-width:1300px; margin:0 auto; }}
  .sub {{ font-size:13px; color:var(--txt-suave); }}
  form.filtros {{ display:flex; gap:8px; flex-wrap:wrap; align-items:end; margin:12px 0; }}
  form.filtros label {{ display:flex; flex-direction:column; font-size:12px; color:var(--txt-suave); gap:4px; }}
  .tabla {{ overflow-x:auto; }}
  td.mute {{ color:var(--txt-mute); }}
</style></head>
<body><div class="cont">
  <h1>Autorregistros desde la app</h1>
  <p class="sub">Independientes que crearon su cuenta con su número de WhatsApp. {datos['total']} registro(s).</p>
  <form class="filtros" method="get" action="/admin/registros">
    <label>País<select name="pais"><option value="">Todos</option>
      {opciones([(p, p) for p in datos['paises']], f['pais'])}</select></label>
    <label>Rubro<select name="rubro"><option value="">Todos</option>
      {opciones([(r['clave'], r['nombre']) for r in datos['rubros']], f['rubro'])}</select></label>
    <label>Desde<input type="date" name="desde" value="{escape(f['desde'] or '')}"></label>
    <label>Hasta<input type="date" name="hasta" value="{escape(f['hasta'] or '')}"></label>
    <button type="submit">Filtrar</button>
    <a class="boton" href="/admin/registros">Limpiar</a>
    <a class="boton" href="/admin/registros.csv{('?' + escape(qs)) if qs else ''}">Exportar CSV</a>
  </form>
  <div class="tabla"><table>
    <thead><tr>{''.join(f'<th>{escape(t)}</th>' for _, t in COLUMNAS)}</tr></thead>
    <tbody>{cuerpo}</tbody>
  </table></div>
</div></body></html>""")
