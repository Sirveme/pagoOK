"""`recibido_en` usa la hora del celular solo si es razonable; si no, la del servidor.

Ejecutar:  python -m unittest tests.test_recibido_en
"""
import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, str(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.setdefault("DATABASE_URL", "postgresql://dummy:dummy@localhost/dummy")

from app.api.notificaciones import recibido_en_para  # noqa: E402


def _ms(dt: datetime) -> int:
    """epoch ms tratando el datetime naive como UTC (como lo manda la app)."""
    return int((dt - datetime(1970, 1, 1)).total_seconds() * 1000)


class RecibidoEnTest(unittest.TestCase):
    def setUp(self):
        self.servidor = datetime(2026, 10, 30, 15, 51, 37)  # UTC naive

    def test_usa_celular_si_es_razonable(self):
        # El aviso llegó al celular 13 s antes de que el servidor lo recibiera: se usa esa hora.
        celular = self.servidor - timedelta(seconds=13)
        r = recibido_en_para(_ms(celular), self.servidor)
        self.assertEqual(r, celular)

    def test_usa_servidor_si_celular_es_posterior(self):
        # Reloj del celular adelantado 20 s (posterior al servidor): se descarta, usa servidor.
        celular = self.servidor + timedelta(seconds=20)
        r = recibido_en_para(_ms(celular), self.servidor)
        self.assertEqual(r, self.servidor)

    def test_usa_servidor_si_diferencia_mayor_a_5_min(self):
        # Aviso atrasado (Yape lo entregó tarde): el celular marca mucho antes; usa servidor.
        celular = self.servidor - timedelta(minutes=12)
        r = recibido_en_para(_ms(celular), self.servidor)
        self.assertEqual(r, self.servidor)

    def test_usa_servidor_si_no_hay_timestamp_celular(self):
        self.assertEqual(recibido_en_para(None, self.servidor), self.servidor)
        self.assertEqual(recibido_en_para(0, self.servidor), self.servidor)


if __name__ == "__main__":
    unittest.main()
