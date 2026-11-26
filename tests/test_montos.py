"""zWeb-15: el monto siempre sale con 2 decimales y acepta el espacio como
separador de miles ("S/ 1 234.50" -> 1234.50; antes se leía 1).

Ejecutar:  python -m unittest tests.test_montos
"""
import os
import sys
import unittest
from decimal import Decimal

sys.path.insert(0, str(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.setdefault("DATABASE_URL", "postgresql://dummy:dummy@localhost/dummy")

from app.api.parser import _extraer_monto, parsear  # noqa: E402

YAPE = "com.bcp.innovacxion.yapeapp"
INTERBANK = "pe.com.interbank.mobilebanking"
BCP = "com.bcp.bancadigital"


class TestFormatosDeMonto(unittest.TestCase):
    CASOS = [
        ("S/ 1", "1.00"),
        ("S/ 1.0", "1.00"),
        ("S/ 1.00", "1.00"),
        ("S/1.07", "1.07"),
        ("S/. 5.50", "5.50"),
        ("S/.50.00", "50.00"),
        ("S/ 1,234.50", "1234.50"),
        ("S/ 1.234,50", "1234.50"),
        ("S/ 12,50", "12.50"),
        ("S/ 1 234.50", "1234.50"),
        ("S/ 1 234", "1234.00"),
        ("S/ 100 000.00", "100000.00"),
        ("S/ 1 234.50", "1234.50"),   # espacio duro
        ("S/ 1 234.50", "1234.50"),   # espacio fino (Android)
        ("$ 1 234.50", "1234.50"),
        ("$26.19", "26.19"),
    ]

    def test_cada_formato_con_dos_decimales(self):
        for texto, esperado in self.CASOS:
            m = _extraer_monto(f"Juan te envió un pago por {texto}. Gracias")
            self.assertEqual(str(m), esperado, texto)   # str: exige los 2 decimales

    def test_punto_final_de_oracion_no_es_decimal(self):
        self.assertEqual(str(_extraer_monto("te envió un pago por S/ 5. El cód. de seguridad es: 1")), "5.00")
        self.assertEqual(str(_extraer_monto("te envió un pago por S/ 5.5. El cód.")), "5.50")

    def test_espacio_que_no_es_de_miles_no_se_une(self):
        # Solo grupos exactos de 3 dígitos: "S/ 20 2025" o "S/ 1 el 25" no son miles.
        self.assertEqual(str(_extraer_monto("S/ 20 2025")), "20.00")
        self.assertEqual(str(_extraer_monto("S/ 1 el 25/11")), "1.00")

    def test_ambiguo_1_punto_500_se_mantiene(self):
        # Documentado en parser.MONTO_NUM: el punto se toma como decimal (1.50), no 1500.
        self.assertEqual(str(_extraer_monto("S/ 1.500")), "1.50")

    def test_sin_monto(self):
        self.assertIsNone(_extraer_monto("Ingresaste a tu Banca por Internet."))
        self.assertIsNone(_extraer_monto("S/ ."))


class TestTextosReales(unittest.TestCase):
    """Formatos reales de producción (nombres reemplazados)."""

    def _ok(self, package, titulo, texto, monto, tipo="ingreso"):
        r = parsear(package, titulo, texto)
        self.assertIsNotNone(r, texto)
        self.assertEqual((str(r["monto"]), r["tipo"]), (monto, tipo), texto)
        return r

    def test_plin_interbank_26_nov(self):
        # Filas 486-490 (25/26-nov), nombre reemplazado. "S/ 1.0" se guardaba como 1.0; ahora 1.00.
        self._ok(INTERBANK, "Interbank", "ANA MARIA TORRES PAZ te ha plineado S/ 1.00", "1.00")   # 486/487
        self._ok(INTERBANK, "Interbank", "Ana Maria Torres Paz te ha plineado S/ 1.0", "1.00")    # 489
        self._ok(INTERBANK, "Interbank", "ANA MARIA TORRES PAZ te ha plineado S/ 1.07", "1.07")   # 490
        r = self._ok(INTERBANK, "Interbank", "Ana Maria Torres Paz te ha plineado S/ 1 234.50", "1234.50")
        self.assertEqual(r["titular"], "Ana Maria Torres Paz")

    def test_yape(self):
        self._ok(YAPE, "Confirmación de Pago",
                 "Juan Perez te envió un pago por S/ 80. El cód. de seguridad es: 135", "80.00")
        self._ok(YAPE, "Yape!", "Juan Carlos Perez Lopez te yapeó S/ 15.00", "15.00")
        self._ok(YAPE, "Confirmación de Pago",
                 "Juan Perez te envió un pago por S/ 1 500. El cód. de seguridad es: 135", "1500.00")
        r = self._ok(YAPE, "Yape", "Yapeaste S/ 1 234.50 a Juan Perez.", "1234.50", tipo="egreso")
        self.assertEqual(r["titular"], "Juan Perez")

    def test_interbank_egresos(self):
        self._ok(INTERBANK, "Interbank",
                 "Realizaste un consumo de S/.50.00 en TIENDA con tu Tarjeta de Crédito.", "50.00", tipo="egreso")
        r = self._ok(INTERBANK, "", "TRAN S/ 1 234.50\nA ****534\n(25/10 08:48:33)", "1234.50", tipo="egreso")
        self.assertEqual(r["titular"], "****534")
        r = self._ok(INTERBANK, "", "TRAN S/ 10.00\nA ****534\n(25/10 08:48:33)", "10.00", tipo="egreso")
        self.assertEqual(r["titular"], "****534")
        self._ok(INTERBANK, "Plin", "Plineaste S/ 1 000.00 a Ana R Torres P", "1000.00", tipo="egreso")

    def test_bcp(self):
        self._ok(BCP, "BCP", "Recibiste S/ 1 250.00 de Juan Perez", "1250.00")
        self._ok(BCP, "BCP", "Recibiste S/ 50.00 de Juan Perez", "50.00")


if __name__ == "__main__":
    unittest.main()
