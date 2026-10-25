"""Tests de detección de cuentas propias (posible_interno).

SQLite en memoria, sin Postgres. Ejecutar:
  python -m unittest tests.test_deteccion_interno
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DATABASE_URL", "postgresql://dummy:dummy@localhost/dummy")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.admin.db import Base  # noqa: E402
import app.admin.models  # noqa: E402,F401
import app.api.models_webhook  # noqa: E402,F401
from app.admin.models import Empresa, Persona  # noqa: E402
from app.api.models_internos import PersonaCuenta, TitularConfirmadoExterno  # noqa: E402
from app.services.deteccion_interno import es_posible_interno, normalizar  # noqa: E402


class DeteccionInternoTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        self.Session = sessionmaker(bind=self.engine, autoflush=False)
        Base.metadata.create_all(self.engine)

        db = self.Session()
        try:
            duenio = Persona(
                pais_codigo="PE", id_fiscal="41234567",
                nombre_completo="Duilio Cesar Restuccia Eslava", es_duenio=True,
            )
            db.add(duenio)
            db.flush()
            emp = Empresa(
                pais_codigo="PE", id_fiscal="20100000001",
                razon_social="Perú Sistemas Pro", slug="perusistemas",
                duenio_id=duenio.id,
            )
            db.add(emp)
            db.flush()
            self.empresa_id = emp.id
            self.persona_id = duenio.id

            for nombre, bille, banco in [
                ("DUILIO RESTUCCIA", "yape", "bcp"),
                ("Duilio Cesar Restuccia Eslava", "plin", "interbank"),
                ("Duilio C Restuccia E", "plin", "interbank"),
                ("D. C. Restuccia E.", "plin", "interbank"),
            ]:
                db.add(PersonaCuenta(
                    persona_id=duenio.id, empresa_id=emp.id,
                    nombre_en_push=nombre, nombre_normalizado=normalizar(nombre),
                    billetera=bille, banco=banco,
                ))
            db.commit()
        finally:
            db.close()

    def tearDown(self):
        Base.metadata.drop_all(self.engine)

    def test_match_exacto(self):
        db = self.Session()
        try:
            self.assertTrue(es_posible_interno(self.empresa_id, "Duilio Cesar Restuccia Eslava", db))
            # Con puntos/tildes distintos pero mismo normalizado:
            self.assertTrue(es_posible_interno(self.empresa_id, "D. C. Restuccia E.", db))
        finally:
            db.close()

    def test_familiar_iniciales_apellido(self):
        # "Duilia Restuccia" (hermana ficticia): misma inicial + apellido -> sugiere.
        db = self.Session()
        try:
            self.assertTrue(es_posible_interno(self.empresa_id, "Duilia Restuccia", db))
        finally:
            db.close()

    def test_confirmado_externo_no_vuelve_a_sugerir(self):
        db = self.Session()
        try:
            db.add(TitularConfirmadoExterno(
                empresa_id=self.empresa_id, nombre_normalizado=normalizar("Duilia Restuccia"),
            ))
            db.commit()
            self.assertFalse(es_posible_interno(self.empresa_id, "Duilia Restuccia", db))
        finally:
            db.close()

    def test_desconocido_no_es_interno(self):
        db = self.Session()
        try:
            self.assertFalse(es_posible_interno(self.empresa_id, "Empresa Cliente SAC", db))
            self.assertFalse(es_posible_interno(self.empresa_id, "", db))
        finally:
            db.close()

    def test_normalizacion(self):
        self.assertEqual(normalizar("D. C. Restuccia E."), "d c restuccia e")
        self.assertEqual(normalizar("Duilío  Césâr"), "duilio cesar")


if __name__ == "__main__":
    unittest.main(verbosity=2)
