"""Genera y ACTIVA una clave API (pk_live_...) para la empresa de un RUC.

Pensado para correr en Railway cuando el botón del panel no alcanza:

    railway run python scripts/generar_api_key.py 20616219686

- Mismo formato y hash que el resto del sistema (app.api.models_cuenta): la BD
  guarda solo el hash SHA-256; la clave en claro se imprime UNA sola vez.
- Crea una cuenta NUEVA y activa para esa empresa. No desactiva ni modifica
  ninguna clave existente, ni de esta empresa ni de otras (la de PERU SISTEMAS
  sigue intacta).
- Escribe en DATABASE_URL al terminar (por eso se corre con `railway run`).

Opcional:
    railway run python scripts/generar_api_key.py 20616219686 --nombre "Sistema de MITA"
"""
from __future__ import annotations

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
    print("  API KEY (se muestra UNA sola vez - guardala ahora):")
    print(f"    {api_key}")
    print("=" * 62)
    print()


def main() -> None:
    p = argparse.ArgumentParser(description="Generar y activar una clave API para la empresa de un RUC")
    p.add_argument("ruc", help="RUC / identificador fiscal de la empresa (ej. 20616219686)")
    p.add_argument("--nombre", default=None,
                   help="Nombre para reconocer la clave (por defecto, la razon social de la empresa)")
    args = p.parse_args()

    ruc = args.ruc.strip()

    from app.admin.db import SessionLocal
    from app.admin.models import Empresa

    db = SessionLocal()
    try:
        empresas = db.query(Empresa).filter(Empresa.id_fiscal == ruc).all()
        if not empresas:
            print(f"X No existe ninguna empresa con id_fiscal = {ruc!r}", file=sys.stderr)
            sys.exit(1)
        if len(empresas) > 1:
            print(f"X Hay {len(empresas)} empresas con ese id_fiscal; afina con el slug en PGAdmin:", file=sys.stderr)
            for e in empresas:
                print(f"    id={e.id} slug={e.slug} pais={e.pais_codigo} {e.razon_social}", file=sys.stderr)
            sys.exit(1)
        empresa = empresas[0]

        existentes = db.query(CuentaApi).filter(CuentaApi.empresa_id == empresa.id).order_by(CuentaApi.id).all()
        if existentes:
            print(f"Claves que ya tiene {empresa.slug!r} (no se tocan):")
            for c in existentes:
                print(f"    id={c.id} {'activa  ' if c.activa else 'inactiva'} {c.prefijo or '':<16} {c.nombre}")

        nombre = (args.nombre or empresa.nombre_comercial or empresa.razon_social or "Clave API").strip()[:150]
        api_key = generar_api_key()
        cuenta = CuentaApi(
            empresa_id=empresa.id,
            nombre=nombre,
            api_key_hash=hash_api_key(api_key),
            prefijo=prefijo_visible(api_key),
            activa=True,
        )
        db.add(cuenta)
        db.commit()
        db.refresh(cuenta)

        _mostrar_key(api_key)
        print(f"OK clave nueva activa: id={cuenta.id} prefijo={cuenta.prefijo} nombre={cuenta.nombre!r}")
        print(f"   empresa: {empresa.razon_social} (slug={empresa.slug}, id={empresa.id})")
    finally:
        db.close()


if __name__ == "__main__":
    main()
