"""Tests offline de zWeb-06: canal de correo bancario, administración de bancos,
código de revisión de Google Play y cierre del registro antiguo para la 1.6.

DKIM se reemplaza por un verificador falso (sin DNS). Base de datos: la de
tests/test_multiempresa.py (perusistemas, clinica-a, clinica-b...).
Ejecutar:  python -m unittest tests.test_correo_bancos
"""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from email.message import EmailMessage
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.test_multiempresa import CONSENTIMIENTO, Base_  # noqa: E402

from app.admin.models import Empresa  # noqa: E402
from app.api.models_correo import CorreoEmpresa, CorreoRaw, SolicitudBanco  # noqa: E402
from app.api.models_eventos import EntregaEvento, SuscripcionWebhook  # noqa: E402
from app.api.models_multiempresa import BancoConfig  # noqa: E402
from app.api.models_webhook import Dispositivo, NotificacionRaw, PagoDetectado  # noqa: E402
from app.services import correo_entrante, eliminacion  # noqa: E402
from app.services.conciliacion import consultar_pago  # noqa: E402
from app.services.parser_correo import parsear_correo  # noqa: E402

LIMA = ZoneInfo("America/Lima")
DOMINIO_BCP = "bcp.com.pe"          # en producción, el dominio real sale del primer correo (configurable)
INTERBANK = "pe.com.interbank.mobilebanking"
MESES = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
         "octubre", "noviembre", "diciembre"]

# ---- Correos REALES del BCP (nombres, tarjetas y cuentas anonimizados) ----
CORREO_A = """Realizaste un consumo de S/ 20.00 con tu Tarjeta de Débito BCP en PLIN-JUAN PEREZ.
Total del consumo\tS/ 20.00
Operación realizada\tConsumo Tarjeta de Débito
Fecha y hora\t24 de octubre de 2026 - 08:16 AM
Número de Tarjeta de Débito\t************1234
Empresa\tPLIN-JUAN PEREZ
Número de operación\t689868"""
CORREO_B = """Realizaste un consumo de S/ 15.78 con tu Tarjeta de Débito BCP en SUPERMERCADO KOKO SHOP.
Total del consumo\tS/ 15.78
Operación realizada\tConsumo Tarjeta de Débito
Fecha y hora\t18 de octubre de 2026 - 09:01 PM
Número de Tarjeta de Débito\t************1234
Empresa\tSUPERMERCADO KOKO SHOP
Número de operación\t467638"""
CORREO_C1 = """Realizaste un yapeo a celular de S/ 7.00 desde tu Clasica Soles.
Monto enviado\tS/ 7.00
Operación realizada\tYapear a celular
Fecha y hora\t23 de octubre de 2026 - 01:14 PM
Enviado a\tAna Maria Torres P.
*** **1 234
Destino\tInterbank
Desde\tClasica
**** 0001
Moneda\tSoles
Canal\tBanca Móvil BCP
Número de operación\t02764028"""
CORREO_C2 = """Realizaste un yapeo a celular de S/ 10.00 desde tu Cuenta de ahorro Soles.
Monto enviado\tS/ 10.00
Operación realizada\tYapear a celular
Fecha y hora\t23 de octubre de 2026 - 11:57 AM
Enviado a\tLuisa M Garcia C.
*** **5 678
Destino\tYape
Desde\tCuenta de ahorro
**** 0001
Moneda\tSoles
Mensaje\tPrueba
Canal\tBanca Móvil BCP
Número de operación\t02181739"""
CORREO_D = """Operación realizada:
Pago de servicios
Número de operación:
02279440
Fecha y hora:\tMartes, 20 Octubre 2026 - 11:51 A. M.
Empresa:\tMOVISTAR-INTEGRATEL PERU
Servicio:\t3 MOVISTAR MOVIL
Cuenta de origen:\tCuenta de ahorros
**** 0001
Monto total:\tS/ 26.75"""


def mime(texto, remitente="BCP <notificaciones@bcp.com.pe>", asunto="Constancia de operación",
         message_id=None, html=False):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = remitente, "duilio@gmail.com", asunto
    if message_id:
        m["Message-ID"] = message_id
    if html:
        filas = "".join(f"<tr><td>{a}</td><td>{b}</td></tr>" for a, _, b in (l.partition("\t") for l in texto.split("\n")))
        m.set_content("version texto")
        m.add_alternative(f"<html><body><table>{filas}</table></body></html>", subtype="html")
    else:
        m.set_content(texto)
    return m.as_bytes()


def ahora_lima_texto(delta_min=0):
    t = datetime.now(LIMA) + timedelta(minutes=delta_min)
    h12 = t.hour % 12 or 12
    return f"{t.day} de {MESES[t.month]} de {t.year} - {h12:02d}:{t.minute:02d} {'AM' if t.hour < 12 else 'PM'}"


class BaseCorreo(Base_):
    def setUp(self):
        super().setUp()
        self.db.add_all([
            BancoConfig(pais="PE", clave="bcp", nombre="BCP", tipo="banco", orden=1, dominios_correo=[DOMINIO_BCP],
                        paquetes_android=["pe.com.bcp.bcppersonas"], guia_alertas="Activa las alertas en la app BCP"),
            BancoConfig(pais="PE", clave="yape", nombre="Yape", tipo="billetera", orden=1,
                        paquetes_android=["com.bcp.innovacxion.yapeapp"]),
            BancoConfig(pais="PE", clave="caja_maynas", nombre="Caja Maynas", tipo="caja", orden=1),
            BancoConfig(pais="PE", clave="viejo", nombre="Banco Viejo", tipo="banco", orden=9, activo=False),
        ])
        self.db.commit()
        self.dir_psp = correo_entrante.direccion(correo_entrante.correo_de_empresa(self.db, self.psp.id).codigo)
        self.dir_a = correo_entrante.direccion(correo_entrante.correo_de_empresa(self.db, self.a.id).codigo)

    def procesar(self, destinatario, cuerpo, dominios=(DOMINIO_BCP,), **kw):
        return correo_entrante.procesar_correo(self.db, destinatario, mime(cuerpo, **kw),
                                               verificar=lambda _m: list(dominios))


# =====================================================================
# PARSER: los 5 ejemplos reales
# =====================================================================
class TestParserCorreoBCP(unittest.TestCase):
    def test_a_consumo_que_es_envio_a_plin(self):
        d = parsear_correo("bcp", "", CORREO_A)["datos"]
        self.assertEqual((d["tipo_egreso"], d["monto"], d["moneda"], d["contraparte"], d["destino"]),
                         ("envio_billetera", Decimal("20.00"), "PEN", "PLIN-JUAN PEREZ", "Plin"))
        self.assertEqual((d["codigo_operacion"], d["ultimos4"]), ("689868", "1234"))
        self.assertEqual(d["fecha_utc"], datetime(2026, 10, 24, 13, 16))      # 08:16 Lima

    def test_b_consumo_en_comercio(self):
        d = parsear_correo("bcp", "", CORREO_B)["datos"]
        self.assertEqual((d["tipo_egreso"], d["monto"], d["contraparte"], d["destino"]),
                         ("consumo_tarjeta", Decimal("15.78"), "SUPERMERCADO KOKO SHOP", None))
        self.assertEqual(d["fecha_utc"], datetime(2026, 10, 19, 2, 1))        # 09:01 PM Lima del 18

    def test_c_yapeos_desde_la_app_del_bcp(self):
        d1 = parsear_correo("bcp", "", CORREO_C1)["datos"]
        d2 = parsear_correo("bcp", "", CORREO_C2)["datos"]
        self.assertEqual((d1["tipo_egreso"], d1["monto"], d1["contraparte"], d1["destino"], d1["ultimos4"]),
                         ("envio_billetera", Decimal("7.00"), "Ana Maria Torres P.", "Interbank", "0001"))
        self.assertEqual((d2["monto"], d2["destino"], d2["codigo_operacion"]), (Decimal("10.00"), "Yape", "02181739"))
        self.assertEqual(d2["fecha_utc"], datetime(2026, 10, 23, 16, 57))

    def test_d_pago_de_servicios_con_otro_formato_de_fecha(self):
        d = parsear_correo("bcp", "", CORREO_D)["datos"]
        self.assertEqual((d["tipo_egreso"], d["monto"], d["contraparte"], d["codigo_operacion"], d["ultimos4"]),
                         ("pago_servicio", Decimal("26.75"), "MOVISTAR-INTEGRATEL PERU", "02279440", "0001"))
        self.assertEqual(d["fecha_utc"], datetime(2026, 10, 20, 16, 51))      # 11:51 A. M. Lima

    def test_nunca_guarda_mas_de_4_digitos(self):
        for texto in (CORREO_A, CORREO_C1, CORREO_D):
            self.assertEqual(len(parsear_correo("bcp", "", texto)["datos"]["ultimos4"]), 4)

    def test_sin_formato_promocion_y_banco_sin_formatos(self):
        self.assertEqual(parsear_correo("bcp", "Aviso", "Un texto del BCP que no conocemos")["estado"], "sin_formato")
        self.assertEqual(parsear_correo("bcp", "¡Aprovecha esta promoción!", "Gana puntos")["estado"], "descartado")
        self.assertEqual(parsear_correo("bcp", "", "Por tu seguridad, nunca compartas tu clave")["estado"], "descartado")
        self.assertEqual(parsear_correo("interbank", "", CORREO_B)["estado"], "sin_formato")


# =====================================================================
# RECEPCIÓN: DKIM, destinatario, Gmail
# =====================================================================
class TestRecepcion(BaseCorreo):
    def test_correo_valido_crea_egreso_de_origen_correo(self):
        c = self.procesar(self.dir_psp, CORREO_A)
        self.assertEqual(c.estado, "procesado")
        p = self.db.get(PagoDetectado, c.pago_id)
        self.assertEqual((p.empresa_id, p.tipo, p.origen, p.tipo_egreso, p.destino, p.tarjeta_ultimos4, p.codigo_operacion),
                         (self.psp.id, "egreso", "correo", "envio_billetera", "Plin", "1234", "689868"))

    def test_correo_html_se_convierte_a_texto(self):
        c = self.procesar(self.dir_psp, CORREO_B, html=True)
        self.assertEqual(c.estado, "procesado")
        self.assertIn("SUPERMERCADO KOKO SHOP", c.cuerpo_texto)

    def test_dkim_invalido_o_de_otro_dominio_se_rechaza_sin_crear_nada(self):
        for dominios in ([], ["evil.com"], ["bcp.com.pe.evil.com"]):
            c = self.procesar(self.dir_psp, CORREO_A, dominios=dominios, message_id=f"<{len(dominios)}@x>")
            self.assertEqual(c.estado, "rechazado", dominios)
            self.assertIsNone(c.pago_id)
        self.assertEqual(self.db.query(PagoDetectado).filter_by(origen="correo").count(), 0)

    def test_destinatario_inexistente_o_inactivo_se_descarta(self):
        viejo = self.dir_psp
        correo_entrante.regenerar_correo(self.db, self.psp.id)
        for dest in (viejo, "egresos+noexiste@bancos.pagook.pro", "otro@bancos.pagook.pro"):
            c = self.procesar(dest, CORREO_A)
            self.assertEqual((c.estado, c.empresa_id, c.cuerpo_texto), ("descartado", None, None), dest)
        self.assertEqual(self.db.query(PagoDetectado).filter_by(origen="correo").count(), 0)

    def test_mismo_message_id_no_se_procesa_dos_veces(self):
        c1 = self.procesar(self.dir_psp, CORREO_A, message_id="<abc@bcp>")
        c2 = self.procesar(self.dir_psp, CORREO_A, message_id="<abc@bcp>")
        self.assertEqual(c1.id, c2.id)
        self.assertEqual(self.db.query(PagoDetectado).filter_by(origen="correo").count(), 1)

    def test_confirmacion_de_gmail_y_panel(self):
        c = self.procesar(self.dir_a, "Recibimos una solicitud para reenviar el correo.\nCódigo de confirmación: 987654321",
                          dominios=["google.com"], remitente="Gmail <forwarding-noreply@google.com>",
                          asunto="(#987654321) Confirmación de reenvío de Gmail - Recibir correo de duilio@gmail.com")
        self.assertEqual((c.estado, c.codigo_confirmacion), ("confirmacion_gmail", "987654321"))
        dueno = self.login("+51911111111")
        d = dueno.get("/api/v1/panel/correo").json()
        self.assertEqual(d["codigo_gmail"], "987654321")
        self.assertTrue(d["direccion"].startswith("egresos+") and d["direccion"].endswith("@bancos.pagook.pro"))
        html = dueno.get("/panel/correo").text
        self.assertIn("Tu código es", html)
        self.assertIn("987654321", html)
        self.assertIn("Redirigir", html)

    def test_confirmacion_de_gmail_falsa_se_rechaza(self):
        c = self.procesar(self.dir_a, "Código de confirmación: 111111111", dominios=["evil.com"],
                          remitente="forwarding-noreply@google.com")
        self.assertEqual(c.estado, "rechazado")

    def test_endpoint_exige_secreto(self):
        c = self.cliente()
        cuerpo = mime(CORREO_A)
        h = {"X-Destinatario": self.dir_psp, "Content-Type": "message/rfc822"}
        with mock.patch.dict(os.environ, {"CORREO_ENTRANTE_SECRETO": ""}):
            self.assertEqual(c.post("/api/v1/correo/entrante", content=cuerpo, headers=h).status_code, 503)
        with mock.patch.dict(os.environ, {"CORREO_ENTRANTE_SECRETO": "s3creto"}), \
             mock.patch("app.services.correo_entrante.verificar_dkim", lambda _m: [DOMINIO_BCP]):
            self.assertEqual(c.post("/api/v1/correo/entrante", content=cuerpo,
                                    headers={**h, "X-Correo-Secreto": "malo"}).status_code, 401)
            r = c.post("/api/v1/correo/entrante", content=cuerpo, headers={**h, "X-Correo-Secreto": "s3creto"})
        self.assertEqual((r.status_code, r.json()["estado"]), (200, "procesado"))

    def test_aislamiento_correo_de_a_nunca_cae_en_b(self):
        c = self.procesar(self.dir_a, CORREO_B)
        self.assertEqual(self.db.get(PagoDetectado, c.pago_id).empresa_id, self.a.id)
        self.assertEqual(self.db.query(PagoDetectado).filter_by(empresa_id=self.b.id, origen="correo").count(), 0)
        dueno_b = self.login("+51922222222")
        self.assertEqual(dueno_b.get("/api/v1/panel/correo").json()["correos"], [])

    def test_egreso_por_correo_no_aparece_en_consultar_ni_dispara_webhooks(self):
        self.db.add(SuscripcionWebhook(empresa_id=self.psp.id, url_destino="https://x.test", secreto_hmac="s",
                                       tipos_suscritos=["pago.confirmado"]))
        self.db.commit()
        with mock.patch("app.services.eventos.despertar_worker"):
            c = self.procesar(self.dir_psp, CORREO_A)
        r = consultar_pago(self.db, empresa_id=self.psp.id, cuenta_id=None, monto=Decimal("20.00"),
                           fecha_hora_declarada=datetime(2026, 10, 24, 8, 16), ventana_minutos=30)
        self.assertEqual(r["nivel"], "sin_coincidencia")
        self.assertEqual(self.db.query(EntregaEvento).count(), 0)
        self.assertIsNotNone(c.pago_id)

    def test_egresos_de_correo_se_ven_con_origen(self):
        self.procesar(self.dir_psp, CORREO_B)
        d = self.cliente().get("/api/v1/perusistemas/egresos?desde=&hasta=").json()
        self.assertEqual([m["origen"] for m in d["movimientos"]], ["correo"])
        self.assertIn("✉️ correo", self.cliente().get("/perusistemas/egresos").text)

    def test_reprocesar_tras_agregar_el_dominio_del_banco(self):
        c = self.procesar(self.dir_psp, CORREO_A, dominios=["alertas.viabcp.com"])
        self.assertEqual(c.estado, "rechazado")
        bcp = self.db.query(BancoConfig).filter_by(clave="bcp").one()
        bcp.dominios_correo = [DOMINIO_BCP, "viabcp.com"]
        self.db.commit()
        correo_entrante.reprocesar_correo(self.db, c, verificar=lambda _m: ["alertas.viabcp.com"])
        self.assertEqual(c.estado, "procesado")


# =====================================================================
# SIN DUPLICADOS ENTRE CELULAR Y CORREO
# =====================================================================
class TestDuplicados(BaseCorreo):
    TEXTO_NOTIF = "Realizaste un consumo de S/.15.78 en SUPERMERCADO KOKO con tu Tarjeta de Crédito."

    def _notificacion(self):
        with mock.patch("app.api.push_service.enviar_push_a_empresa", return_value={}):
            r = self.cliente().post("/api/v1/notificaciones/inbound", headers={"X-Device-Token": "tok_psp"},
                                    json={"package": INTERBANK, "titulo": "Interbank", "texto": self.TEXTO_NOTIF})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def _correo_ahora(self, delta_min=1):
        return self.procesar(self.dir_psp, CORREO_B.replace("18 de octubre de 2026 - 09:01 PM", ahora_lima_texto(delta_min)))

    def _egresos(self):
        self.db.expire_all()
        return self.db.query(PagoDetectado).filter_by(empresa_id=self.psp.id, tipo="egreso").all()

    def test_primero_celular_luego_correo(self):
        n = self._notificacion()
        c = self._correo_ahora(delta_min=1)
        egresos = self._egresos()
        self.assertEqual(len(egresos), 1)
        p = egresos[0]
        self.assertEqual((c.pago_id, p.origen, p.notificacion_raw_id), (n["pago_id"], "notificacion", n["raw_id"]))
        self.assertEqual(p.codigo_operacion, "467638")       # el correo completó el número de operación
        self.assertEqual(p.tarjeta_ultimos4, "1234")

    def test_primero_correo_luego_celular(self):
        c = self._correo_ahora(delta_min=-1)
        n = self._notificacion()
        egresos = self._egresos()
        self.assertEqual(len(egresos), 1)
        p = egresos[0]
        self.assertEqual((p.id, p.origen, p.notificacion_raw_id, n["pago_id"]), (c.pago_id, "correo", n["raw_id"], c.pago_id))
        self.assertEqual(self.db.get(NotificacionRaw, n["raw_id"]).estado_parseo, "parseada")

    def test_fuera_de_3_minutos_son_dos_operaciones(self):
        self._notificacion()
        self._correo_ahora(delta_min=10)
        self.assertEqual(len(self._egresos()), 2)


# =====================================================================
# ADMINISTRACIÓN DE BANCOS, CATÁLOGO Y SOLICITUDES
# =====================================================================
class TestAdminBancos(BaseCorreo):
    H = {"X-Admin-Token": "adm"}

    def test_exige_token(self):
        with mock.patch.dict(os.environ, {"ADMIN_TOKEN": "adm"}):
            c = self.cliente()
            self.assertEqual(c.get("/admin/bancos").status_code, 401)
            self.assertEqual(c.get("/admin/bancos?token=malo").status_code, 401)
            self.assertEqual(c.get("/admin/bancos?token=adm").status_code, 200)
            self.assertEqual(c.get("/api/v1/admin/bancos").status_code, 401)
            self.assertEqual(c.get("/api/v1/admin/bancos", headers={"X-Admin-Token": "x"}).status_code, 401)

    def test_crear_validar_editar_y_desactivar(self):
        with mock.patch.dict(os.environ, {"ADMIN_TOKEN": "adm"}):
            c = self.cliente()
            base = {"pais": "PE", "clave": "caja_sullana", "nombre": "Caja Sullana", "tipo": "caja"}
            self.assertEqual(c.post("/api/v1/admin/bancos", headers=self.H,
                                    json={**base, "paquetes_android": "sullana"}).status_code, 400)
            self.assertEqual(c.post("/api/v1/admin/bancos", headers=self.H,
                                    json={**base, "dominios_correo": "no es dominio"}).status_code, 400)
            r = c.post("/api/v1/admin/bancos", headers=self.H,
                       json={**base, "paquetes_android": "pe.com.sullana.app", "dominios_correo": "cajasullana.pe"})
            self.assertEqual(r.status_code, 200, r.text)
            bid = r.json()["id"]
            self.assertEqual(c.post("/api/v1/admin/bancos", headers=self.H, json=base).status_code, 409)
            r = c.put(f"/api/v1/admin/bancos/{bid}", headers=self.H, json={"activo": False})
            self.assertFalse(r.json()["activo"])
            self.assertIsNotNone(self.db.get(BancoConfig, bid))            # no se borra
            tipos = [b["tipo"] for b in c.get("/api/v1/admin/bancos?pais=PE", headers=self.H).json()["bancos"]]
            self.assertEqual(tipos, sorted(tipos, key=["billetera", "banco", "caja"].index))

    def test_bancos_config_agrega_campos_nuevos_y_oculta_inactivos(self):
        d = self.cliente().get("/api/v1/bancos-config?pais=PE").json()
        claves = [b["clave"] for b in d["bancos"]]
        self.assertEqual(claves, ["yape", "bcp", "caja_maynas"])          # billetera, banco, caja
        bcp = d["bancos"][1]
        self.assertEqual((bcp["paquetes_android"], bcp["guia_alertas"]),
                         (["pe.com.bcp.bcppersonas"], "Activa las alertas en la app BCP"))
        self.assertNotIn("dominios_correo", bcp)

    def test_solicitud_de_banco_limite_por_ip_y_empresa_desde_el_token(self):
        c = self.cliente()
        self.assertEqual(c.post("/api/v1/bancos/solicitud", json={"pais": "PE"}).status_code, 400)
        r = c.post("/api/v1/bancos/solicitud", json={"pais": "PE", "nombre_banco": "Caja Sullana", "empresa_id": 999},
                   headers={"X-Device-Token": "tok_a"})
        self.assertEqual(r.status_code, 200)
        s = self.db.query(SolicitudBanco).one()
        self.assertEqual((s.empresa_id, s.nombre_normalizado), (self.a.id, "caja sullana"))
        for _ in range(4):
            self.assertEqual(c.post("/api/v1/bancos/solicitud", json={"pais": "PE", "nombre_banco": "Caja Sullana"}).status_code, 200)
        self.assertEqual(c.post("/api/v1/bancos/solicitud", json={"pais": "PE", "nombre_banco": "Otro"}).status_code, 429)
        with mock.patch.dict(os.environ, {"ADMIN_TOKEN": "adm"}):
            sol = c.get("/api/v1/admin/bancos/solicitudes", headers=self.H).json()["solicitudes"]
        self.assertEqual((sol[0]["nombre"], sol[0]["pedidos"]), ("Caja Sullana", 5))

    def test_admin_ve_correos_sin_formato(self):
        self.procesar(self.dir_psp, "Texto nuevo del BCP que todavía no conocemos")
        with mock.patch.dict(os.environ, {"ADMIN_TOKEN": "adm"}):
            d = self.cliente().get("/api/v1/admin/correos?estado=sin_formato", headers=self.H).json()
        self.assertEqual(d["correos"][0]["empresa"], "perusistemas")


# =====================================================================
# PURGA Y ELIMINACIÓN
# =====================================================================
class TestPurgaYEliminacion(BaseCorreo):
    def test_purga_borra_el_texto_del_correo_a_los_90_dias(self):
        from scripts.purgar_crudos import purgar_correos
        viejo = self.procesar(self.dir_psp, CORREO_A)
        nuevo = self.procesar(self.dir_psp, CORREO_B)
        viejo.recibido_en = datetime.now(timezone.utc) - timedelta(days=91)
        self.db.commit()
        self.assertEqual(purgar_correos(self.db, 90, aplicar=True), 1)
        self.db.commit()
        self.db.expire_all()
        v, n = self.db.get(CorreoRaw, viejo.id), self.db.get(CorreoRaw, nuevo.id)
        self.assertEqual((v.cuerpo_texto, v.asunto, v.mime), (None, None, None))
        self.assertIsNotNone(n.cuerpo_texto)
        self.assertIsNotNone(self.db.get(PagoDetectado, v.pago_id))          # el egreso se conserva

    def test_eliminar_empresa_borra_sus_correos(self):
        self.procesar(self.dir_a, CORREO_B)
        a_id = self.a.id
        eliminacion.eliminar_empresa(self.db, self.db.get(Empresa, a_id), "autoservicio")
        self.db.commit()
        self.assertEqual(self.db.query(CorreoRaw).filter_by(empresa_id=a_id).count(), 0)
        self.assertEqual(self.db.query(CorreoEmpresa).filter_by(empresa_id=a_id).count(), 0)


# =====================================================================
# REVISOR DE GOOGLE PLAY Y REGISTRO ANTIGUO
# =====================================================================
class TestRevisionPlayYRegistro(Base_):
    def setUp(self):
        super().setUp()
        self.demo = Empresa(pais_codigo="PE", id_fiscal="DEMO-PLAY", razon_social="PAGOOK DEMO REVISION",
                            slug="demo-revision", estado_cuenta="activa", prueba_fin=None)
        self.db.add(self.demo)
        self.db.commit()

    def _vincular(self, codigo, instalacion):
        return self.cliente().post("/api/v1/dispositivos/vincular", json={
            "codigo": codigo, "instalacion_id": instalacion, "app_version": "1.6.0", **CONSENTIMIENTO})

    def test_codigo_de_revision_no_vence_y_se_usa_varias_veces(self):
        with mock.patch.dict(os.environ, {"CODIGO_REVISION_PLAY": "PLAY-REV-2026"}):
            r1 = self._vincular("play-rev-2026", "revisor-1")
            r2 = self._vincular("PLAY-REV-2026", "revisor-2")
        self.assertEqual((r1.status_code, r2.status_code), (200, 200))
        for r in (r1, r2):
            self.assertEqual(r.json()["empresa"]["razon_social"], "PAGOOK DEMO REVISION")
            self.assertEqual(self.db.query(Dispositivo).filter_by(token=r.json()["token"]).one().empresa_id, self.demo.id)

    def test_sin_variable_el_codigo_de_revision_no_sirve(self):
        with mock.patch.dict(os.environ, {"CODIGO_REVISION_PLAY": ""}):
            self.assertEqual(self._vincular("PLAY-REV-2026", "revisor-1").status_code, 400)

    def test_la_demo_de_revision_no_se_puede_eliminar(self):
        self.assertFalse(eliminacion.eliminacion_permitida(self.db.get(Empresa, self.demo.id)))

    def _registrar(self, **datos):
        return self.cliente().post("/api/v1/dispositivos/registrar",
                                   json={"empresa_slug": "perusistemas", "instalacion_id": "inst-x", **datos})

    def test_registro_sin_codigo_rechazado_desde_la_1_6(self):
        for datos in ({"app_version": "1.6.0"}, {"app_version": "1.7.2"}, {"app_version": "1.5.1", "version_code": 6000}):
            r = self._registrar(**datos)
            self.assertEqual(r.status_code, 403, datos)
            self.assertIn("Vincular", r.json()["detail"])
        self.assertEqual(self.db.query(Dispositivo).filter_by(instalacion_id="inst-x").count(), 0)

    def test_registro_sin_codigo_sigue_igual_para_1_5(self):
        for version in ("1.5.0", "1.5.1", "1.4-beta-fix3", ""):
            r = self.cliente().post("/api/v1/dispositivos/registrar", json={
                "empresa_slug": "perusistemas", "instalacion_id": f"inst-{version}", "app_version": version})
            self.assertEqual(r.status_code, 201, version)
            disp = self.db.query(Dispositivo).filter_by(token=r.json()["token"]).one()
            self.assertEqual(disp.empresa_id, self.psp.id)

    def test_celular_ya_registrado_que_se_actualiza_a_1_6_conserva_su_token(self):
        r1 = self._registrar(app_version="1.5.1")
        r2 = self._registrar(app_version="1.6.0")
        self.assertEqual((r1.status_code, r2.status_code), (201, 201))
        self.assertEqual(r1.json()["token"], r2.json()["token"])


if __name__ == "__main__":
    unittest.main()
