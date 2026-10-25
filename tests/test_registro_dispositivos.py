"""Tests del auto-registro de dispositivos.

SQLite en memoria. Ejecutar:  python -m unittest tests.test_registro_dispositivos
"""
import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DATABASE_URL", "postgresql://dummy:dummy@localhost/dummy")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.admin.db import Base, get_db  # noqa: E402
import app.admin.models  # noqa: E402,F401
from app.admin.models import CodigoInvitacion, Empresa  # noqa: E402
from app.api.models_webhook import Dispositivo  # noqa: E402,F401
from app.api.router_device import reiniciar_rate_limit_registro  # noqa: E402
from app.main import app  # noqa: E402


class RegistroDispositivosTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        self.Session = sessionmaker(bind=self.engine, autoflush=False)
        Base.metadata.create_all(self.engine)

        def _override():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = _override
        self.client = TestClient(app)
        reiniciar_rate_limit_registro()  # aislar el rate limit entre tests
        self._seed()

    def tearDown(self):
        app.dependency_overrides.clear()
        Base.metadata.drop_all(self.engine)
        reiniciar_rate_limit_registro()

    def _seed(self):
        db = self.Session()
        try:
            # Empresa ABIERTA (acepta auto-registro sin código).
            abierta = Empresa(pais_codigo="PE", id_fiscal="20100000001",
                              razon_social="Perú Sistemas Pro", nombre_comercial="Perú Sistemas Pro",
                              slug="perusistemas", acepta_autoregistro=True)
            # Empresa CERRADA (requiere código).
            cerrada = Empresa(pais_codigo="PE", id_fiscal="20100000002",
                              razon_social="Cerrada SAC", slug="cerrada",
                              acepta_autoregistro=False)
            db.add_all([abierta, cerrada])
            db.flush()
            # Un código válido para la empresa cerrada.
            db.add(CodigoInvitacion(
                empresa_id=cerrada.id, codigo="ABC123",
                expira_en=datetime.utcnow() + timedelta(days=7), max_usos=10, usos_actuales=0,
            ))
            db.commit()
            self.slug_abierta = "perusistemas"
            self.slug_cerrada = "cerrada"
        finally:
            db.close()

    def _registrar(self, **cambios):
        body = {
            "empresa_slug": self.slug_abierta,
            "manufacturer": "Xiaomi",
            "modelo": "Redmi Note 12",
            "android_version": "14",
            "app_version": "1.4-beta",
            "instalacion_id": "uuid-1",
        }
        body.update(cambios)
        return self.client.post("/api/v1/dispositivos/registrar", json=body)

    def test_registro_ok_genera_token(self):
        r = self._registrar(instalacion_id="uuid-nueva")
        self.assertEqual(r.status_code, 201)
        j = r.json()
        self.assertTrue(j["token"].startswith("tok_"))
        self.assertIn("dispositivo_id", j)
        self.assertEqual(j["empresa_slug"], "perusistemas")

    def test_idempotencia_misma_instalacion(self):
        r1 = self._registrar(instalacion_id="uuid-fija")
        r2 = self._registrar(instalacion_id="uuid-fija")
        self.assertEqual(r1.status_code, 201)
        self.assertEqual(r2.status_code, 201)
        self.assertEqual(r1.json()["token"], r2.json()["token"])
        self.assertEqual(r1.json()["dispositivo_id"], r2.json()["dispositivo_id"])
        # No se creó un segundo dispositivo.
        db = self.Session()
        try:
            self.assertEqual(db.query(Dispositivo).count(), 1)
        finally:
            db.close()

    def test_empresa_inexistente_404(self):
        r = self._registrar(empresa_slug="no-existe")
        self.assertEqual(r.status_code, 404)

    def test_sin_codigo_empresa_cerrada_403(self):
        r = self._registrar(empresa_slug=self.slug_cerrada, instalacion_id="uuid-x")
        self.assertEqual(r.status_code, 403)

    def test_codigo_valido_empresa_cerrada_201(self):
        r = self._registrar(empresa_slug=self.slug_cerrada, codigo_invitacion="ABC123", instalacion_id="uuid-y")
        self.assertEqual(r.status_code, 201)
        # El código incrementó su contador de usos.
        db = self.Session()
        try:
            inv = db.query(CodigoInvitacion).filter_by(codigo="ABC123").first()
            self.assertEqual(inv.usos_actuales, 1)
        finally:
            db.close()

    def test_codigo_invalido_400(self):
        r = self._registrar(empresa_slug=self.slug_cerrada, codigo_invitacion="NOEXISTE", instalacion_id="uuid-z")
        self.assertEqual(r.status_code, 400)

    def test_rate_limit_429(self):
        # 10 permitidos, el 11.º debe dar 429 (misma IP: X-Forwarded-For fija).
        cabecera = {"X-Forwarded-For": "203.0.113.99"}
        codigos = []
        for i in range(11):
            r = self.client.post(
                "/api/v1/dispositivos/registrar",
                json={"empresa_slug": self.slug_abierta, "instalacion_id": f"uuid-rl-{i}",
                      "manufacturer": "X", "modelo": "Y", "android_version": "14", "app_version": "1.0"},
                headers=cabecera,
            )
            codigos.append(r.status_code)
        self.assertTrue(all(c == 201 for c in codigos[:10]), codigos)
        self.assertEqual(codigos[10], 429)


if __name__ == "__main__":
    unittest.main(verbosity=2)
