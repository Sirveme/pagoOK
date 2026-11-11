"""Tests de los pagos de prueba (es_prueba) — zWeb-09.

Un pago de prueba (botón "Simular pago de prueba"):
  - nunca sale en la API pública (listar/consultar) ni se marca como usado,
  - nunca dispara pago.confirmado (webhooks),
  - no cuenta para reportes de negocio (resumen del día),
  - un pago real sigue comportándose igual,
  - hay un tope de 10 pagos de prueba por dispositivo por hora.

SQLite en memoria + get_db sobreescrito; el HTTP saliente se simula.
Ejecutar:  python -m unittest tests.test_pagos_prueba
"""
import os
import sys
import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal
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
from app.api.models_webhook import Dispositivo, PagoDetectado  # noqa: E402
from app.api.models_cuenta import CuentaApi, generar_api_key, hash_api_key  # noqa: E402
from app.api.models_eventos import EntregaEvento, SuscripcionWebhook  # noqa: E402
from app.services import eventos as ev  # noqa: E402
from app.services.resumen_empresa import calcular_resumen_dia  # noqa: E402
from app.main import app  # noqa: E402


class PagosPruebaBase(unittest.TestCase):
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

        emp = Empresa(pais_codigo="PE", id_fiscal="20123456789",
                      razon_social="Bodega Prueba", slug="bodega-prueba")
        self.db.add(emp)
        self.db.flush()
        self.emp_id = emp.id

        # Credencial API (listar/consultar/marcar-usado).
        self.api_key = generar_api_key()
        self.db.add(CuentaApi(empresa_id=emp.id, nombre="Sistema",
                              api_key_hash=hash_api_key(self.api_key),
                              prefijo="pk_x", activa=True))
        # Dispositivo (inbound).
        self.db.add(Dispositivo(empresa_id=emp.id, token="tok_test", nombre="Cel", activo=True))
        # Suscripción a pago.confirmado (para comprobar que la prueba NO encola).
        self.db.add(SuscripcionWebhook(
            empresa_id=emp.id, nombre="QueVendi", url_destino="https://qv.test/hook",
            secreto_hmac="whsec_a", tipos_suscritos=["pago.confirmado"], activo=True))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        app.dependency_overrides.clear()
        Base.metadata.drop_all(self.engine)

    def _pago(self, **kw):
        datos = dict(empresa_id=self.emp_id, metodo="yape", monto=Decimal("15.00"),
                     titular="Juan Perez", recibido_en=datetime(2026, 11, 2, 19, 0, 0),
                     tipo="ingreso", tipo_incierto=False, es_prueba=False)
        datos.update(kw)
        p = PagoDetectado(**datos)
        self.db.add(p)
        self.db.commit()
        return p

    def _h(self):
        return {"X-API-Key": self.api_key}

    def _entregas(self):
        self.db.expire_all()
        return self.db.query(EntregaEvento).all()


class TestApiPublica(PagosPruebaBase):
    def test_prueba_no_sale_en_listar(self):
        real = self._pago(monto=Decimal("20.00"))
        prueba = self._pago(monto=Decimal("30.00"), es_prueba=True)
        r = self.client.get("/api/v1/pagos", headers=self._h())
        self.assertEqual(r.status_code, 200, r.text)
        ids = {p["id"] for p in r.json()["pagos"]}
        self.assertIn(str(real.id), ids)
        self.assertNotIn(str(prueba.id), ids)

    def test_prueba_no_sale_en_consultar(self):
        self._pago(monto=Decimal("30.00"), es_prueba=True)
        r = self.client.post("/api/v1/pagos/consultar", headers=self._h(), json={
            "monto": "30.00", "fecha_hora_declarada": "2026-11-02T19:00:00+00:00", "ventana_minutos": 60})
        self.assertEqual(r.status_code, 200, r.text)
        # Sin coincidencia: el único pago de ese monto es de prueba.
        self.assertNotEqual(r.json()["nivel"], "alta")

    def test_real_si_sale_en_consultar(self):
        self._pago(monto=Decimal("40.00"), es_prueba=False)
        r = self.client.post("/api/v1/pagos/consultar", headers=self._h(), json={
            "monto": "40.00", "fecha_hora_declarada": "2026-11-02T19:00:00+00:00", "ventana_minutos": 60})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["nivel"], "alta")

    def test_prueba_no_se_marca_usado(self):
        prueba = self._pago(monto=Decimal("30.00"), es_prueba=True)
        r = self.client.post(f"/api/v1/pagos/{prueba.id}/marcar-usado", headers=self._h(),
                             json={"referencia_externa": "venta-1"})
        self.assertEqual(r.status_code, 404, r.text)


class TestEventos(PagosPruebaBase):
    def test_prueba_no_emite(self):
        self.assertFalse(ev.debe_emitir_pago_confirmado(self._pago(es_prueba=True)))
        self.assertIsNone(ev.emitir_pago_confirmado(self.db, self._pago(es_prueba=True)))

    def test_real_si_emite(self):
        self.assertTrue(ev.debe_emitir_pago_confirmado(self._pago(es_prueba=False)))


class TestResumen(PagosPruebaBase):
    def test_prueba_excluida_del_resumen(self):
        dia = date(2026, 11, 2)
        self._pago(monto=Decimal("50.00"), es_prueba=False,
                   recibido_en=datetime(2026, 11, 2, 12, 0, 0))
        self._pago(monto=Decimal("999.00"), es_prueba=True,
                   recibido_en=datetime(2026, 11, 2, 13, 0, 0))
        r = calcular_resumen_dia(self.emp_id, dia, self.db)
        self.assertEqual(r["total_ingresos"], Decimal("50.00"))
        self.assertEqual(r["cuenta_ingresos"], 1)


class TestInbound(PagosPruebaBase):
    def _inbound(self, es_prueba):
        body = {"package": "com.bcp.innovacxion.yapeapp", "titulo": "Yape!",
                "texto": "Juan Perez te yapeó S/ 15.00"}
        if es_prueba:
            body["es_prueba"] = True
        with mock.patch("app.services.eventos.despertar_worker"), \
             mock.patch("app.api.push_service.enviar_push_a_empresa", return_value={}) as push:
            r = self.client.post("/api/v1/notificaciones/inbound",
                                 headers={"X-Device-Token": "tok_test"}, json=body)
        return r, push

    def test_inbound_prueba_marca_y_no_notifica(self):
        r, push = self._inbound(es_prueba=True)
        self.assertEqual(r.status_code, 200, r.text)
        pago_id = r.json()["pago_id"]
        self.assertIsNotNone(pago_id)
        self.db.expire_all()
        pago = self.db.get(PagoDetectado, pago_id)
        self.assertTrue(pago.es_prueba)
        self.assertEqual(len(self._entregas()), 0)  # no encola pago.confirmado
        push.assert_not_called()                    # ni push

    def test_inbound_real_si_notifica(self):
        r, push = self._inbound(es_prueba=False)
        self.assertEqual(r.status_code, 200, r.text)
        self.db.expire_all()
        pago = self.db.get(PagoDetectado, r.json()["pago_id"])
        self.assertFalse(pago.es_prueba)
        self.assertEqual(len(self._entregas()), 1)
        push.assert_called_once()

    def test_limite_10_por_hora(self):
        with mock.patch("app.services.eventos.despertar_worker"), \
             mock.patch("app.api.push_service.enviar_push_a_empresa", return_value={}):
            for i in range(10):
                r = self.client.post("/api/v1/notificaciones/inbound",
                                     headers={"X-Device-Token": "tok_test"},
                                     json={"package": "com.bcp.innovacxion.yapeapp", "titulo": "Yape!",
                                           "texto": "Juan Perez te yapeó S/ 15.00", "es_prueba": True,
                                           "sbn_key": f"k{i}"})
                self.assertEqual(r.status_code, 200, r.text)
            # El 11º supera el tope.
            r = self.client.post("/api/v1/notificaciones/inbound",
                                 headers={"X-Device-Token": "tok_test"},
                                 json={"package": "com.bcp.innovacxion.yapeapp", "titulo": "Yape!",
                                       "texto": "Juan Perez te yapeó S/ 15.00", "es_prueba": True,
                                       "sbn_key": "k10"})
            self.assertEqual(r.status_code, 429, r.text)


if __name__ == "__main__":
    unittest.main()
