"""Tests offline del contrato de eventos v1 (webhooks salientes firmados).

SQLite en memoria + get_db sobreescrito; el HTTP saliente se simula.
Ejecutar:  python -m unittest tests.test_eventos
"""
import json
import os
import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DATABASE_URL", "postgresql://dummy:dummy@localhost/dummy")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.admin.db import Base, get_db  # noqa: E402
import app.admin.models  # noqa: E402,F401
from app.admin.models import Empresa  # noqa: E402
from app.api.models_webhook import Dispositivo, PagoDetectado  # noqa: E402
from app.api.models_eventos import EntregaEvento, SuscripcionWebhook  # noqa: E402
from app.services import eventos as ev  # noqa: E402
from app.main import app  # noqa: E402



class EventosBase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        self.Session = sessionmaker(bind=self.engine, autoflush=False)
        Base.metadata.create_all(self.engine)
        # Las entregas se encolan con la hora real: los tiempos del worker parten de ahí.
        self.t0 = ev.ahora_utc()

        def _override_get_db():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = _override_get_db
        self.client = TestClient(app)
        self.db = self.Session()

        emp = Empresa(pais_codigo="PE", id_fiscal="20123456789",
                      razon_social="Bodega Prueba", slug="bodega-prueba")
        otra = Empresa(pais_codigo="PE", id_fiscal="20999999999",
                       razon_social="Otra", slug="otra")
        self.db.add_all([emp, otra])
        self.db.flush()
        self.emp_id, self.otra_id = emp.id, otra.id

        self.s_pago = SuscripcionWebhook(
            empresa_id=emp.id, nombre="QueVendi", url_destino="https://qv.test/hook",
            secreto_hmac="whsec_a", tipos_suscritos=["pago.confirmado"], activo=True)
        self.s_otro_tipo = SuscripcionWebhook(
            empresa_id=emp.id, nombre="Solo egresos", url_destino="https://x.test/hook",
            secreto_hmac="whsec_b", tipos_suscritos=["egreso.registrado"], activo=True)
        self.s_inactiva = SuscripcionWebhook(
            empresa_id=emp.id, nombre="Apagada", url_destino="https://y.test/hook",
            secreto_hmac="whsec_c", tipos_suscritos=["pago.confirmado"], activo=False)
        self.s_otra_empresa = SuscripcionWebhook(
            empresa_id=otra.id, nombre="Otra", url_destino="https://z.test/hook",
            secreto_hmac="whsec_d", tipos_suscritos=["pago.confirmado"], activo=True)
        self.db.add_all([self.s_pago, self.s_otro_tipo, self.s_inactiva, self.s_otra_empresa])
        self.db.commit()

    def tearDown(self):
        self.db.close()
        app.dependency_overrides.clear()
        Base.metadata.drop_all(self.engine)

    def _pago(self, **kw):
        datos = dict(empresa_id=self.emp_id, metodo="yape", monto=Decimal("15.5"),
                     titular="Juan Perez", recibido_en=datetime(2026, 10, 2, 19, 0, 0),
                     tipo="ingreso", tipo_incierto=False)
        datos.update(kw)
        p = PagoDetectado(**datos)
        self.db.add(p)
        self.db.commit()
        return p

    def _entregas(self):
        self.db.expire_all()
        return self.db.query(EntregaEvento).order_by(EntregaEvento.id).all()


class TestEmisor(EventosBase):
    def test_sobre_y_datos_pago_confirmado(self):
        sobre = ev.emitir_pago_confirmado(self.db, self._pago(codigo_operacion="123456"))
        self.assertEqual(sobre["tipo"], "pago.confirmado")
        self.assertEqual(sobre["version"], "v1")
        uuid.UUID(sobre["evento_id"])
        self.assertEqual(sobre["empresa"],
                         {"id_fiscal": "20123456789", "pais": "PE", "id_fiscal_tipo": "RUC"})
        self.assertEqual(sobre["moneda"], "PEN")
        self.assertTrue(sobre["ocurrido_en"].endswith("-05:00"))
        d = sobre["datos"]
        self.assertEqual(d["monto"], "15.50")
        self.assertEqual(d["canal"], "yape")
        self.assertEqual(d["institucion"], "Yape")
        self.assertEqual(d["contraparte"], "Juan Perez")
        self.assertEqual(d["codigo_operacion"], "123456")
        self.assertEqual(d["fecha_hora_banco"], "2026-10-02T14:00:00-05:00")
        self.assertIsNone(d["referencia_pedido"])

    def test_solo_suscripciones_activas_de_la_empresa_y_el_tipo(self):
        ev.emitir_pago_confirmado(self.db, self._pago())
        entregas = self._entregas()
        self.assertEqual([e.suscripcion_id for e in entregas], [self.s_pago.id])
        self.assertEqual(entregas[0].ultimo_estado, "pendiente")
        self.assertEqual(entregas[0].intentos, 0)

    def test_sin_suscriptores_no_encola(self):
        sobre = ev.emitir_evento("pago.confirmado", self.otra_id + 100, {}, db=self.db)
        self.assertIsNone(sobre)
        self.assertEqual(len(self._entregas()), 0)

    def test_tipo_incierto_y_egreso_no_emiten(self):
        self.assertIsNone(ev.emitir_pago_confirmado(self.db, self._pago(tipo_incierto=True)))
        self.assertIsNone(ev.emitir_pago_confirmado(self.db, self._pago(tipo="egreso")))
        self.assertEqual(len(self._entregas()), 0)

    def test_transferencia_usa_banco_y_canal_otro(self):
        d = ev.armar_datos_pago_confirmado(self._pago(metodo="transferencia", banco="BCP"))
        self.assertEqual((d["canal"], d["institucion"]), ("transferencia", "BCP"))
        d = ev.armar_datos_pago_confirmado(self._pago(metodo="bim"))
        self.assertEqual((d["canal"], d["institucion"]), ("otro", "BIM"))

    def test_no_duplica_entrega_mismo_evento_misma_suscripcion(self):
        from sqlalchemy.exc import IntegrityError
        ev.emitir_pago_confirmado(self.db, self._pago())
        e = self._entregas()[0]
        self.db.add(EntregaEvento(evento_id=e.evento_id, suscripcion_id=e.suscripcion_id,
                                  tipo=e.tipo, payload=e.payload))
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()


class TestEntregaYReintentos(EventosBase):
    def _enviador(self, *codigos):
        llamadas = []
        cola = list(codigos)

        def enviar(url, cuerpo, cabeceras):
            llamadas.append((url, cuerpo, cabeceras))
            c = cola.pop(0)
            return c, None if c and 200 <= c < 300 else "falló"
        return enviar, llamadas

    def test_entrega_firmada_ok(self):
        sobre = ev.emitir_pago_confirmado(self.db, self._pago())
        enviar, llamadas = self._enviador(200)
        self.assertEqual(ev.procesar_pendientes(self.db, enviar=enviar, ahora=self.t0 + timedelta(seconds=1)), 1)

        url, cuerpo, cab = llamadas[0]
        self.assertEqual(url, "https://qv.test/hook")
        self.assertTrue(ev.verificar_firma(cuerpo, cab["X-PagoOK-Firma"], "whsec_a"))
        self.assertFalse(ev.verificar_firma(cuerpo, cab["X-PagoOK-Firma"], "whsec_otro"))
        self.assertEqual(cab["X-PagoOK-Evento-Id"], sobre["evento_id"])
        self.assertEqual(json.loads(cuerpo), sobre)

        e = self._entregas()[0]
        self.assertEqual((e.ultimo_estado, e.intentos, e.ultimo_codigo_http), ("entregado", 1, 200))
        self.assertIsNotNone(e.entregado_en)
        self.assertIsNone(e.proximo_reintento_en)

    def test_reintentos_con_espera_creciente_hasta_fallido_definitivo(self):
        ev.emitir_pago_confirmado(self.db, self._pago())
        enviar, llamadas = self._enviador(500, None, 502, 503, 504, 500)
        ahora = self.t0
        esperas_vistas = []
        for intento in range(1, ev.MAX_INTENTOS + 1):
            ahora += timedelta(minutes=30)  # siempre vencido
            self.assertEqual(ev.procesar_pendientes(self.db, enviar=enviar, ahora=ahora), 1)
            e = self._entregas()[0]
            self.assertEqual((e.ultimo_estado, e.intentos), ("fallido", intento))
            if e.proximo_reintento_en is not None:
                prox = e.proximo_reintento_en.replace(tzinfo=timezone.utc)
                esperas_vistas.append(prox - ahora)
        self.assertEqual(esperas_vistas, list(ev.ESPERAS_REINTENTO[:ev.MAX_INTENTOS - 1]))
        self.assertIsNone(e.proximo_reintento_en)
        self.assertEqual(e.ultimo_codigo_http, 500)
        # Ya no se toma más.
        self.assertEqual(ev.procesar_pendientes(self.db, enviar=enviar, ahora=ahora + timedelta(days=1)), 0)
        self.assertEqual(len(llamadas), ev.MAX_INTENTOS)

    def test_no_reintenta_antes_de_tiempo_y_luego_entrega(self):
        ev.emitir_pago_confirmado(self.db, self._pago())
        enviar, llamadas = self._enviador(500, 204)
        ahora = self.t0 + timedelta(hours=1)
        ev.procesar_pendientes(self.db, enviar=enviar, ahora=ahora)
        # 0.5 s después aún no vence el reintento de 1 s.
        self.assertEqual(ev.procesar_pendientes(self.db, enviar=enviar, ahora=ahora + timedelta(milliseconds=500)), 0)
        self.assertEqual(ev.procesar_pendientes(self.db, enviar=enviar, ahora=ahora + timedelta(seconds=2)), 1)
        e = self._entregas()[0]
        self.assertEqual((e.ultimo_estado, e.intentos, e.ultimo_codigo_http), ("entregado", 2, 204))

    def test_suscripcion_desactivada_no_envia(self):
        ev.emitir_pago_confirmado(self.db, self._pago())
        self.s_pago.activo = False
        self.db.commit()
        enviar, llamadas = self._enviador()
        ev.procesar_pendientes(self.db, enviar=enviar, ahora=self.t0 + timedelta(hours=1))
        self.assertEqual(llamadas, [])
        e = self._entregas()[0]
        self.assertEqual(e.ultimo_estado, "fallido")
        self.assertIsNone(e.proximo_reintento_en)


class TestIngestaEmite(EventosBase):
    def test_inbound_yape_encola_pago_confirmado(self):
        self.db.add(Dispositivo(empresa_id=self.emp_id, token="tok_test", nombre="Cel", activo=True))
        self.db.commit()
        with mock.patch("app.services.eventos.despertar_worker") as despertar, \
             mock.patch("app.api.push_service.enviar_push_a_empresa", return_value={}):
            r = self.client.post(
                "/api/v1/notificaciones/inbound",
                headers={"X-Device-Token": "tok_test"},
                json={"package": "com.bcp.innovacxion.yapeapp", "titulo": "Yape!",
                      "texto": "Juan Perez te yapeó S/ 15.00"},
            )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIsNotNone(r.json()["pago_id"])
        entregas = self._entregas()
        self.assertEqual(len(entregas), 1)
        self.assertEqual(entregas[0].payload["datos"]["pago_id"], r.json()["pago_id"])
        self.assertEqual(entregas[0].payload["datos"]["monto"], "15.00")
        despertar.assert_called_once()


class TestReceptorEjemplo(EventosBase):
    URL = "/api/v1/webhooks/receptor-ejemplo"

    def _post(self, sobre, secreto="whsec_ej"):
        cuerpo = ev.serializar_cuerpo(sobre)
        return self.client.post(self.URL, content=cuerpo,
                                headers=ev.cabeceras_para(sobre, cuerpo, secreto))

    def _sobre(self):
        return ev.armar_sobre("pago.confirmado",
                              {"id_fiscal": "1", "pais": "PE", "id_fiscal_tipo": "RUC"}, "PEN",
                              {"monto": "1.00", "canal": "yape"})

    def test_firma_valida_y_luego_duplicado(self):
        sobre = self._sobre()
        with mock.patch.dict(os.environ, {"PAGOOK_WEBHOOK_SECRETO_EJEMPLO": "whsec_ej"}):
            r1 = self._post(sobre)
            r2 = self._post(sobre)
        self.assertEqual((r1.status_code, r1.json()["status"]), (200, "ok"))
        self.assertEqual((r2.status_code, r2.json()["status"]), (200, "duplicado"))

    def test_firma_invalida_401(self):
        with mock.patch.dict(os.environ, {"PAGOOK_WEBHOOK_SECRETO_EJEMPLO": "whsec_ej"}):
            r = self._post(self._sobre(), secreto="whsec_falso")
        self.assertEqual(r.status_code, 401)

    def test_cuerpo_alterado_401(self):
        sobre = self._sobre()
        cuerpo = ev.serializar_cuerpo(sobre)
        cab = ev.cabeceras_para(sobre, cuerpo, "whsec_ej")
        with mock.patch.dict(os.environ, {"PAGOOK_WEBHOOK_SECRETO_EJEMPLO": "whsec_ej"}):
            r = self.client.post(self.URL, content=cuerpo.replace(b"1.00", b"9.00"), headers=cab)
        self.assertEqual(r.status_code, 401)

    def test_sin_secreto_configurado_503(self):
        with mock.patch.dict(os.environ, {"PAGOOK_WEBHOOK_SECRETO_EJEMPLO": ""}):
            r = self._post(self._sobre())
        self.assertEqual(r.status_code, 503)


if __name__ == "__main__":
    unittest.main()
