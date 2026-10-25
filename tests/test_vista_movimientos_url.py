"""Regresión del síntoma 3: la vista de ingresos/egresos debe llamar al
endpoint PLURAL (/ingresos, /egresos), no al singular (404).

Ejecutar:  python -m unittest tests.test_vista_movimientos_url
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DATABASE_URL", "postgresql://dummy:dummy@localhost/dummy")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.admin.db import Base, get_db  # noqa: E402
import app.admin.models  # noqa: E402,F401
from app.admin.models import Empresa  # noqa: E402
import app.api.models_webhook  # noqa: E402,F401
from app.api.router_internos import _html_lista_movimientos  # noqa: E402
from app.main import app  # noqa: E402


class VistaMovimientosUrlTest(unittest.TestCase):
    # ---- Regresión de string: el fetch usa el endpoint plural ----
    def test_html_egresos_usa_url_plural(self):
        html = _html_lista_movimientos("perusistemas", "Perú Sistemas", "egreso")
        self.assertIn('const TIPO = "egreso";', html)
        # El fetch debe construir la URL plural (TIPO + "s?" + filtros).
        self.assertIn('+ TIPO + "s?" + filtrosQS()', html)
        # Y NO la singular (bug original que daba 404).
        self.assertNotIn('+ TIPO + "?', html)

    def test_html_ingresos_usa_url_plural(self):
        html = _html_lista_movimientos("perusistemas", "Perú Sistemas", "ingreso")
        self.assertIn('const TIPO = "ingreso";', html)
        self.assertIn('+ TIPO + "s?" + filtrosQS()', html)

    # ---- Contrato de rutas: plural 200, singular 404 ----
    def test_endpoint_plural_vs_singular(self):
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Session = sessionmaker(bind=engine, autoflush=False)
        Base.metadata.create_all(engine)

        def _ov():
            db = Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = _ov
        try:
            db = Session()
            db.add(Empresa(pais_codigo="PE", id_fiscal="20100000001",
                           razon_social="Perú Sistemas", slug="perusistemas"))
            db.commit()
            db.close()

            c = TestClient(app)
            self.assertEqual(c.get("/api/v1/perusistemas/egresos").status_code, 200)   # plural OK
            self.assertEqual(c.get("/api/v1/perusistemas/ingresos").status_code, 200)  # plural OK
            self.assertEqual(c.get("/api/v1/perusistemas/egreso").status_code, 404)    # singular 404
        finally:
            app.dependency_overrides.clear()
            Base.metadata.drop_all(engine)


if __name__ == "__main__":
    unittest.main(verbosity=2)
