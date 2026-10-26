"""Tests offline: formatos reales de Interbank como egresos.

  - Aviso push de la app: "TRAN S/ 10.00 / A ****534 / (25/10 08:48:33)" (sin título).
  - Correo "Constancia de transferencia" de servicioalcliente@netinterbank.com.pe.
  - Son la misma operación: en cualquier orden queda un solo registro.

Datos anonimizados. DKIM se reemplaza por un verificador falso (sin DNS).
Ejecutar:  python -m unittest tests.test_interbank
"""
import sys
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.test_correo_bancos import INTERBANK, LIMA, BaseCorreo  # noqa: E402

from app.api.models_multiempresa import BancoConfig  # noqa: E402
from app.api.models_webhook import NotificacionRaw, PagoDetectado  # noqa: E402
from app.api.parser import parsear  # noqa: E402
from app.services.parser_correo import parsear_correo  # noqa: E402

DOMINIO_INTERBANK = "netinterbank.com.pe"
REMITENTE = "Interbank <servicioalcliente@netinterbank.com.pe>"
MESES_ABREV = ["", "Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Set", "Oct", "Nov", "Dic"]

# ---- Aviso push REAL (25/10/2026 20:48:33 Lima) ----
PUSH_TRAN = "TRAN S/ 10.00\nA ****534\n(25/10 08:48:33)"

# ---- Correo REAL (nombre y cuentas anonimizados) ----
CORREO_TRANSFERENCIA = """Hola JUAN, te enviamos tu
Constancia de transferencia
Código de operación
01831239
Fecha y hora
25 Oct 2026 08:48 PM
Cuenta a cargo
Cuenta Simple
740 ********716
Cuenta destino
ANA TORRES
002390********05334
Tipo de operación
Transferencia inmediata
Monto y moneda
S/ 10.00
Comisión
S/ 0.00
Monto total
S/ 10.00"""


def fecha_interbank(delta_min=0):
    t = datetime.now(LIMA) + timedelta(minutes=delta_min)
    return f"{t.day} {MESES_ABREV[t.month]} {t.year} {t.hour % 12 or 12:02d}:{t.minute:02d} {'AM' if t.hour < 12 else 'PM'}"


class TestPushInterbank(unittest.TestCase):
    def test_tran_es_transferencia_enviada(self):
        r = parsear(INTERBANK, "", PUSH_TRAN)
        self.assertEqual((r["tipo"], r["tipo_egreso"], r["metodo"], r["banco"]),
                         ("egreso", "transferencia_enviada", "transferencia", "Interbank"))
        self.assertEqual((r["monto"], r["moneda"], r["titular"]), (Decimal("10.00"), "PEN", "****534"))
        self.assertIsNone(r["tarjeta_ultimos4"])     # 534 es la cuenta DESTINO, no la de origen
        self.assertIsNone(r["codigo_operacion"])

    def test_tran_en_una_sola_linea_y_en_dolares(self):
        r = parsear(INTERBANK, None, "TRAN US$ 25.50 A ****1234 (25/10 08:48:33)")
        self.assertEqual((r["tipo"], r["monto"], r["moneda"]), ("egreso", Decimal("25.50"), "USD"))

    def test_tran_no_se_confunde_con_ingreso_ni_promocion(self):
        # Fuera de la app de Interbank no es este formato.
        self.assertIsNone(parsear("com.otra.app", "", PUSH_TRAN))
        # Con señal de ingreso nunca es egreso.
        r = parsear(INTERBANK, "", "TRAN S/ 10.00 A ****534. Recibiste una transferencia")
        self.assertTrue(r is None or r["tipo"] == "ingreso")
        # Promociones de Interbank con monto: no son pagos.
        self.assertIsNone(parsear(INTERBANK, "", "Gana S/ 10.00 en tu próxima TRAN. Participa ya"))
        self.assertIsNone(parsear(INTERBANK, "", "TRANSFIERE y gana S/ 10.00"))


class TestCorreoInterbank(unittest.TestCase):
    def test_constancia_de_transferencia(self):
        r = parsear_correo("interbank", "Constancia de transferencia", CORREO_TRANSFERENCIA)
        self.assertEqual((r["estado"], r["tipo"]), ("procesado", "egreso"))
        d = r["datos"]
        self.assertEqual((d["tipo_egreso"], d["metodo"]), ("transferencia_enviada", "transferencia"))
        self.assertEqual((d["monto"], d["moneda"], d["comision"]), (Decimal("10.00"), "PEN", Decimal("0.00")))
        self.assertEqual((d["codigo_operacion"], d["contraparte"]), ("01831239", "ANA TORRES"))
        self.assertEqual(d["fecha_utc"], datetime(2026, 10, 26, 1, 48))   # 20:48 Lima = 01:48 UTC
        # Solo dígitos finales visibles, máximo 4: nunca el "740" del inicio.
        self.assertEqual((d["ultimos4"], d["destino"]), ("716", "Cuenta ****5334"))

    def test_otro_correo_de_interbank_queda_sin_formato(self):
        r = parsear_correo("interbank", "Aviso", "Hola JUAN\nTu estado de cuenta ya está disponible")
        self.assertEqual(r["estado"], "sin_formato")


class TestDuplicadosInterbank(BaseCorreo):
    def setUp(self):
        super().setUp()
        self.db.add(BancoConfig(pais="PE", clave="interbank", nombre="Interbank", tipo="banco", orden=2,
                                dominios_correo=[DOMINIO_INTERBANK], paquetes_android=[INTERBANK]))
        self.db.commit()

    def _push(self):
        with mock.patch("app.api.push_service.enviar_push_a_empresa", return_value={}):
            r = self.cliente().post("/api/v1/notificaciones/inbound", headers={"X-Device-Token": "tok_psp"},
                                    json={"package": INTERBANK, "titulo": None, "texto": PUSH_TRAN})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def _correo(self, delta_min, empresa_dir=None):
        cuerpo = CORREO_TRANSFERENCIA.replace("25 Oct 2026 08:48 PM", fecha_interbank(delta_min))
        return self.procesar(empresa_dir or self.dir_psp, cuerpo, dominios=(DOMINIO_INTERBANK,),
                             remitente=REMITENTE, asunto="Constancia de transferencia")

    def _egresos(self):
        self.db.expire_all()
        return self.db.query(PagoDetectado).filter_by(empresa_id=self.psp.id, tipo="egreso").all()

    def test_primero_push_luego_correo(self):
        n = self._push()
        self.assertEqual(n["estado"], "parseada")
        c = self._correo(delta_min=0)
        self.assertEqual(c.estado, "procesado")
        egresos = self._egresos()
        self.assertEqual(len(egresos), 1)
        p = egresos[0]
        self.assertEqual((c.pago_id, p.id, p.origen, p.notificacion_raw_id), (n["pago_id"], n["pago_id"], "notificacion", n["raw_id"]))
        self.assertEqual((p.tipo_egreso, p.codigo_operacion), ("transferencia_enviada", "01831239"))  # completado por el correo
        self.assertEqual((p.tarjeta_ultimos4, p.destino), ("716", "Cuenta ****5334"))

    def test_primero_correo_luego_push(self):
        c = self._correo(delta_min=-1)
        n = self._push()
        egresos = self._egresos()
        self.assertEqual(len(egresos), 1)
        p = egresos[0]
        self.assertEqual((p.id, p.origen, p.notificacion_raw_id, n["pago_id"]), (c.pago_id, "correo", n["raw_id"], c.pago_id))
        self.assertEqual((p.tipo_egreso, p.titular, p.banco), ("transferencia_enviada", "ANA TORRES", "Interbank"))
        self.assertEqual(self.db.get(NotificacionRaw, n["raw_id"]).estado_parseo, "parseada")

    def test_correo_de_interbank_sin_dominio_registrado_se_rechaza(self):
        c = self.procesar(self.dir_psp, CORREO_TRANSFERENCIA, dominios=("netinterbank-falso.com",), remitente=REMITENTE)
        self.assertEqual(c.estado, "rechazado")
        self.assertEqual(self._egresos(), [])


if __name__ == "__main__":
    unittest.main()
