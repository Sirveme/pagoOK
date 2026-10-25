"""Gestión de suscripciones de webhooks salientes (contrato de eventos v1).

El secreto HMAC se muestra UNA sola vez: entrégalo al sistema receptor por un
canal seguro. PagoOK lo guarda en claro porque lo necesita para firmar.

Por defecto NO toca la BD: imprime el secreto y el SQL para PGAdmin
(regla SQL-primero). --aplicar lo ejecuta contra DATABASE_URL.

Ejemplos:
  python scripts/gestion_webhooks.py crear --empresa-id 1 --nombre "QueVendi" \
      --url https://quevendi.pro/webhooks/pagook
  python scripts/gestion_webhooks.py crear ... --tipos pago.confirmado --aplicar
  python scripts/gestion_webhooks.py probar --url http://localhost:4000/api/v1/webhooks/receptor-ejemplo \
      --secreto whsec_xxx
"""
import argparse
import sys
from pathlib import Path
from urllib.parse import urlparse

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.eventos import (  # noqa: E402
    TIPO_PAGO_CONFIRMADO,
    armar_sobre,
    cabeceras_para,
    enviar_http,
    generar_secreto,
    serializar_cuerpo,
)


def _validar_url(url: str) -> None:
    p = urlparse(url)
    local = p.hostname in ("localhost", "127.0.0.1")
    if p.scheme != "https" and not (p.scheme == "http" and local):
        print("✗ url_destino debe ser HTTPS (http solo para localhost)")
        sys.exit(1)


def _sql(texto: str) -> str:
    return texto.replace("'", "''")


def cmd_crear(args) -> None:
    _validar_url(args.url)
    tipos = [t.strip() for t in args.tipos.split(",") if t.strip()]
    secreto = generar_secreto()

    print()
    print("=" * 62)
    print("  SECRETO HMAC (se muestra UNA sola vez — entrégalo al receptor):")
    print(f"    {secreto}")
    print("=" * 62)
    print()

    if not args.aplicar:
        arr = ", ".join(f"'{_sql(t)}'" for t in tipos)
        print("-- SQL para PGAdmin (modo SQL-primero, NO se ejecutó):")
        print(
            "INSERT INTO suscripcion_webhook (empresa_id, nombre, url_destino, secreto_hmac, tipos_suscritos)\n"
            f"VALUES ({args.empresa_id}, '{_sql(args.nombre)}', '{_sql(args.url)}', "
            f"'{secreto}', ARRAY[{arr}]);"
        )
        print("\n(usa --aplicar para ejecutarlo contra DATABASE_URL)")
        return

    from app.admin.db import SessionLocal
    from app.admin.models import Empresa
    from app.api.models_eventos import SuscripcionWebhook

    db = SessionLocal()
    try:
        if not db.get(Empresa, args.empresa_id):
            print(f"✗ No existe empresa id={args.empresa_id}")
            sys.exit(1)
        s = SuscripcionWebhook(
            empresa_id=args.empresa_id, nombre=args.nombre, url_destino=args.url,
            secreto_hmac=secreto, tipos_suscritos=tipos, activo=True,
        )
        db.add(s)
        db.commit()
        print(f"✓ Suscripción creada id={s.id} tipos={tipos}")
    finally:
        db.close()


def cmd_probar(args) -> None:
    """Envía un pago.confirmado de prueba, firmado, sin tocar la BD."""
    sobre = armar_sobre(
        TIPO_PAGO_CONFIRMADO,
        {"id_fiscal": "20000000001", "pais": "PE", "id_fiscal_tipo": "RUC"},
        "PEN",
        {
            "pago_id": 0,
            "monto": "15.00",
            "canal": "yape",
            "institucion": "Yape",
            "contraparte": "Juan Pérez (PRUEBA)",
            "codigo_operacion": None,
            "fecha_hora_banco": None,
            "referencia_pedido": "PRUEBA-001",
        },
    )
    cuerpo = serializar_cuerpo(sobre)
    codigo, error = enviar_http(args.url, cuerpo, cabeceras_para(sobre, cuerpo, args.secreto))
    print(f"evento_id={sobre['evento_id']} → HTTP {codigo} {error or ''}")


def main() -> None:
    p = argparse.ArgumentParser(description="Suscripciones de webhooks de PagoOK")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("crear", help="Crear suscripción (genera secreto)")
    c.add_argument("--empresa-id", type=int, required=True)
    c.add_argument("--nombre", required=True)
    c.add_argument("--url", required=True)
    c.add_argument("--tipos", default=TIPO_PAGO_CONFIRMADO, help="separados por coma")
    c.add_argument("--aplicar", action="store_true")
    c.set_defaults(func=cmd_crear)

    t = sub.add_parser("probar", help="Enviar un evento de prueba firmado a una URL")
    t.add_argument("--url", required=True)
    t.add_argument("--secreto", required=True)
    t.set_defaults(func=cmd_probar)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
