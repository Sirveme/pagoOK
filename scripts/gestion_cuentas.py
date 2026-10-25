"""Gestión de credenciales de la API pública v1 (sin UI).

Crea cuentas y rota api_keys. La api_key en claro se muestra UNA sola vez.

SEGURIDAD / BD VIVA:
  Por defecto NO toca la base de datos: imprime la api_key y el SQL a aplicar
  en PGAdmin (regla SQL-primero, Railway con datos reales).
  Use --aplicar para ejecutar contra DATABASE_URL (solo si sabe lo que hace).

Ejemplos:
  python scripts/gestion_cuentas.py listar --aplicar
  python scripts/gestion_cuentas.py crear --empresa-id 1 --nombre "Producto pagos"
  python scripts/gestion_cuentas.py crear --empresa-id 1 --nombre "Producto pagos" --aplicar
  python scripts/gestion_cuentas.py rotar --cuenta-id 3 --aplicar
"""
import argparse
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.api.models_cuenta import (  # noqa: E402
    CuentaApi,
    generar_api_key,
    hash_api_key,
    prefijo_visible,
)


def _mostrar_key(api_key: str) -> None:
    print()
    print("=" * 62)
    print("  API KEY (se muestra UNA sola vez — guárdala cifrada):")
    print(f"    {api_key}")
    print("=" * 62)
    print()


def cmd_crear(args) -> None:
    api_key = generar_api_key()
    h = hash_api_key(api_key)
    pref = prefijo_visible(api_key)

    _mostrar_key(api_key)

    if not args.aplicar:
        print("-- SQL para PGAdmin (modo SQL-primero, NO se ejecutó):")
        print(
            "INSERT INTO cuenta_api (empresa_id, nombre, api_key_hash, prefijo, activa)\n"
            f"VALUES ({args.empresa_id}, '{args.nombre}', '{h}', '{pref}', true);"
        )
        print("\n(usa --aplicar para ejecutarlo contra DATABASE_URL)")
        return

    from app.admin.db import SessionLocal
    from app.admin.models import Empresa

    db = SessionLocal()
    try:
        empresa = db.query(Empresa).filter(Empresa.id == args.empresa_id).first()
        if not empresa:
            print(f"✗ No existe empresa id={args.empresa_id}")
            sys.exit(1)
        cuenta = CuentaApi(
            empresa_id=args.empresa_id,
            nombre=args.nombre,
            api_key_hash=h,
            prefijo=pref,
            activa=True,
        )
        db.add(cuenta)
        db.commit()
        db.refresh(cuenta)
        print(f"✓ Cuenta creada id={cuenta.id} para empresa "
              f"'{empresa.nombre_comercial or empresa.razon_social}'")
    finally:
        db.close()


def cmd_rotar(args) -> None:
    api_key = generar_api_key()
    h = hash_api_key(api_key)
    pref = prefijo_visible(api_key)

    _mostrar_key(api_key)

    if not args.aplicar:
        print("-- SQL para PGAdmin (modo SQL-primero, NO se ejecutó):")
        print(
            f"UPDATE cuenta_api SET api_key_hash = '{h}', prefijo = '{pref}'\n"
            f"WHERE id = {args.cuenta_id};"
        )
        print("\n(usa --aplicar para ejecutarlo contra DATABASE_URL)")
        return

    from app.admin.db import SessionLocal

    db = SessionLocal()
    try:
        cuenta = db.query(CuentaApi).filter(CuentaApi.id == args.cuenta_id).first()
        if not cuenta:
            print(f"✗ No existe cuenta_api id={args.cuenta_id}")
            sys.exit(1)
        cuenta.api_key_hash = h
        cuenta.prefijo = pref
        db.commit()
        print(f"✓ api_key rotada para cuenta id={cuenta.id} ('{cuenta.nombre}')")
    finally:
        db.close()


def cmd_listar(args) -> None:
    if not args.aplicar:
        print("`listar` lee la BD; agrega --aplicar para conectarte a DATABASE_URL.")
        return

    from app.admin.db import SessionLocal

    db = SessionLocal()
    try:
        cuentas = db.query(CuentaApi).order_by(CuentaApi.id).all()
        if not cuentas:
            print("(sin cuentas)")
            return
        print(f"{'id':>3}  {'empresa':>7}  {'activa':>6}  prefijo        nombre")
        print("-" * 60)
        for c in cuentas:
            print(f"{c.id:>3}  {c.empresa_id:>7}  {str(c.activa):>6}  "
                  f"{(c.prefijo or ''):<14} {c.nombre}")
    finally:
        db.close()


def main() -> None:
    p = argparse.ArgumentParser(description="Gestión de credenciales API v1 PagoOK")
    sub = p.add_subparsers(dest="cmd", required=True)

    pc = sub.add_parser("crear", help="Crear una cuenta y generar su api_key")
    pc.add_argument("--empresa-id", type=int, required=True)
    pc.add_argument("--nombre", required=True)
    pc.add_argument("--aplicar", action="store_true", help="Ejecutar en la BD")
    pc.set_defaults(func=cmd_crear)

    pr = sub.add_parser("rotar", help="Rotar la api_key de una cuenta existente")
    pr.add_argument("--cuenta-id", type=int, required=True)
    pr.add_argument("--aplicar", action="store_true", help="Ejecutar en la BD")
    pr.set_defaults(func=cmd_rotar)

    pl = sub.add_parser("listar", help="Listar cuentas")
    pl.add_argument("--aplicar", action="store_true", help="Conectarse a la BD")
    pl.set_defaults(func=cmd_listar)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
