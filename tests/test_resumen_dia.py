"""Tests del resumen diario (total vs "del negocio").

SQLite en memoria. Ejecutar:  python -m unittest tests.test_resumen_dia
"""
import os
import sys
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DATABASE_URL", "postgresql://dummy:dummy@localhost/dummy")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.admin.db import Base  # noqa: E402
import app.admin.models  # noqa: E402,F401
from app.admin.models import Empresa  # noqa: E402
from app.api.models_webhook import PagoDetectado  # noqa: E402
from app.services.resumen_empresa import calcular_resumen_dia  # noqa: E402


class ResumenDiaTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        self.Session = sessionmaker(bind=self.engine, autoflush=False)
        Base.metadata.create_all(self.engine)

        # 2026-07-04 15:00 UTC == 2026-07-04 10:00 en Lima (UTC-5): cae en el día.
        cuando = datetime(2026, 7, 4, 15, 0, 0)
        db = self.Session()
        try:
            emp = Empresa(pais_codigo="PE", id_fiscal="20100000001",
                          razon_social="Empresa", slug="emp")
            db.add(emp)
            db.flush()
            self.empresa_id = emp.id

            def pago(tipo, monto, interno=False, conf=None):
                return PagoDetectado(
                    empresa_id=emp.id, metodo="yape", monto=Decimal(monto),
                    tipo=tipo, recibido_en=cuando,
                    posible_interno=interno, confirmacion_usuario=conf,
                )

            db.add_all([
                pago("ingreso", "100.00"),
                pago("ingreso", "50.00", interno=True, conf=None),                 # sin confirmar
                pago("ingreso", "30.00", interno=True, conf="confirmado_interno"), # excluir negocio
                pago("egreso", "20.00"),
                pago("egreso", "10.00", interno=True, conf="confirmado_interno"),  # excluir negocio
            ])
            db.commit()
        finally:
            db.close()

    def tearDown(self):
        Base.metadata.drop_all(self.engine)

    def test_resumen(self):
        db = self.Session()
        try:
            r = calcular_resumen_dia(self.empresa_id, date(2026, 7, 4), db)
        finally:
            db.close()

        self.assertEqual(r["total_ingresos"], Decimal("180.00"))
        self.assertEqual(r["total_egresos"], Decimal("30.00"))
        self.assertEqual(r["diferencia"], Decimal("150.00"))
        # Del negocio: excluye los internos confirmados (30 ingreso, 10 egreso).
        self.assertEqual(r["ingresos_negocio"], Decimal("150.00"))
        self.assertEqual(r["egresos_negocio"], Decimal("20.00"))
        self.assertEqual(r["diferencia_negocio"], Decimal("130.00"))
        self.assertEqual(r["cuenta_ingresos"], 3)
        self.assertEqual(r["cuenta_egresos"], 2)
        self.assertEqual(r["cuenta_internos_sin_confirmar"], 1)

    def test_otro_dia_vacio(self):
        db = self.Session()
        try:
            r = calcular_resumen_dia(self.empresa_id, date(2026, 7, 1), db)
        finally:
            db.close()
        self.assertEqual(r["total_ingresos"], Decimal("0.00"))
        self.assertEqual(r["cuenta_ingresos"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
