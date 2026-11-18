"""Tests de zWeb-11: zombies y alarmas con fecha + detalle en el panel de estado.

SQLite en memoria. Ejecutar:  python -m unittest tests.test_incidentes_dispositivo
"""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DATABASE_URL", "postgresql://dummy:dummy@localhost/dummy")
os.environ["ADMIN_TOKEN"] = "token-admin-de-prueba"
os.environ["PAGOOK_ENTORNO"] = "desarrollo"   # cookie sin Secure (TestClient usa http)

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.admin.db import Base, get_db  # noqa: E402
import app.admin.models  # noqa: E402,F401
from app.admin.models import Empresa  # noqa: E402
from app.api.models_webhook import Dispositivo  # noqa: E402
from app.api.models_device import DeviceEstadoActual  # noqa: E402
from app.api.models_incidente import DispositivoIncidente  # noqa: E402
from app.main import app  # noqa: E402

ADMIN = {"X-Admin-Token": "token-admin-de-prueba"}

SANO = {
    "estado": "verde", "ultimo_ping_guardian_ok": 1720123456789, "ultimo_pago_bancario": 1720120000000,
    "pings_fallidos_consecutivos": 0, "veces_zombie_total": 0, "listener_conectado": True,
    "permiso_notificaciones": True, "manufacturer": "motorola", "modelo": "moto g06",
    "android_version": "15", "app_version": "1.7.1",
}


class IncidentesDispositivoTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
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
        db = self.Session()
        try:
            emp = Empresa(pais_codigo="PE", id_fiscal="20616219686", razon_social="MITA SAC",
                          nombre_comercial="MITA", slug="mita")
            db.add(emp)
            db.flush()
            d = Dispositivo(empresa_id=emp.id, token="tok_mita", nombre="moto g06", activo=True)
            db.add(d)
            db.commit()
            self.disp_id = d.id
        finally:
            db.close()

    def tearDown(self):
        app.dependency_overrides.clear()
        Base.metadata.drop_all(self.engine)

    def _hb(self, **cambios):
        payload = dict(SANO)
        payload.update(cambios)
        r = self.client.post("/api/v1/device/heartbeat", json=payload, headers={"X-Device-Token": "tok_mita"})
        self.assertEqual(r.status_code, 200)
        return r

    def _detalle(self):
        r = self.client.get(f"/api/v1/admin/dispositivos/{self.disp_id}/detalle", headers=ADMIN)
        self.assertEqual(r.status_code, 200)
        return r.json()

    # ---- Registro desde el heartbeat ----
    def test_zombie_con_lector_caido_queda_activo_con_que_hacer_y_se_cierra_solo(self):
        self._hb(veces_zombie_total=0)
        self._hb(veces_zombie_total=1, listener_conectado=False)
        incs = self._detalle()["incidentes"]
        self.assertEqual(len(incs), 1)
        z = incs[0]
        self.assertEqual((z["tipo"], z["causa"], z["activa"]), ("zombie", "lector_desconectado", True))
        self.assertIn("acceso a notificaciones", z["accion"])
        self.assertRegex(z["ocurrido_lima"], r"^\d{2}/\d{2}/\d{4} \d{2}:\d{2}$")
        # Siguiente heartbeat sano: se cierra solo.
        self._hb(veces_zombie_total=1)
        z = self._detalle()["incidentes"][0]
        self.assertFalse(z["activa"])
        self.assertIsNotNone(z["cerrada_lima"])

    def test_contadores_previos_quedan_sin_fecha_y_no_inventan_eventos(self):
        # Primer heartbeat del equipo con contador 22: anterior al registro.
        self._hb(veces_zombie_total=22)
        j = self._detalle()
        self.assertEqual(j["incidentes"], [])
        self.assertEqual(j["sin_fecha"], {"zombies": 22, "alarmas": 0})
        # La app se reinstaló (contador baja): tampoco es un zombie.
        self._hb(veces_zombie_total=0)
        self.assertEqual(self._detalle()["incidentes"], [])

    def test_zombie_que_llega_ya_sano_nace_cerrado_y_suma_la_cantidad(self):
        self._hb(veces_zombie_total=1)
        self._hb(veces_zombie_total=4)          # la app se recuperó sola entre heartbeats
        z = self._detalle()["incidentes"][0]
        self.assertEqual((z["cantidad"], z["activa"], z["causa"], z["accion"]), (3, False, None, None))
        self.assertEqual(self._detalle()["sin_fecha"]["zombies"], 1)

    def test_alarma_en_transicion_a_rojo_y_cierre(self):
        self._hb()
        self._hb(estado="rojo", pings_fallidos_consecutivos=3)
        self._hb(estado="rojo", pings_fallidos_consecutivos=4)   # sigue rojo: no duplica
        incs = self._detalle()["incidentes"]
        self.assertEqual([i["tipo"] for i in incs], ["alarma"])
        self.assertTrue(incs[0]["activa"])
        self.assertIn("3 pings", incs[0]["mensaje"])
        self._hb(estado="verde")
        self.assertFalse(self._detalle()["incidentes"][0]["activa"])

    def test_heartbeat_responde_200_aunque_falle_el_registro_de_incidentes(self):
        self._hb()
        with mock.patch("app.services.incidentes.registrar_desde_heartbeat", side_effect=RuntimeError("sin tabla")):
            self._hb(estado="rojo", veces_zombie_total=5)
        db = self.Session()
        try:
            dea = db.get(DeviceEstadoActual, self.disp_id)
            self.assertEqual((dea.estado, dea.veces_zombie_total), ("rojo", 5))   # el heartbeat sí se guardó
        finally:
            db.close()

    # ---- Detalle y "Marcar como revisada" ----
    def test_detalle_carga_con_alarma_y_marcar_revisada_cambia_el_estado(self):
        self._hb()
        self._hb(estado="rojo", listener_conectado=False)
        j = self._detalle()
        d = j["dispositivo"]
        for campo in ("app_version", "android_version", "ultimo_heartbeat_lima", "listener_conectado",
                      "permiso_notificaciones", "ultimo_pago_capturado_lima"):
            self.assertIn(campo, d)
        self.assertEqual((d["app_version"], d["android_version"], d["listener_conectado"]), ("1.7.1", "15", False))
        self.assertIn("acceso a notificaciones", d["accion_actual"])
        alarma = next(i for i in j["incidentes"] if i["tipo"] == "alarma")
        self.assertFalse(alarma["revisada"])

        url = f"/api/v1/admin/incidentes/{alarma['id']}/revisar"
        self.assertEqual(self.client.post(url, json={"nombre": "Duilio"}).status_code, 401)   # sin sesión
        r = self.client.post(url, json={"nombre": "Duilio"}, headers=ADMIN)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["ya_estaba_revisada"])
        alarma = next(i for i in self._detalle()["incidentes"] if i["id"] == alarma["id"])
        self.assertTrue(alarma["revisada"])
        self.assertEqual(alarma["revisada_por"], "Duilio · superadmin (script)")
        self.assertIsNotNone(alarma["revisada_lima"])
        # Idempotente: una segunda revisión no pisa la auditoría de la primera.
        r2 = self.client.post(url, json={"nombre": "Otro"}, headers=ADMIN)
        self.assertTrue(r2.json()["ya_estaba_revisada"])
        self.assertEqual(r2.json()["incidente"]["revisada_por"], "Duilio · superadmin (script)")
        db = self.Session()
        try:
            self.assertIsNotNone(db.get(DispositivoIncidente, alarma["id"]).revisada_ip)
        finally:
            db.close()

    def test_revisar_desde_el_panel_con_cookie(self):
        self._hb()
        self._hb(estado="rojo")
        inc_id = self._detalle()["incidentes"][0]["id"]
        self.client.post("/admin/acceso", data={"token": "token-admin-de-prueba",
                                                "destino": "/admin/dispositivos/estado"})
        r = self.client.post(f"/api/v1/admin/incidentes/{inc_id}/revisar", json={})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["incidente"]["revisada_por"], "superadmin (panel)")

    def test_detalle_requiere_admin_y_404(self):
        self.assertEqual(self.client.get(f"/api/v1/admin/dispositivos/{self.disp_id}/detalle").status_code, 401)
        self.assertEqual(self.client.get("/api/v1/admin/dispositivos/9999/detalle", headers=ADMIN).status_code, 404)
        self.assertEqual(self.client.post("/api/v1/admin/incidentes/9999/revisar", headers=ADMIN).status_code, 404)

    # ---- zWeb-12: duración, lector al inicio y cómo se recuperó ----
    def test_detalle_trae_inicio_duracion_version_y_lector(self):
        self._hb()
        self._hb(veces_zombie_total=1, listener_conectado=False)
        z = self._detalle()["incidentes"][0]
        self.assertTrue(z["duracion"].startswith("Sigue activo"))
        self.assertEqual((z["app_version"], z["listener_conectado"]), ("1.7.1", False))
        self.assertIsNone(z["recuperacion"])
        self._hb(veces_zombie_total=1)
        z = self._detalle()["incidentes"][0]
        self.assertIn(z["duracion"], ("menos de 1 min", "1 min"))
        self.assertFalse(z["activa"])

    def test_recuperacion_sola_cuando_la_app_se_reconecta(self):
        self._hb(veces_rebind_exitoso=2)
        self._hb(veces_zombie_total=1, listener_conectado=False, veces_rebind_exitoso=2)
        self._hb(veces_zombie_total=1, veces_rebind_exitoso=3)
        z = self._detalle()["incidentes"][0]
        self.assertEqual((z["recuperacion"], z["recuperacion_texto"]), ("sola", "Se recuperó sola"))

    def test_recuperacion_con_intervencion_al_actualizar_o_dar_permiso(self):
        self._hb(app_version="1.6.0")
        self._hb(estado="rojo", listener_conectado=False, app_version="1.6.0")
        self._hb(app_version="1.7.1")                                   # se actualizó la app
        a = self._detalle()["incidentes"][0]
        self.assertEqual(a["recuperacion"], "intervencion")
        self.assertIn("1.6.0 → 1.7.1", a["recuperacion_detalle"])
        self._hb(veces_zombie_total=1, permiso_notificaciones=False)
        self._hb(veces_zombie_total=1, permiso_notificaciones=True)     # se volvió a dar el permiso
        z = next(i for i in self._detalle()["incidentes"] if i["tipo"] == "zombie")
        self.assertEqual((z["recuperacion"], z["causa"]), ("intervencion", "sin_permiso"))

    def test_recuperacion_sin_determinar_sin_senales(self):
        self._hb()
        self._hb(estado="rojo")
        self._hb()
        self.assertEqual(self._detalle()["incidentes"][0]["recuperacion"], "sin_determinar")

    def test_formato_de_duracion(self):
        from app.services.incidentes import fmt_duracion
        self.assertEqual([fmt_duracion(s) for s in (20, 60, 3599, 3600, 3 * 3600 + 300, 26 * 3600, 48 * 3600)],
                         ["menos de 1 min", "1 min", "59 min", "1 h", "3 h 5 min", "1 d 2 h", "2 d"])

    def test_pagina_de_estado_en_tema_oscuro_sin_blanco(self):
        r = self.client.post("/admin/acceso", data={"token": "token-admin-de-prueba",
                                                    "destino": "/admin/dispositivos/estado"})
        html = r.text.lower()
        for blanco in ("#ffffff", "#fff;", "#fff ", "#fff}", "background:white", "#faf6ef"):
            self.assertNotIn(blanco, html)
        self.assertIn("--bg:#1a0303", html)
        self.assertIn("/admin/testers-metricas", html)

    def test_nombre_de_empresa_no_puede_cerrar_el_script(self):
        db = self.Session()
        try:
            db.get(Empresa, db.get(Dispositivo, self.disp_id).empresa_id).nombre_comercial = "</script><b>x"
            db.commit()
        finally:
            db.close()
        r = self.client.post("/admin/acceso", data={"token": "token-admin-de-prueba",
                                                    "destino": "/admin/dispositivos/estado"})
        self.assertNotIn("</script><b>x", r.text)
        self.assertIn("<\\/script><b>x", r.text)

    def test_pagina_trae_el_panel_lateral(self):
        r = self.client.post("/admin/acceso", data={"token": "token-admin-de-prueba",
                                                    "destino": "/admin/dispositivos/estado"})
        self.assertEqual(r.status_code, 200)
        for texto in ('id="lateral"', "abrirDetalle", "Marcar como revisada", "max-width:480px"):
            self.assertIn(texto, r.text)
        self.assertNotIn("token-admin-de-prueba", r.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
