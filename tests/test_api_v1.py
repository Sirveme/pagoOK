"""Tests offline de la API pública v1 (sin red, sin Postgres).

Usa SQLite en memoria y sobreescribe la dependencia get_db. No toca la BD viva.
Ejecutar:  python -m unittest tests.test_api_v1   (o python tests/test_api_v1.py)
"""
import os
import sys
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

# Asegurar que el proyecto está en el path y que existe un DATABASE_URL dummy
# ANTES de importar app (db.py lo exige al import; create_engine es perezoso,
# nunca se conecta porque sobreescribimos get_db con SQLite).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DATABASE_URL", "postgresql://dummy:dummy@localhost/dummy")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.admin.db import Base, get_db  # noqa: E402
import app.admin.models  # noqa: E402,F401  (registra tablas en Base.metadata)
from app.api.models_webhook import PagoDetectado  # noqa: E402
from app.api.models_cuenta import CuentaApi, generar_api_key, hash_api_key  # noqa: E402
from app.main import app  # noqa: E402


class ApiV1TestCase(unittest.TestCase):
    def setUp(self):
        # SQLite en memoria compartida entre conexiones del test.
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.Session = sessionmaker(bind=self.engine, autoflush=False)
        Base.metadata.create_all(self.engine)

        # Inyectar la sesión SQLite en la app.
        def _override_get_db():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = _override_get_db
        self.client = TestClient(app)

        self._seed()

    def tearDown(self):
        app.dependency_overrides.clear()
        Base.metadata.drop_all(self.engine)

    def _seed(self):
        """Dos empresas, dos cuentas (A y B), pagos variados."""
        db = self.Session()
        try:
            # Empresas (FK a pais no se aplica en SQLite por defecto)
            from app.admin.models import Empresa
            emp_a = Empresa(pais_codigo="PE", id_fiscal="20100000001",
                            razon_social="Empresa A", slug="emp-a")
            emp_b = Empresa(pais_codigo="PE", id_fiscal="20100000002",
                            razon_social="Empresa B", slug="emp-b")
            db.add_all([emp_a, emp_b])
            db.flush()
            self.emp_a_id, self.emp_b_id = emp_a.id, emp_b.id

            # Cuentas API
            self.key_a = generar_api_key()
            self.key_b = generar_api_key()
            cuenta_a = CuentaApi(empresa_id=emp_a.id, nombre="Cuenta A",
                                 api_key_hash=hash_api_key(self.key_a),
                                 prefijo="pk_a", activa=True)
            cuenta_b = CuentaApi(empresa_id=emp_a.id, nombre="Cuenta B",
                                 api_key_hash=hash_api_key(self.key_b),
                                 prefijo="pk_b", activa=True)
            # Una cuenta inactiva para probar 401
            self.key_off = generar_api_key()
            cuenta_off = CuentaApi(empresa_id=emp_a.id, nombre="Inactiva",
                                   api_key_hash=hash_api_key(self.key_off),
                                   prefijo="pk_off", activa=False)
            db.add_all([cuenta_a, cuenta_b, cuenta_off])
            db.flush()
            self.cuenta_a_id, self.cuenta_b_id = cuenta_a.id, cuenta_b.id

            ahora = datetime(2026, 6, 7, 14, 32, 0)
            # Pagos de empresa A
            p1 = PagoDetectado(empresa_id=emp_a.id, metodo="yape",
                               monto=Decimal("15.00"), titular="Juan Perez",
                               recibido_en=ahora)
            p2 = PagoDetectado(empresa_id=emp_a.id, metodo="plin",
                               monto=Decimal("9.90"), titular="Ana Lopez",
                               recibido_en=ahora - timedelta(hours=2))
            p3 = PagoDetectado(empresa_id=emp_a.id, metodo="transferencia",
                               monto=Decimal("15.00"), banco="BCP", titular="",
                               recibido_en=ahora - timedelta(days=1))
            # Pago de empresa B (no debe verlo la cuenta A)
            p_b = PagoDetectado(empresa_id=emp_b.id, metodo="yape",
                                monto=Decimal("15.00"), titular="Otro",
                                recibido_en=ahora)
            db.add_all([p1, p2, p3, p_b])
            db.flush()
            self.p1_id, self.p2_id, self.p3_id, self.p_b_id = p1.id, p2.id, p3.id, p_b.id
            db.commit()
        finally:
            db.close()

    # ---- helpers ----
    def _h(self, key):
        return {"X-API-Key": key}

    # =========================================================
    # AUTH
    # =========================================================
    def test_sin_api_key_401(self):
        r = self.client.get("/api/v1/pagos")
        self.assertEqual(r.status_code, 401)
        self.assertIn("detail", r.json())

    def test_api_key_invalida_401(self):
        r = self.client.get("/api/v1/pagos", headers=self._h("pk_live_noexiste"))
        self.assertEqual(r.status_code, 401)

    def test_api_key_inactiva_401(self):
        r = self.client.get("/api/v1/pagos", headers=self._h(self.key_off))
        self.assertEqual(r.status_code, 401)

    # =========================================================
    # AISLAMIENTO ENTRE CUENTAS
    # =========================================================
    def test_aislamiento_no_ve_pagos_de_otra_empresa(self):
        r = self.client.get("/api/v1/pagos", headers=self._h(self.key_a))
        self.assertEqual(r.status_code, 200)
        ids = {p["id"] for p in r.json()["pagos"]}
        self.assertIn(str(self.p1_id), ids)
        self.assertNotIn(str(self.p_b_id), ids)  # pago de empresa B oculto

    # =========================================================
    # FILTROS
    # =========================================================
    def test_filtro_monto_exacto(self):
        r = self.client.get("/api/v1/pagos?monto=15.00", headers=self._h(self.key_a))
        montos = {p["id"] for p in r.json()["pagos"]}
        self.assertIn(str(self.p1_id), montos)   # 15.00
        self.assertIn(str(self.p3_id), montos)   # 15.00
        self.assertNotIn(str(self.p2_id), montos)  # 9.90 excluido

    def test_filtro_ventana_desde_hasta(self):
        # Solo el pago de las 14:32 del 2026-06-07
        r = self.client.get(
            "/api/v1/pagos?desde=2026-06-07T14:00:00&hasta=2026-06-07T15:00:00",
            headers=self._h(self.key_a),
        )
        ids = {p["id"] for p in r.json()["pagos"]}
        self.assertEqual(ids, {str(self.p1_id)})

    def test_param_fecha_invalida_400(self):
        r = self.client.get("/api/v1/pagos?desde=no-es-fecha", headers=self._h(self.key_a))
        self.assertEqual(r.status_code, 400)

    def test_mapeo_banco_y_nombre(self):
        r = self.client.get("/api/v1/pagos?monto=15.00", headers=self._h(self.key_a))
        by_id = {p["id"]: p for p in r.json()["pagos"]}
        self.assertEqual(by_id[str(self.p1_id)]["banco"], "yape")
        self.assertEqual(by_id[str(self.p1_id)]["nombre"], "Juan Perez")
        self.assertEqual(by_id[str(self.p3_id)]["banco"], "BCP")
        self.assertIsNone(by_id[str(self.p3_id)]["nombre"])  # titular vacío -> null

    # =========================================================
    # RECLAMO
    # =========================================================
    def test_reclamo_feliz_200(self):
        r = self.client.post(f"/api/v1/pagos/{self.p1_id}/reclamar", headers=self._h(self.key_a))
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["reclamado"])

    def test_doble_reclamo_otra_cuenta_409(self):
        r1 = self.client.post(f"/api/v1/pagos/{self.p1_id}/reclamar", headers=self._h(self.key_a))
        self.assertEqual(r1.status_code, 200)
        r2 = self.client.post(f"/api/v1/pagos/{self.p1_id}/reclamar", headers=self._h(self.key_b))
        self.assertEqual(r2.status_code, 409)

    def test_reclamo_idempotente_mismo_reclamante_200(self):
        r1 = self.client.post(f"/api/v1/pagos/{self.p1_id}/reclamar", headers=self._h(self.key_a))
        r2 = self.client.post(f"/api/v1/pagos/{self.p1_id}/reclamar", headers=self._h(self.key_a))
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200)

    def test_reclamado_por_otro_no_aparece_en_listado(self):
        # Cuenta B reclama p1; cuenta A ya no debe verlo
        self.client.post(f"/api/v1/pagos/{self.p1_id}/reclamar", headers=self._h(self.key_b))
        r = self.client.get("/api/v1/pagos", headers=self._h(self.key_a))
        ids = {p["id"] for p in r.json()["pagos"]}
        self.assertNotIn(str(self.p1_id), ids)
        # Pero la cuenta B sí lo ve (reclamado por ella misma)
        rb = self.client.get("/api/v1/pagos", headers=self._h(self.key_b))
        idsb = {p["id"] for p in rb.json()["pagos"]}
        self.assertIn(str(self.p1_id), idsb)

    def test_reclamo_pago_inexistente_404(self):
        r = self.client.post("/api/v1/pagos/999999/reclamar", headers=self._h(self.key_a))
        self.assertEqual(r.status_code, 404)

    def test_reclamo_pago_de_otra_empresa_404(self):
        r = self.client.post(f"/api/v1/pagos/{self.p_b_id}/reclamar", headers=self._h(self.key_a))
        self.assertEqual(r.status_code, 404)

    def test_reclamo_id_no_numerico_404(self):
        r = self.client.post("/api/v1/pagos/abc/reclamar", headers=self._h(self.key_a))
        self.assertEqual(r.status_code, 404)

    # =========================================================
    # INGESTA ANDROID INTACTA (smoke)
    # =========================================================
    def test_ingesta_ping_sin_auth(self):
        r = self.client.get("/api/v1/ping")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ok")

    def test_ingesta_inbound_sigue_exigiendo_device_token(self):
        r = self.client.post("/api/v1/notificaciones/inbound", json={"texto": "x"})
        self.assertEqual(r.status_code, 401)  # falta X-Device-Token


if __name__ == "__main__":
    unittest.main(verbosity=2)
