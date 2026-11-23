"""Tests de zWeb-14: entrar con el número desde la app y autorregistro de
independientes (LATAM). SQLite en memoria + TestClient.

Cubre: configuración por país y validación de cada país; flujo de un número
nuevo, de un dueño con una empresa y de un dueño con varias; tickets vencidos,
reutilizados o de otro número; un número existente no se registra dos veces;
autorregistro apagado; el cajero no vincula; el código de revisión y
/dispositivos/vincular siguen igual; /admin/registros; el selector de empresas
del panel; y la política de privacidad v3.

Ejecutar:  python -m unittest tests.test_acceso_app
"""
import csv
import hashlib
import io
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
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
from app.api.models_multiempresa import Membresia, Otp, TicketAcceso, Usuario  # noqa: E402
from app.api.models_webhook import Dispositivo  # noqa: E402
from app.api.router_acceso_app import reiniciar_limites_acceso_app  # noqa: E402
from app.api.router_device import reiniciar_rate_limit_registro  # noqa: E402
from app.api.router_panel import reiniciar_limites_panel  # noqa: E402
from app.api.router_vinculacion import limpiar_cache_bancos, reiniciar_limite_vinculacion  # noqa: E402
from app.main import app  # noqa: E402
from app.services import cuenta_empresa, otp, registro_paises, revision  # noqa: E402

EQUIPO = {"instalacion_id": "inst-1", "manufacturer": "motorola", "modelo": "moto g06",
          "android_version": "15", "app_version": "1.8.0", "consentimiento_version": "2026-11-v3",
          "bancos_declarados": ["yape"]}
NUEVO = "+51955555555"
REV_CELULAR = "+51900000111"
REV_CODIGO = "REV-2026-OK"
ADMIN_TOKEN = "token-admin-de-prueba-zweb14"


class EnviadorCaptura(otp.EnviadorOTP):
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
        for reiniciar in (reiniciar_limites_panel, reiniciar_limite_vinculacion, reiniciar_rate_limit_registro,
                          limpiar_cache_bancos, reiniciar_limites_acceso_app, revision.reiniciar_fallos_panel):
            reiniciar()
        self.enviador = EnviadorCaptura()
        otp.usar_enviador(self.enviador)
        self.env = mock.patch.dict(os.environ, {"AUTOREGISTRO_ACTIVO": "true", "PRUEBA_DIAS_INDEPENDIENTE": "14",
                                                "ADMIN_TOKEN": ADMIN_TOKEN})
        self.env.start()
        self.db = self.Session()
        self._seed()
        self.c = TestClient(app)

    def tearDown(self):
        self.env.stop()
        otp.usar_enviador(None)
        self.db.close()
        app.dependency_overrides.clear()
        Base.metadata.drop_all(self.engine)

    def _seed(self):
        db = self.db
        for cod, nom, mon, fis in (("PE", "Perú", "PEN", "RUC"), ("CO", "Colombia", "COP", "NIT"),
                                   ("MX", "México", "MXN", "RFC")):
            db.add(Pais(codigo=cod, nombre=nom, moneda_codigo=mon, moneda_simbolo="$", nombre_id_fiscal=fis))
        self.a = Empresa(pais_codigo="PE", id_fiscal="20100000001", id_fiscal_tipo="RUC",
                         razon_social="CLINICA A", slug="clinica-a")
        self.b = Empresa(pais_codigo="PE", id_fiscal="20100000002", id_fiscal_tipo="RUC",
                         razon_social="TALLER B", slug="taller-b")
        self.c_emp = Empresa(pais_codigo="PE", id_fiscal="20100000003", id_fiscal_tipo="RUC",
                             razon_social="FARMACIA C", nombre_comercial="BOTICA C", slug="botica-c")
        self.rev = Empresa(pais_codigo="PE", id_fiscal="20999999999", razon_social="DEMO REVISION",
                           slug="revision-play", es_cuenta_revision=True)
        for e in (self.a, self.b, self.c_emp, self.rev):
            cuenta_empresa.iniciar_prueba(e)
        db.add_all([self.a, self.b, self.c_emp, self.rev])
        db.flush()
        self.ana = Usuario(nombre="Ana", celular="+51911111111")      # dueña de A y C
        self.beto = Usuario(nombre="Beto", celular="+51922222222")    # dueño de B
        self.carla = Usuario(nombre="Carla", celular="+51933333333")  # cajera de A
        self.revisor = Usuario(nombre="Revisor", celular=REV_CELULAR)
        db.add_all([self.ana, self.beto, self.carla, self.revisor])
        db.flush()
        db.add_all([
            Membresia(usuario_id=self.ana.id, empresa_id=self.a.id, rol="dueno"),
            Membresia(usuario_id=self.ana.id, empresa_id=self.c_emp.id, rol="dueno"),
            Membresia(usuario_id=self.beto.id, empresa_id=self.b.id, rol="dueno"),
            Membresia(usuario_id=self.carla.id, empresa_id=self.a.id, rol="cajero"),
            Membresia(usuario_id=self.revisor.id, empresa_id=self.rev.id, rol="contador"),
        ])
        db.commit()

    # ---------------- ayudas ----------------
    def solicitar(self, celular, pais="PE"):
        return self.c.post("/api/v1/app/acceso/solicitar", json={"pais": pais, "celular": celular})

    def verificar(self, celular, pais="PE", codigo=None, **extra):
        e164 = registro_paises.normalizar(registro_paises.pais(pais), celular) or celular
        body = {"pais": pais, "celular": celular,
                "codigo": codigo if codigo is not None else self.enviador.codigos.get(e164), **EQUIPO}
        body.update(extra)
        return self.c.post("/api/v1/app/acceso/verificar", json=body)

    def entrar(self, celular, pais="PE", **extra):
        r = self.solicitar(celular, pais)
        self.assertEqual(r.status_code, 200, r.text)
        r = self.verificar(celular, pais, **extra)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def crear(self, ticket, pais="PE", nombre="Rosa Quispe", rubro="transporte_personas", **extra):
        body = {"ticket": ticket, "pais": pais, "nombre": nombre, "rubro": rubro, **extra}
        return self.c.post("/api/v1/app/registro/crear", json=body)

    def detalle(self, r):
        return r.json()["detail"]


# =====================================================================
# Configuración por país y validaciones
# =====================================================================
class TestConfigPaises(Base_):
    def test_config_lista_paises_y_rubros(self):
        d = self.c.get("/api/v1/app/registro/config").json()
        self.assertEqual([p["pais"] for p in d["paises"]], ["PE", "BO", "CO", "EC", "MX", "CL"])
        self.assertEqual([r["clave"] for r in d["rubros"]],
                         ["transporte_personas", "tecnico_profesional", "peluqueria_barberia", "salud",
                          "bodega_tienda", "restaurante_comida", "comercio", "otro"])
        self.assertEqual(d["rubros"][0], {"clave": "transporte_personas", "nombre": "Transporte de personas",
                                          "incluye": "Taxi, mototaxi, transporte público",
                                          "icono": "local_taxi", "color": "#F59E0B"})
        for r in d["rubros"]:                     # todos con ícono y color válidos, sin repetir
            self.assertRegex(r["icono"], r"^[a-z_]+$")
            self.assertRegex(r["color"], r"^#[0-9A-F]{6}$")
        self.assertEqual(len({r["icono"] for r in d["rubros"]}), 8)
        self.assertEqual(len({r["color"] for r in d["rubros"]}), 8)
        self.assertEqual((d["autoregistro_activo"], d["prueba_dias"]), (True, 14))

    def test_config_de_un_pais(self):
        d = self.c.get("/api/v1/app/registro/config?pais=co").json()
        self.assertEqual((d["pais"], d["prefijo"], d["nombre"]), ("CO", "+57", "Colombia"))
        self.assertEqual(d["celular"], {"digitos": 10, "empieza_con": "3", "patron": r"^3\d{9}$",
                                        "ejemplo": "3001234567"})
        r = self.c.get("/api/v1/app/registro/config?pais=AR")
        self.assertEqual((r.status_code, self.detalle(r)["codigo"]), (404, "pais_no_disponible"))

    def test_validacion_de_cada_pais(self):
        casos = {
            "PE": (["987654321", "+51 987 654 321", "51987654321"], ["887654321", "98765432", "9876543210"]),
            "BO": (["71234567", "+591 7123 4567", "59171234567"], ["7123456", "712345678"]),
            "CO": (["3001234567", "+57 300 123 4567"], ["2001234567", "300123456"]),
            "EC": (["991234567", "+593 99 123 4567"], ["891234567", "99123456"]),
            "MX": (["5512345678", "+52 55 1234 5678", "5212345678"], ["551234567", "551234567890"]),
            "CL": (["912345678", "+56 9 1234 5678"], ["812345678", "91234567"]),
        }
        for pais, (validos, invalidos) in casos.items():
            cfg = registro_paises.pais(pais)
            for v in validos:
                e164 = registro_paises.normalizar(cfg, v)
                self.assertIsNotNone(e164, f"{pais} {v}")
                self.assertTrue(e164.startswith(f"+{cfg.prefijo}"), f"{pais} {v}")
                self.assertEqual(len(e164), 1 + len(cfg.prefijo) + cfg.digitos, f"{pais} {v}")
            for v in invalidos:
                self.assertIsNone(registro_paises.normalizar(cfg, v), f"{pais} {v}")
        # Un local mexicano que empieza con "52" no se confunde con el prefijo.
        self.assertEqual(registro_paises.normalizar(registro_paises.pais("MX"), "5212345678"), "+525212345678")

    def test_solicitar_con_celular_o_pais_invalido(self):
        r = self.solicitar("87654321")
        self.assertEqual((r.status_code, self.detalle(r)["codigo"]), (400, "celular_invalido"))
        self.assertIn("9 dígitos", self.detalle(r)["mensaje"])
        r = self.solicitar("987654321", pais="AR")
        self.assertEqual((r.status_code, self.detalle(r)["codigo"]), (400, "pais_no_disponible"))

    def test_solicitar_responde_igual_exista_o_no_el_numero(self):
        r1, r2 = self.solicitar("922222222"), self.solicitar("955555555")
        self.assertEqual((r1.status_code, r2.status_code), (200, 200))
        self.assertEqual(set(r1.json()), set(r2.json()))
        self.assertEqual((r1.json()["modo"], r2.json()["modo"]), ("otp", "otp"))
        self.assertIn("+51 ••• ••• 555", r2.json()["mensaje"])
        self.assertIn(NUEVO, self.enviador.codigos)        # el número nuevo también recibe su código

    def test_limite_de_codigos_por_numero(self):
        for _ in range(otp.MAX_OTP_POR_CELULAR):
            self.assertEqual(self.solicitar("955555555").status_code, 200)
        r = self.solicitar("955555555")
        self.assertEqual((r.status_code, self.detalle(r)["codigo"]), (429, "demasiados_codigos"))

    def test_produccion_sin_whatsapp_responde_503(self):
        otp.usar_enviador(None)
        with mock.patch.dict(os.environ, {"PAGOOK_ENTORNO": "produccion", "WA_ACCESS_TOKEN": ""}):
            r = self.solicitar("955555555")
        self.assertEqual((r.status_code, self.detalle(r)["codigo"]), (503, "otp_no_disponible"))


# =====================================================================
# Flujos completos
# =====================================================================
class TestNumeroNuevo(Base_):
    def test_flujo_completo_de_un_numero_nuevo(self):
        d = self.entrar("955 555 555")
        self.assertEqual((d["estado"], d["pais"], d["vence_en_seg"], d["prueba_dias"]), ("nuevo", "PE", 900, 14))
        self.assertTrue(d["ticket"].startswith("tk_"))
        r = self.crear(d["ticket"], nombre="  Rosa   Quispe ", rubro="transporte_personas")
        self.assertEqual(r.status_code, 200, r.text)
        j = r.json()
        self.assertEqual(j["estado"], "creado")
        self.assertEqual(j["empresa"]["tipo"], "independiente")
        self.assertEqual((j["empresa"]["nombre"], j["empresa"]["id_fiscal"], j["empresa"]["id_fiscal_tipo"]),
                         ("Rosa Quispe", None, None))
        self.assertEqual((j["cuenta"]["estado"], j["cuenta"]["dias_restantes"]), ("prueba", 14))

        e = self.db.query(Empresa).filter_by(id=j["empresa"]["id"]).one()
        self.assertEqual((e.tipo_empresa, e.pais_codigo, e.rubro, e.id_fiscal), ("independiente", "PE", "transporte_personas", None))
        self.assertEqual((e.razon_social, e.nombre_comercial, e.visible_en_testers), ("Rosa Quispe", "Rosa Quispe", False))
        self.assertTrue(e.slug.startswith("rosaquispe-"))
        u = self.db.query(Usuario).filter_by(celular=NUEVO).one()
        self.assertEqual(u.nombre, "Rosa Quispe")
        m = self.db.query(Membresia).filter_by(usuario_id=u.id).one()
        self.assertEqual((m.empresa_id, m.rol), (e.id, "dueno"))
        disp = self.db.query(Dispositivo).filter_by(token=j["token"]).one()
        self.assertEqual((disp.empresa_id, disp.manufacturer, disp.modelo, disp.consentimiento_version, disp.pais),
                         (e.id, "motorola", "moto g06", "2026-11-v3", "PE"))
        self.assertIsNotNone(disp.consentimiento_fecha)

        # El token del celular ya captura pagos en SU empresa.
        r = self.c.post("/api/v1/notificaciones/inbound", headers={"X-Device-Token": j["token"]},
                        json={"package": "com.bcp.innovacxion.yapeapp", "titulo": "Confirmación de Pago",
                              "texto": "Yape! Juan Perez te envió un pago por S/ 5"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["estado"], "parseada")

        # Y con el mismo número entra al panel web (una sola empresa: directo al Inicio).
        self.c.post("/panel/login/otp", json={"celular": NUEVO})
        r = self.c.post("/panel/login/verificar", json={"celular": NUEVO, "codigo": self.enviador.codigos[NUEVO]})
        self.assertEqual((r.status_code, r.json()["redirigir"]), (200, "/panel"))

    def test_nombre_comercial_es_el_nombre_visible(self):
        d = self.entrar("955555555")
        j = self.crear(d["ticket"], nombre="Luis Ramos", rubro="peluqueria_barberia",
                       nombre_comercial="Barbería El Flaco").json()
        self.assertEqual(j["empresa"]["nombre"], "Barbería El Flaco")
        e = self.db.get(Empresa, j["empresa"]["id"])
        self.assertEqual((e.razon_social, e.nombre_comercial), ("Luis Ramos", "Barbería El Flaco"))

    def test_otros_paises(self):
        d = self.entrar("300 123 4567", pais="CO")
        j = self.crear(d["ticket"], pais="CO", nombre="Camilo", rubro="salud").json()
        self.assertEqual((j["empresa"]["pais"], j["empresa"]["id_fiscal_tipo"]), ("CO", None))
        self.assertEqual(self.db.query(Usuario).filter_by(celular="+573001234567").count(), 1)

    def test_validaciones_del_registro(self):
        d = self.entrar("955555555")
        for extra, codigo in (({"nombre": "R"}, "nombre_invalido"), ({"nombre": "x" * 61}, "nombre_invalido"),
                              ({"nombre": "12345"}, "nombre_invalido"), ({"rubro": "astronauta"}, "rubro_invalido"),
                              ({"pais": "CO"}, "pais_no_coincide")):
            r = self.crear(d["ticket"], **extra)
            self.assertEqual((r.status_code, self.detalle(r)["codigo"]), (400, codigo), extra)
        # Los errores de validación no gastan el ticket.
        self.assertEqual(self.crear(d["ticket"]).status_code, 200)

    def test_prueba_configurable_y_ampliacion_igual(self):
        with mock.patch.dict(os.environ, {"PRUEBA_DIAS_INDEPENDIENTE": "20"}):
            j = self.crear(self.entrar("955555555")["ticket"]).json()
        self.assertEqual(j["cuenta"]["dias_restantes"], 20)
        e = self.db.get(Empresa, j["empresa"]["id"])
        # La ampliación única de +15 días sigue igual: se ofrece en los 2 últimos días.
        self.assertFalse(cuenta_empresa.info_cuenta(None, e)["puede_ampliar"])
        e.prueba_fin = datetime.now(timezone.utc) + timedelta(days=1)
        self.db.commit()
        info = cuenta_empresa.ampliar_prueba(self.db, e)
        self.assertEqual(info["dias_restantes"], 16)
        with self.assertRaises(cuenta_empresa.CuentaError):
            cuenta_empresa.ampliar_prueba(self.db, e)

    def test_sin_consentimiento_no_gasta_el_codigo(self):
        self.solicitar("955555555")
        r = self.verificar("955555555", consentimiento_version="")
        self.assertEqual((r.status_code, self.detalle(r)["codigo"]), (400, "consentimiento_requerido"))
        self.assertEqual(self.verificar("955555555").json()["estado"], "nuevo")

    def test_codigo_incorrecto(self):
        self.solicitar("955555555")
        r = self.verificar("955555555", codigo="000000" if self.enviador.codigos[NUEVO] != "000000" else "111111")
        self.assertEqual((r.status_code, self.detalle(r)["codigo"]), (400, "codigo_invalido"))

    def test_codigo_del_panel_no_sirve_en_la_app(self):
        self.c.post("/panel/login/otp", json={"celular": "+51922222222"})   # propósito 'login'
        r = self.verificar("922222222", codigo=self.enviador.codigos["+51922222222"])
        self.assertEqual((r.status_code, self.detalle(r)["codigo"]), (400, "codigo_invalido"))
        self.assertEqual(self.db.query(Otp).filter_by(proposito="app").count(), 0)


class TestDuenosExistentes(Base_):
    def test_dueno_de_una_empresa_vincula_directo(self):
        d = self.entrar("922222222")
        self.assertEqual(d["estado"], "vinculado")
        self.assertEqual((d["empresa"]["id"], d["empresa"]["nombre"], d["empresa"]["tipo"]), (self.b.id, "TALLER B", "negocio"))
        self.assertEqual({k: d["empresa"][k] for k in ("razon_social", "id_fiscal", "id_fiscal_tipo", "pais")},
                         {"razon_social": "TALLER B", "id_fiscal": "20100000002", "id_fiscal_tipo": "RUC", "pais": "PE"})
        disp = self.db.query(Dispositivo).filter_by(token=d["token"]).one()
        self.assertEqual((disp.empresa_id, disp.instalacion_id), (self.b.id, "inst-1"))
        # Misma instalación otra vez: se reutiliza el mismo dispositivo (como /dispositivos/vincular).
        self.assertEqual(self.entrar("922222222")["token"], d["token"])
        self.assertEqual(self.db.query(Dispositivo).filter_by(empresa_id=self.b.id).count(), 1)
        self.assertEqual(self.db.query(TicketAcceso).count(), 0)

    def test_dueno_de_varias_elige_con_ticket(self):
        d = self.entrar("911111111")
        self.assertEqual((d["estado"], d["vence_en_seg"]), ("elegir_empresa", 600))
        self.assertEqual([(e["empresa_id"], e["nombre"]) for e in d["empresas"]],
                         [(self.a.id, "CLINICA A"), (self.c_emp.id, "BOTICA C")])
        # Una empresa que no es suya: 403 (y el ticket sigue sirviendo).
        r = self.c.post("/api/v1/app/acceso/vincular", json={"ticket": d["ticket"], "empresa_id": self.b.id})
        self.assertEqual((r.status_code, self.detalle(r)["codigo"]), (403, "sin_permiso"))
        r = self.c.post("/api/v1/app/acceso/vincular", json={"ticket": d["ticket"], "empresa_id": self.c_emp.id})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["estado"], r.json()["empresa"]["id"]), ("vinculado", self.c_emp.id))
        disp = self.db.query(Dispositivo).filter_by(token=r.json()["token"]).one()
        self.assertEqual((disp.empresa_id, disp.modelo), (self.c_emp.id, "moto g06"))
        # Reutilizado: 400.
        r = self.c.post("/api/v1/app/acceso/vincular", json={"ticket": d["ticket"], "empresa_id": self.a.id})
        self.assertEqual((r.status_code, self.detalle(r)["codigo"]), (400, "ticket_invalido"))

    def test_numero_existente_no_puede_registrarse(self):
        # verificar trata al dueño como existente: nunca le da ticket de registro.
        self.assertEqual(self.entrar("922222222")["estado"], "vinculado")
        self.assertEqual(self.db.query(TicketAcceso).filter_by(tipo="registro").count(), 0)
        # Dos tickets de registro del mismo número nuevo: solo el primero crea cuenta.
        t1 = self.entrar("955555555")["ticket"]
        t2 = self.entrar("955555555")["ticket"]
        self.assertEqual(self.crear(t1).status_code, 200)
        r = self.crear(t2)
        self.assertEqual((r.status_code, self.detalle(r)["codigo"]), (409, "ya_registrado"))
        self.assertEqual(self.db.query(Usuario).filter_by(celular=NUEVO).count(), 1)
        self.assertEqual(self.db.query(Empresa).filter_by(tipo_empresa="independiente").count(), 1)
        # Y si vuelve a entrar, ya es dueño: vincula su empresa.
        self.assertEqual(self.entrar("955555555")["estado"], "vinculado")

    def test_cajero_no_puede_vincular(self):
        d = self.entrar("933333333")
        self.assertEqual(d["estado"], "sin_permiso")
        self.assertIn("cajero de CLINICA A", d["mensaje"])
        self.assertNotIn("token", d)
        self.assertEqual(self.db.query(Dispositivo).count(), 0)
        self.assertEqual(self.db.query(TicketAcceso).count(), 0)

    def test_usuario_desactivado(self):
        self.beto.activo = False
        self.db.commit()
        self.assertEqual(self.entrar("922222222")["estado"], "cuenta_desactivada")
        self.assertEqual(self.db.query(Dispositivo).count(), 0)


class TestTickets(Base_):
    def test_ticket_vencido(self):
        t = self.entrar("955555555")["ticket"]
        fila = self.db.query(TicketAcceso).one()
        fila.expira_en = datetime.now(timezone.utc) - timedelta(seconds=1)
        self.db.commit()
        r = self.crear(t)
        self.assertEqual((r.status_code, self.detalle(r)["codigo"]), (400, "ticket_invalido"))

    def test_ticket_reutilizado(self):
        t = self.entrar("955555555")["ticket"]
        self.assertEqual(self.crear(t).status_code, 200)
        r = self.crear(t)
        self.assertEqual((r.status_code, self.detalle(r)["codigo"]), (400, "ticket_invalido"))

    def test_ticket_de_otro_numero(self):
        t = self.entrar("955555555")["ticket"]
        r = self.crear(t, celular="966666666")
        self.assertEqual((r.status_code, self.detalle(r)["codigo"]), (400, "ticket_invalido"))
        # Con el número correcto, el mismo ticket sí sirve (y la cuenta es de ESE número).
        j = self.crear(t, celular="+51 955 555 555").json()
        self.assertEqual(self.db.query(Usuario).filter_by(celular=NUEVO).count(), 1)
        self.assertEqual(self.db.query(Usuario).filter_by(celular="+51966666666").count(), 0)
        self.assertEqual(j["estado"], "creado")

    def test_ticket_inventado_o_de_otro_tipo(self):
        r = self.crear("tk_inventado")
        self.assertEqual((r.status_code, self.detalle(r)["codigo"]), (400, "ticket_invalido"))
        t_elegir = self.entrar("911111111")["ticket"]
        r = self.crear(t_elegir)
        self.assertEqual((r.status_code, self.detalle(r)["codigo"]), (400, "ticket_invalido"))
        t_reg = self.entrar("955555555")["ticket"]
        r = self.c.post("/api/v1/app/acceso/vincular", json={"ticket": t_reg, "empresa_id": self.a.id})
        self.assertEqual((r.status_code, self.detalle(r)["codigo"]), (400, "ticket_invalido"))

    def test_ticket_se_guarda_hasheado(self):
        t = self.entrar("955555555")["ticket"]
        fila = self.db.query(TicketAcceso).one()
        self.assertEqual(fila.token_hash, hashlib.sha256(t.encode()).hexdigest())
        self.assertNotIn(t, str(fila.datos_equipo))
        self.assertEqual((fila.celular, fila.pais, fila.datos_equipo["modelo"]), (NUEVO, "PE", "moto g06"))


class TestAutoregistroApagado(Base_):
    def test_numero_nuevo_recibe_el_mensaje(self):
        with mock.patch.dict(os.environ, {"AUTOREGISTRO_ACTIVO": "false"}):
            d = self.entrar("955555555")
            self.assertEqual(d, {"estado": "registro_cerrado",
                                 "mensaje": "Por ahora el registro es por invitación. Escríbenos por WhatsApp."})
            self.assertFalse(self.c.get("/api/v1/app/registro/config?pais=PE").json()["autoregistro_activo"])
            # Los dueños existentes siguen entrando.
            self.assertEqual(self.entrar("922222222")["estado"], "vinculado")
        self.assertEqual(self.db.query(TicketAcceso).count(), 0)

    def test_ticket_previo_no_sirve_si_se_apaga(self):
        t = self.entrar("955555555")["ticket"]
        with mock.patch.dict(os.environ, {"AUTOREGISTRO_ACTIVO": "0"}):
            r = self.crear(t)
        self.assertEqual((r.status_code, self.detalle(r)["codigo"]), (403, "registro_cerrado"))
        self.assertEqual(self.db.query(Empresa).filter_by(tipo_empresa="independiente").count(), 0)


# =====================================================================
# Lo que sigue igual: código de revisión y /dispositivos/vincular
# =====================================================================
class TestSinCambios(Base_):
    def _codigo_panel(self):
        self.c.post("/panel/login/otp", json={"celular": "+51922222222"})
        self.c.post("/panel/login/verificar", json={"celular": "+51922222222",
                                                     "codigo": self.enviador.codigos["+51922222222"]})
        return self.c.post("/api/v1/panel/dispositivos/codigo").json()["codigo"]

    def test_vincular_con_codigo_del_panel_igual(self):
        codigo = self._codigo_panel()
        body = {"codigo": codigo, "instalacion_id": "inst-9", "manufacturer": "samsung", "modelo": "A15",
                "consentimiento_version": "2026-10", "consentimiento_fecha": "2026-10-23T10:00:00-05:00"}
        r = TestClient(app).post("/api/v1/dispositivos/vincular", json=body)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(set(r.json()), {"token", "dispositivo_id", "empresa"})
        self.assertEqual(r.json()["empresa"], {"razon_social": "TALLER B", "id_fiscal": "20100000002",
                                               "id_fiscal_tipo": "RUC", "pais": "PE"})
        disp = self.db.query(Dispositivo).filter_by(token=r.json()["token"]).one()
        self.assertEqual((disp.empresa_id, disp.instalacion_id, disp.manufacturer), (self.b.id, "inst-9", "samsung"))
        self.assertEqual(TestClient(app).post("/api/v1/dispositivos/vincular", json=body).status_code, 400)

    def test_codigo_revision_play_igual(self):
        body = {"codigo": "PLAY-REV-2026", "consentimiento_version": "2026-10",
                "consentimiento_fecha": "2026-10-23T10:00:00-05:00"}
        with mock.patch.dict(os.environ, {"CODIGO_REVISION_PLAY": "PLAY-REV-2026"}):
            r = self.c.post("/api/v1/dispositivos/vincular", json=body)
            r2 = self.c.post("/api/v1/dispositivos/vincular", json=body)   # se usa varias veces
        self.assertEqual((r.status_code, r2.status_code), (200, 200), r.text)
        self.assertEqual(r.json()["empresa"]["razon_social"], "DEMO REVISION")

    def test_revisor_de_play_tambien_entra_con_su_numero(self):
        env = {"REVISION_PANEL_CELULAR": REV_CELULAR,
               "REVISION_PANEL_CODIGO_HASH": hashlib.sha256(REV_CODIGO.encode()).hexdigest()}
        with mock.patch.dict(os.environ, env):
            r = self.solicitar("900000111")
            self.assertEqual(r.json()["modo"], "revision")
            self.assertEqual(self.enviador.codigos, {})                        # sin WhatsApp
            r = self.verificar("900000111", codigo="MALO")
            self.assertEqual((r.status_code, self.detalle(r)["codigo"]), (400, "codigo_invalido"))
            d = self.verificar("900000111", codigo=REV_CODIGO).json()
        self.assertEqual((d["estado"], d["empresa"]["id"]), ("vinculado", self.rev.id))


# =====================================================================
# /admin/registros
# =====================================================================
class TestAdminRegistros(Base_):
    def _dos_registros(self):
        j1 = self.crear(self.entrar("955555555")["ticket"], nombre="Rosa Quispe", rubro="transporte_personas").json()
        j2 = self.crear(self.entrar("3001234567", pais="CO")["ticket"], pais="CO", nombre="Camilo",
                        rubro="salud", nombre_comercial="Consultorio Camilo").json()
        return j1, j2

    def test_exige_superadmin(self):
        self.assertEqual(self.c.get("/api/v1/admin/registros").status_code, 401)
        self.assertEqual(self.c.get("/admin/registros.csv").status_code, 401)
        r = self.c.get("/admin/registros")
        self.assertEqual(r.status_code, 401)
        self.assertIn('action="/admin/acceso"', r.text)

    def test_lista_filtros_y_csv(self):
        self._dos_registros()
        h = {"X-Admin-Token": ADMIN_TOKEN}
        d = self.c.get("/api/v1/admin/registros", headers=h).json()
        self.assertEqual(d["total"], 2)                                  # solo independientes
        fila = next(f for f in d["registros"] if f["pais"] == "PE")
        self.assertEqual((fila["nombre"], fila["celular"], fila["rubro_nombre"], fila["dispositivo"],
                          fila["app_version"], fila["estado_cuenta"], fila["dias_restantes"]),
                         ("Rosa Quispe", NUEVO, "Transporte de personas", "motorola moto g06", "1.8.0", "prueba", 14))
        self.assertIsNone(fila["ultimo_pago_lima"])
        co = self.c.get("/api/v1/admin/registros?pais=co", headers=h).json()
        self.assertEqual([(f["nombre"], f["nombre_comercial"]) for f in co["registros"]],
                         [("Camilo", "Consultorio Camilo")])
        self.assertEqual(self.c.get("/api/v1/admin/registros?rubro=salud", headers=h).json()["total"], 1)
        hoy = datetime.now(timezone(timedelta(hours=-5))).date()
        self.assertEqual(self.c.get(f"/api/v1/admin/registros?desde={hoy}&hasta={hoy}", headers=h).json()["total"], 2)
        manana = hoy + timedelta(days=1)
        self.assertEqual(self.c.get(f"/api/v1/admin/registros?desde={manana}", headers=h).json()["total"], 0)

        r = self.c.get("/admin/registros.csv?pais=PE", headers=h)
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment", r.headers["content-disposition"])
        filas = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))
        self.assertEqual(filas[0][:3], ["Fecha (Lima)", "Nombre", "Nombre comercial"])
        self.assertEqual(len(filas), 2)
        self.assertEqual(filas[1][1], "Rosa Quispe")

    def test_html_con_sesion_de_superadmin(self):
        self._dos_registros()
        r = self.c.post("/admin/acceso", data={"token": ADMIN_TOKEN, "destino": "/admin/registros"},
                        follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        r = self.c.get("/admin/registros?rubro=transporte_personas")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Autorregistros desde la app", r.text)
        self.assertIn("Rosa Quispe", r.text)
        self.assertNotIn("Camilo", r.text)
        self.assertIn("/admin/registros.csv?rubro=transporte_personas", r.text)


# =====================================================================
# Panel: selector de empresas (4b)
# =====================================================================
class TestElegirEmpresa(Base_):
    def _login(self, celular):
        c = TestClient(app)
        c.post("/panel/login/otp", json={"celular": celular})
        r = c.post("/panel/login/verificar", json={"celular": celular, "codigo": self.enviador.codigos[celular]})
        return c, r.json()

    def test_una_sola_empresa_nunca_ve_el_selector(self):
        c, j = self._login("+51922222222")
        self.assertEqual(j["redirigir"], "/panel")
        r = c.get("/panel/elegir-empresa", follow_redirects=False)
        self.assertEqual((r.status_code, r.headers["location"]), (303, "/panel"))

    def test_varias_empresas_fila_clicable_y_continuar(self):
        c, j = self._login("+51911111111")
        self.assertEqual(j["redirigir"], "/panel/elegir-empresa")
        r = c.get("/panel/elegir-empresa")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Continuar", r.text)
        self.assertIn("Entrar", r.text)
        self.assertNotIn(">Actual<", r.text)
        self.assertEqual(r.text.count('class="tarjeta fila fila-empresa"'), 2)
        self.assertIn('data-actual="si"', r.text)
        self.assertIn('location.href = "/panel"', r.text)


# =====================================================================
# Eliminar cuenta en la web con el celular (independientes sin id fiscal)
# =====================================================================
class TestEliminarConCelular(Base_):
    def _independiente(self, celular="955555555", nombre="Rosa Quispe"):
        j = self.crear(self.entrar(celular)["ticket"], nombre=nombre).json()
        return self.db.get(Empresa, j["empresa"]["id"])

    def _solicitar(self, **datos):
        base = {"pais": "PE", "nombre": "Rosa Quispe"}
        return self.c.post("/eliminar-cuenta/solicitud", json={**base, **datos})

    def test_formulario_acepta_el_celular(self):
        from app.api.models_privacidad import SolicitudEliminacion
        pagina = self.c.get("/eliminar-cuenta").text
        self.assertIn("Con mi número de celular (desde la app)", pagina)
        self.assertIn("Con el RUC o identificador fiscal de mi negocio", pagina)
        r = self._solicitar(celular="955 555 555")
        self.assertEqual(r.status_code, 200, r.text)
        s = self.db.query(SolicitudEliminacion).one()
        # Sin contacto aparte, se usa el mismo celular.
        self.assertEqual((s.pais, s.id_fiscal, s.celular, s.contacto), ("PE", None, NUEVO, NUEVO))
        self.assertEqual(self._solicitar(celular="12345").status_code, 400)      # formato del país
        self.assertEqual(self._solicitar().status_code, 400)                     # ni id fiscal ni celular
        # El camino con id fiscal sigue igual.
        self.assertEqual(self._solicitar(id_fiscal="20100000002", contacto="b@b.pe").status_code, 200)

    def test_admin_encuentra_la_empresa_por_el_celular(self):
        e = self._independiente()
        self._solicitar(celular="955555555")
        self._solicitar(celular="966666666")                                       # sin cuenta
        self.c.post("/admin/acceso", data={"token": ADMIN_TOKEN, "destino": "/admin/eliminaciones"})
        r = self.c.get("/admin/eliminaciones")
        self.assertEqual(r.status_code, 200)
        self.assertIn(f"Celular {NUEVO}", r.text)
        self.assertIn(f"gestion_empresas.py eliminar --empresa {e.slug} --solicitud", r.text)
        self.assertIn("(independiente)", r.text)
        self.assertIn("No hay empresa con ese celular", r.text)

    def test_script_elimina_al_independiente_por_su_solicitud(self):
        from app.api.models_privacidad import EliminacionAuditoria, SolicitudEliminacion
        from app.services import eliminacion
        from scripts import gestion_empresas as g
        e = self._independiente()
        otra = self._independiente("966666666", "Luis Ramos")
        self.entrar("955555555")                      # deja OTP usado; el ticket ya se gastó
        self._solicitar(celular="955555555")
        s = self.db.query(SolicitudEliminacion).one()
        with self.assertRaises(g.ErrorGestion):        # la solicitud no es de esa empresa
            g.eliminar_empresa_cmd(self.db, otra.slug, s.id)
        self.db.rollback()
        hash_esperado, slug, slug_otra = eliminacion.hash_auditoria(self.db, e), e.slug, otra.slug
        hash_otra = eliminacion.hash_auditoria(self.db, otra)
        g.eliminar_empresa_cmd(self.db, slug, s.id)
        self.db.commit()
        self.db.expire_all()
        self.assertIsNone(self.db.query(Empresa).filter_by(slug=slug).first())
        self.assertEqual(self.db.query(Usuario).filter_by(celular=NUEVO).count(), 0)
        self.assertEqual(self.db.query(TicketAcceso).filter_by(celular=NUEVO).count(), 0)
        self.assertEqual(self.db.query(Otp).filter_by(celular=NUEVO).count(), 0)
        s = self.db.get(SolicitudEliminacion, s.id)
        self.assertEqual((s.estado, s.celular, s.nombre, s.contacto), ("ejecutada", None, None, None))
        a = self.db.query(EliminacionAuditoria).one()
        # La auditoría usa el celular del dueño (no "PE|" vacío, igual para todos los independientes).
        self.assertEqual(a.id_fiscal_hash, hash_esperado)
        self.assertNotEqual(a.id_fiscal_hash, eliminacion.hash_id_fiscal("PE", None))
        self.assertNotEqual(a.id_fiscal_hash, hash_otra)
        self.assertIsNotNone(self.db.query(Empresa).filter_by(slug=slug_otra).first())   # la otra sigue


# =====================================================================
# Privacidad
# =====================================================================
class TestPrivacidad(Base_):
    def test_politica_v3_menciona_celular_nombre_y_rubro(self):
        fuente = Path("docs/legal/privacidad.md").read_text(encoding="utf-8")
        self.assertIn("Versión 2026-11-v3", fuente)
        seccion4 = fuente.split("## 4.")[1].split("\n## ")[0]
        for palabra in ("número de celular", "nombre", "rubro", "No pedimos documento de identidad"):
            self.assertIn(palabra, seccion4)
        self.assertIn("código para entrar", fuente.split("## 5.")[1].split("\n## ")[0])
        self.assertEqual(self.c.get("/api/v1/privacidad/version").json(), {"version": "2026-11-v3"})


if __name__ == "__main__":
    unittest.main()
