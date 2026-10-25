"""Tests offline de zWeb-05: egresos, /privacidad y /eliminar-cuenta.

Reusa la base de datos de prueba de tests/test_multiempresa.py (SQLite en
memoria: perusistemas, clinica-a, clinica-b, sus dueños, celulares y API keys).

Ejecutar:  python -m unittest tests.test_egresos_privacidad
"""
import io
import json
import os
import sys
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.test_multiempresa import AHORA, YAPE, Base_  # noqa: E402

from app.admin.models import Empresa  # noqa: E402
from app.api.models_cuenta import CuentaApi  # noqa: E402
from app.api.models_eventos import EntregaEvento, SuscripcionWebhook  # noqa: E402
from app.api.models_multiempresa import Membresia, Sesion, Usuario  # noqa: E402
from app.api.models_pago_uso import PagoUso  # noqa: E402
from app.api.models_privacidad import EliminacionAuditoria, SolicitudEliminacion  # noqa: E402
from app.api.models_webhook import Dispositivo, NotificacionRaw, PagoDetectado  # noqa: E402
from app.api.parser import parsear  # noqa: E402
from app.services import eliminacion, eventos  # noqa: E402
from app.services.conciliacion import consultar_pago  # noqa: E402

INTERBANK = "pe.com.interbank.mobilebanking"
FRASE = "Sí, quiero llevar mis datos y eliminarlos de PagoOK"

# Textos REALES de producción (raws 61, 75, 76, 84, 91, 94), con nombres de
# personas reemplazados. Los comercios se dejan: así los muestra el banco.
EGRESOS_REALES = [
    (61, "Se realizó un pago recurrente de $26.19 en HOSTINGER* HOSTINGER.C con tu Tarjeta de Débito.",
     "USD", Decimal("26.19"), "cargo_recurrente"),
    (75, "Plineaste S/ 1.00 a Ana R Torres P", "PEN", Decimal("1.00"), "envio_billetera"),
    (76, "Plineaste S/ 1.00 a Ana R Torres P", "PEN", Decimal("1.00"), "envio_billetera"),
    (84, "Realizaste un consumo de S/.50.00 en COMPA#IA OPERADORA DE L con tu Tarjeta de Crédito.",
     "PEN", Decimal("50.00"), "consumo_tarjeta"),
    (91, "Realizaste un consumo de S/.42.00 en Chapos con tu Tarjeta de Crédito.",
     "PEN", Decimal("42.00"), "consumo_tarjeta"),
    (94, "Realizaste un consumo de S/.70.70 en EPS SEDALORETO SA  QPS con tu Tarjeta de Crédito.",
     "PEN", Decimal("70.70"), "consumo_tarjeta"),
]


# =====================================================================
# PARSER DE EGRESOS
# =====================================================================
class TestParserEgresos(unittest.TestCase):
    def test_cada_egreso_real_del_inventario(self):
        for raw_id, texto, moneda, monto, tipo_egreso in EGRESOS_REALES:
            r = parsear(INTERBANK, "Interbank", texto)
            self.assertIsNotNone(r, raw_id)
            self.assertEqual((r["tipo"], r["moneda"], r["monto"], r["tipo_egreso"]),
                             ("egreso", moneda, monto, tipo_egreso), raw_id)
            self.assertFalse(r["tipo_incierto"], raw_id)

    def test_contraparte_tal_como_la_muestra_el_banco(self):
        self.assertIn("Ana", parsear(INTERBANK, "Interbank", EGRESOS_REALES[1][1])["titular"])
        self.assertIn("Chapos", parsear(INTERBANK, "Interbank", EGRESOS_REALES[4][1])["titular"])

    def test_usd_se_guarda_como_usd_sin_convertir(self):
        r = parsear(INTERBANK, "Interbank", EGRESOS_REALES[0][1])
        self.assertEqual((r["moneda"], r["monto"]), ("USD", Decimal("26.19")))

    def test_senal_de_ingreso_nunca_sale_como_egreso(self):
        casos = [
            "Juan Perez te ha plineado S/ 1.00",
            "Te plinearon S/ 5.00",
            "Recibiste S/ 50.00 de Juan Perez. Consumo disponible en tu Tarjeta de Débito",
            "Te depositaron S/ 20.00 en tu Tarjeta de Débito",
            "Juan Perez te envió un pago por S/ 30. Pagaste a tiempo tu tarjeta",
        ]
        for texto in casos:
            r = parsear(INTERBANK, "Interbank", texto)
            self.assertTrue(r is None or r["tipo"] == "ingreso", texto)

    def test_promociones_y_avisos_no_son_egresos(self):
        for titulo, texto in [
            ("Interbank", "Aprovecha: realizaste un consumo de S/ 100.00 y gana millas"),
            ("Hoy vence el pago de tu tarjeta", "Evita caer en mora. Omite si ya pagaste S/ 300.00"),
            ("Interbank", "Ingresaste a tu Banca por Internet."),
            ("Interbank", "Regístrate y acumula hasta 2 Millas por cada $1 consumido"),
        ]:
            r = parsear(INTERBANK, titulo, texto)
            self.assertTrue(r is None or r["tipo"] != "egreso", texto)

    def test_monto_ilegible_no_se_guarda(self):
        self.assertIsNone(parsear(INTERBANK, "Interbank", "Realizaste un consumo en TIENDA con tu Tarjeta de Crédito."))

    def test_ultimos_4_solo_si_el_banco_los_muestra(self):
        r = parsear(INTERBANK, "Interbank", "Realizaste un consumo de S/ 10.00 en TIENDA con tu tarjeta ****4321")
        self.assertEqual(r["tarjeta_ultimos4"], "4321")
        self.assertIsNone(parsear(INTERBANK, "Interbank", EGRESOS_REALES[3][1])["tarjeta_ultimos4"])

    def test_ingresos_sin_claves_nuevas(self):
        r = parsear(YAPE, "Confirmación de Pago", "Juan Perez te envió un pago por S/ 80. El cód. de seguridad es: 135")
        self.assertNotIn("tipo_egreso", r)   # los ingresos salen exactamente como antes


# =====================================================================
# EGRESOS EN LA BASE: inbound, consulta, webhooks y API
# =====================================================================
class TestEgresosAislados(Base_):
    def _egreso(self, empresa=None, monto="50.00", moneda="PEN", tipo_egreso="consumo_tarjeta", hace_min=5):
        p = PagoDetectado(empresa_id=(empresa or self.a).id, metodo="tarjeta", monto=Decimal(monto),
                          moneda=moneda, titular="TIENDA", tipo="egreso", tipo_egreso=tipo_egreso,
                          recibido_en=datetime.utcnow() - timedelta(minutes=hace_min))
        self.db.add(p)
        self.db.commit()
        return p

    def test_inbound_guarda_el_egreso_en_usd(self):
        with mock.patch("app.api.push_service.enviar_push_a_empresa", return_value={}):
            r = self.cliente().post("/api/v1/notificaciones/inbound", headers={"X-Device-Token": "tok_a"},
                                    json={"package": INTERBANK, "titulo": "Interbank", "texto": EGRESOS_REALES[0][1]})
        p = self.db.get(PagoDetectado, r.json()["pago_id"])
        self.assertEqual((p.tipo, p.tipo_egreso, p.moneda, p.monto), ("egreso", "cargo_recurrente", "USD", Decimal("26.19")))

    def test_consultar_y_pago_confirmado_ignoran_egresos(self):
        egreso = self._egreso(monto="40.00")          # mismo monto que pago_a: no debe estorbar
        self.db.add(SuscripcionWebhook(empresa_id=self.a.id, url_destino="https://a.test", secreto_hmac="sa",
                                       tipos_suscritos=["pago.confirmado"]))
        self.db.commit()
        r = consultar_pago(self.db, empresa_id=self.a.id, cuenta_id=None, monto=Decimal("40.00"),
                           fecha_hora_declarada=datetime.now(timezone.utc), ventana_minutos=60)
        self.assertEqual((r["nivel"], r["pago"]["pago_id"]), ("alta", self.pago_a.id))
        solo_egreso = self._egreso(monto="12.34")
        r = consultar_pago(self.db, empresa_id=self.a.id, cuenta_id=None, monto=Decimal("12.34"),
                           fecha_hora_declarada=datetime.now(timezone.utc), ventana_minutos=60)
        self.assertEqual(r["nivel"], "sin_coincidencia")
        with mock.patch("app.services.eventos.despertar_worker"):
            self.assertIsNone(eventos.emitir_pago_confirmado(self.db, egreso))
            self.assertIsNone(eventos.emitir_pago_confirmado(self.db, solo_egreso))
        self.assertEqual(self.db.query(EntregaEvento).count(), 0)

    def test_api_y_caja_nunca_devuelven_egresos(self):
        egreso = self._egreso(monto="40.00")
        h = {"X-API-Key": self.key_a}
        c = self.cliente()
        self.assertEqual([p["id"] for p in c.get("/api/v1/pagos", headers=h).json()["pagos"]], [str(self.pago_a.id)])
        self.assertEqual(c.post(f"/api/v1/pagos/{egreso.id}/marcar-usado", headers=h,
                                json={"referencia_externa": "X"}).status_code, 404)
        self.assertEqual(c.post(f"/api/v1/pagos/{egreso.id}/reclamar", headers=h).status_code, 404)
        caja = c.get("/api/v1/pagos/buscar?monto=40&minutos=60", headers={"X-Device-Token": "tok_a"}).json()
        self.assertEqual([p["id"] for p in caja["pagos"]], [self.pago_a.id])
        recientes = c.get("/api/v1/pagos/recientes", headers={"X-Device-Token": "tok_a"}).json()
        self.assertNotIn(egreso.id, [p["id"] for p in recientes["pagos"]])
        self.assertEqual(self.login("+51911111111").post(f"/api/v1/panel/pagos/{egreso.id}/marcar-usado",
                                                         json={"referencia_externa": "X"}).status_code, 404)


# =====================================================================
# PÁGINA /{slug}/egresos Y CSV
# =====================================================================
class TestPaginaEgresos(Base_):
    def setUp(self):
        super().setUp()
        for monto, moneda, tipo in (("50.00", "PEN", "consumo_tarjeta"), ("1.00", "PEN", "envio_billetera"),
                                    ("26.19", "USD", "cargo_recurrente")):
            self.db.add(PagoDetectado(empresa_id=self.a.id, metodo="tarjeta", monto=Decimal(monto), moneda=moneda,
                                      titular="TIENDA", tipo="egreso", tipo_egreso=tipo,
                                      recibido_en=datetime.utcnow() - timedelta(hours=1)))
        self.db.add(PagoDetectado(empresa_id=self.b.id, metodo="tarjeta", monto=Decimal("999.00"), titular="OTRA",
                                  tipo="egreso", tipo_egreso="otro_egreso", recibido_en=datetime.utcnow()))
        self.db.commit()

    def test_filtros_y_totales_por_moneda(self):
        c = self.login("+51911111111")
        d = c.get("/api/v1/clinica-a/egresos").json()
        self.assertEqual(d["total"], 3)
        self.assertEqual(d["totales_por_moneda"], {"PEN": {"total": "51.00", "cantidad": 2},
                                                   "USD": {"total": "26.19", "cantidad": 1}})
        d = c.get("/api/v1/clinica-a/egresos?tipo_egreso=cargo_recurrente").json()
        self.assertEqual([m["tipo_egreso"] for m in d["movimientos"]], ["cargo_recurrente"])
        self.assertEqual(d["movimientos"][0]["tipo_egreso_label"], "Cargo recurrente")
        html = c.get("/clinica-a/egresos").text
        self.assertIn('id="tipo-egreso"', html)
        self.assertIn("egresos.csv", html)

    def test_csv_solo_dueno_y_contador_con_sesion(self):
        dueno = self.login("+51911111111")
        r = dueno.get("/api/v1/clinica-a/egresos.csv")
        self.assertEqual(r.status_code, 200)
        self.assertIn("cargo_recurrente", r.text)
        self.assertIn("USD", r.text)
        self.assertNotIn("OTRA", r.text)                      # nada de la empresa B
        self.miembro(self.a, "contador", "+51955500001")
        self.assertEqual(self.login("+51955500001").get("/api/v1/clinica-a/egresos.csv").status_code, 200)
        self.miembro(self.a, "cajero", "+51955500002")
        self.assertEqual(self.login("+51955500002").get("/api/v1/clinica-a/egresos.csv").status_code, 403)
        self.assertEqual(self.cliente().get("/api/v1/clinica-a/egresos.csv").status_code, 401)
        # perusistemas en modo legacy: la página sí, el CSV no (pide sesión).
        self.assertEqual(self.cliente().get("/api/v1/perusistemas/egresos").status_code, 200)
        self.assertEqual(self.cliente().get("/api/v1/perusistemas/egresos.csv").status_code, 401)


# =====================================================================
# REPROCESO DE EGRESOS HISTÓRICOS
# =====================================================================
class TestReprocesoEgresos(Base_):
    def setUp(self):
        super().setUp()
        self.fecha = datetime(2026, 5, 22, 5, 9, 0)
        self.raws = []
        for _, texto, *_ in EGRESOS_REALES[:3]:
            raw = NotificacionRaw(empresa_id=self.psp.id, package_app=INTERBANK, titulo="Interbank", texto=texto,
                                  estado_parseo="ignorada", timestamp_servidor=self.fecha)
            self.db.add(raw)
            self.raws.append(raw)
        self.ingreso = NotificacionRaw(empresa_id=self.psp.id, package_app=YAPE, titulo="Yape", estado_parseo="ignorada",
                                       texto="Juan Perez te envió un pago por S/ 3.5. El cód. de seguridad es: 1",
                                       timestamp_servidor=self.fecha)
        self.otra = NotificacionRaw(empresa_id=self.a.id, package_app=INTERBANK, titulo="Interbank",
                                    texto=EGRESOS_REALES[3][1], estado_parseo="ignorada", timestamp_servidor=self.fecha)
        self.db.add_all([self.ingreso, self.otra])
        self.db.commit()

    def test_simula_aplica_idempotente_y_sin_webhooks(self):
        from scripts.reprocesar_egresos import reprocesar_egresos
        psp = self.db.get(Empresa, self.psp.id)
        reporte = reprocesar_egresos(self.db, psp, desde=self.fecha.date(), aplicar=False)
        self.db.rollback()
        self.assertTrue(any("3 egreso(s) por crear" in l for l in reporte))
        self.assertEqual(self.db.query(PagoDetectado).filter_by(tipo="egreso").count(), 0)

        prohibido = mock.patch("app.services.eventos.emitir_evento", side_effect=AssertionError("webhook"))
        with prohibido:
            reprocesar_egresos(self.db, psp, desde=self.fecha.date(), aplicar=True)
            self.db.commit()
            reprocesar_egresos(self.db, psp, desde=self.fecha.date(), aplicar=True)   # 2da vez: no duplica
            self.db.commit()
        egresos = self.db.query(PagoDetectado).filter_by(tipo="egreso").all()
        self.assertEqual(len(egresos), 3)
        self.assertTrue(all(e.empresa_id == self.psp.id and e.recibido_en == self.fecha for e in egresos))
        self.assertEqual({e.tipo_egreso for e in egresos}, {"cargo_recurrente", "envio_billetera"})
        self.assertIsNone(self.db.query(PagoDetectado).filter_by(notificacion_raw_id=self.ingreso.id).first())
        self.assertIsNone(self.db.query(PagoDetectado).filter_by(notificacion_raw_id=self.otra.id).first())
        self.assertEqual(self.db.query(EntregaEvento).count(), 0)


# =====================================================================
# /privacidad
# =====================================================================
class TestPrivacidad(Base_):
    def test_pagina_indice_y_version(self):
        r = self.cliente().get("/privacidad")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Política de Privacidad de PagoOK", r.text)
        self.assertIn("2026-10-v1", r.text)
        self.assertIn('class="toc"', r.text)                 # índice de secciones
        self.assertIn("<table>", r.text)                      # las tablas del texto se renderizan
        for ruta in ("/privacidad", "/static/legal/legal.css", "/static/panel/panel.css"):
            self.assertNotIn("#ffffff", self.cliente().get(ruta).text.lower(), ruta)
        self.assertEqual(self.cliente().get("/api/v1/privacidad/version").json(), {"version": "2026-10-v1"})

    def test_texto_plano_mismo_contenido(self):
        r = self.cliente().get("/privacidad.txt")
        self.assertTrue(r.headers["content-type"].startswith("text/plain"))
        fuente = Path("docs/legal/privacidad.md").read_text(encoding="utf-8")
        self.assertIn("PERU SISTEMAS PRO E.I.R.L.", r.text)
        self.assertNotIn("**", r.text)
        # Cada párrafo del archivo aparece (sin el marcado) en el texto plano.
        self.assertIn("PagoOK lee únicamente las notificaciones", r.text)
        self.assertEqual(fuente.count("\n## ") + (1 if fuente.startswith("## ") else 0) > 5, True)

    def _sin_cache(self):
        from app.api import router_privacidad
        router_privacidad._cache_politica.clear()
        self.addCleanup(router_privacidad._cache_politica.clear)
        return router_privacidad

    def test_no_depende_del_directorio_de_trabajo(self):
        self._sin_cache()
        antes = os.getcwd()
        os.chdir(Path(__file__).resolve().parent)
        try:
            self.assertEqual(self.cliente().get("/privacidad.txt").status_code, 200)
        finally:
            os.chdir(antes)

    def test_sin_libreria_markdown_la_pagina_sigue_disponible(self):
        self._sin_cache()
        with mock.patch.dict(sys.modules, {"markdown": None}), self.assertLogs("pagook", "ERROR") as logs:
            r = self.cliente().get("/privacidad")
        self.assertEqual(r.status_code, 200)
        self.assertIn("PagoOK lee únicamente las notificaciones", r.text)
        self.assertIn("markdown", logs.output[0])

    def test_sin_archivo_503_con_log_y_eliminar_cuenta_sigue_funcionando(self):
        rp = self._sin_cache()
        with mock.patch.object(rp, "RUTA_POLITICA", Path("no/existe.md")), self.assertLogs("pagook", "ERROR") as logs:
            c = self.cliente()
            self.assertEqual(c.get("/privacidad").status_code, 503)
            self.assertEqual(c.get("/privacidad.txt").status_code, 503)
            self.assertEqual(c.get("/api/v1/privacidad/version").json(), {"version": None})
            self.assertEqual(c.get("/eliminar-cuenta").status_code, 200)
        self.assertIn("privacidad.md", logs.output[0])


# =====================================================================
# /eliminar-cuenta: formulario público y administrador
# =====================================================================
class TestSolicitudEliminacion(Base_):
    def test_formulario_guarda_y_limita_5_por_dia(self):
        c = self.cliente()
        self.assertIn("Entrar para descargar y eliminar", c.get("/eliminar-cuenta").text)
        datos = {"pais": "PE", "id_fiscal": "20100000001", "nombre": "Ana", "contacto": "+51911111111"}
        self.assertEqual(c.post("/eliminar-cuenta/solicitud", json={"pais": "PE"}).status_code, 400)
        for _ in range(5):
            r = c.post("/eliminar-cuenta/solicitud", json=datos)
            self.assertEqual(r.status_code, 200)
        self.assertIn("máximo de 15 días", r.json()["mensaje"])
        self.assertEqual(c.post("/eliminar-cuenta/solicitud", json=datos).status_code, 429)
        s = self.db.query(SolicitudEliminacion).first()
        self.assertEqual((s.estado, s.id_fiscal), ("pendiente", "20100000001"))
        self.assertNotIn("testclient", s.ip_hash)             # nunca la IP en claro

    def test_admin_ve_solicitudes_solo_con_token(self):
        self.cliente().post("/eliminar-cuenta/solicitud",
                            json={"pais": "PE", "id_fiscal": "20100000001", "nombre": "Ana", "contacto": "a@b.pe"})
        with mock.patch.dict(os.environ, {"ADMIN_TOKEN": "adm"}):
            self.assertEqual(self.cliente().get("/admin/eliminaciones").status_code, 401)
            r = self.cliente().get("/admin/eliminaciones?token=adm")
        self.assertEqual(r.status_code, 200)
        self.assertIn("clinica-a", r.text)
        self.assertIn("gestion_empresas.py eliminar --empresa clinica-a --solicitud", r.text)


# =====================================================================
# DESCARGA Y ELIMINACIÓN
# =====================================================================
MODELOS_POR_EMPRESA = [PagoDetectado, NotificacionRaw, Dispositivo, CuentaApi, Membresia, Sesion,
                       SuscripcionWebhook, PagoUso]


class TestEliminacion(Base_):
    def setUp(self):
        super().setUp()
        self.db.add(SuscripcionWebhook(empresa_id=self.a.id, url_destino="https://a.test", secreto_hmac="whsec_A",
                                       tipos_suscritos=["pago.confirmado"]))
        self.db.add(SuscripcionWebhook(empresa_id=self.b.id, url_destino="https://b.test", secreto_hmac="whsec_B",
                                       tipos_suscritos=["pago.confirmado"]))
        disp = self.db.query(Dispositivo).filter_by(token="tok_a").one()
        disp.instalacion_id = "inst-secreta-a"
        self.db.add(NotificacionRaw(empresa_id=self.a.id, dispositivo_id=disp.id, package_app=YAPE, texto="x"))
        self.db.add(PagoDetectado(empresa_id=self.a.id, metodo="tarjeta", monto=Decimal("9.00"), tipo="egreso",
                                  tipo_egreso="otro_egreso", titular="T", recibido_en=datetime.utcnow()))
        self.db.commit()

    def _conteos(self, empresa_id):
        return {m.__tablename__: self.db.query(m).filter(m.empresa_id == empresa_id).count() for m in MODELOS_POR_EMPRESA}

    def test_zip_tiene_todas_las_tablas_y_ningun_secreto(self):
        c = self.login("+51911111111")
        r = c.get("/api/v1/panel/exportar")
        self.assertEqual(r.headers["content-type"], "application/zip")
        z = zipfile.ZipFile(io.BytesIO(r.content))
        nombres = set(z.namelist())
        for tabla in ("empresa", "dispositivos", "ingresos", "egresos", "equipo", "credenciales_api"):
            self.assertIn(f"{tabla}.csv", nombres)
        datos = json.loads(z.read("datos.json"))
        self.assertEqual(set(datos), {"empresa", "dispositivos", "ingresos", "egresos", "equipo", "credenciales_api"})
        self.assertEqual(len(datos["ingresos"]), 1)
        self.assertEqual(len(datos["egresos"]), 1)
        self.assertEqual(datos["credenciales_api"][0]["nombre"], "Sis A")
        todo = b"".join(z.read(n) for n in nombres).decode("utf-8", "ignore")
        secretos = ["tok_a", "inst-secreta-a", "whsec_A", self.key_a,
                    self.db.query(CuentaApi).filter_by(empresa_id=self.a.id).one().api_key_hash]
        for s in secretos:
            self.assertNotIn(s, todo, s)
        self.assertNotIn("CLINICA B", todo)

    def test_sin_frase_exacta_o_sin_descarga_no_borra(self):
        c = self.login("+51911111111")
        antes = self._conteos(self.a.id)
        for cuerpo in ({"frase": "si quiero", "descarga": "no_necesito"},
                       {"frase": FRASE.lower(), "descarga": "no_necesito"},
                       {"frase": FRASE},
                       {"frase": FRASE, "descarga": "descargado"}):      # dice que descargó, pero no lo hizo
            self.assertEqual(c.post("/api/v1/panel/eliminar", json=cuerpo).status_code, 400, cuerpo)
        self.db.expire_all()
        self.assertEqual(self._conteos(self.a.id), antes)

    def test_cajero_y_contador_no_pueden(self):
        for i, rol in enumerate(("cajero", "contador")):
            cel = f"+5194440000{i}"
            self.miembro(self.a, rol, cel)
            r = self.login(cel).post("/api/v1/panel/eliminar", json={"frase": FRASE, "descarga": "no_necesito"})
            self.assertEqual(r.status_code, 403, rol)
            self.assertEqual(self.login(cel).get("/api/v1/panel/exportar").status_code, 403, rol)
        self.assertIsNotNone(self.db.get(Empresa, self.a.id))

    def test_demo_y_perusistemas_no_se_pueden_eliminar(self):
        self.miembro(self.psp, "dueno", "+51944400010")
        r = self.login("+51944400010").post("/api/v1/panel/eliminar", json={"frase": FRASE, "descarga": "no_necesito"})
        self.assertEqual(r.status_code, 403)
        with mock.patch.dict(os.environ, {"SLUGS_DEMO": "perusistemas,clinica-a"}):
            r = self.login("+51911111111").post("/api/v1/panel/eliminar",
                                                json={"frase": FRASE, "descarga": "no_necesito"})
            self.assertEqual(r.status_code, 403)
            with self.assertRaises(eliminacion.EliminacionProhibida):
                eliminacion.eliminar_empresa(self.db, self.db.get(Empresa, self.a.id), "solicitud")
        self.db.rollback()
        self.assertIsNotNone(self.db.get(Empresa, self.a.id))
        self.assertIsNotNone(self.db.get(Empresa, self.psp.id))

    def test_eliminacion_completa_y_las_demas_intactas(self):
        a_id, b_id, psp_id = self.a.id, self.b.id, self.psp.id
        antes_b = self._conteos(b_id)
        antes_psp = self._conteos(psp_id)
        c = self.login("+51911111111")
        c.get("/api/v1/panel/exportar")                                   # descarga primero
        r = c.post("/api/v1/panel/eliminar", json={"frase": FRASE, "descarga": "descargado"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["mensaje"], "Tus datos fueron eliminados.")
        self.db.expire_all()
        # 0 filas de la empresa A en cada tabla; la empresa ya no existe.
        self.assertEqual(set(self._conteos(a_id).values()), {0})
        self.assertIsNone(self.db.get(Empresa, a_id))
        self.assertEqual(self.db.query(Usuario).filter_by(celular="+51911111111").count(), 0)
        # Token de dispositivo y API key de A -> 401; la sesión ya no sirve.
        anon = self.cliente()
        self.assertEqual(anon.get("/api/v1/pagos/recientes", headers={"X-Device-Token": "tok_a"}).status_code, 401)
        self.assertEqual(anon.post("/api/v1/pagos/consultar", headers={"X-API-Key": self.key_a},
                                   json={"monto": "40.00", "fecha_hora_declarada": AHORA.isoformat()}).status_code, 401)
        self.assertEqual(c.get("/api/v1/panel/yo").status_code, 401)
        # Las demás empresas, intactas.
        self.assertEqual(self._conteos(b_id), antes_b)
        self.assertEqual(self._conteos(psp_id), antes_psp)
        self.assertEqual(self.db.query(SuscripcionWebhook).filter_by(empresa_id=b_id).count(), 1)
        # Auditoría mínima: sin nombres ni datos de pagos.
        a = self.db.query(EliminacionAuditoria).one()
        self.assertEqual(a.canal, "autoservicio")
        self.assertEqual(a.conteos["pago_detectado"], 2)
        self.assertNotIn("CLINICA", json.dumps(a.conteos))
        self.assertNotEqual(a.id_fiscal_hash, "20100000001")

    def test_script_elimina_y_cierra_la_solicitud(self):
        from scripts import gestion_empresas as g
        s = SolicitudEliminacion(pais="PE", id_fiscal="20100000001", nombre="Ana", contacto="a@b.pe",
                                 ip_hash="h", estado="pendiente")
        otra = SolicitudEliminacion(pais="PE", id_fiscal="20100000002", nombre="Beto", contacto="b@b.pe",
                                    ip_hash="h", estado="pendiente")
        self.db.add_all([s, otra])
        self.db.commit()
        with self.assertRaises(g.ErrorGestion):                # la solicitud es de otra empresa
            g.eliminar_empresa_cmd(self.db, "clinica-a", otra.id)
        self.db.rollback()
        lineas = g.eliminar_empresa_cmd(self.db, "clinica-a", s.id)
        self.db.commit()
        self.assertTrue(any("TOTAL" in l for l in lineas))
        self.db.expire_all()
        s = self.db.get(SolicitudEliminacion, s.id)
        self.assertEqual((s.estado, s.nombre, s.contacto, s.id_fiscal), ("ejecutada", None, None, "[eliminado]"))
        self.assertEqual(self.db.query(EliminacionAuditoria).one().canal, "solicitud")
        with self.assertRaises(g.ErrorGestion):
            g.eliminar_empresa_cmd(self.db, "perusistemas")


# =====================================================================
# RETENCIÓN: purga del texto crudo
# =====================================================================
class TestPurgaCrudos(Base_):
    def test_purga_solo_texto_viejo_y_no_toca_pagos(self):
        from scripts.purgar_crudos import purgar_crudos
        viejo = NotificacionRaw(empresa_id=self.a.id, package_app=YAPE, titulo="Yape", estado_parseo="parseada",
                                texto="Juan Perez te envió un pago por S/ 40", sbn_key="k1",
                                timestamp_servidor=datetime.utcnow() - timedelta(days=91))
        nuevo = NotificacionRaw(empresa_id=self.a.id, package_app=YAPE, titulo="Yape", estado_parseo="parseada",
                                texto="Ana te envió un pago por S/ 5", timestamp_servidor=datetime.utcnow())
        self.db.add_all([viejo, nuevo])
        self.db.flush()
        pago = self.db.get(PagoDetectado, self.pago_a.id)
        pago.notificacion_raw_id = viejo.id
        self.db.commit()
        pagos_antes = [(p.id, p.monto, p.titular) for p in self.db.query(PagoDetectado).order_by(PagoDetectado.id)]

        self.assertEqual(purgar_crudos(self.db, 90, aplicar=False), 1)
        self.db.rollback()
        self.assertEqual(purgar_crudos(self.db, 90, aplicar=True), 1)
        self.db.commit()
        self.db.expire_all()
        v, n = self.db.get(NotificacionRaw, viejo.id), self.db.get(NotificacionRaw, nuevo.id)
        self.assertEqual((v.texto, v.titulo, v.sbn_key, v.estado_parseo), (None, None, "k1", "parseada"))
        self.assertIsNotNone(n.texto)
        self.assertEqual([(p.id, p.monto, p.titular) for p in self.db.query(PagoDetectado).order_by(PagoDetectado.id)],
                         pagos_antes)


if __name__ == "__main__":
    unittest.main()


# =====================================================================
# FILTROS DE /ingresos Y /egresos (rango, zona horaria, "Solo del negocio")
# =====================================================================
class TestFiltrosMovimientos(Base_):
    def setUp(self):
        super().setUp()
        def pago(tipo, utc, **kw):
            p = PagoDetectado(empresa_id=self.psp.id, metodo=kw.pop("metodo", "tarjeta"), monto=Decimal("10.00"),
                              titular="X", tipo=tipo, recibido_en=utc, **kw)
            self.db.add(p)
            return p
        # Egreso real de mayo (raw 61) y dos en el borde del día 23/10 de Lima.
        self.mayo = pago("egreso", datetime(2026, 5, 16, 15, 50), tipo_egreso="cargo_recurrente")
        self.noche_22 = pago("egreso", datetime(2026, 10, 23, 2, 0), tipo_egreso="otro_egreso")   # 22/10 21:00 Lima
        self.noche_23 = pago("egreso", datetime(2026, 10, 24, 3, 0), tipo_egreso="otro_egreso")   # 23/10 22:00 Lima
        self.propio = pago("egreso", datetime(2026, 10, 24, 1, 0), tipo_egreso="envio_billetera", metodo="plin",
                           posible_interno=True, confirmacion_usuario="confirmado_interno")       # 23/10 20:00 Lima
        self.ingreso_23 = pago("ingreso", datetime(2026, 10, 24, 3, 30), metodo="yape")
        self.db.commit()

    def _ids(self, tipo, qs=""):
        r = self.cliente().get(f"/api/v1/perusistemas/{tipo}s?{qs}")
        self.assertEqual(r.status_code, 200, r.text)
        return {m["id"] for m in r.json()["movimientos"]}

    def test_dia_de_lima_con_zona_horaria(self):
        hoy = "desde=2026-10-23T00:00:00-05:00&hasta=2026-10-23T23:59:59-05:00"
        ids = self._ids("egreso", hoy.replace("+", "%2B"))
        self.assertIn(self.noche_23.id, ids)          # 22:00 de Lima del 23: antes se perdía
        self.assertNotIn(self.noche_22.id, ids)       # 21:00 de Lima del 22: antes se colaba
        self.assertIn(self.ingreso_23.id, self._ids("ingreso", hoy))

    def test_fecha_sin_zona_se_toma_como_lima(self):
        ids = self._ids("egreso", "desde=2026-10-23T00:00:00&hasta=2026-10-23T23:59:59")
        self.assertIn(self.noche_23.id, ids)
        self.assertNotIn(self.noche_22.id, ids)

    def test_todo_y_rango_personalizado_muestran_mayo(self):
        self.assertIn(self.mayo.id, self._ids("egreso", "desde=&hasta="))                      # "Todo"
        self.assertIn(self.mayo.id, self._ids("egreso", "desde=2026-05-01T00:00:00-05:00&hasta=2026-05-31T23:59:59-05:00"))
        self.assertNotIn(self.mayo.id, self._ids("egreso", "desde=2026-06-01T00:00:00-05:00"))

    def test_solo_del_negocio_oculta_solo_cuentas_propias_confirmadas(self):
        todos = self._ids("egreso", "desde=&hasta=&solo_negocio=false")
        negocio = self._ids("egreso", "desde=&hasta=&solo_negocio=true")
        self.assertEqual(todos - negocio, {self.propio.id})
        self.assertIn(self.mayo.id, negocio)

    def test_pagina_trae_todos_por_defecto_y_rango_personalizado(self):
        for tipo in ("egresos", "ingresos"):
            html = self.cliente().get(f"/perusistemas/{tipo}").text
            self.assertIn('<option value="" selected>Todos</option>', html, tipo)
            self.assertIn('data-rango="todo"', html, tipo)
            self.assertIn('id="f-desde"', html, tipo)
            self.assertIn('id="f-hasta"', html, tipo)
            self.assertIn('document.getElementById("metodo").value = "";', html, tipo)   # reinicio al cargar
            self.assertIn("isoConZona", html, tipo)
        self.assertIn('class="solo-ingreso"', self.cliente().get("/perusistemas/egresos").text)
