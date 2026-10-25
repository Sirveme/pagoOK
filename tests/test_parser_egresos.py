"""Tests del parser: separación ingreso/egreso, titular, moneda (ejemplos reales).

Ejecutar:  python -m unittest tests.test_parser_egresos
"""
import os
import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DATABASE_URL", "postgresql://dummy:dummy@localhost/dummy")

from app.api.parser import parsear  # noqa: E402


class ParserEgresosTest(unittest.TestCase):
    # ---------- INGRESOS ----------
    def test_ingreso_plin_interbank(self):
        r = parsear("com.interbank", "Plin", "DUILIA RESTUCCIA te ha plineado S/ 2.00")
        self.assertIsNotNone(r)
        self.assertEqual(r["metodo"], "plin")
        self.assertEqual(r["tipo"], "ingreso")
        self.assertEqual(r["monto"], Decimal("2.00"))
        self.assertEqual(r["moneda"], "PEN")
        self.assertFalse(r["tipo_incierto"])

    def test_ingreso_plin_nombre_completo(self):
        r = parsear("com.interbank", "Plin", "Duilio Cesar Restuccia Eslava te ha plineado S/ 1.00")
        self.assertEqual(r["tipo"], "ingreso")
        self.assertIn("Restuccia", r["titular"])

    def test_ingreso_yape_envio_pago(self):
        r = parsear("com.bcp.innovacxion.yapeapp", "Yape!", "Yape! DUILIO RESTUCCIA te envió un pago por S/ 1")
        self.assertEqual(r["metodo"], "yape")
        self.assertEqual(r["tipo"], "ingreso")
        self.assertEqual(r["monto"], Decimal("1"))
        self.assertFalse(r["tipo_incierto"])

    # ---------- EGRESOS ----------
    def test_egreso_plin_con_titular(self):
        r = parsear("com.interbank", "Plin", "Plineaste S/ 1.00 a Duilio C Restuccia E")
        self.assertEqual(r["metodo"], "plin")
        self.assertEqual(r["tipo"], "egreso")
        self.assertEqual(r["monto"], Decimal("1.00"))
        self.assertIn("Restuccia", r["titular"])
        self.assertFalse(r["tipo_incierto"])

    def test_egreso_tarjeta_recurrente_usd(self):
        r = parsear(
            "com.bcp", "Notificación",
            "Se realizó un pago recurrente de $ 100.00 en ANTHROPIC* CLAUDE SUB con tu Tarjeta de Débito",
        )
        self.assertIsNotNone(r)
        self.assertEqual(r["tipo"], "egreso")
        self.assertEqual(r["moneda"], "USD")
        self.assertEqual(r["monto"], Decimal("100.00"))
        self.assertIn("ANTHROPIC", (r["titular"] or "").upper())

    def test_egreso_yape_con_titular(self):
        r = parsear("com.bcp.innovacxion.yapeapp", "Yape!", "Yapeaste S/ 8.00 a Maria Lopez")
        self.assertEqual(r["tipo"], "egreso")
        self.assertIn("Maria", r["titular"])

    # ---------- SIN VERBO (dudoso) -> NO es pago ----------
    def test_sin_verbo_no_es_pago(self):
        # Antes: ingreso marcado incierto. Ahora: sin verbo que confirme el
        # ingreso no se registra como pago (queda 'ignorada' en el raw).
        self.assertIsNone(
            parsear("com.bcp.innovacxion.yapeapp", "Yape", "Yape! S/ 5.00 operación 12345678")
        )

    # ---------- PROMOCIONES DE YAPE (casos reales de producción) ----------
    def test_promocion_gana_hasta_no_es_pago(self):
        self.assertIsNone(parsear(
            "com.bcp.innovacxion.yapeapp", "¡Suerte asegurada!🍀",
            "Gana hasta S/3,000 afiliándote a Dinero Más Seguro🔒¡Todos ganan!",
        ))

    def test_promocion_participa_no_es_pago(self):
        self.assertIsNone(parsear(
            "com.bcp.innovacxion.yapeapp", "Tu dinero seguro a S/ 4.99 al mes💜",
            "Protege tus cuentas Yape y BCP y participa por S/5,000. T&C.",
        ))

    # ---------- MONTO CON PUNTO FINAL (casos reales: raw 95, 102) ----------
    def test_monto_un_decimal_seguido_de_punto(self):
        r = parsear(
            "com.bcp.innovacxion.yapeapp", "Confirmación de Pago",
            "Sasha Sal* te envió un pago por S/ 5.5. El cód. de seguridad es: 667",
        )
        self.assertEqual(r["monto"], Decimal("5.5"))
        self.assertEqual(r["tipo"], "ingreso")
        self.assertFalse(r["tipo_incierto"])

    def test_monto_entero_seguido_de_punto_sigue_igual(self):
        r = parsear(
            "com.bcp.innovacxion.yapeapp", "Confirmación de Pago",
            "Juan Perez te envió un pago por S/ 50. El cód. de seguridad es: 552",
        )
        self.assertEqual(r["monto"], Decimal("50"))

    def test_monto_con_miles_seguido_de_punto(self):
        r = parsear(
            "com.bcp.innovacxion.yapeapp", "Confirmación de Pago",
            "Juan Perez te envió un pago por S/ 1,500.00. El cód. de seguridad es: 123",
        )
        self.assertEqual(r["monto"], Decimal("1500.00"))

    # ---------- PLIN EN PASIVA (caso real: raw 146) ----------
    def test_te_plinearon_es_ingreso_cierto(self):
        r = parsear("pe.com.interbank.mobilebanking", "Interbank", "Te plinearon S/ 1.00")
        self.assertEqual(r["monto"], Decimal("1.00"))
        self.assertEqual(r["tipo"], "ingreso")
        self.assertFalse(r["tipo_incierto"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
