"""Tests del titular con caracteres que antes rompían la captura: guion y apóstrofo.

Yape trunca apellidos como 'Pedro De-*' (guion + máscara) y hay apellidos reales
con apóstrofo ('O'Brien'). El guion no estaba en NOMBRE_CHARS y el titular quedaba
vacío (caso real: pago 178, 'Pedro De-* te envió un pago por S/ 35').

Ejecutar:  python -m unittest tests.test_parser_titular
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DATABASE_URL", "postgresql://dummy:dummy@localhost/dummy")

from app.api.parser import parsear  # noqa: E402

PKG = "com.bcp.innovacxion.yapeapp"


class TitularConGuionYApostrofoTest(unittest.TestCase):
    def test_apellido_truncado_con_guion(self):
        # El caso real que dejó el pago 178 sin titular.
        r = parsear(PKG, "Confirmación de Pago",
                    "Pedro De-* te envió un pago por S/ 35. El cód. de seguridad es: 940")
        self.assertIsNotNone(r)
        self.assertEqual(r["titular"], "Pedro De-*")

    def test_apostrofo_recto(self):
        r = parsear(PKG, "Confirmación de Pago", "O'Brien te envió un pago por S/ 50")
        self.assertEqual(r["titular"], "O'Brien")

    def test_apostrofo_tipografico(self):
        r = parsear(PKG, "Confirmación de Pago", "O’Brien te envió un pago por S/ 50")
        self.assertEqual(r["titular"], "O’Brien")

    def test_no_rompe_los_nombres_normales(self):
        # La máscara con asterisco y los nombres completos siguen igual que antes.
        r = parsear(PKG, "Yape!", "Erwin Sch* te envió un pago por S/ 1. El cód. de seguridad es: 660")
        self.assertEqual(r["titular"], "Erwin Sch*")
        r = parsear(PKG, "Yape!", "Yape! DUILIO RESTUCCIA te envió un pago por S/ 1")
        self.assertEqual(r["titular"], "DUILIO RESTUCCIA")


if __name__ == "__main__":
    unittest.main()
