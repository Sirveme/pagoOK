"""`recibido_en` (hora del pago) usa la hora del celular si es válida y razonable:
no futura (tolerancia +2 min) y no más vieja que 6 h; si no, la del servidor (zWeb-15).

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

    def test_en_vivo_usa_celular(self):
        # El aviso llegó al celular 13 s antes de que el servidor lo recibiera: se usa esa hora.
        celular = self.servidor - timedelta(seconds=13)
        self.assertEqual(recibido_en_para(_ms(celular), self.servidor), celular)

    def test_aviso_de_yape_entregado_tarde_usa_celular(self):
        # Antes (regla de 5 min) usaba la del servidor; la del celular es más cercana al pago.
        celular = self.servidor - timedelta(minutes=12)
        self.assertEqual(recibido_en_para(_ms(celular), self.servidor), celular)

    def test_recuperado_a_las_4_horas_conserva_su_hora(self):
        celular = self.servidor - timedelta(hours=4)
        self.assertEqual(recibido_en_para(_ms(celular), self.servidor), celular)

    def test_recuperado_a_las_7_horas_usa_recepcion(self):
        celular = self.servidor - timedelta(hours=7)
        self.assertEqual(recibido_en_para(_ms(celular), self.servidor), self.servidor)

    def test_borde_de_6_horas(self):
        justo = self.servidor - timedelta(hours=6)
        casi = justo + timedelta(seconds=1)
        self.assertEqual(recibido_en_para(_ms(justo), self.servidor), self.servidor)
        self.assertEqual(recibido_en_para(_ms(casi), self.servidor), casi)

    def test_reloj_adelantado_dentro_de_tolerancia_usa_servidor(self):
        # Adelantado 20 s / 2 min: se acepta como "ahora", pero nunca se guarda una hora futura.
        for adelanto in (timedelta(seconds=20), timedelta(seconds=30), timedelta(minutes=2)):
            celular = self.servidor + adelanto
            self.assertEqual(recibido_en_para(_ms(celular), self.servidor), self.servidor, adelanto)

    def test_timestamp_futuro_usa_recepcion(self):
        for adelanto in (timedelta(minutes=3), timedelta(hours=5), timedelta(days=400)):
            celular = self.servidor + adelanto
            self.assertEqual(recibido_en_para(_ms(celular), self.servidor), self.servidor, adelanto)

    def test_timestamp_invalido_usa_recepcion(self):
        for valor in (None, 0, "", "abc", "nan", -1, 10 ** 20, [1]):
            self.assertEqual(recibido_en_para(valor, self.servidor), self.servidor, repr(valor))

    def test_timestamp_en_texto_valido(self):
        # Algún cliente lo manda como string: se acepta si es un número válido.
        celular = self.servidor - timedelta(minutes=30)
        self.assertEqual(recibido_en_para(str(_ms(celular)), self.servidor), celular)


if __name__ == "__main__":
    unittest.main()
