"""zWeb-15: un pago recuperado conserva su hora real en todo el backend.

Caso de cambio de día: un Yape de las 23:50 (Lima) del 25-nov que la app
recupera y el servidor recibe a las 00:10 del 26-nov pertenece al 25-nov en
"Pagos de hoy", el CSV, el webhook pago.confirmado, la consulta y el push.

Reusa la base de prueba de tests/test_multiempresa.py (SQLite en memoria).

Ejecutar:  python -m unittest tests.test_hora_real_pagos
"""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.test_multiempresa import YAPE, Base_  # noqa: E402

from app.api.models_webhook import NotificacionRaw, PagoDetectado  # noqa: E402
from app.api.push_service import formatear_push_pago  # noqa: E402
from app.services import eventos  # noqa: E402
from app.services.conciliacion import consultar_pago  # noqa: E402

# 23:50 Lima del 25-nov = 04:50 UTC del 26-nov; 00:10 Lima del 26-nov = 05:10 UTC.
PAGO_UTC = datetime(2026, 11, 26, 4, 50, 0)
RECEPCION_UTC = datetime(2026, 11, 26, 5, 10, 0)
TEXTO = "Rosa Quispe te envió un pago por S/ 23.50. El cód. de seguridad es: 418"


def _ms(dt: datetime) -> int:
    return int((dt - datetime(1970, 1, 1)).total_seconds() * 1000)


def _reloj(utc_fijo: datetime):
    """Subclase de datetime con utcnow()/now() fijos (UTC naive de referencia)."""
    class _DT(datetime):
        @classmethod
        def utcnow(cls):
            return utc_fijo

        @classmethod
        def now(cls, tz=None):
            aware = utc_fijo.replace(tzinfo=timezone.utc)
            return aware.astimezone(tz) if tz else utc_fijo
    return _DT


class TestCambioDeDia(Base_):
    def _inbound_recuperado(self, ts_celular=PAGO_UTC, recepcion=RECEPCION_UTC):
        with mock.patch("app.api.notificaciones.datetime", _reloj(recepcion)), \
                mock.patch("app.services.eventos.despertar_worker"):
            r = self.cliente().post(
                "/api/v1/notificaciones/inbound", headers={"X-Device-Token": "tok_a"},
                json={"package": YAPE, "titulo": "Confirmación de Pago", "texto": TEXTO,
                      "timestamp_celular": _ms(ts_celular), "sbn_key": "0|yape|recuperado|1"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIsNotNone(r.json()["pago_id"])
        self.db.expire_all()
        return self.db.get(PagoDetectado, r.json()["pago_id"])

    def test_guarda_hora_del_pago_y_hora_de_recepcion(self):
        pago = self._inbound_recuperado()
        self.assertEqual(pago.recibido_en, PAGO_UTC)
        self.assertEqual(pago.monto, Decimal("23.50"))
        raw = self.db.get(NotificacionRaw, pago.notificacion_raw_id)
        self.assertEqual(raw.timestamp_servidor, RECEPCION_UTC)
        self.assertEqual(raw.timestamp_celular, _ms(PAGO_UTC))

    def test_pagos_de_hoy_lo_cuenta_en_el_dia_anterior(self):
        self._inbound_recuperado()
        c = self.login("+51911111111")
        # 25-nov 23:55 Lima: está en "Pagos de hoy".
        with mock.patch("app.api.router_panel.datetime", _reloj(datetime(2026, 11, 26, 4, 55))):
            hoy = c.get("/api/v1/panel/pagos/hoy").json()
        self.assertIn("Rosa Quispe", [p["contraparte"] for p in hoy["pagos"]])
        # 26-nov 00:15 Lima: ya no (no pertenece al día de la recepción).
        with mock.patch("app.api.router_panel.datetime", _reloj(datetime(2026, 11, 26, 5, 15))):
            hoy = c.get("/api/v1/panel/pagos/hoy").json()
        self.assertNotIn("Rosa Quispe", [p["contraparte"] for p in hoy["pagos"]])

    def test_csv_lo_pone_el_25(self):
        self._inbound_recuperado()
        c = self.login("+51911111111")
        csv_25 = c.get("/api/v1/panel/reportes/movimientos.csv?desde=2026-11-25&hasta=2026-11-25").text
        csv_26 = c.get("/api/v1/panel/reportes/movimientos.csv?desde=2026-11-26&hasta=2026-11-26").text
        self.assertIn("Rosa Quispe", csv_25)
        self.assertIn("2026-11-25T23:50:00-05:00", csv_25)
        self.assertNotIn("Rosa Quispe", csv_26)

    def test_webhook_push_y_consulta_usan_la_hora_del_pago(self):
        pago = self._inbound_recuperado()
        datos = eventos.armar_datos_pago_confirmado(pago)
        self.assertEqual(datos["fecha_hora_banco"], "2026-11-25T23:50:00-05:00")
        self.assertIn("23:50", formatear_push_pago(pago)[1])
        r = consultar_pago(self.db, empresa_id=self.a.id, cuenta_id=None, monto=Decimal("23.50"),
                           fecha_hora_declarada=datetime(2026, 11, 25, 23, 48,
                                                         tzinfo=timezone(timedelta(hours=-5))),
                           ventana_minutos=15)
        self.assertEqual((r["nivel"], r["pago"]["pago_id"]), ("alta", pago.id))

    def test_recuperado_a_las_7_horas_usa_recepcion(self):
        pago = self._inbound_recuperado(ts_celular=RECEPCION_UTC - timedelta(hours=7))
        self.assertEqual(pago.recibido_en, RECEPCION_UTC)

    def test_timestamp_futuro_usa_recepcion(self):
        pago = self._inbound_recuperado(ts_celular=RECEPCION_UTC + timedelta(minutes=10))
        self.assertEqual(pago.recibido_en, RECEPCION_UTC)


if __name__ == "__main__":
    unittest.main()
