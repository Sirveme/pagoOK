"""Tests del heartbeat de dispositivos y del dashboard admin (JSON).

SQLite en memoria. Ejecutar:  python -m unittest tests.test_device_heartbeat
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DATABASE_URL", "postgresql://dummy:dummy@localhost/dummy")
os.environ["ADMIN_TOKEN"] = "token-admin-de-prueba"

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.admin.db import Base, get_db  # noqa: E402
import app.admin.models  # noqa: E402,F401
from app.admin.models import Empresa  # noqa: E402
from app.api.models_webhook import Dispositivo  # noqa: E402
from app.api.models_device import DeviceEstadoActual  # noqa: E402,F401
from app.main import app  # noqa: E402


PAYLOAD_OK = {
    "estado": "verde",
    "ultimo_ping_guardian_ok": 1720123456789,
    "ultimo_pago_bancario": 1720120000000,
    "pings_fallidos_consecutivos": 0,
    "veces_zombie_total": 3,
    "veces_rebind_intentado": 5,
    "veces_rebind_exitoso": 3,
    "manufacturer": "Xiaomi",
    "modelo": "Redmi Note 12",
    "android_version": "14",
    "app_version": "1.4-beta",
}


class DeviceHeartbeatTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        self.Session = sessionmaker(bind=self.engine, autoflush=False)
        Base.metadata.create_all(self.engine)

        def _override():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = _override
        self.client = TestClient(app)
        self._seed()

    def tearDown(self):
        app.dependency_overrides.clear()
        Base.metadata.drop_all(self.engine)

    def _seed(self):
        db = self.Session()
        try:
            emp = Empresa(pais_codigo="PE", id_fiscal="20100000001",
                          razon_social="Perú Sistemas Pro", nombre_comercial="Perú Sistemas Pro",
                          slug="perusistemas")
            db.add(emp)
            db.flush()
            self.empresa_id = emp.id
            # 3 dispositivos con token
            self.tokens = {}
            for nombre in ["disp_a", "disp_b", "disp_c"]:
                d = Dispositivo(empresa_id=emp.id, token=f"tok_{nombre}", nombre=nombre, activo=True)
                db.add(d)
                db.flush()
                self.tokens[nombre] = d.token
            db.commit()
        finally:
            db.close()

    def _hb(self, token, **cambios):
        payload = dict(PAYLOAD_OK)
        payload.update(cambios)
        return self.client.post(
            "/api/v1/device/heartbeat", json=payload, headers={"X-Device-Token": token}
        )

    # ---- Test 1: heartbeat válido -> 200 + UPSERT ----
    def test_heartbeat_ok_upsert(self):
        r = self._hb(self.tokens["disp_a"])
        self.assertEqual(r.status_code, 200)
        # Contrato de siempre + bloque `cuenta` (aditivo, zWeb-04).
        cuerpo = r.json()
        self.assertEqual({k: cuerpo[k] for k in ("ok", "next_check_seconds")},
                         {"ok": True, "next_check_seconds": 60})
        self.assertEqual(set(cuerpo["cuenta"]), {"estado", "dias_restantes", "puede_ampliar"})

        db = self.Session()
        try:
            fila = db.query(DeviceEstadoActual).all()
            self.assertEqual(len(fila), 1)
            self.assertEqual(fila[0].estado, "verde")
            self.assertEqual(fila[0].manufacturer, "Xiaomi")
            self.assertIsNotNone(fila[0].ultimo_heartbeat)
            # Tarea 1: los campos rebind ahora se persisten.
            self.assertEqual(fila[0].veces_rebind_intentado, 5)
            self.assertEqual(fila[0].veces_rebind_exitoso, 3)
        finally:
            db.close()

    # ---- Test 2: sin token -> 401 ----
    def test_sin_token_401(self):
        r = self.client.post("/api/v1/device/heartbeat", json=PAYLOAD_OK)
        self.assertEqual(r.status_code, 401)

    # ---- Test 3: estado inválido -> 400 ----
    def test_estado_invalido_400(self):
        r = self._hb(self.tokens["disp_a"], estado="azul")
        self.assertEqual(r.status_code, 400)

    def test_falta_campo_400(self):
        payload = dict(PAYLOAD_OK)
        del payload["manufacturer"]
        r = self.client.post("/api/v1/device/heartbeat", json=payload,
                             headers={"X-Device-Token": self.tokens["disp_a"]})
        self.assertEqual(r.status_code, 400)

    # ---- Test 4: transición verde -> rojo incrementa alarma ----
    def test_alarma_incrementa_en_transicion_a_rojo(self):
        self._hb(self.tokens["disp_a"], estado="verde")
        self._hb(self.tokens["disp_a"], estado="rojo")
        db = self.Session()
        try:
            fila = db.query(DeviceEstadoActual).filter_by().first()
            self.assertEqual(fila.estado, "rojo")
            self.assertEqual(fila.veces_alarma_disparada, 1)
        finally:
            db.close()
        # Un segundo rojo NO vuelve a incrementar (sigue siendo rojo).
        self._hb(self.tokens["disp_a"], estado="rojo")
        db = self.Session()
        try:
            fila = db.query(DeviceEstadoActual).first()
            self.assertEqual(fila.veces_alarma_disparada, 1)
        finally:
            db.close()

    # ---- Test 5: admin JSON ordena rojo/amarillo/verde ----
    def test_admin_dispositivos_ordenado(self):
        self._hb(self.tokens["disp_a"], estado="verde")
        self._hb(self.tokens["disp_b"], estado="amarillo")
        self._hb(self.tokens["disp_c"], estado="rojo")
        r = self.client.get("/api/v1/admin/dispositivos",
                            headers={"X-Admin-Token": "token-admin-de-prueba"})
        self.assertEqual(r.status_code, 200)
        j = r.json()
        estados = [d["estado"] for d in j["dispositivos"]]
        self.assertEqual(estados, ["rojo", "amarillo", "verde"])
        self.assertEqual(j["resumen"]["total"], 3)
        self.assertEqual(j["resumen"]["rojo"], 1)
        self.assertEqual(j["resumen"]["verde"], 1)

    # ---- Dispositivos inactivos NO aparecen y no cuentan en el total ----
    def test_dispositivos_inactivos_excluidos(self):
        from datetime import datetime, timezone
        self._hb(self.tokens["disp_a"], estado="verde")
        self._hb(self.tokens["disp_b"], estado="amarillo")
        self._hb(self.tokens["disp_c"], estado="rojo")
        # Dispositivo desactivado con estado rojo (insertado directo).
        db = self.Session()
        try:
            d = Dispositivo(empresa_id=self.empresa_id, token="tok_off", nombre="off", activo=False)
            db.add(d)
            db.flush()
            off_id = d.id
            db.add(DeviceEstadoActual(
                dispositivo_id=d.id, estado="rojo",
                ultimo_heartbeat=datetime.now(timezone.utc),
            ))
            db.commit()
        finally:
            db.close()

        r = self.client.get("/api/v1/admin/dispositivos",
                            headers={"X-Admin-Token": "token-admin-de-prueba"})
        j = r.json()
        ids = [d["dispositivo_id"] for d in j["dispositivos"]]
        self.assertNotIn(off_id, ids)                 # el inactivo no aparece
        self.assertEqual(j["resumen"]["total"], 3)    # total solo cuenta activos
        estados = [d["estado"] for d in j["dispositivos"]]
        self.assertEqual(estados, ["rojo", "amarillo", "verde"])  # activos ordenados

    # ---- Test 6: admin sin token -> 401 ----
    def test_admin_sin_token_401(self):
        r = self.client.get("/api/v1/admin/dispositivos")
        self.assertEqual(r.status_code, 401)

    def test_admin_token_incorrecto_401(self):
        r = self.client.get("/api/v1/admin/dispositivos", headers={"X-Admin-Token": "malo"})
        self.assertEqual(r.status_code, 401)


if __name__ == "__main__":
    unittest.main(verbosity=2)
