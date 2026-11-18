"""Retención: borra el TEXTO crudo de las notificaciones y de los correos del
banco con más de N días.

La política de privacidad (sección 8) dice que el texto completo del aviso se
borra a los 90 días. Esto deja en NULL `titulo`, `texto` y `error_parseo` de
notificacion_raw en TODOS los estados (parseada, ignorada, fallida,
no_reconocido…). `error_parseo` también se borra porque guarda el mensaje del
error del parser, que puede arrastrar parte del texto.
NO borra filas ni toca los pagos/egresos ya procesados (pago_detectado guarda
monto, pagador, fecha, etc. por su cuenta). Tampoco toca sbn_key (evita
duplicados) ni el estado del parseo.

Sale con código 0 si todo salió bien (1 si falló y se hizo ROLLBACK; 2 si no
pudo conectarse). No pregunta nada: sirve tal cual como cron de Railway.

Uso:
  python scripts/purgar_crudos.py --dias 90            (simulación: solo cuenta)
  python scripts/purgar_crudos.py --dias 90 --aplicar

Programarlo en Railway como servicio cron (ver railway.purga.json):
  1. Proyecto PagoOK.pro > New > GitHub Repo > el mismo repo, rama main.
  2. Settings > Config-as-code > ruta: /railway.purga.json
     (IMPORTANTE: sin esto toma railway.json y levantaría uvicorn, que nunca termina).
  3. Variables: solo DATABASE_URL = ${{Postgres-DB.DATABASE_URL}} (red interna).
  4. El archivo ya trae el comando, el horario 0 8 * * * (03:00 de Lima; Railway
     usa UTC) y restartPolicyType NEVER. El servicio arranca, purga y termina.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import or_  # noqa: E402

from scripts._conexion import abrir_sesion, explicar_error  # noqa: E402
import app.admin.models  # noqa: E402,F401
from app.api.models_webhook import NotificacionRaw  # noqa: E402


def purgar_crudos(db, dias: int, aplicar: bool, ahora: datetime | None = None) -> int:
    """Cantidad de notificaciones afectadas. Solo escribe si `aplicar` (sin commit)."""
    if dias < 1:
        raise ValueError("--dias debe ser 1 o más")
    limite = (ahora or datetime.now(timezone.utc)).replace(tzinfo=None) - timedelta(days=dias)
    q = db.query(NotificacionRaw).filter(
        NotificacionRaw.timestamp_servidor < limite,
        or_(NotificacionRaw.texto.isnot(None), NotificacionRaw.titulo.isnot(None),
            NotificacionRaw.error_parseo.isnot(None)),
    )
    if not aplicar:
        return q.count()
    return q.update({NotificacionRaw.texto: None, NotificacionRaw.titulo: None,
                     NotificacionRaw.error_parseo: None}, synchronize_session=False)


def purgar_correos(db, dias: int, aplicar: bool, ahora: datetime | None = None) -> int:
    """Igual para los correos del banco (zWeb-06): borra asunto, texto y el
    mensaje original (mime) de correo_raw con más de N días. Los egresos ya
    creados a partir de esos correos se conservan."""
    from app.api.models_correo import CorreoRaw

    if dias < 1:
        raise ValueError("--dias debe ser 1 o más")
    limite = (ahora or datetime.now(timezone.utc)) - timedelta(days=dias)
    q = db.query(CorreoRaw).filter(
        CorreoRaw.recibido_en < limite,
        or_(CorreoRaw.cuerpo_texto.isnot(None), CorreoRaw.asunto.isnot(None), CorreoRaw.mime.isnot(None)),
    )
    if not aplicar:
        return q.count()
    return q.update({CorreoRaw.cuerpo_texto: None, CorreoRaw.asunto: None, CorreoRaw.mime: None},
                    synchronize_session=False)


def main() -> None:
    p = argparse.ArgumentParser(description="Borra el texto crudo de notificaciones antiguas")
    p.add_argument("--dias", type=int, default=90)
    p.add_argument("--aplicar", action="store_true", help="borrar (sin esto, solo cuenta)")
    args = p.parse_args()
    # "Conectando…" + 15 s de límite; la simulación (solo contar) va en solo lectura.
    db = abrir_sesion(solo_lectura=not args.aplicar)
    try:
        print(f"{'Borrando' if args.aplicar else 'Contando'} el texto crudo de más de {args.dias} días…", flush=True)
        n = purgar_crudos(db, args.dias, args.aplicar)
        m = purgar_correos(db, args.dias, args.aplicar)
        if args.aplicar:
            db.commit()
            print(f"Texto crudo borrado en {n} notificación(es) y {m} correo(s) con más de {args.dias} días.")
        else:
            db.rollback()
            print(f"Simulación (solo lectura): {n} notificación(es) y {m} correo(s) con más de {args.dias} días "
                  f"tienen texto crudo. Usa --aplicar para borrarlo.")
    except SystemExit:
        raise
    except Exception as exc:
        db.rollback()
        print(f"ERROR: {explicar_error(exc)}\nROLLBACK: no se borró nada.", file=sys.stderr)
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
