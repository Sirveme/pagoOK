"""Tests de la cuenta de revisión de Google Play (es_cuenta_revision) — zWeb-09.

  - No vence ni se puede suspender.
  - No se puede eliminar.
  - No genera alertas de celular caído.
  - Acceso al panel del revisor: celular ficticio + código fijo (hash), sin OTP.

Ejecutar:  python -m unittest tests.test_revision_play
"""
import hashlib
import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DATABASE_URL", "postgresql://dummy:dummy@localhost/dummy")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.admin.db import Base, get_db  # noqa: E402
import app.admin.models  # noqa: E402,F401
from app.admin.models import Empresa  # noqa: E402
from app.api.models_multiempresa import Usuario, Membresia  # noqa: E402
from app.api.models_webhook import Dispositivo  # noqa: E402
from app.services import cuenta_empresa, eliminacion, revision  # noqa: E402
from app.services import alerta_dispositivos as alerta  # noqa: E402
from app.api.router_panel import reiniciar_limites_panel  # noqa: E402
from app.main import app  # noqa: E402

CODIGO = "REV-2026-OK"
CODIGO_HASH = hashlib.sha256(CODIGO.encode()).hexdigest()
CELULAR = "+51900000111"


class RevisionBase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        self.Session = sessionmaker(bind=self.engine, autoflush=False)
        Base.metadata.create_all(self.engine)

        def _override_get_db():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = _override_get_db
        self.client = TestClient(app)
        self.db = self.Session()
        reiniciar_limites_panel()

        self.rev = Empresa(pais_codigo="PE", id_fiscal="REVISION-PLAY",
                           razon_social="REVISION GOOGLE PLAY", slug="revision-play",
                           es_cuenta_revision=True, estado_cuenta="activa")
        self.normal = Empresa(pais_codigo="PE", id_fiscal="20111111111",
                              razon_social="Bodega", slug="bodega", estado_cuenta="activa")
        self.db.add_all([self.rev, self.normal])
        self.db.commit()

    def tearDown(self):
        self.db.close()
        app.dependency_overrides.clear()
        Base.metadata.drop_all(self.engine)


class TestProtecciones(RevisionBase):
    def test_nunca_vence(self):
        self.assertTrue(cuenta_empresa.nunca_vence(self.rev))
        self.assertFalse(cuenta_empresa.nunca_vence(self.normal))

    def test_no_se_suspende(self):
        with self.assertRaises(cuenta_empresa.CuentaError):
            cuenta_empresa.suspender(self.rev)

    def test_no_se_elimina(self):
        self.assertFalse(eliminacion.eliminacion_permitida(self.rev))
        self.assertTrue(eliminacion.eliminacion_permitida(self.normal))

    def test_refrescar_estado_la_mantiene_activa(self):
        self.rev.estado_cuenta = "vencida"
        self.assertEqual(cuenta_empresa.refrescar_estado(self.db, self.rev), "activa")

    def test_empresa_revision_por_bandera(self):
        self.assertEqual(revision.empresa_revision(self.db).id, self.rev.id)


class TestAlertaCaida(RevisionBase):
    def test_no_alerta_a_la_cuenta_de_revision(self):
        caido = datetime.utcnow() - timedelta(minutes=40)
        d_rev = Dispositivo(empresa_id=self.rev.id, token="tok_rev", nombre="Cel rev",
                            activo=True, ultimo_ping=caido)
        d_norm = Dispositivo(empresa_id=self.normal.id, token="tok_norm", nombre="Cel norm",
                             activo=True, ultimo_ping=caido)
        self.db.add_all([d_rev, d_norm])
        self.db.commit()

        alerta.revisar(self.db, ahora=datetime.utcnow(), enviar=lambda *a, **k: True)
        self.db.expire_all()
        # La cuenta de revisión no se marca como caída; la normal sí.
        self.assertIsNone(self.db.get(Dispositivo, d_rev.id).alerta_caida_en)
        self.assertIsNotNone(self.db.get(Dispositivo, d_norm.id).alerta_caida_en)


class TestAccesoRevision(RevisionBase):
    def setUp(self):
        super().setUp()
        u = Usuario(nombre="Revisor", celular=CELULAR, activo=True)
        self.db.add(u)
        self.db.flush()
        self.db.add(Membresia(usuario_id=u.id, empresa_id=self.rev.id, rol="contador", activo=True))
        self.db.commit()
        revision.reiniciar_fallos_panel()
        self.env = mock.patch.dict(os.environ, {
            "REVISION_PANEL_CELULAR": CELULAR,
            "REVISION_PANEL_CODIGO_HASH": CODIGO_HASH,
        })
        self.env.start()

    def tearDown(self):
        self.env.stop()
        super().tearDown()

    def test_otp_no_envia_whatsapp(self):
        r = self.client.post("/panel/login/otp", json={"celular": CELULAR})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["ok"])
        # El front usa este flag para la casilla alfanumérica larga.
        self.assertEqual(r.json().get("modo"), "revision")

    def test_codigo_largo_alfanumerico_entra(self):
        # Un código largo y alfanumérico (hasta 40) se acepta tal cual (solo .strip()).
        codigo = "RevPlay-2026_Acceso-LARGO-abc123"
        with mock.patch.dict(os.environ, {
            "REVISION_PANEL_CODIGO_HASH": hashlib.sha256(codigo.encode()).hexdigest()}):
            r = self.client.post("/panel/login/verificar",
                                 json={"celular": CELULAR, "codigo": f"  {codigo}  "})
        self.assertEqual(r.status_code, 200, r.text)

    def test_login_con_codigo_fijo(self):
        r = self.client.post("/panel/login/verificar", json={"celular": CELULAR, "codigo": CODIGO})
        self.assertEqual(r.status_code, 200, r.text)
        empresas = r.json()["empresas"]
        self.assertEqual(empresas[0]["rol"], "contador")
        self.assertIn("pagook_sesion", r.cookies)

    def test_codigo_incorrecto_rechaza(self):
        r = self.client.post("/panel/login/verificar", json={"celular": CELULAR, "codigo": "mal"})
        self.assertEqual(r.status_code, 400, r.text)

    def test_otro_celular_no_usa_el_codigo(self):
        # Un celular cualquiera NO entra con el código de revisión.
        self.assertFalse(revision.es_celular_revision("+51987654321"))

    def test_limite_de_intentos_fallidos(self):
        # 5 fallos del código fijo bloquean (429); el correcto ya no entra hasta
        # que pase la ventana. Tan estricto como el tope del OTP (5 por código).
        for _ in range(revision.MAX_FALLOS_PANEL):
            r = self.client.post("/panel/login/verificar", json={"celular": CELULAR, "codigo": "mal"})
            self.assertEqual(r.status_code, 400, r.text)
        r = self.client.post("/panel/login/verificar", json={"celular": CELULAR, "codigo": CODIGO})
        self.assertEqual(r.status_code, 429, r.text)


if __name__ == "__main__":
    unittest.main()
