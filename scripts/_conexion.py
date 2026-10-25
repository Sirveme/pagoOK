"""Conexión a la BD para los scripts que se corren desde la PC de Duilio.

La URL pública de Railway (mainline.proxy.rlwy.net) tiene 100-450 ms por
consulta. Sin límite de tiempo, un problema de red deja el script colgado sin
decir nada. Aquí:
  - se muestra "Conectando…" antes de intentar,
  - la conexión falla con un mensaje claro si no responde en 15 s,
  - cada consulta tiene un tope (statement_timeout) para no quedar esperando
    para siempre detrás de un bloqueo (p. ej. una transacción abierta en PGAdmin).
"""
from __future__ import annotations

import sys
import time

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

TIMEOUT_CONEXION_SEG = 15
TIMEOUT_CONSULTA_MS = 60_000


def abrir_sesion(solo_lectura: bool = False) -> Session:
    """Sesión lista para usar, o termina el script con un mensaje claro."""
    from app.admin.db import DATABASE_URL

    host = DATABASE_URL.split("@")[-1].split("/")[0] if "@" in DATABASE_URL else "?"
    print(f"Conectando a la base de datos ({host})…", flush=True)
    opciones = f"-c statement_timeout={TIMEOUT_CONSULTA_MS}"
    if solo_lectura:
        opciones += " -c default_transaction_read_only=on"
    engine = create_engine(
        DATABASE_URL,
        connect_args={"connect_timeout": TIMEOUT_CONEXION_SEG, "options": opciones,
                      "keepalives": 1, "keepalives_idle": 30},
        pool_pre_ping=False,
    )
    inicio = time.time()
    try:
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
    except OperationalError as exc:
        motivo = str(exc.orig).strip().splitlines()[0] if exc.orig else str(exc)
        print(f"\nERROR: no se pudo conectar en {TIMEOUT_CONEXION_SEG} s a {host}.\n"
              f"  Detalle: {motivo}\n"
              "  Revisa: tu conexión a internet, que DATABASE_URL en .env sea la URL PÚBLICA de Railway\n"
              "  (Railway > Postgres > Connect > Public Network) y que el servicio de la base esté encendido.",
              file=sys.stderr, flush=True)
        sys.exit(2)
    print(f"Conectado en {time.time() - inicio:.1f} s{' (solo lectura)' if solo_lectura else ''}.", flush=True)
    return sessionmaker(bind=engine, autoflush=False)()


def explicar_error(exc: Exception) -> str:
    """Mensaje legible para errores de consulta frecuentes."""
    texto = str(getattr(exc, "orig", exc))
    if "statement timeout" in texto or "canceling statement" in texto:
        return ("La consulta superó 60 s. Suele ser un bloqueo: ¿hay una transacción abierta en PGAdmin "
                "(un BEGIN sin COMMIT/ROLLBACK)? Ciérrala y vuelve a intentar.")
    if "read-only transaction" in texto:
        return "La sesión es de solo lectura: no se puede escribir."
    return texto.splitlines()[0]
