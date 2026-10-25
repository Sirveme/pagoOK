"""Tests offline del sprint multi-empresa (zWeb-04). SQLite en memoria.

Cubre: aislamiento entre empresas, vinculación por código, invitaciones,
revocación inmediata, matriz de permisos, prueba 7+15 y vencimiento perezoso,
cuenta vencida (captura sí, consulta 402, webhooks en cola), compatibilidad
con la app 1.5.x, catálogo de bancos y el script de gestión de empresas.

Ejecutar:  python -m unittest tests.test_multiempresa
"""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DATABASE_URL", "postgresql://dummy:dummy@localhost/dummy")
os.environ["PAGOOK_ENTORNO"] = "desarrollo"   # cookie sin Secure (TestClient usa http)

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.admin.db import Base, get_db  # noqa: E402
import app.admin.models  # noqa: E402,F401
from app.admin.models import Empresa, Pais  # noqa: E402
from app.api.models_cuenta import CuentaApi, generar_api_key, hash_api_key  # noqa: E402
from app.api.models_eventos import EntregaEvento, SuscripcionWebhook  # noqa: E402
from app.api.models_multiempresa import (  # noqa: E402
    BancoConfig, CodigoVinculacion, Invitacion, Membresia, Sesion, Usuario,
)
from app.api.models_webhook import Dispositivo, PagoDetectado  # noqa: E402
from app.api.router_device import reiniciar_rate_limit_registro  # noqa: E402
from app.api.router_panel import reiniciar_limites_panel  # noqa: E402
from app.api.router_vinculacion import limpiar_cache_bancos, reiniciar_limite_vinculacion  # noqa: E402
from app.main import app  # noqa: E402
from app.services import cuenta_empresa, eventos, otp  # noqa: E402

AHORA = datetime.now(timezone.utc)
CONSENTIMIENTO = {"consentimiento_version": "2026-10", "consentimiento_fecha": "2026-10-23T10:00:00-05:00"}
YAPE = "com.bcp.innovacxion.yapeapp"


class EnviadorCaptura(otp.EnviadorOTP):
    """Guarda el último código por celular (en vez de enviarlo por WhatsApp)."""

    def __init__(self):
        self.codigos = {}

    def enviar(self, celular_e164, codigo):
        self.codigos[celular_e164] = codigo
        return True


class Base_(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False)

        def _db():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = _db
        reiniciar_limites_panel()
        reiniciar_limite_vinculacion()
        reiniciar_rate_limit_registro()
        limpiar_cache_bancos()
        self.enviador = EnviadorCaptura()
        otp.usar_enviador(self.enviador)
        self.db = self.Session()
        self._seed()

    def tearDown(self):
        otp.usar_enviador(None)
        self.db.close()
        app.dependency_overrides.clear()
        Base.metadata.drop_all(self.engine)

    # ---------------- datos ----------------
    def _seed(self):
        db = self.db
        db.add(Pais(codigo="PE", nombre="Perú", moneda_codigo="PEN", moneda_simbolo="S/", nombre_id_fiscal="RUC"))
        self.psp = Empresa(pais_codigo="PE", id_fiscal="20615446565", razon_social="PERU SISTEMAS PRO E.I.R.L.",
                           slug="perusistemas", acepta_autoregistro=True, estado_cuenta="activa", prueba_fin=None)
        self.a = Empresa(pais_codigo="PE", id_fiscal="20100000001", razon_social="CLINICA A", slug="clinica-a")
        self.b = Empresa(pais_codigo="PE", id_fiscal="20100000002", razon_social="CLINICA B", slug="clinica-b")
        for e in (self.a, self.b):
            cuenta_empresa.iniciar_prueba(e)
        db.add_all([self.psp, self.a, self.b])
        db.flush()
        self.dueno_a = Usuario(nombre="Ana Dueña", celular="+51911111111")
        self.dueno_b = Usuario(nombre="Beto Dueño", celular="+51922222222")
        db.add_all([self.dueno_a, self.dueno_b])
        db.flush()
        db.add_all([Membresia(usuario_id=self.dueno_a.id, empresa_id=self.a.id, rol="dueno"),
                    Membresia(usuario_id=self.dueno_b.id, empresa_id=self.b.id, rol="dueno")])
        self.disp_a = Dispositivo(empresa_id=self.a.id, token="tok_a", nombre="Cel A", activo=True)
        self.disp_b = Dispositivo(empresa_id=self.b.id, token="tok_b", nombre="Cel B", activo=True)
        self.disp_psp = Dispositivo(empresa_id=self.psp.id, token="tok_psp", nombre="Motorola", activo=True)
        db.add_all([self.disp_a, self.disp_b, self.disp_psp])
        self.key_a, self.key_b = generar_api_key(), generar_api_key()
        db.add_all([CuentaApi(empresa_id=self.a.id, nombre="Sis A", api_key_hash=hash_api_key(self.key_a), activa=True),
                    CuentaApi(empresa_id=self.b.id, nombre="Sis B", api_key_hash=hash_api_key(self.key_b), activa=True)])
        hoy = datetime.utcnow().replace(microsecond=0) - timedelta(minutes=5)
        self.pago_a = PagoDetectado(empresa_id=self.a.id, metodo="yape", monto=Decimal("40.00"),
                                    titular="Juan Perez", recibido_en=hoy, tipo="ingreso")
        self.pago_b = PagoDetectado(empresa_id=self.b.id, metodo="yape", monto=Decimal("77.00"),
                                    titular="Maria Ruiz", recibido_en=hoy, tipo="ingreso")
        db.add_all([self.pago_a, self.pago_b])
        db.commit()

    # ---------------- ayudas ----------------
    def cliente(self):
        return TestClient(app)

    def login(self, celular, cliente=None):
        c = cliente or self.cliente()
        r = c.post("/panel/login/otp", json={"celular": celular})
        self.assertEqual(r.status_code, 200, r.text)
        r = c.post("/panel/login/verificar", json={"celular": celular, "codigo": self.enviador.codigos[celular]})
        self.assertEqual(r.status_code, 200, r.text)
        return c

    def miembro(self, empresa, rol, celular, nombre="Colaborador"):
        u = Usuario(nombre=nombre, celular=celular)
        self.db.add(u)
        self.db.flush()
        self.db.add(Membresia(usuario_id=u.id, empresa_id=empresa.id, rol=rol))
        self.db.commit()
        return u

    def vencer(self, empresa):
        e = self.db.get(Empresa, empresa.id)
        e.prueba_fin = AHORA - timedelta(hours=1)
        self.db.commit()


# =====================================================================
# AISLAMIENTO
# =====================================================================
class TestAislamiento(Base_):
    def test_sesion_de_a_no_ve_ni_toca_b(self):
        c = self.login("+51911111111")
        hoy = c.get("/api/v1/panel/pagos/hoy").json()
        self.assertEqual([p["pago_id"] for p in hoy["pagos"]], [self.pago_a.id])
        r = c.post("/api/v1/panel/pagos/buscar", json={"monto": "77.00"}).json()
        self.assertEqual(r["nivel"], "sin_coincidencia")
        self.assertEqual(c.post(f"/api/v1/panel/pagos/{self.pago_b.id}/marcar-usado",
                                json={"referencia_externa": "X"}).status_code, 404)
        self.assertEqual(c.delete(f"/api/v1/panel/equipo/{self.dueno_b.id}").status_code, 404)
        self.assertEqual([m["usuario_id"] for m in c.get("/api/v1/panel/equipo").json()["miembros"]], [self.dueno_a.id])
        self.assertEqual([d["id"] for d in c.get("/api/v1/panel/dispositivos").json()["dispositivos"]], [self.disp_a.id])
        self.assertEqual([k["nombre"] for k in c.get("/api/v1/panel/api-keys").json()["api_keys"]], ["Sis A"])
        csv_txt = c.get("/api/v1/panel/reportes/movimientos.csv").text
        self.assertIn("Juan Perez", csv_txt)
        self.assertNotIn("Maria Ruiz", csv_txt)

    def test_slug_de_otra_empresa_responde_403(self):
        c = self.login("+51911111111")
        self.assertEqual(c.get("/api/v1/clinica-b/ingresos").status_code, 403)
        self.assertEqual(c.get("/clinica-b/ingresos").status_code, 403)
        self.assertEqual(c.post(f"/api/v1/clinica-b/pagos/{self.pago_b.id}/confirmacion",
                                json={"es_propia": True}).status_code, 403)
        self.assertEqual(c.get("/clinica-b/config/cuentas").status_code, 403)
        # La propia sí.
        self.assertEqual(c.get("/api/v1/clinica-a/ingresos").json()["total"], 1)

    def test_sin_sesion_otra_empresa_exige_login(self):
        c = self.cliente()
        self.assertEqual(c.get("/api/v1/clinica-a/ingresos").status_code, 401)
        r = c.get("/clinica-a/ingresos", follow_redirects=False)
        self.assertEqual((r.status_code, r.headers["location"]), (303, "/panel/login"))

    def test_api_key_de_a_no_ve_ni_marca_b(self):
        h = {"X-API-Key": self.key_a}
        c = self.cliente()
        r = c.post("/api/v1/pagos/consultar", headers=h,
                   json={"monto": "77.00", "fecha_hora_declarada": datetime.now(timezone.utc).isoformat()}).json()
        self.assertEqual(r["nivel"], "sin_coincidencia")
        self.assertEqual(c.post(f"/api/v1/pagos/{self.pago_b.id}/marcar-usado", headers=h,
                                json={"referencia_externa": "X"}).status_code, 404)
        self.assertEqual(c.post(f"/api/v1/pagos/{self.pago_b.id}/reclamar", headers=h).status_code, 404)
        self.assertEqual([p["id"] for p in c.get("/api/v1/pagos", headers=h).json()["pagos"]], [str(self.pago_a.id)])

    def test_dispositivo_de_a_solo_ve_y_guarda_en_a(self):
        c = self.cliente()
        r = c.get("/api/v1/pagos/recientes", headers={"X-Device-Token": "tok_a"}).json()
        self.assertEqual([p["id"] for p in r["pagos"]], [self.pago_a.id])
        with mock.patch("app.api.push_service.enviar_push_a_empresa", return_value={}):
            r = c.post("/api/v1/notificaciones/inbound", headers={"X-Device-Token": "tok_a"},
                       json={"package": YAPE, "titulo": "Yape", "texto": "Luis Diaz te yapeó S/ 12.00"})
        self.assertEqual(self.db.get(PagoDetectado, r.json()["pago_id"]).empresa_id, self.a.id)

    def test_webhooks_solo_a_suscripciones_de_su_empresa(self):
        self.db.add_all([
            SuscripcionWebhook(empresa_id=self.a.id, url_destino="https://a.test", secreto_hmac="sa",
                               tipos_suscritos=["pago.confirmado"]),
            SuscripcionWebhook(empresa_id=self.b.id, url_destino="https://b.test", secreto_hmac="sb",
                               tipos_suscritos=["pago.confirmado"]),
        ])
        self.db.commit()
        with mock.patch("app.services.eventos.despertar_worker"):
            eventos.emitir_pago_confirmado(self.db, self.db.get(PagoDetectado, self.pago_a.id))
        destinos = [e.suscripcion.url_destino for e in self.db.query(EntregaEvento).all()]
        self.assertEqual(destinos, ["https://a.test"])


# =====================================================================
# VINCULACIÓN POR CÓDIGO
# =====================================================================
class TestVinculacion(Base_):
    def _codigo(self):
        c = self.login("+51911111111")
        r = c.post("/api/v1/panel/dispositivos/codigo")
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()
        self.assertRegex(d["codigo"], r"^[A-HJ-NP-Z2-9]{3}-[A-HJ-NP-Z2-9]{3}$")
        self.assertEqual(d["url_qr"], f"pagook://vincular?c={d['codigo']}")
        return d["codigo"]

    def _vincular(self, codigo, **extra):
        body = {"codigo": codigo, "instalacion_id": "inst-1", "manufacturer": "motorola",
                "modelo": "moto g", "android_version": "14", "app_version": "1.6.0", **CONSENTIMIENTO}
        body.update(extra)
        return self.cliente().post("/api/v1/dispositivos/vincular", json=body)

    def test_vincular_ok_y_un_solo_uso(self):
        codigo = self._codigo()
        r = self._vincular(codigo.lower().replace("-", ""), bancos_declarados=["yape", "plin"], pais="PE")
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()
        self.assertEqual(d["empresa"], {"razon_social": "CLINICA A", "id_fiscal": "20100000001",
                                        "id_fiscal_tipo": "RUC", "pais": "PE"})
        disp = self.db.query(Dispositivo).filter_by(token=d["token"]).one()
        self.assertEqual((disp.empresa_id, disp.bancos_declarados, disp.consentimiento_version),
                         (self.a.id, ["yape", "plin"], "2026-10"))
        self.assertEqual(self._vincular(codigo).status_code, 400)   # ya usado

    def test_codigo_vencido_e_invalido_dan_el_mismo_400(self):
        codigo = self._codigo()
        cv = self.db.query(CodigoVinculacion).one()
        cv.expira_en = AHORA - timedelta(minutes=1)
        self.db.commit()
        vencido = self._vincular(codigo)
        invalido = self._vincular("ZZZ-ZZZ")
        self.assertEqual((vencido.status_code, invalido.status_code), (400, 400))
        self.assertEqual(vencido.json()["detail"], invalido.json()["detail"])

    def test_sin_consentimiento_400(self):
        codigo = self._codigo()
        r = self.cliente().post("/api/v1/dispositivos/vincular", json={"codigo": codigo})
        self.assertEqual(r.status_code, 400)
        self.assertIn("consentimiento", r.json()["detail"])

    def test_limite_5_fallos_por_ip(self):
        for _ in range(5):
            self.assertEqual(self._vincular("AAA-AAA").status_code, 400)
        self.assertEqual(self._vincular("AAA-AAA").status_code, 429)


# =====================================================================
# INVITACIONES Y REVOCACIÓN
# =====================================================================
class TestInvitaciones(Base_):
    def _invitar(self, rol="cajero"):
        dueno = self.login("+51911111111")
        r = dueno.post("/api/v1/panel/invitaciones", json={"rol": rol})
        self.assertEqual(r.status_code, 200, r.text)
        return dueno, r.json()["url"].rsplit("/", 1)[1]

    def _unirse(self, token, celular="+51933333333", nombre="Carla Caja"):
        c = self.cliente()
        r = c.post(f"/unirse/{token}/otp", json={"nombre": nombre, "celular": celular})
        if r.status_code != 200:
            return c, r
        return c, c.post(f"/unirse/{token}/verificar",
                         json={"nombre": nombre, "celular": celular, "codigo": self.enviador.codigos[celular]})

    def test_invitacion_asigna_rol_y_es_de_un_solo_uso(self):
        _, token = self._invitar("almacen")
        self.assertEqual(self.cliente().get(f"/unirse/{token}").status_code, 200)
        c, r = self._unirse(token)
        self.assertEqual(r.status_code, 200, r.text)
        yo = c.get("/api/v1/panel/yo").json()
        self.assertEqual((yo["rol"], yo["empresa"]["id"]), ("almacen", self.a.id))
        self.assertEqual(self._unirse(token, celular="+51944444444")[1].status_code, 400)

    def test_invitacion_vence_a_las_24h(self):
        _, token = self._invitar()
        inv = self.db.query(Invitacion).one()
        inv.expira_en = AHORA - timedelta(seconds=1)
        self.db.commit()
        self.assertEqual(self._unirse(token)[1].status_code, 400)

    def test_revocar_invalida_sesiones_en_el_acto(self):
        dueno, token = self._invitar("cajero")
        cajero, r = self._unirse(token)
        self.assertEqual(cajero.get("/api/v1/panel/pagos/hoy").status_code, 200)
        uid = self.db.query(Usuario).filter_by(celular="+51933333333").one().id
        r = dueno.delete(f"/api/v1/panel/equipo/{uid}")
        self.assertEqual((r.status_code, r.json()["sesiones_cerradas"]), (200, 1))
        self.assertEqual(cajero.get("/api/v1/panel/pagos/hoy").status_code, 401)

    def test_dueno_no_puede_quitarse_a_si_mismo(self):
        dueno = self.login("+51911111111")
        self.assertEqual(dueno.delete(f"/api/v1/panel/equipo/{self.dueno_a.id}").status_code, 400)


# =====================================================================
# MATRIZ DE PERMISOS
# =====================================================================
class TestPermisos(Base_):
    def test_matriz_completa(self):
        clientes = {"dueno": self.login("+51911111111")}
        for i, rol in enumerate(("cajero", "almacen", "contador")):
            cel = f"+5195555555{i}"
            self.miembro(self.a, rol, cel)
            clientes[rol] = self.login(cel)

        pid = self.pago_a.id
        acciones = {
            "ver_pagos": lambda c: c.get("/api/v1/panel/pagos/hoy"),
            "buscar": lambda c: c.post("/api/v1/panel/pagos/buscar", json={"monto": "40.00"}),
            "reportes": lambda c: c.get("/api/v1/panel/reportes/movimientos.csv"),
            "dispositivos": lambda c: c.get("/api/v1/panel/dispositivos"),
            "codigo": lambda c: c.post("/api/v1/panel/dispositivos/codigo"),
            "equipo": lambda c: c.get("/api/v1/panel/equipo"),
            "invitar": lambda c: c.post("/api/v1/panel/invitaciones", json={"rol": "cajero"}),
            "api_keys": lambda c: c.get("/api/v1/panel/api-keys"),
        }
        esperado = {  # dueño, cajero, almacén, contador
            "ver_pagos": (200, 200, 403, 200),
            "buscar": (200, 200, 200, 200),
            "reportes": (200, 403, 403, 200),
            "dispositivos": (200, 403, 403, 403),
            "codigo": (200, 403, 403, 403),
            "equipo": (200, 403, 403, 403),
            "invitar": (200, 403, 403, 403),
            "api_keys": (200, 403, 403, 403),
        }
        for accion, fn in acciones.items():
            obtenido = tuple(fn(clientes[r]).status_code for r in ("dueno", "cajero", "almacen", "contador"))
            self.assertEqual(obtenido, esperado[accion], accion)

        # Marcar usado: dueño y cajero sí; almacén y contador no.
        self.assertEqual(clientes["almacen"].post(f"/api/v1/panel/pagos/{pid}/marcar-usado",
                                                  json={"referencia_externa": "P1"}).status_code, 403)
        self.assertEqual(clientes["contador"].post(f"/api/v1/panel/pagos/{pid}/marcar-usado",
                                                   json={"referencia_externa": "P1"}).status_code, 403)
        self.assertEqual(clientes["cajero"].post(f"/api/v1/panel/pagos/{pid}/marcar-usado",
                                                 json={"referencia_externa": "P1"}).status_code, 200)
        self.assertEqual(clientes["dueno"].post(f"/api/v1/panel/pagos/{pid}/marcar-usado",
                                                json={"referencia_externa": "P2"}).status_code, 409)

        # Almacén: solo si existe, sin listado, pagador ni totales.
        r = clientes["almacen"].post("/api/v1/panel/pagos/buscar", json={"monto": "40.00"}).json()
        self.assertNotIn("pago", r)
        self.assertNotIn("candidatos", r)
        self.assertIn("existe", r)

    def test_pago_usado_en_panel_ya_no_esta_disponible_en_la_api(self):
        self.login("+51911111111").post(f"/api/v1/panel/pagos/{self.pago_a.id}/marcar-usado",
                                         json={"referencia_externa": "BOLETA-1"})
        h = {"X-API-Key": self.key_a}
        c = self.cliente()
        self.assertEqual(c.get("/api/v1/pagos", headers=h).json()["pagos"], [])
        self.assertEqual(c.post(f"/api/v1/pagos/{self.pago_a.id}/reclamar", headers=h).status_code, 409)


# =====================================================================
# PRUEBA 7 + 15 Y CUENTA VENCIDA
# =====================================================================
class TestCuenta(Base_):
    def test_prueba_7_dias_ampliacion_unica_y_409(self):
        e = self.db.get(Empresa, self.a.id)
        self.assertEqual(cuenta_empresa.dias_restantes(e), 7)
        self.assertFalse(cuenta_empresa.info_cuenta(self.db, e)["puede_ampliar"])
        e.prueba_fin = AHORA + timedelta(days=1, hours=1)
        self.db.commit()
        self.assertTrue(cuenta_empresa.info_cuenta(self.db, e)["puede_ampliar"])

        c = self.cliente()
        r = c.post("/api/v1/cuenta/ampliar-prueba", headers={"X-Device-Token": "tok_a"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["cuenta"]["dias_restantes"], 17)
        r2 = c.post("/api/v1/cuenta/ampliar-prueba", headers={"X-Device-Token": "tok_a"})
        self.assertEqual(r2.status_code, 409)
        self.assertIn("ya fue ampliada", r2.json()["detail"])

    def test_ampliar_desde_sesion_solo_dueno(self):
        self.miembro(self.a, "cajero", "+51966666666")
        cajero = self.login("+51966666666")
        self.assertEqual(cajero.post("/api/v1/cuenta/ampliar-prueba").status_code, 403)
        self.assertEqual(self.login("+51911111111").post("/api/v1/cuenta/ampliar-prueba").status_code, 200)

    def test_paso_perezoso_a_vencida(self):
        self.vencer(self.a)
        e = self.db.get(Empresa, self.a.id)
        self.assertEqual(e.estado_cuenta, "prueba")                # aún no se leyó
        r = self.cliente().post("/api/v1/device/heartbeat", headers={"X-Device-Token": "tok_a"}, json={
            "estado": "verde", "ultimo_ping_guardian_ok": 0, "ultimo_pago_bancario": 0,
            "pings_fallidos_consecutivos": 0, "veces_zombie_total": 0, "manufacturer": "m",
            "modelo": "x", "android_version": "14", "app_version": "1.5.2"})
        self.assertEqual(r.json()["cuenta"], {"estado": "vencida", "dias_restantes": 0, "puede_ampliar": False})
        self.db.expire_all()
        self.assertEqual(self.db.get(Empresa, self.a.id).estado_cuenta, "vencida")
        self.assertEqual(self.cliente().post("/api/v1/cuenta/ampliar-prueba",
                                             headers={"X-Device-Token": "tok_a"}).status_code, 409)

    def test_vencida_captura_si_consulta_402_panel_solo_lectura(self):
        self.vencer(self.a)
        c = self.cliente()
        with mock.patch("app.api.push_service.enviar_push_a_empresa", return_value={}):
            r = c.post("/api/v1/notificaciones/inbound", headers={"X-Device-Token": "tok_a"},
                       json={"package": YAPE, "titulo": "Yape", "texto": "Luis Diaz te yapeó S/ 12.00"})
        self.assertIsNotNone(r.json()["pago_id"])                  # el pago se guardó
        r = c.post("/api/v1/pagos/consultar", headers={"X-API-Key": self.key_a},
                   json={"monto": "12.00", "fecha_hora_declarada": datetime.now(timezone.utc).isoformat()})
        self.assertEqual((r.status_code, r.json()["detail"]), (402, "cuenta_vencida"))
        dueno = self.login("+51911111111")
        self.assertEqual(dueno.get("/api/v1/panel/pagos/hoy").status_code, 200)      # leer sí
        self.assertEqual(dueno.post("/api/v1/panel/dispositivos/codigo").status_code, 402)
        self.assertEqual(dueno.post("/api/v1/panel/invitaciones", json={"rol": "cajero"}).status_code, 402)
        self.assertTrue(dueno.get("/api/v1/panel/yo").json()["solo_lectura"])

    def test_vencida_webhooks_quedan_en_cola_hasta_activar(self):
        s = SuscripcionWebhook(empresa_id=self.a.id, url_destino="https://a.test", secreto_hmac="sa",
                               tipos_suscritos=["pago.confirmado"])
        self.db.add(s)
        self.db.commit()
        with mock.patch("app.services.eventos.despertar_worker"):
            eventos.emitir_pago_confirmado(self.db, self.db.get(PagoDetectado, self.pago_a.id))
        self.vencer(self.a)
        enviados = []
        enviar = lambda url, cuerpo, cab: (enviados.append(url) or (200, None))  # noqa: E731
        luego = datetime.now(timezone.utc) + timedelta(minutes=1)
        self.assertEqual(eventos.procesar_pendientes(self.db, enviar=enviar, ahora=luego), 0)
        entrega = self.db.query(EntregaEvento).one()
        self.assertEqual((entrega.ultimo_estado, entrega.intentos), ("pendiente", 0))
        cuenta_empresa.activar(self.db.get(Empresa, self.a.id))
        self.db.commit()
        self.assertEqual(eventos.procesar_pendientes(self.db, enviar=enviar, ahora=luego), 1)
        self.assertEqual(enviados, ["https://a.test"])

    def test_perusistemas_siempre_activa(self):
        psp = self.db.get(Empresa, self.psp.id)
        psp.estado_cuenta, psp.prueba_fin = "prueba", AHORA - timedelta(days=30)
        self.db.commit()
        self.assertEqual(cuenta_empresa.refrescar_estado(self.db, psp), "activa")
        with self.assertRaises(cuenta_empresa.CuentaError):
            cuenta_empresa.suspender(psp)


# =====================================================================
# COMPATIBILIDAD CON LA APP 1.5.x
# =====================================================================
class TestCompatibilidad(Base_):
    def test_registro_heartbeat_e_inbound_viejos_caen_en_perusistemas(self):
        c = self.cliente()
        r = c.post("/api/v1/dispositivos/registrar", json={
            "empresa_slug": "perusistemas", "instalacion_id": "viejo-1", "manufacturer": "motorola",
            "modelo": "moto e6", "android_version": "9", "app_version": "1.5.0"})
        self.assertEqual(r.status_code, 201, r.text)
        token = r.json()["token"]
        self.assertEqual(self.db.query(Dispositivo).filter_by(token=token).one().empresa_id, self.psp.id)
        r = c.post("/api/v1/device/heartbeat", headers={"X-Device-Token": token}, json={
            "estado": "verde", "ultimo_ping_guardian_ok": 0, "ultimo_pago_bancario": 0,
            "pings_fallidos_consecutivos": 0, "veces_zombie_total": 0, "manufacturer": "motorola",
            "modelo": "moto e6", "android_version": "9", "app_version": "1.5.0"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["cuenta"]["estado"], "activa")
        with mock.patch("app.api.push_service.enviar_push_a_empresa", return_value={}):
            r = c.post("/api/v1/notificaciones/inbound", headers={"X-Device-Token": token},
                       json={"package": YAPE, "titulo": "Yape", "texto": "Juan Perez te yapeó S/ 15.00"})
        self.assertEqual(self.db.get(PagoDetectado, r.json()["pago_id"]).empresa_id, self.psp.id)

    def test_registro_viejo_no_entra_a_otra_empresa_sin_codigo(self):
        r = self.cliente().post("/api/v1/dispositivos/registrar",
                                json={"empresa_slug": "clinica-a", "instalacion_id": "x"})
        self.assertEqual(r.status_code, 403)

    def test_heartbeat_guarda_campos_nuevos_opcionales(self):
        self.cliente().post("/api/v1/device/heartbeat", headers={"X-Device-Token": "tok_psp"}, json={
            "estado": "verde", "ultimo_ping_guardian_ok": 0, "ultimo_pago_bancario": 0,
            "pings_fallidos_consecutivos": 0, "veces_zombie_total": 0, "manufacturer": "m", "modelo": "x",
            "android_version": "14", "app_version": "1.5.2", "pais": "pe", "nivel_equipo": "alto",
            "bancos_declarados": ["yape", "plin"], "egresos_activo": False, **CONSENTIMIENTO})
        d = self.db.query(Dispositivo).filter_by(token="tok_psp").one()
        self.assertEqual((d.pais, d.nivel_equipo, d.bancos_declarados, d.egresos_activo),
                         ("PE", "alto", ["yape", "plin"], False))
        self.assertIsNotNone(d.consentimiento_fecha)

    def test_ingresos_perusistemas_publico_mientras_legacy(self):
        c = self.cliente()
        self.assertEqual(c.get("/perusistemas/ingresos").status_code, 200)
        self.assertEqual(c.get("/api/v1/perusistemas/ingresos").status_code, 200)
        with mock.patch.dict(os.environ, {"LEGACY_PUBLICO_PERUSISTEMAS": "false"}):
            r = c.get("/perusistemas/ingresos", follow_redirects=False)
            self.assertEqual((r.status_code, r.headers["location"]), (303, "/panel/login"))
            self.assertEqual(c.get("/api/v1/perusistemas/ingresos").status_code, 401)


# =====================================================================
# OTP Y CATÁLOGO DE BANCOS
# =====================================================================
class TestOtpYBancos(Base_):
    def test_respuesta_igual_exista_o_no_el_celular(self):
        c = self.cliente()
        r1 = c.post("/panel/login/otp", json={"celular": "+51911111111"})
        r2 = c.post("/panel/login/otp", json={"celular": "+51900000000"})
        self.assertEqual((r1.status_code, r1.json()), (r2.status_code, r2.json()))
        self.assertIn("+51911111111", self.enviador.codigos)
        self.assertNotIn("+51900000000", self.enviador.codigos)

    def test_codigo_incorrecto_y_maximo_5_intentos(self):
        c = self.cliente()
        c.post("/panel/login/otp", json={"celular": "+51911111111"})
        bueno = self.enviador.codigos["+51911111111"]
        malo = "000000" if bueno != "000000" else "111111"
        for _ in range(5):
            self.assertEqual(c.post("/panel/login/verificar",
                                    json={"celular": "+51911111111", "codigo": malo}).status_code, 400)
        # Tras 5 fallos, ni el código correcto sirve.
        self.assertEqual(c.post("/panel/login/verificar",
                                json={"celular": "+51911111111", "codigo": bueno}).status_code, 400)

    def test_produccion_sin_whatsapp_responde_503(self):
        otp.usar_enviador(None)
        with mock.patch.dict(os.environ, {"PAGOOK_ENTORNO": "produccion", "WA_ACCESS_TOKEN": ""}):
            r = self.cliente().post("/panel/login/otp", json={"celular": "+51911111111"})
        self.assertEqual((r.status_code, r.json()["detail"]), (503, "otp_no_disponible"))

    def test_sesion_cookie_httponly_y_30_dias(self):
        c = self.cliente()
        c.post("/panel/login/otp", json={"celular": "+51911111111"})
        r = c.post("/panel/login/verificar",
                   json={"celular": "+51911111111", "codigo": self.enviador.codigos["+51911111111"]})
        cookie = r.headers["set-cookie"].lower()
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=lax", cookie)
        self.assertIn(f"max-age={30 * 86400}", cookie)
        ses = self.db.query(Sesion).one()
        self.assertNotIn(c.cookies.get("pagook_sesion"), ses.token_hash)   # solo el hash en BD

    def test_bancos_config(self):
        self.db.add_all([
            BancoConfig(pais="PE", clave="bcp", nombre="BCP", tipo="banco", orden=1),
            BancoConfig(pais="PE", clave="plin", nombre="Plin", tipo="billetera", orden=2),
            BancoConfig(pais="PE", clave="yape", nombre="Yape", tipo="billetera", orden=1),
        ])
        self.db.commit()
        c = self.cliente()
        r = c.get("/api/v1/bancos-config?pais=PE")
        self.assertEqual([b["clave"] for b in r.json()["bancos"]], ["yape", "plin", "bcp"])
        self.assertIn("max-age=3600", r.headers["cache-control"])
        r = c.get("/api/v1/bancos-config?pais=BO")
        self.assertEqual((r.status_code, r.json()), (200, {"bancos": []}))


# =====================================================================
# PÁGINAS DEL PANEL (renderizan sin errores)
# =====================================================================
class TestPaginas(Base_):
    def test_paginas_renderizan_segun_rol(self):
        c = self.cliente()
        r = c.get("/panel", follow_redirects=False)
        self.assertEqual((r.status_code, r.headers["location"]), (303, "/panel/login"))
        self.assertIn("Enviarme el código", c.get("/panel/login").text)

        dueno = self.login("+51911111111")
        inicio = dueno.get("/panel").text
        self.assertIn("CLINICA A", inicio)
        self.assertIn("periodo de prueba", inicio)
        self.assertIn("/panel/dispositivos", inicio)
        self.assertIn("Vincular un celular", dueno.get("/panel/dispositivos").text)
        self.assertIn("Quitar acceso", dueno.get("/panel/equipo").text)
        self.assertEqual(dueno.get("/panel/elegir-empresa").status_code, 200)
        self.assertNotIn("#ffffff", dueno.get("/static/panel/panel.css").text.lower())

        self.miembro(self.a, "almacen", "+51977777777")
        almacen = self.login("+51977777777")
        inicio = almacen.get("/panel").text
        self.assertNotIn("Pagos de hoy", inicio)
        self.assertNotIn("/panel/dispositivos", inicio)
        r = almacen.get("/panel/equipo", follow_redirects=False)
        self.assertEqual((r.status_code, r.headers["location"]), (303, "/panel"))

        self.vencer(self.a)
        self.assertIn("solo lectura", dueno.get("/panel").text)

    def test_unirse_con_enlace_invalido(self):
        self.assertIn("ya no es válido", self.cliente().get("/unirse/no-existe").text)


# =====================================================================
# RUTAS CERRADAS ANTES DEL DESPLIEGUE
# =====================================================================
class TestRutasCerradas(Base_):
    def setUp(self):
        super().setUp()
        # router_demo abre su propia sesión (no usa get_db): se apunta a SQLite.
        self._p = mock.patch("app.api.router_demo.SessionLocal", self.Session)
        self._p.start()

    def tearDown(self):
        self._p.stop()
        super().tearDown()

    # ---- /admin/empresas/nuevas: token de administrador ----
    def test_empresas_nuevas_exige_token_admin(self):
        c = self.cliente()
        form = {"ruc": "20111111111", "razon_social": "INTRUSA SAC"}
        with mock.patch.dict(os.environ, {"ADMIN_TOKEN": "secreto-admin"}):
            self.assertEqual(c.get("/admin/empresas/nuevas").status_code, 401)
            self.assertEqual(c.get("/admin/empresas/nuevas?token=malo").status_code, 401)
            self.assertNotIn("CLINICA A", c.get("/admin/empresas/nuevas").text)   # no lista empresas
            self.assertEqual(c.post("/admin/empresas/nuevas", data=form).status_code, 401)
            self.assertEqual(c.post("/admin/empresas/nuevas", data={**form, "token": "malo"}).status_code, 401)
            self.assertEqual(self.db.query(Empresa).filter_by(id_fiscal="20111111111").count(), 0)
            ok = c.get("/admin/empresas/nuevas?token=secreto-admin")
            self.assertEqual(ok.status_code, 200)
            self.assertIn('name="token" value="secreto-admin"', ok.text)
            r = c.post("/admin/empresas/nuevas", data={**form, "token": "secreto-admin"})
            self.assertEqual(r.status_code, 200)
        self.assertEqual(self.db.query(Empresa).filter_by(id_fiscal="20111111111").count(), 1)

    def test_empresas_nuevas_sin_admin_token_configurado_queda_cerrada(self):
        with mock.patch.dict(os.environ, {"ADMIN_TOKEN": ""}):
            self.assertEqual(self.cliente().get("/admin/empresas/nuevas?token=").status_code, 401)

    # ---- /flujo-caja/{slug}: sesión con permiso de reportes ----
    def test_flujo_caja_aislado_por_sesion_y_rol(self):
        anon = self.cliente()
        self.assertEqual(anon.get("/api/v1/flujo-caja/clinica-a").status_code, 401)
        r = anon.get("/flujo-caja/clinica-a", follow_redirects=False)
        self.assertEqual((r.status_code, r.headers["location"]), (303, "/panel/login"))

        dueno_a = self.login("+51911111111")
        self.assertEqual(dueno_a.get("/api/v1/flujo-caja/clinica-a").status_code, 200)
        self.assertEqual(dueno_a.get("/flujo-caja/clinica-a").status_code, 200)
        self.assertEqual(dueno_a.get("/api/v1/flujo-caja/clinica-b").status_code, 403)
        self.assertEqual(dueno_a.get("/flujo-caja/clinica-b").status_code, 403)

        self.miembro(self.a, "cajero", "+51988888881")
        cajero = self.login("+51988888881")          # cajero no tiene "reportes"
        self.assertEqual(cajero.get("/api/v1/flujo-caja/clinica-a").status_code, 403)
        self.miembro(self.a, "contador", "+51988888882")
        self.assertEqual(self.login("+51988888882").get("/api/v1/flujo-caja/clinica-a").status_code, 200)

    def test_flujo_caja_perusistemas_legacy(self):
        anon = self.cliente()
        self.assertEqual(anon.get("/api/v1/flujo-caja/perusistemas").status_code, 200)
        with mock.patch.dict(os.environ, {"LEGACY_PUBLICO_PERUSISTEMAS": "false"}):
            self.assertEqual(anon.get("/api/v1/flujo-caja/perusistemas").status_code, 401)

    # ---- /{slug}/receptores: sesión con permiso de reportes ----
    def test_receptores_aislados(self):
        from app.admin.models import CodigoInvitacion
        anon = self.cliente()
        r = anon.get("/clinica-a/receptores", follow_redirects=False)
        self.assertEqual((r.status_code, r.headers["location"]), (303, "/panel/login"))
        self.assertEqual(anon.post("/clinica-a/receptores/codigo-nuevo", data={}).status_code, 401)
        self.assertEqual(anon.post("/clinica-a/receptores/1/revocar").status_code, 401)
        self.assertEqual(anon.post("/clinica-a/receptores/codigo/1/desactivar").status_code, 401)
        self.assertEqual(self.db.query(CodigoInvitacion).count(), 0)

        dueno_b = self.login("+51922222222")
        self.assertEqual(dueno_b.get("/clinica-a/receptores").status_code, 403)
        self.assertEqual(dueno_b.post("/clinica-a/receptores/codigo-nuevo", data={}).status_code, 403)
        self.assertEqual(self.db.query(CodigoInvitacion).count(), 0)

        self.miembro(self.a, "cajero", "+51988888883")
        self.assertEqual(self.login("+51988888883").get("/clinica-a/receptores").status_code, 403)

        dueno_a = self.login("+51911111111")
        self.assertEqual(dueno_a.get("/clinica-a/receptores").status_code, 200)
        r = dueno_a.post("/clinica-a/receptores/codigo-nuevo", data={}, follow_redirects=False)
        self.assertIn(r.status_code, (200, 303))
        self.db.expire_all()
        self.assertEqual(self.db.query(CodigoInvitacion).filter_by(empresa_id=self.a.id).count(), 1)

    def test_receptores_perusistemas_legacy(self):
        self.assertEqual(self.cliente().get("/perusistemas/receptores").status_code, 200)
        with mock.patch.dict(os.environ, {"LEGACY_PUBLICO_PERUSISTEMAS": "false"}):
            r = self.cliente().get("/perusistemas/receptores", follow_redirects=False)
            self.assertEqual(r.status_code, 303)

    # ---- /api/v1/push/test/{slug}: sesión de dueño de esa empresa ----
    def test_push_test_solo_dueno_de_la_empresa(self):
        with mock.patch("app.api.push_service.enviar_push_a_empresa", return_value={"enviados": 0}) as enviar:
            self.assertEqual(self.cliente().post("/api/v1/push/test/clinica-a").status_code, 401)
            self.assertEqual(self.login("+51922222222").post("/api/v1/push/test/clinica-a").status_code, 403)
            for i, rol in enumerate(("cajero", "almacen", "contador")):
                cel = f"+5198888889{i}"
                self.miembro(self.a, rol, cel)
                self.assertEqual(self.login(cel).post("/api/v1/push/test/clinica-a").status_code, 403, rol)
            enviar.assert_not_called()
            self.assertEqual(self.login("+51911111111").post("/api/v1/push/test/clinica-a").status_code, 200)
            self.assertEqual(enviar.call_args.kwargs["empresa_id"], self.a.id)

    def test_push_test_perusistemas_legacy(self):
        with mock.patch("app.api.push_service.enviar_push_a_empresa", return_value={"enviados": 0}):
            self.assertEqual(self.cliente().post("/api/v1/push/test/perusistemas").status_code, 200)
            with mock.patch.dict(os.environ, {"LEGACY_PUBLICO_PERUSISTEMAS": "false"}):
                self.assertEqual(self.cliente().post("/api/v1/push/test/perusistemas").status_code, 401)

    # ---- /{slug} y /api/v1/demo/{slug}: solo slugs de demo ----
    def test_demo_solo_para_slugs_de_demo(self):
        c = self.cliente()
        self.assertEqual(c.get("/perusistemas").status_code, 200)
        self.assertEqual(c.get("/api/v1/demo/perusistemas").status_code, 200)
        for ruta in ("/clinica-a", "/20100000001"):        # slug y RUC de una empresa no demo
            self.assertEqual(c.get(ruta).status_code, 404, ruta)
        no_demo = c.get("/api/v1/demo/clinica-a")
        inexistente = c.get("/api/v1/demo/no-existe")
        self.assertEqual((no_demo.status_code, no_demo.json()), (inexistente.status_code, inexistente.json()))
        self.assertEqual(c.post("/api/v1/demo/clinica-a/buscar", json={"monto": "40.00"}).status_code, 404)
        self.assertNotIn("Juan Perez", no_demo.text)

    def test_slugs_demo_configurables(self):
        c = self.cliente()
        with mock.patch.dict(os.environ, {"SLUGS_DEMO": "clinica-a"}):
            self.assertEqual(c.get("/api/v1/demo/clinica-a").status_code, 200)
            self.assertEqual(c.get("/api/v1/demo/perusistemas").status_code, 404)

    def test_testers_solo_enlaza_demos(self):
        html = self.cliente().get("/testers").text
        self.assertIn("Toca la demo para ver sus pagos en vivo", html)
        self.assertIn('href="/perusistemas"', html)
        self.assertNotIn('href="/clinica-a"', html)
        self.assertNotIn('href="/clinica-b"', html)


# =====================================================================
# SCRIPT DE GESTIÓN
# =====================================================================
class TestScriptEmpresas(Base_):
    def test_crear_activar_suspender_listar(self):
        from scripts import gestion_empresas as g
        log = g.crear_empresa(self.db, pais="PE", id_fiscal="20601234567", razon_social=None,
                              dueno_nombre="Zoe Dueña", dueno_celular="987 000 111",
                              consultar=lambda ruc: {"ruc": ruc, "razon_social": "CLINICA NUEVA S.A.C.",
                                                     "estado": "ACTIVO", "condicion": "HABIDO", "direccion": ""})
        self.db.commit()
        e = self.db.query(Empresa).filter_by(id_fiscal="20601234567").one()
        self.assertEqual((e.razon_social, e.estado_cuenta, e.id_fiscal_tipo, e.acepta_autoregistro),
                         ("CLINICA NUEVA S.A.C.", "prueba", "RUC", False))
        u = self.db.query(Usuario).filter_by(celular="+51987000111").one()
        self.assertEqual(self.db.query(Membresia).filter_by(usuario_id=u.id, empresa_id=e.id).one().rol, "dueno")
        self.assertTrue(any("RUC validado" in l for l in log))

        g.activar_empresa(self.db, e.slug)
        self.assertEqual((e.estado_cuenta, e.prueba_fin), ("activa", None))
        g.suspender_empresa(self.db, e.slug)
        self.assertEqual(e.estado_cuenta, "suspendida")
        with self.assertRaises(g.ErrorGestion):
            g.suspender_empresa(self.db, "perusistemas")
        self.assertTrue(any(e.slug in l for l in g.listar_empresas(self.db)))

    def test_crear_sin_api_de_ruc_exige_razon_social(self):
        from scripts import gestion_empresas as g
        with self.assertRaises(g.ErrorGestion):
            g.crear_empresa(self.db, pais="PE", id_fiscal="20601234568", razon_social=None,
                            dueno_nombre="X", dueno_celular="+51987000222", consultar=lambda ruc: None)

    def test_asignar_dueno_a_empresa_existente(self):
        from scripts import gestion_empresas as g
        # Simulación: no agrega nada.
        log = g.asignar_dueno(self.db, "clinica-b", "Duilio", "967 317 946", escribir=False)
        self.assertTrue(any("Usuario dueño nuevo" in l for l in log))
        self.assertIsNone(self.db.query(Usuario).filter_by(celular="+51967317946").first())
        # Aplicado: usuario nuevo + membresía de dueño; ya puede pedir código.
        g.asignar_dueno(self.db, "clinica-b", "Duilio", "+51967317946")
        self.db.commit()
        u = self.db.query(Usuario).filter_by(celular="+51967317946").one()
        self.assertEqual(self.db.query(Membresia).filter_by(usuario_id=u.id, empresa_id=self.b.id).one().rol, "dueno")
        with self.assertRaises(g.ErrorGestion):
            g.asignar_dueno(self.db, "clinica-b", "Duilio", "+51967317946")
        # Usuario existente (dueño de A) que pasa a ser también dueño de B, y miembro que sube a dueño.
        g.asignar_dueno(self.db, "clinica-b", "X", "+51911111111")
        otro = self.miembro(self.b, "cajero", "+51933333333")
        g.asignar_dueno(self.db, "clinica-b", "X", "+51933333333")
        self.db.commit()
        self.assertEqual(self.db.query(Membresia).filter_by(usuario_id=otro.id, empresa_id=self.b.id).one().rol, "dueno")
        with self.assertRaises(g.ErrorGestion):
            g.asignar_dueno(self.db, "no-existe", "X", "+51944444444")


if __name__ == "__main__":
    unittest.main()
