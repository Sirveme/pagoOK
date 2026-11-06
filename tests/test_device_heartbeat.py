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
os.environ["PAGOOK_ENTORNO"] = "desarrollo"   # cookie sin Secure (TestClient usa http)

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.admin.db import Base, get_db  # noqa: E402
import app.admin.models  # noqa: E402,F401
from app.admin.models import Empresa  # noqa: E402
from app.api.models_webhook import Dispositivo  # noqa: E402
from app.api.models_device import DeviceEstadoActual  # noqa: E402,F401
from app.api.models_cuenta import CuentaApi, generar_api_key, hash_api_key  # noqa: E402
from app.main import app  # noqa: E402


PAYLOAD_OK = {
    "estado": "verde",
    "ultimo_ping_guardian_ok": 1720123456789,
    "ultimo_pago_bancario": 1720120000000,
    "pings_fallidos_consecutivos": 0,
    "veces_zombie_total": 3,
    "veces_rebind_intentado": 5,
    "veces_rebind_exitoso": 3,
    # Equipo sano (app 1.6.1): el lector reporta que está conectado -> verde.
    "listener_conectado": True,
    "permiso_notificaciones": True,
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
            # API key del cliente (para GET /api/v1/dispositivos/estado).
            self.api_key = generar_api_key()
            db.add(CuentaApi(empresa_id=emp.id, nombre="Cuenta MITA",
                             api_key_hash=hash_api_key(self.api_key), prefijo="pk_t", activa=True))
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

    # ---- Test 5: admin JSON, último heartbeat más reciente primero ----
    def test_admin_dispositivos_ordenado(self):
        self._hb(self.tokens["disp_a"], estado="verde")
        self._hb(self.tokens["disp_b"], estado="amarillo")
        self._hb(self.tokens["disp_c"], estado="rojo")
        r = self.client.get("/api/v1/admin/dispositivos",
                            headers={"X-Admin-Token": "token-admin-de-prueba"})
        self.assertEqual(r.status_code, 200)
        j = r.json()
        # Conectados: el estado reportado por el equipo manda (el peor de los dos).
        estados = [d["estado"] for d in j["dispositivos"]]
        self.assertEqual(estados, ["rojo", "amarillo", "verde"])   # c, b, a: del más reciente al más antiguo
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

    # ---- Estado único por conexión, inactivos, archivar, empresas sin equipos ----
    def _con_heartbeat_hace(self, nombre, minutos, estado="verde", empresa_id=None):
        """Dispositivo con DEA cuyo último heartbeat fue hace `minutos`."""
        from datetime import datetime, timedelta, timezone
        db = self.Session()
        try:
            d = Dispositivo(empresa_id=empresa_id or self.empresa_id, token=f"tok_{nombre}",
                            nombre=nombre, activo=True,
                            creado_en=datetime.utcnow() - timedelta(days=30))
            db.add(d)
            db.flush()
            db.add(DeviceEstadoActual(dispositivo_id=d.id, estado=estado, manufacturer="motorola",
                                      listener_conectado=True,
                                      ultimo_heartbeat=datetime.now(timezone.utc) - timedelta(minutes=minutos)))
            db.commit()
            return d.id
        finally:
            db.close()

    def _admin(self):
        r = self.client.get("/api/v1/admin/dispositivos", headers={"X-Admin-Token": "token-admin-de-prueba"})
        self.assertEqual(r.status_code, 200)
        return r.json()

    def test_umbrales_de_conexion_20_y_45_min(self):
        from app.api.router_device import estado_conexion
        self.assertEqual([estado_conexion(m) for m in (0, 19.9, 20, 45, 45.1, None)],
                         ["verde", "verde", "amarillo", "amarillo", "rojo", "rojo"])
        ids = {m: self._con_heartbeat_hace(f"m{m}", m) for m in (5, 30, 60)}
        por_id = {d["dispositivo_id"]: d for d in self._admin()["dispositivos"]}
        self.assertEqual([por_id[ids[m]]["estado"] for m in (5, 30, 60)], ["verde", "amarillo", "rojo"])
        # Reportó "verde" pero lleva 60 min sin heartbeat: un solo estado, rojo.
        self.assertEqual(por_id[ids[60]]["motivo"], "Sin heartbeat reciente")

    def test_contadores_coherentes_y_inactivos_aparte(self):
        self._hb(self.tokens["disp_a"], estado="verde")
        self._hb(self.tokens["disp_b"], estado="rojo")
        self._con_heartbeat_hace("amar", 25)
        self._con_heartbeat_hace("viejo", 8 * 24 * 60, estado="rojo")   # 8 días: inactivo
        j = self._admin()
        r = j["resumen"]
        # disp_c nunca envió heartbeat pero se registró hoy: activo y rojo.
        self.assertEqual((r["verde"], r["amarillo"], r["rojo"], r["inactivos"]), (1, 1, 2, 1))
        self.assertEqual(r["verde"] + r["amarillo"] + r["rojo"], r["total"])
        self.assertEqual(r["total"], 4)
        inactivos = [d for d in j["dispositivos"] if d["inactivo"]]
        self.assertEqual([d["nombre"] for d in inactivos], ["viejo"])
        nunca = next(d for d in j["dispositivos"] if d["nombre"] == "disp_c")
        self.assertEqual((nunca["estado"], nunca["motivo"]), ("rojo", "Nunca envió un heartbeat"))

    def test_registrado_hace_mas_de_7_dias_sin_heartbeat_es_inactivo(self):
        from datetime import datetime, timedelta
        db = self.Session()
        try:
            db.query(Dispositivo).filter_by(nombre="disp_c").update(
                {Dispositivo.creado_en: datetime.utcnow() - timedelta(days=40)})
            db.commit()
        finally:
            db.close()
        c = next(d for d in self._admin()["dispositivos"] if d["nombre"] == "disp_c")
        self.assertTrue(c["inactivo"])

    def test_orden_por_empresa_y_heartbeat_mas_reciente(self):
        db = self.Session()
        try:
            otra = Empresa(pais_codigo="PE", id_fiscal="20100000002", razon_social="Abarrotes Ana",
                           nombre_comercial="Abarrotes Ana", slug="ana")
            db.add(otra)
            db.commit()
            otra_id = otra.id
        finally:
            db.close()
        self._con_heartbeat_hace("p_viejo", 30)
        self._con_heartbeat_hace("p_nuevo", 2)
        self._con_heartbeat_hace("a_uno", 10, empresa_id=otra_id)
        nombres = [d["nombre"] for d in self._admin()["dispositivos"] if not d["inactivo"]]
        # "Abarrotes Ana" antes que "Perú Sistemas Pro"; disp_a/b/c nunca enviaron: al final.
        self.assertEqual(nombres[:3], ["a_uno", "p_nuevo", "p_viejo"])
        self.assertEqual(set(nombres[3:]), {"disp_a", "disp_b", "disp_c"})

    def test_empresas_sin_equipos_en_seccion_aparte(self):
        db = self.Session()
        try:
            vacia = Empresa(pais_codigo="PE", id_fiscal="20100000003", razon_social="Hotel Selva",
                            nombre_comercial="Hotel Selva", slug="selva")
            con_viejo = Empresa(pais_codigo="PE", id_fiscal="20100000004", razon_social="Contadores AyF",
                                nombre_comercial="Contadores AyF", slug="ayf")
            db.add_all([vacia, con_viejo])
            db.commit()
            con_viejo_id = con_viejo.id
        finally:
            db.close()
        self._con_heartbeat_hace("ayf_viejo", 10 * 24 * 60, empresa_id=con_viejo_id)
        j = self._admin()
        sin = {e["slug"]: e["equipos_inactivos"] for e in j["empresas_sin_equipos"]}
        self.assertEqual(sin, {"selva": 0, "ayf": 1})          # perusistemas tiene equipos activos
        empresas_en_tabla = {d["empresa"] for d in j["dispositivos"] if not d["inactivo"]}
        self.assertNotIn("Hotel Selva", empresas_en_tabla)

    def test_archivar_solo_inactivos_y_sin_borrar(self):
        viejo = self._con_heartbeat_hace("viejo", 8 * 24 * 60)
        reciente = self._con_heartbeat_hace("reciente", 3)
        url = "/api/v1/admin/dispositivos/{}/archivar"
        h = {"X-Admin-Token": "token-admin-de-prueba"}
        self.assertEqual(self.client.post(url.format(viejo)).status_code, 401)
        self.assertEqual(self.client.post(url.format(reciente), headers=h).status_code, 409)
        self.assertEqual(self.client.post(url.format(viejo), headers=h).status_code, 200)
        self.assertEqual(self.client.post(url.format(viejo), headers=h).status_code, 404)  # ya archivado
        db = self.Session()
        try:
            d = db.get(Dispositivo, viejo)
            self.assertFalse(d.activo)                                   # archivado...
            self.assertIsNotNone(db.get(DeviceEstadoActual, viejo))      # ...sin borrar nada
        finally:
            db.close()
        ids = [d["dispositivo_id"] for d in self._admin()["dispositivos"]]
        self.assertNotIn(viejo, ids)

    def test_pagina_html_sesion_sin_token_en_url(self):
        # Sin sesión: muestra el login (no filtra el token por la URL).
        r0 = self.client.get("/admin/dispositivos/estado")
        self.assertEqual(r0.status_code, 401)
        self.assertIn("Ingresa el token", r0.text)
        self.assertEqual(r0.cookies.get("pagook_superadmin"), None)
        # El `?token=` de antes ya NO abre el dashboard.
        self.assertIn("Ingresa el token",
                      self.client.get("/admin/dispositivos/estado?token=token-admin-de-prueba").text)
        # Login por POST -> cookie firmada -> dashboard (el TestClient sigue el 303).
        r1 = self.client.post(
            "/admin/acceso",
            data={"token": "token-admin-de-prueba", "destino": "/admin/dispositivos/estado"},
        )
        self.assertEqual(r1.status_code, 200)
        html = r1.text
        for texto in ("Mostrar inactivos", "Empresas sin equipos", "archivar", "Último pago"):
            self.assertIn(texto, html)
        # El token nunca se incrusta en el HTML del dashboard.
        self.assertNotIn("token-admin-de-prueba", html)
        # Y la API JSON ahora acepta la cookie (sin header).
        self.assertEqual(self.client.get("/api/v1/admin/dispositivos").status_code, 200)

    def test_token_admin_incorrecto_no_abre_dashboard(self):
        r = self.client.post(
            "/admin/acceso",
            data={"token": "malo", "destino": "/admin/dispositivos/estado"},
        )
        # Redirige al destino, que sin cookie vuelve a mostrar el login.
        self.assertIn("Ingresa el token", r.text)
        self.assertEqual(self.client.get("/api/v1/admin/dispositivos").status_code, 401)

    def test_salir_borra_la_sesion(self):
        self.client.post("/admin/acceso",
                         data={"token": "token-admin-de-prueba", "destino": "/admin/dispositivos/estado"})
        self.assertEqual(self.client.get("/api/v1/admin/dispositivos").status_code, 200)
        self.client.get("/admin/salir")
        self.assertEqual(self.client.get("/api/v1/admin/dispositivos").status_code, 401)

    # ---- Item 1/2: el lector de notificaciones (listener) ----
    def test_heartbeat_guarda_listener_y_permiso(self):
        self._hb(self.tokens["disp_a"], listener_conectado=True, permiso_notificaciones=True)
        db = self.Session()
        try:
            fila = db.query(DeviceEstadoActual).first()
            self.assertEqual(fila.listener_conectado, True)
            self.assertEqual(fila.permiso_notificaciones, True)
        finally:
            db.close()

    def test_app_vieja_sin_listener_queda_amarillo(self):
        # App vieja: no reporta el lector (NULL). Nunca verde "por defecto":
        # queda AMARILLO pidiendo actualizar, para no dar falsa tranquilidad.
        self._hb(self.tokens["disp_a"], estado="verde",
                 listener_conectado=None, permiso_notificaciones=None)
        j = self._admin()
        d = next(x for x in j["dispositivos"] if x["nombre"] == "disp_a")
        self.assertIsNone(d["listener_conectado"])
        self.assertEqual(d["estado"], "amarillo")
        self.assertEqual(d["motivo"], "Sin dato del lector: actualiza la app")

    def test_listener_desconectado_es_rojo_aunque_heartbeat_verde(self):
        # El incidente 06-11: heartbeat verde y reciente, pero el lector caído.
        self._hb(self.tokens["disp_a"], estado="verde", listener_conectado=False,
                 permiso_notificaciones=True)
        d = next(x for x in self._admin()["dispositivos"] if x["nombre"] == "disp_a")
        self.assertEqual(d["estado"], "rojo")
        self.assertEqual(d["motivo"], "El lector de notificaciones está desconectado")
        self.assertEqual(d["listener_conectado"], False)

    # ---- Item 3: API de clientes GET /api/v1/dispositivos/estado ----
    def _estado_cliente(self, key=None):
        r = self.client.get("/api/v1/dispositivos/estado",
                            headers={"X-API-Key": key or self.api_key})
        self.assertEqual(r.status_code, 200)
        return r.json()

    def test_api_cliente_requiere_api_key(self):
        self.assertEqual(self.client.get("/api/v1/dispositivos/estado").status_code, 401)

    def test_api_cliente_campos_y_estado(self):
        self._hb(self.tokens["disp_a"], estado="verde", listener_conectado=True)
        self._hb(self.tokens["disp_b"], estado="verde", listener_conectado=False)
        lista = self._estado_cliente()
        # Contrato: lista de equipos con exactamente estos campos.
        esperados = {"id", "nombre", "estado", "motivo", "ultimo_heartbeat",
                     "listener_conectado", "ultimo_pago_capturado", "avisos_no_reconocidos"}
        self.assertTrue(all(set(x) == esperados for x in lista))
        por_nombre = {x["nombre"]: x for x in lista}
        self.assertEqual(por_nombre["disp_a"]["estado"], "verde")
        self.assertEqual(por_nombre["disp_b"]["estado"], "rojo")          # lector caído
        self.assertEqual(por_nombre["disp_b"]["listener_conectado"], False)
        # Ordenado del peor estado al mejor.
        self.assertEqual(lista[0]["estado"], "rojo")

    def test_api_cliente_aislada_por_empresa(self):
        # Equipo de OTRA empresa no debe aparecer con la API key de la mía.
        db = self.Session()
        try:
            otra = Empresa(pais_codigo="PE", id_fiscal="20100000099", razon_social="Otra SAC",
                           nombre_comercial="Otra SAC", slug="otra")
            db.add(otra)
            db.flush()
            db.add(Dispositivo(empresa_id=otra.id, token="tok_otra", nombre="de_otra", activo=True))
            db.commit()
        finally:
            db.close()
        nombres = {x["nombre"] for x in self._estado_cliente()}
        self.assertIn("disp_a", nombres)
        self.assertNotIn("de_otra", nombres)

    def test_ultimo_pago_capturado_tras_inbound(self):
        # Un Yape capturado deja marca en el equipo y la ve el cliente.
        r = self.client.post(
            "/api/v1/notificaciones/inbound",
            headers={"X-Device-Token": self.tokens["disp_a"]},
            json={"package": "com.bcp.innovacxion.yapeapp", "titulo": "Yape!",
                  "texto": "Juan Perez te yapeó S/ 15.00"},
        )
        self.assertEqual(r.status_code, 200)
        self.assertIsNotNone(r.json()["pago_id"])
        db = self.Session()
        try:
            d = db.query(Dispositivo).filter_by(nombre="disp_a").first()
            self.assertIsNotNone(d.ultimo_pago_capturado)
        finally:
            db.close()
        d = next(x for x in self._estado_cliente() if x["nombre"] == "disp_a")
        self.assertIsNotNone(d["ultimo_pago_capturado"])

    # ---- Item 2c: avisos de billetera que la app no supo interpretar ----
    def _no_reconocido(self, token, **cambios):
        payload = {"package": "com.bcp.innovacxion.yapeapp", "titulo": "Yape!",
                   "texto": "Aviso con un formato nuevo por S/ 1"}
        payload.update(cambios)
        return self.client.post("/api/v1/device/no-reconocido", json=payload,
                                headers={"X-Device-Token": token})

    def test_no_reconocido_requiere_token(self):
        r = self.client.post("/api/v1/device/no-reconocido",
                             json={"package": "x", "texto": "y"})
        self.assertEqual(r.status_code, 401)

    def test_no_reconocido_se_guarda_y_pinta_amarillo(self):
        # Equipo sano (heartbeat verde, lector OK) + un aviso sin interpretar.
        self._hb(self.tokens["disp_a"], estado="verde")
        r = self._no_reconocido(self.tokens["disp_a"])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "guardado")
        d = next(x for x in self._admin()["dispositivos"] if x["nombre"] == "disp_a")
        self.assertEqual(d["avisos_no_reconocidos"], 1)
        self.assertEqual(d["estado"], "amarillo")
        self.assertIn("sin interpretar", d["motivo"])

    def test_no_reconocido_dedup_por_sbn_key(self):
        self._hb(self.tokens["disp_a"], estado="verde")
        self._no_reconocido(self.tokens["disp_a"], sbn_key="k1")
        r = self._no_reconocido(self.tokens["disp_a"], sbn_key="k1")
        self.assertEqual(r.json()["status"], "duplicado")
        d = next(x for x in self._admin()["dispositivos"] if x["nombre"] == "disp_a")
        self.assertEqual(d["avisos_no_reconocidos"], 1)

    def test_no_reconocido_texto_vacio_400(self):
        r = self._no_reconocido(self.tokens["disp_a"], texto="   ")
        self.assertEqual(r.status_code, 400)

    # ---- Test 6: admin sin token -> 401 ----
    def test_admin_sin_token_401(self):
        r = self.client.get("/api/v1/admin/dispositivos")
        self.assertEqual(r.status_code, 401)

    def test_admin_token_incorrecto_401(self):
        r = self.client.get("/api/v1/admin/dispositivos", headers={"X-Admin-Token": "malo"})
        self.assertEqual(r.status_code, 401)


if __name__ == "__main__":
    unittest.main(verbosity=2)
