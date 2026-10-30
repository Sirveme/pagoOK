"""Lógica de la alerta de caída de celular: un aviso por caída y uno por regreso.

Ejecutar:  python -m unittest tests.test_alerta_dispositivos
"""
import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "postgresql://dummy:dummy@localhost/dummy")

from app.services.alerta_dispositivos import (  # noqa: E402
    evaluar, ultimo_contacto, revisar_con_lock, UMBRAL_CAIDA,
)

AHORA = datetime(2026, 10, 30, 16, 0, 0)


# --- Fakes mínimos para probar el advisory lock sin PostgreSQL ---
class _FakeResult:
    def __init__(self, val):
        self._val = val

    def scalar(self):
        return self._val


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *a, **k):
        return self

    def all(self):
        return self._rows


class _FakeDB:
    """Imita lo justo de una Session: el SELECT del lock y un query vacío."""
    def __init__(self, lock_val, dispositivos=None):
        self.lock_val = lock_val
        self._disp = dispositivos or []
        self.commits = 0

    def execute(self, *a, **k):
        return _FakeResult(self.lock_val)       # resultado de pg_try_advisory_xact_lock

    def query(self, _model):
        return _FakeQuery(self._disp)

    def get(self, _model, _pk):
        return None

    def commit(self):
        self.commits += 1


class EvaluarTest(unittest.TestCase):
    def test_caida_cuando_no_hay_senal_hace_mas_de_30_min_y_no_se_ha_avisado(self):
        ultimo = AHORA - timedelta(minutes=31)
        self.assertEqual(evaluar(True, ultimo, None, AHORA), "caida")

    def test_caida_cuando_nunca_hubo_senal(self):
        self.assertEqual(evaluar(True, None, None, AHORA), "caida")

    def test_no_repite_el_aviso_si_ya_se_avisó_la_caida(self):
        ultimo = AHORA - timedelta(hours=2)
        self.assertIsNone(evaluar(True, ultimo, AHORA - timedelta(minutes=90), AHORA))

    def test_regreso_cuando_vuelve_la_senal_y_habia_caida_avisada(self):
        ultimo = AHORA - timedelta(minutes=1)
        self.assertEqual(evaluar(True, ultimo, AHORA - timedelta(minutes=40), AHORA), "regreso")

    def test_sin_accion_si_esta_conectado_y_no_habia_caida(self):
        ultimo = AHORA - timedelta(minutes=2)
        self.assertIsNone(evaluar(True, ultimo, None, AHORA))

    def test_dispositivo_inactivo_nunca_alerta(self):
        self.assertIsNone(evaluar(False, None, None, AHORA))

    def test_justo_en_el_umbral_todavia_no_es_caida(self):
        self.assertIsNone(evaluar(True, AHORA - UMBRAL_CAIDA, None, AHORA))


class UltimoContactoTest(unittest.TestCase):
    def test_toma_la_senal_mas_reciente(self):
        ping = AHORA - timedelta(minutes=10)
        hb = AHORA - timedelta(minutes=3)
        self.assertEqual(ultimo_contacto(ping, hb), hb)

    def test_ninguna_senal(self):
        self.assertIsNone(ultimo_contacto(None, None))


class AdvisoryLockTest(unittest.TestCase):
    """Con varios workers, solo el que toma el lock revisa; el otro lo salta."""

    def _enviar_que_falla_si_se_llama(self, *a, **k):
        raise AssertionError("no debería enviarse nada sin ser líder")

    def test_no_revisa_si_otro_worker_tiene_el_lock(self):
        db = _FakeDB(lock_val=False)
        out = revisar_con_lock(db, ahora=AHORA, enviar=self._enviar_que_falla_si_se_llama)
        self.assertEqual(out["lider"], False)
        self.assertEqual(out["caidas"], 0)
        self.assertEqual(db.commits, 0)   # ni siquiera abrió la revisión

    def test_revisa_si_es_el_lider(self):
        db = _FakeDB(lock_val=True, dispositivos=[])   # sin dispositivos -> nada que avisar
        out = revisar_con_lock(db, ahora=AHORA, enviar=self._enviar_que_falla_si_se_llama)
        self.assertEqual(out["lider"], True)
        self.assertEqual(out["caidas"], 0)
        self.assertEqual(out["regresos"], 0)
        self.assertEqual(db.commits, 1)   # revisó y cerró su transacción


if __name__ == "__main__":
    unittest.main()
