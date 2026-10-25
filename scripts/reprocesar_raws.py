"""Reprocesa notificaciones (notificacion_raw) con el parser ACTUAL.

Uso:
  python scripts/reprocesar_raws.py --ids 95,102,120,146,156 --cuenta-reclamo 3            (simulación)
  python scripts/reprocesar_raws.py --ids 95,102,120,146,156 --cuenta-reclamo 3 --aplicar  (escribe)

Garantías:
  - Cada pago recuperado queda RECLAMADO y USADO, para que ningún sistema lo
    tome como disponible:
      * reclamado_por = --cuenta-reclamo (lo saca de GET /api/v1/pagos de las
        demás credenciales; el FK exige una cuenta_api real de la empresa).
      * fila en pago_uso con la nota NOTA_HISTORICO (lo saca de la consulta y
        hace que marcar-usado responda 409 a cualquiera).
  - NO emite webhooks, eventos ni Web Push. Escribe directo con el ORM; nunca
    llama a emitir_evento / emitir_pago_confirmado ni a push_service (que solo
    se disparan desde POST /api/v1/notificaciones/inbound). Son pagos de hace
    meses: un aviso tardío podría disparar acciones en Facturalo u otros.
  - Conserva la fecha original: recibido_en = timestamp_servidor del raw. Así el
    pago no aparece como reciente ni calza con consultas de hoy.
  - Idempotente: si el raw ya tiene un pago en pago_detectado, lo salta.
  - Todo en UNA transacción: o se aplican todos, o ninguno.
"""
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts._conexion import abrir_sesion, explicar_error  # noqa: E402
import app.admin.models  # noqa: E402,F401  (registra empresa/cuenta_api para los FK)
from app.api.models_cuenta import CuentaApi  # noqa: E402
from app.api.models_pago_uso import PagoUso  # noqa: E402
from app.api.models_webhook import NotificacionRaw, PagoDetectado  # noqa: E402
from app.api.parser import parsear  # noqa: E402

NOTA_HISTORICO = "histórico recuperado 2026-10-18"
SISTEMA_REPROCESO = "PagoOK - reproceso"


def reprocesar(db, ids: list[int], aplicar: bool, cuenta_reclamo_id: int | None = None) -> list[str]:
    """Devuelve una línea de reporte por raw. Solo escribe si `aplicar`.

    Cada pago creado queda reclamado por `cuenta_reclamo_id` y registrado en
    pago_uso con NOTA_HISTORICO. Con `aplicar`, la cuenta es obligatoria.
    """
    cuenta = db.get(CuentaApi, cuenta_reclamo_id) if cuenta_reclamo_id is not None else None
    if aplicar and cuenta is None:
        raise ValueError("Con --aplicar se requiere --cuenta-reclamo con una cuenta_api existente")
    reporte = []
    for raw_id in ids:
        raw = db.get(NotificacionRaw, raw_id)
        if raw is None:
            reporte.append(f"raw#{raw_id}: NO existe -> se omite")
            continue

        existente = db.query(PagoDetectado).filter(PagoDetectado.notificacion_raw_id == raw_id).first()
        if existente is not None:
            reporte.append(
                f"raw#{raw_id}: ya tiene pago_id={existente.id} (monto={existente.monto}, "
                f"tipo={existente.tipo}, tipo_incierto={existente.tipo_incierto}) -> se omite"
            )
            continue

        parsed = parsear(raw.package_app or "", raw.titulo or "", raw.texto or "")
        if not parsed:
            reporte.append(f"raw#{raw_id}: el parser actual NO lo reconoce como pago -> se omite")
            continue

        if cuenta is not None and cuenta.empresa_id != raw.empresa_id:
            raise ValueError(
                f"raw#{raw_id} es de la empresa {raw.empresa_id}, pero la cuenta_api "
                f"{cuenta.id} es de la empresa {cuenta.empresa_id}. No se escribe nada."
            )

        titular = parsed.get("titular") or ""
        try:
            from app.services.deteccion_interno import es_posible_interno
            posible_interno = es_posible_interno(raw.empresa_id, titular, db)
        except Exception:
            posible_interno = False

        linea = (
            f"raw#{raw_id}: {raw.estado_parseo} -> parseada | {parsed['metodo']} "
            f"S/ {parsed['monto']} {parsed['tipo']} titular={titular!r} "
            f"recibido_en={raw.timestamp_servidor} (fecha original)"
        )
        if aplicar:
            pago = PagoDetectado(
                empresa_id=raw.empresa_id,
                notificacion_raw_id=raw.id,
                metodo=parsed["metodo"],
                monto=parsed["monto"],
                moneda=parsed.get("moneda", "PEN"),
                titular=titular,
                titular_corto=parsed.get("titular_corto") or "",
                codigo_operacion=parsed.get("codigo_operacion"),
                banco=parsed.get("banco"),
                tipo=parsed.get("tipo", "ingreso"),
                posible_interno=posible_interno,
                confirmacion_usuario=None,
                tipo_incierto=bool(parsed.get("tipo_incierto", False)),
                recibido_en=raw.timestamp_servidor,
                reclamado_por=cuenta.id,
                reclamado_en=datetime.now(timezone.utc).replace(tzinfo=None),
            )
            db.add(pago)
            raw.estado_parseo = "parseada"
            raw.metodo_detectado = parsed["metodo"]
            raw.error_parseo = None
            db.flush()
            db.add(PagoUso(
                pago_id=pago.id,
                empresa_id=raw.empresa_id,
                cuenta_api_id=cuenta.id,
                sistema_que_uso=SISTEMA_REPROCESO,
                referencia_externa=NOTA_HISTORICO,
            ))
            db.flush()
            linea += f" -> CREADO pago_id={pago.id}, reclamado y usado ({NOTA_HISTORICO!r})"
        else:
            destino = f"cuenta_api {cuenta.id}" if cuenta else "(falta --cuenta-reclamo)"
            linea += f" -> (simulación: quedaría reclamado por {destino} con nota {NOTA_HISTORICO!r})"
        reporte.append(linea)
    return reporte


def main() -> None:
    p = argparse.ArgumentParser(description="Reprocesa notificacion_raw sin emitir webhooks")
    p.add_argument("--ids", required=True, help="ids de notificacion_raw separados por coma")
    p.add_argument("--cuenta-reclamo", type=int,
                   help="id de cuenta_api (misma empresa) que figurará como dueña del reclamo")
    p.add_argument("--aplicar", action="store_true", help="escribir en la BD (sin esto, solo simula)")
    args = p.parse_args()
    ids = [int(x) for x in args.ids.split(",") if x.strip()]

    # "Conectando…" + 15 s de límite; la simulación va en solo lectura.
    db = abrir_sesion(solo_lectura=not args.aplicar)
    try:
        print(f"Procesando {len(ids)} notificación(es)…", flush=True)
        for linea in reprocesar(db, ids, args.aplicar, args.cuenta_reclamo):
            print(linea, flush=True)
        if args.aplicar:
            print("Guardando…", flush=True)
            db.commit()
            print("\nCOMMIT hecho. Pagos reclamados y usados como histórico. "
                  "No se emitieron webhooks, eventos ni push.")
        else:
            db.rollback()
            print("\nSimulación (solo lectura): no se escribió nada. Usa --aplicar para escribir.")
    except SystemExit:
        raise
    except Exception as exc:
        db.rollback()
        print(f"\nERROR: {explicar_error(exc)}\nROLLBACK: no se escribió nada.", file=sys.stderr)
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
