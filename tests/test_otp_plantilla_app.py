"""Tests de zWeb-16: plantilla zero-tap para el OTP de la app (OTP_PLANTILLA_APP).

Cubre: el payload de envío con la plantilla nueva; el respaldo a la plantilla
actual sin la variable, o cuando Meta rechaza la nueva (no aprobada/pausada),
con la pausa que evita reintentarla en cada pedido; que un error que no es de
plantilla no reenvía; y que el panel web sigue con WA_OTP_PLANTILLA.

Se intercepta httpx.post: no sale nada a Meta.

Ejecutar:  python -m unittest tests.test_otp_plantilla_app
"""
import json
import os
import unittest
from unittest import mock

from tests.test_acceso_app import Base_
from app.services import otp

PLANTILLA_ACTUAL = "pagook_codigo"
PLANTILLA_APP = "pagook_acceso_app"
ENV_WA = {"WA_ACCESS_TOKEN": "tok-de-prueba", "WA_PHONE_NUMBER_ID": "123456789",
          "WA_OTP_PLANTILLA": PLANTILLA_ACTUAL, "WA_OTP_IDIOMA": "es", "WA_OTP_BOTON": "1",
          "GRAPH_API_VERSION": "v23.0"}


class _Resp:
    def __init__(self, status_code, cuerpo=None):
        self.status_code = status_code
        self.text = json.dumps(cuerpo or {})


def _error_meta(code, mensaje="error"):
    return _Resp(400, {"error": {"code": code, "message": mensaje, "fbtrace_id": "fb1"}})


class MetaFalsa:
    """Captura cada POST a la Cloud API y responde según la plantilla."""

    def __init__(self, respuestas=None):
        self.envios = []
        self.respuestas = respuestas or {}

    def __call__(self, url, json=None, timeout=None, headers=None):
        self.envios.append({"url": url, "cuerpo": json})
        return self.respuestas.get(json["template"]["name"], _Resp(200, {"messages": [{"id": "wamid.1"}]}))

    def plantillas(self):
        return [e["cuerpo"]["template"]["name"] for e in self.envios]


class BaseMeta(Base_):
    def setUp(self):
        super().setUp()
        otp.usar_enviador(None)          # envío real hasta httpx.post
        otp.reiniciar_plantilla_app()
        self.meta = MetaFalsa()
        self.p_http = mock.patch("app.services.otp.httpx.post", self.meta)
        self.p_http.start()

    def tearDown(self):
        self.p_http.stop()
        otp.reiniciar_plantilla_app()
        super().tearDown()

    def con_wa(self, **extra):
        return mock.patch.dict(os.environ, {**ENV_WA, **extra})

    def codigo_de(self, envio):
        comp = envio["cuerpo"]["template"]["components"]
        return comp[0]["parameters"][0]["text"]


class TestEnvioApp(BaseMeta):
    def test_payload_con_la_plantilla_nueva(self):
        with self.con_wa(OTP_PLANTILLA_APP=PLANTILLA_APP):
            r = self.solicitar("955555555")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(self.meta.envios), 1)
        envio = self.meta.envios[0]
        self.assertEqual(envio["url"], "https://graph.facebook.com/v23.0/123456789/messages")
        cuerpo = envio["cuerpo"]
        self.assertEqual(cuerpo["to"], "51955555555")
        self.assertEqual(cuerpo["type"], "template")
        tpl = cuerpo["template"]
        self.assertEqual((tpl["name"], tpl["language"]), (PLANTILLA_APP, {"code": "es"}))
        codigo = self.codigo_de(envio)
        self.assertRegex(codigo, r"^\d{6}$")
        self.assertEqual(tpl["components"], [
            {"type": "body", "parameters": [{"type": "text", "text": codigo}]},
            {"type": "button", "sub_type": "url", "index": "0",
             "parameters": [{"type": "text", "text": codigo}]},
        ])
        # El código enviado es el que se verifica después.
        r = self.verificar("955555555", codigo=codigo)
        self.assertEqual(r.json()["estado"], "nuevo")

    def test_boton_siempre_va_en_la_plantilla_nueva(self):
        with self.con_wa(OTP_PLANTILLA_APP=PLANTILLA_APP, WA_OTP_BOTON="0"):
            self.solicitar("955555555")
        tipos = [c["type"] for c in self.meta.envios[0]["cuerpo"]["template"]["components"]]
        self.assertEqual(tipos, ["body", "button"])

    def test_sin_la_variable_usa_la_plantilla_actual(self):
        for valor in ("", "   "):
            with self.subTest(valor=repr(valor)), self.con_wa(OTP_PLANTILLA_APP=valor):
                self.meta.envios.clear()
                self.assertEqual(self.solicitar("955555555").status_code, 200)
                self.assertEqual(self.meta.plantillas(), [PLANTILLA_ACTUAL])
        env = {k: v for k, v in os.environ.items() if k != "OTP_PLANTILLA_APP"}
        with mock.patch.dict(os.environ, {**env, **ENV_WA}, clear=True):
            self.meta.envios.clear()
            self.assertEqual(self.solicitar("966666666").status_code, 200)
        self.assertEqual(self.meta.plantillas(), [PLANTILLA_ACTUAL])

    def test_respaldo_si_la_nueva_no_esta_aprobada(self):
        self.meta.respuestas[PLANTILLA_APP] = _error_meta(132001, "Template name does not exist in the translation")
        with self.con_wa(OTP_PLANTILLA_APP=PLANTILLA_APP):
            r = self.solicitar("955555555")
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(self.meta.plantillas(), [PLANTILLA_APP, PLANTILLA_ACTUAL])
            # Mismo código en ambos intentos: es un solo OTP.
            self.assertEqual(self.codigo_de(self.meta.envios[0]), self.codigo_de(self.meta.envios[1]))
            # Durante la pausa ya no se intenta la nueva.
            self.meta.envios.clear()
            self.assertEqual(self.solicitar("966666666").status_code, 200)
            self.assertEqual(self.meta.plantillas(), [PLANTILLA_ACTUAL])
            # Pasada la pausa, se vuelve a probar la nueva.
            with mock.patch("app.services.otp.time.monotonic", return_value=10**9):
                self.meta.envios.clear()
                del self.meta.respuestas[PLANTILLA_APP]
                self.assertEqual(self.solicitar("977777777").status_code, 200)
                self.assertEqual(self.meta.plantillas(), [PLANTILLA_APP])

    def test_respaldo_si_la_nueva_esta_pausada(self):
        self.meta.respuestas[PLANTILLA_APP] = _error_meta(132015, "Template is paused")
        with self.con_wa(OTP_PLANTILLA_APP=PLANTILLA_APP):
            self.assertEqual(self.solicitar("955555555").status_code, 200)
        self.assertEqual(self.meta.plantillas(), [PLANTILLA_APP, PLANTILLA_ACTUAL])

    def test_error_que_no_es_de_plantilla_no_reenvia(self):
        self.meta.respuestas[PLANTILLA_APP] = _error_meta(131026, "Message undeliverable")
        with self.con_wa(OTP_PLANTILLA_APP=PLANTILLA_APP):
            r = self.solicitar("955555555")
        self.assertEqual((r.status_code, self.detalle(r)["codigo"]), (502, "envio_fallido"))
        self.assertEqual(self.meta.plantillas(), [PLANTILLA_APP])

    def test_si_falla_tambien_el_respaldo_responde_502(self):
        self.meta.respuestas[PLANTILLA_APP] = _error_meta(132001)
        self.meta.respuestas[PLANTILLA_ACTUAL] = _error_meta(190, "token vencido")
        with self.con_wa(OTP_PLANTILLA_APP=PLANTILLA_APP):
            r = self.solicitar("955555555")
        self.assertEqual((r.status_code, self.detalle(r)["codigo"]), (502, "envio_fallido"))
        self.assertEqual(self.meta.plantillas(), [PLANTILLA_APP, PLANTILLA_ACTUAL])

    def test_produccion_sin_whatsapp_sigue_en_503(self):
        with self.con_wa(OTP_PLANTILLA_APP=PLANTILLA_APP, PAGOOK_ENTORNO="produccion", WA_ACCESS_TOKEN=""):
            r = self.solicitar("955555555")
        self.assertEqual((r.status_code, self.detalle(r)["codigo"]), (503, "otp_no_disponible"))
        self.assertEqual(self.meta.envios, [])


class TestPanelNoCambia(BaseMeta):
    def test_panel_web_sigue_con_la_plantilla_anterior(self):
        with self.con_wa(OTP_PLANTILLA_APP=PLANTILLA_APP):
            r = self.c.post("/panel/login/otp", json={"celular": "+51922222222"})  # Beto, dueño de B
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.meta.plantillas(), [PLANTILLA_ACTUAL])

    def test_obtener_enviador_no_usa_la_variable(self):
        with self.con_wa(OTP_PLANTILLA_APP=PLANTILLA_APP):
            self.assertEqual(otp.obtener_enviador().plantilla, PLANTILLA_ACTUAL)
            self.assertIsInstance(otp.obtener_enviador_app(), otp.EnviadorApp)


if __name__ == "__main__":
    unittest.main()
