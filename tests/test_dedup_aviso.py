"""zWeb-16: dedupe por empresa + sbn_key + hora del celular.

Incidente 26-nov (equipo #20): Interbank reutiliza la clave
"0|pe.com.interbank.mobilebanking|0|null|10304" (id=0, tag=null). El dedupe solo
por clave descartó un Plin nuevo de S/ 1.05 como duplicado del Plin del día anterior.

Reusa la base de prueba de tests/test_multiempresa.py (SQLite en memoria).

Ejecutar:  python -m unittest tests.test_dedup_aviso
"""
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.test_multiempresa import Base_  # noqa: E402

from app.api.models_webhook import NotificacionRaw, PagoDetectado  # noqa: E402

INTERBANK = "pe.com.interbank.mobilebanking"
CLAVE_NULL = "0|pe.com.interbank.mobilebanking|0|null|10304"
NOMBRE = "ANA MARIA TORRES PAZ"
T0 = int((datetime(2026, 11, 25, 23, 42, 12) - datetime(1970, 1, 1)).total_seconds() * 1000)
UN_DIA = 24 * 3600 * 1000


def _texto(monto):
    return f"{NOMBRE} te ha plineado S/ {monto}"


class _Base(Base_):
    def setUp(self):
        super().setUp()
        self.c = self.cliente()
        p = mock.patch("app.services.eventos.despertar_worker")
        p.start()
        self.addCleanup(p.stop)

    def inbound(self, texto, ts=None, clave=CLAVE_NULL, token="tok_a"):
        cuerpo = {"package": INTERBANK, "titulo": "Interbank", "texto": texto, "sbn_key": clave}
        if ts is not None:
            cuerpo["timestamp_celular"] = ts
        r = self.c.post("/api/v1/notificaciones/inbound", headers={"X-Device-Token": token}, json=cuerpo)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def no_reconocido(self, texto, ts=None, clave=CLAVE_NULL):
        cuerpo = {"package": INTERBANK, "titulo": "Interbank", "texto": texto, "sbn_key": clave}
        if ts is not None:
            cuerpo["timestamp_celular"] = ts
        r = self.c.post("/api/v1/device/no-reconocido", headers={"X-Device-Token": "tok_a"}, json=cuerpo)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def montos(self):
        db = self.Session()
        try:
            return sorted(str(p.monto) for p in db.query(PagoDetectado)
                          .filter(PagoDetectado.empresa_id == self.a.id, PagoDetectado.notificacion_raw_id.isnot(None)))
        finally:
            db.close()


class TestDedupInbound(_Base):
    def test_incidente_26_nov_clave_reutilizada_con_otra_hora_es_pago_nuevo(self):
        self.assertEqual(self.inbound(_texto("1.00"), T0)["status"], "ok")
        r = self.inbound(_texto("1.05"), T0 + UN_DIA - 3600 * 1000)
        self.assertEqual(r["status"], "ok")
        self.assertIsNotNone(r["pago_id"])
        self.assertEqual(self.montos(), ["1.00", "1.05"])

    def test_mismo_texto_mismo_monto_a_otra_hora_es_pago_nuevo(self):
        self.inbound(_texto("1.00"), T0)
        self.assertEqual(self.inbound(_texto("1.00"), T0 + 30 * 60 * 1000)["status"], "ok")
        self.assertEqual(self.montos(), ["1.00", "1.00"])

    def test_reintento_con_la_misma_hora_es_duplicado(self):
        primero = self.inbound(_texto("1.00"), T0)
        r = self.inbound(_texto("1.00"), T0)
        self.assertEqual(r, {"status": "duplicado", "raw_id": primero["raw_id"]})
        # Aunque llegue como texto (algunos clientes mandan el número como string).
        self.assertEqual(self.inbound(_texto("1.00"), str(T0))["status"], "duplicado")
        self.assertEqual(self.montos(), ["1.00"])

    def test_mismo_texto_a_menos_de_60_s_es_duplicado(self):
        self.inbound(_texto("1.00"), T0)
        self.assertEqual(self.inbound(_texto("1.00"), T0 + 20_000)["status"], "duplicado")
        self.assertEqual(self.inbound(_texto("1.00"), T0 - 59_000)["status"], "duplicado")
        self.assertEqual(self.inbound(_texto("1.00"), T0 + 60_000)["status"], "ok")   # borde: ya no

    def test_otro_texto_a_menos_de_60_s_es_pago_nuevo(self):
        self.inbound(_texto("1.00"), T0)
        self.assertEqual(self.inbound(_texto("1.05"), T0 + 20_000)["status"], "ok")
        self.assertEqual(self.montos(), ["1.00", "1.05"])

    def test_sin_hora_del_celular_se_mantiene_la_regla_anterior(self):
        self.inbound(_texto("1.00"), T0)
        self.assertEqual(self.inbound(_texto("1.05"))["status"], "duplicado")
        self.assertEqual(self.inbound(_texto("1.05"), "no-es-hora")["status"], "duplicado")
        self.assertEqual(self.montos(), ["1.00"])

    def test_previo_sin_hora_y_otro_texto_es_pago_nuevo(self):
        self.inbound(_texto("1.00"))                     # app vieja, sin hora
        self.assertEqual(self.inbound(_texto("1.00"), T0)["status"], "duplicado")   # mismo texto
        self.assertEqual(self.inbound(_texto("1.05"), T0)["status"], "ok")
        self.assertEqual(self.montos(), ["1.00", "1.05"])

    def test_sin_clave_nunca_es_duplicado(self):
        self.inbound(_texto("1.00"), T0, clave=None)
        self.assertEqual(self.inbound(_texto("1.00"), T0, clave=None)["status"], "ok")

    def test_la_clave_de_otra_empresa_no_cuenta(self):
        self.inbound(_texto("1.00"), T0, token="tok_b")
        self.assertEqual(self.inbound(_texto("1.00"), T0)["status"], "ok")

    def test_warning_sin_texto_cuando_chocan_claves(self):
        primero = self.inbound(_texto("1.00"), T0)
        with self.assertLogs("pagook", level="WARNING") as cap:
            self.inbound(_texto("1.05"), T0 + 3600 * 1000)
        avisos = [m for m in cap.output if "Clave sbn_key repetida" in m]
        self.assertEqual(len(avisos), 1, cap.output)
        self.assertIn(f"raw_previo={primero['raw_id']}", avisos[0])
        self.assertIn("diferencia_s=3600", avisos[0])
        for prohibido in ("ANA", "TORRES", "plineado", "1.05"):
            self.assertNotIn(prohibido, avisos[0])

    def test_sin_warning_en_un_reintento_normal(self):
        self.inbound(_texto("1.00"), T0)
        with mock.patch("app.services.dedup_aviso.logger") as log:
            self.inbound(_texto("1.00"), T0)
        log.warning.assert_not_called()


class TestDedupNoReconocido(_Base):
    def test_clave_reutilizada_con_otra_hora_se_guarda(self):
        self.assertEqual(self.no_reconocido("Formato nuevo S/ 1", T0)["status"], "guardado")
        self.assertEqual(self.no_reconocido("Formato nuevo S/ 2", T0 + UN_DIA)["status"], "guardado")
        db = self.Session()
        try:
            self.assertEqual(db.query(NotificacionRaw).filter_by(estado_parseo="no_reconocido").count(), 2)
        finally:
            db.close()

    def test_reintento_y_actualizacion_son_duplicado(self):
        self.no_reconocido("Formato nuevo S/ 1", T0)
        self.assertEqual(self.no_reconocido("Formato nuevo S/ 1", T0)["status"], "duplicado")
        self.assertEqual(self.no_reconocido("Formato nuevo S/ 1", T0 + 30_000)["status"], "duplicado")

    def test_sin_hora_regla_anterior(self):
        self.no_reconocido("Formato nuevo S/ 1", T0)
        self.assertEqual(self.no_reconocido("Formato nuevo S/ 9")["status"], "duplicado")

    def test_dedupe_cruzado_entre_inbound_y_no_reconocido(self):
        # Mismo aviso reenviado por el otro camino con la misma hora: no se duplica.
        self.inbound(_texto("1.00"), T0)
        self.assertEqual(self.no_reconocido(_texto("1.00"), T0)["status"], "duplicado")


if __name__ == "__main__":
    unittest.main()
