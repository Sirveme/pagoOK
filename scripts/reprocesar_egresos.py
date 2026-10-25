"""Reprocesa notificaciones históricas y registra SOLO los egresos.

Uso:
  python scripts/reprocesar_egresos.py --empresa perusistemas --desde 2026-05-01            (simulación)
  python scripts/reprocesar_egresos.py --empresa perusistemas --ids 61,75,76,84,91,94 --aplicar

Garantías:
  - Solo crea egresos (tipo='egreso'). Si el parser actual dice que es ingreso
    o no reconoce el aviso, no toca nada: los ingresos tienen su propio flujo.
  - NO emite webhooks, eventos ni push (escribe directo con el ORM). Un egreso
    nunca dispara pago.confirmado de todos modos.
  - Idempotente: si el raw ya tiene un registro en pago_detectado, lo salta.
  - Conserva la fecha original del aviso (recibido_en = timestamp del raw).
  - Solo raws de la empresa indicada (nunca mezcla empresas).
  - La simulación abre la base en SOLO LECTURA.

Rendimiento: por la URL pública de Railway cada consulta tarda 100-450 ms.
Por eso se hacen solo 2 consultas de lectura (no una por notificación), se
muestra el avance a medida que se procesa, y la conexión falla con un mensaje
claro si no responde en 15 s (scripts/_conexion.py).
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.admin.models  # noqa: E402,F401
from app.admin.models import Empresa  # noqa: E402
from app.api.models_webhook import NotificacionRaw, PagoDetectado  # noqa: E402
from app.api.parser import parsear  # noqa: E402
from scripts._conexion import abrir_sesion, explicar_error  # noqa: E402


def reprocesar_egresos(db, empresa: Empresa, *, ids: list[int] | None = None, desde: date | None = None,
                       aplicar: bool = False, mostrar=None) -> list[str]:
    """Devuelve el reporte; `mostrar(linea)` (opcional) imprime el avance en vivo."""
    reporte: list[str] = []

    def _anotar(linea: str) -> None:
        reporte.append(linea)
        if mostrar:
            mostrar(linea)

    q = db.query(NotificacionRaw).filter(NotificacionRaw.empresa_id == empresa.id)
    if ids:
        q = q.filter(NotificacionRaw.id.in_(ids))
    if desde:
        q = q.filter(NotificacionRaw.timestamp_servidor >= datetime.combine(desde, datetime.min.time()))
    raws = q.order_by(NotificacionRaw.id).all()                                   # consulta 1
    ya = {
        rid: (pid, tipo)
        for rid, pid, tipo in db.query(PagoDetectado.notificacion_raw_id, PagoDetectado.id, PagoDetectado.tipo)
        .filter(PagoDetectado.empresa_id == empresa.id,
                PagoDetectado.notificacion_raw_id.in_([r.id for r in raws]))       # consulta 2
    } if raws else {}
    if mostrar:
        mostrar(f"{len(raws)} notificación(es) revisadas; {len(ya)} ya tienen registro.")

    creados = 0
    for raw in raws:
        if raw.id in ya:
            pid, tipo = ya[raw.id]
            if tipo == "egreso":
                _anotar(f"raw#{raw.id}: ya registrado como egreso (pago_id={pid}) -> se omite")
            continue  # ingresos ya registrados: no se mencionan ni se tocan
        parsed = parsear(raw.package_app or "", raw.titulo or "", raw.texto or "")
        if not parsed or parsed.get("tipo") != "egreso":
            continue
        linea = (f"raw#{raw.id} {raw.timestamp_servidor:%Y-%m-%d %H:%M} | {parsed['tipo_egreso']} | "
                 f"{parsed['moneda']} {parsed['monto']} | {parsed.get('banco') or parsed['metodo']} | "
                 f"{(parsed.get('titular') or '')[:30]!r}")
        if aplicar:
            try:
                from app.services.deteccion_interno import es_posible_interno
                posible_interno = es_posible_interno(empresa.id, parsed.get("titular") or "", db)
            except Exception:
                posible_interno = False
            pago = PagoDetectado(
                empresa_id=empresa.id, notificacion_raw_id=raw.id, metodo=parsed["metodo"],
                monto=parsed["monto"], moneda=parsed.get("moneda", "PEN"),
                titular=parsed.get("titular") or "", titular_corto=parsed.get("titular_corto") or "",
                codigo_operacion=parsed.get("codigo_operacion"), banco=parsed.get("banco"),
                tipo="egreso", tipo_egreso=parsed["tipo_egreso"],
                tarjeta_ultimos4=parsed.get("tarjeta_ultimos4"),
                posible_interno=posible_interno, tipo_incierto=False,
                recibido_en=raw.timestamp_servidor,
            )
            db.add(pago)
            raw.estado_parseo = "parseada"
            raw.metodo_detectado = parsed["metodo"]
            db.flush()
            linea += f" -> CREADO pago_id={pago.id}"
        creados += 1
        _anotar(linea)
    _anotar(f"\n{creados} egreso(s) {'creados' if aplicar else 'por crear'}.")
    return reporte


def main() -> None:
    p = argparse.ArgumentParser(description="Reprocesa egresos históricos (sin webhooks)")
    p.add_argument("--empresa", required=True, help="slug de la empresa")
    p.add_argument("--desde", help="YYYY-MM-DD")
    p.add_argument("--ids", help="ids de notificacion_raw separados por coma")
    p.add_argument("--aplicar", action="store_true", help="guardar (sin esto, solo simula)")
    args = p.parse_args()
    if not args.desde and not args.ids:
        p.error("indica --desde o --ids")

    db = abrir_sesion(solo_lectura=not args.aplicar)   # "Conectando…" + 15 s de límite
    try:
        print("Leyendo notificaciones…", flush=True)
        empresa = db.query(Empresa).filter(Empresa.slug == args.empresa.strip().lower()).first()
        if empresa is None:
            print(f"ERROR: no existe la empresa {args.empresa!r}", file=sys.stderr)
            sys.exit(1)
        ids = [int(x) for x in args.ids.split(",") if x.strip()] if args.ids else None
        desde = date.fromisoformat(args.desde) if args.desde else None
        reprocesar_egresos(db, empresa, ids=ids, desde=desde, aplicar=args.aplicar,
                           mostrar=lambda linea: print(linea, flush=True))
        if args.aplicar:
            db.commit()
            print("Guardado. No se emitieron webhooks, eventos ni push.")
        else:
            db.rollback()
            print("Simulación (solo lectura): no se guardó nada. Usa --aplicar para guardar.")
    except SystemExit:
        raise
    except Exception as exc:
        db.rollback()
        print(f"ERROR: {explicar_error(exc)}\nROLLBACK: no se guardó nada.", file=sys.stderr)
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
