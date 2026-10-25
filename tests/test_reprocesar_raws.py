"""Tests offline de scripts/reprocesar_raws.py (SQLite en memoria).

Lo crítico: el reproceso NO debe emitir webhooks, eventos ni push.
Ejecutar:  python -m unittest tests.test_reprocesar_raws
"""
import os
import sys
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DATABASE_URL", "postgresql://dummy:dummy@localhost/dummy")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.admin.db import Base  # noqa: E402
import app.admin.models  # noqa: E402,F401
from app.admin.models import Empresa  # noqa: E402
from app.api.models_cuenta import CuentaApi  # noqa: E402
from app.api.models_eventos import EntregaEvento, SuscripcionWebhook  # noqa: E402
from app.api.models_pago_uso import PagoUso  # noqa: E402
from app.api.models_webhook import NotificacionRaw, PagoDetectado  # noqa: E402
from app.services.conciliacion import consultar_pago  # noqa: E402
from scripts.reprocesar_raws import NOTA_HISTORICO, reprocesar  # noqa: E402

FECHA_ORIGINAL = datetime(2026, 5, 28, 21, 20, 5)


def _prohibido(*a, **kw):
    raise AssertionError("El reproceso NO debe emitir webhooks, eventos ni push")


class ReprocesarRawsTest(unittest.TestCase):
    def setUp(self):
        eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(eng)
        self.db = sessionmaker(bind=eng, autoflush=False)()
        emp = Empresa(pais_codigo="PE", id_fiscal="20100000001", razon_social="A", slug="a")
        otra = Empresa(pais_codigo="PE", id_fiscal="20100000002", razon_social="B", slug="b")
        self.db.add_all([emp, otra])
        self.db.flush()
        # Credencial dueña del reclamo histórico, otra credencial de la misma
        # empresa (ej. Facturalo) y una de otra empresa.
        self.reclamo = CuentaApi(empresa_id=emp.id, nombre="PSP pruebas", api_key_hash="h1", activa=True)
        self.facturalo = CuentaApi(empresa_id=emp.id, nombre="Facturalo", api_key_hash="h2", activa=True)
        self.ajena = CuentaApi(empresa_id=otra.id, nombre="Ajena", api_key_hash="h3", activa=True)
        self.db.add_all([self.reclamo, self.facturalo, self.ajena])
        self.db.flush()
        self.emp_id = emp.id
        # Suscripción activa: si algo emitiera pago.confirmado, habría entregas.
        self.db.add(SuscripcionWebhook(empresa_id=emp.id, url_destino="https://x.test/h",
                                       secreto_hmac="whsec_x", tipos_suscritos=["pago.confirmado"]))
        self.ignorado = NotificacionRaw(
            empresa_id=emp.id, package_app="com.bcp.innovacxion.yapeapp",
            titulo="Confirmación de Pago", estado_parseo="ignorada",
            texto="Sasha Sal* te envió un pago por S/ 5.5. El cód. de seguridad es: 667",
            timestamp_servidor=FECHA_ORIGINAL,
        )
        self.con_pago = NotificacionRaw(
            empresa_id=emp.id, package_app="pe.com.interbank.mobilebanking", titulo="Interbank",
            texto="Te plinearon S/ 1.00", estado_parseo="parseada", timestamp_servidor=FECHA_ORIGINAL,
        )
        self.db.add_all([self.ignorado, self.con_pago])
        self.db.flush()
        self.db.add(PagoDetectado(empresa_id=emp.id, notificacion_raw_id=self.con_pago.id,
                                  metodo="plin", monto=Decimal("1.00"), tipo="ingreso",
                                  recibido_en=FECHA_ORIGINAL))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def _reprocesar(self, aplicar, cuenta_id="por_defecto"):
        if cuenta_id == "por_defecto":
            cuenta_id = self.reclamo.id
        with mock.patch("app.services.eventos.emitir_evento", _prohibido), \
             mock.patch("app.services.eventos.emitir_pago_confirmado", _prohibido), \
             mock.patch("app.services.eventos.despertar_worker", _prohibido), \
             mock.patch("app.api.push_service.enviar_push_a_empresa", _prohibido):
            r = reprocesar(self.db, [self.ignorado.id, self.con_pago.id, 99999], aplicar=aplicar,
                           cuenta_reclamo_id=cuenta_id)
        self.db.commit()
        return r

    def test_simulacion_no_escribe(self):
        self._reprocesar(aplicar=False)
        self.assertEqual(self.db.query(PagoDetectado).count(), 1)

    def test_aplicar_crea_pago_con_fecha_original_y_sin_eventos(self):
        reporte = self._reprocesar(aplicar=True)
        nuevo = self.db.query(PagoDetectado).filter_by(notificacion_raw_id=self.ignorado.id).one()
        self.assertEqual(nuevo.monto, Decimal("5.5"))
        self.assertEqual(nuevo.recibido_en, FECHA_ORIGINAL)
        self.assertFalse(nuevo.tipo_incierto)
        self.assertEqual(self.db.get(NotificacionRaw, self.ignorado.id).estado_parseo, "parseada")
        self.assertEqual(self.db.query(EntregaEvento).count(), 0)
        self.assertTrue(any("ya tiene pago_id" in l for l in reporte))
        self.assertTrue(any("NO existe" in l for l in reporte))

    def test_pago_recuperado_queda_reclamado_y_usado_con_nota(self):
        self._reprocesar(aplicar=True)
        nuevo = self.db.query(PagoDetectado).filter_by(notificacion_raw_id=self.ignorado.id).one()
        self.assertEqual(nuevo.reclamado_por, self.reclamo.id)
        self.assertIsNotNone(nuevo.reclamado_en)
        uso = self.db.query(PagoUso).filter_by(pago_id=nuevo.id).one()
        self.assertEqual(uso.referencia_externa, NOTA_HISTORICO)
        self.assertEqual(uso.cuenta_api_id, self.reclamo.id)
        # El pago que ya existía (raw 146) no se toca.
        previo = self.db.query(PagoDetectado).filter_by(notificacion_raw_id=self.con_pago.id).one()
        self.assertIsNone(previo.reclamado_por)
        self.assertEqual(self.db.query(PagoUso).count(), 1)

    def test_ningun_sistema_lo_ve_como_disponible(self):
        self._reprocesar(aplicar=True)
        for cuenta in (self.facturalo, self.reclamo):
            r = consultar_pago(self.db, empresa_id=self.emp_id, cuenta_id=cuenta.id,
                               monto=Decimal("5.5"), fecha_hora_declarada=FECHA_ORIGINAL.replace(hour=16),
                               ventana_minutos=1440)
            self.assertEqual((r["nivel"], r["motivo"]), ("sin_coincidencia", "ya_usado"))
            self.assertEqual(r["pagos_ya_usados"][0]["uso"]["referencia_externa"], NOTA_HISTORICO)

    def test_aplicar_sin_cuenta_falla_sin_escribir(self):
        with self.assertRaises(ValueError):
            self._reprocesar(aplicar=True, cuenta_id=None)
        self.db.rollback()
        self.assertEqual(self.db.query(PagoDetectado).count(), 1)

    def test_cuenta_de_otra_empresa_falla_sin_escribir(self):
        with self.assertRaises(ValueError):
            self._reprocesar(aplicar=True, cuenta_id=self.ajena.id)
        self.db.rollback()
        self.assertEqual(self.db.query(PagoDetectado).count(), 1)

    def test_idempotente(self):
        self._reprocesar(aplicar=True)
        self._reprocesar(aplicar=True)
        self.assertEqual(self.db.query(PagoDetectado).count(), 2)  # 1 previo + 1 nuevo, sin duplicar


if __name__ == "__main__":
    unittest.main()
