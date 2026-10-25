"""Tests offline de la API de consulta de pagos (pull) y marcar-usado.

SQLite en memoria + get_db sobreescrito. No toca la BD viva.
Ejecutar:  python -m unittest tests.test_consulta_pagos
"""
import os
import sys
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DATABASE_URL", "postgresql://dummy:dummy@localhost/dummy")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.admin.db import Base, get_db  # noqa: E402
import app.admin.models  # noqa: E402,F401
from app.admin.models import Empresa  # noqa: E402
from app.api.models_cuenta import CuentaApi, generar_api_key, hash_api_key  # noqa: E402
from app.api.models_pago_uso import PagoUso  # noqa: E402
from app.api.models_webhook import PagoDetectado  # noqa: E402
from app.services.conciliacion import nombre_coincide  # noqa: E402
from app.main import app  # noqa: E402

URL = "/api/v1/pagos/consultar"
# 10:00 hora de Lima = 15:00 UTC (recibido_en se guarda en UTC naive).
DECLARADA = "2026-10-03T10:00:00-05:00"


def utc(h, m=0):
    return datetime(2026, 10, 3, h, m, 0)


class ConsultaBase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        self.Session = sessionmaker(bind=self.engine, autoflush=False)
        Base.metadata.create_all(self.engine)

        def _override_get_db():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = _override_get_db
        self.client = TestClient(app)
        self.db = self.Session()

        a = Empresa(pais_codigo="PE", id_fiscal="20100000001", razon_social="A", slug="emp-a")
        b = Empresa(pais_codigo="PE", id_fiscal="20100000002", razon_social="B", slug="emp-b")
        self.db.add_all([a, b])
        self.db.flush()
        self.emp_a, self.emp_b = a.id, b.id

        self.key_fact, self.key_qv, self.key_b = generar_api_key(), generar_api_key(), generar_api_key()
        c1 = CuentaApi(empresa_id=a.id, nombre="Facturalo", api_key_hash=hash_api_key(self.key_fact), activa=True)
        c2 = CuentaApi(empresa_id=a.id, nombre="QueVendi", api_key_hash=hash_api_key(self.key_qv), activa=True)
        c3 = CuentaApi(empresa_id=b.id, nombre="Facturalo B", api_key_hash=hash_api_key(self.key_b), activa=True)
        self.db.add_all([c1, c2, c3])
        self.db.commit()
        self.cuenta_fact = c1.id

    def tearDown(self):
        self.db.close()
        app.dependency_overrides.clear()
        Base.metadata.drop_all(self.engine)

    def pago(self, monto="30.00", hora=utc(15, 2), empresa=None, **kw):
        datos = dict(empresa_id=empresa or self.emp_a, metodo="yape", monto=Decimal(monto),
                     titular="Juan Perez Lopez", recibido_en=hora, tipo="ingreso")
        datos.update(kw)
        p = PagoDetectado(**datos)
        self.db.add(p)
        self.db.commit()
        return p.id

    def consultar(self, key=None, **cuerpo):
        body = {"monto": "30.00", "fecha_hora_declarada": DECLARADA}
        body.update(cuerpo)
        r = self.client.post(URL, json=body, headers={"X-API-Key": key or self.key_fact})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def marcar(self, pago_id, ref, key=None, **extra):
        return self.client.post(f"/api/v1/pagos/{pago_id}/marcar-usado",
                                json={"referencia_externa": ref, **extra},
                                headers={"X-API-Key": key or self.key_fact})


class TestNiveles(ConsultaBase):
    def test_alta_candidato_unico(self):
        pid = self.pago(codigo_operacion="00123")
        r = self.consultar()
        self.assertEqual((r["nivel"], r["motivo"], r["accion_sugerida"]),
                         ("alta", "candidato_unico", "aceptar"))
        p = r["pago"]
        self.assertEqual(p["pago_id"], pid)
        self.assertEqual(p["monto"], "30.00")
        self.assertEqual((p["canal"], p["institucion"]), ("yape", "Yape"))
        self.assertEqual(p["contraparte"], "Juan Perez Lopez")
        self.assertEqual(p["codigo_operacion"], "00123")
        self.assertEqual(p["fecha_hora"], "2026-10-03T10:02:00-05:00")
        self.assertEqual(r["candidatos"], [])

    def test_media_varios_candidatos(self):
        ids = {self.pago(hora=utc(15, 5)), self.pago(hora=utc(14, 50), titular="Ana Ruiz")}
        r = self.consultar()
        self.assertEqual((r["nivel"], r["motivo"], r["accion_sugerida"]),
                         ("media", "varios_candidatos", "confirmar_manualmente"))
        self.assertIsNone(r["pago"])
        self.assertEqual({c["pago_id"] for c in r["candidatos"]}, ids)

    def test_sin_coincidencia_por_monto_y_por_ventana(self):
        self.pago(monto="30.50")                 # otro monto
        self.pago(hora=utc(15, 40))              # fuera de ±15 min
        r = self.consultar()
        self.assertEqual((r["nivel"], r["motivo"], r["accion_sugerida"]),
                         ("sin_coincidencia", "sin_pagos", "pedir_voucher"))
        # Con ventana más amplia sí aparece.
        self.assertEqual(self.consultar(ventana_minutos=60)["nivel"], "alta")

    def test_hora_declarada_sin_zona_es_hora_de_lima(self):
        self.pago()
        self.assertEqual(self.consultar(fecha_hora_declarada="2026-10-03T10:00:00")["nivel"], "alta")

    def test_egresos_y_transferencias_internas_no_cuentan(self):
        self.pago(tipo="egreso")
        self.pago(confirmacion_usuario="confirmado_interno")
        self.assertEqual(self.consultar()["nivel"], "sin_coincidencia")

    def test_pago_dudoso_no_cuenta(self):
        # Caso real: promoción de Yape guardada como ingreso tipo_incierto.
        self.pago(monto="3000.00", titular="", tipo_incierto=True)
        r = self.consultar(monto="3000.00")
        self.assertEqual((r["nivel"], r["motivo"]), ("sin_coincidencia", "sin_pagos"))

    def test_listado_v1_excluye_pago_dudoso(self):
        normal = self.pago()
        dudoso = self.pago(monto="3000.00", titular="", tipo_incierto=True)
        r = self.client.get("/api/v1/pagos", headers={"X-API-Key": self.key_fact})
        ids = [p["id"] for p in r.json()["pagos"]]
        self.assertIn(str(normal), ids)
        self.assertNotIn(str(dudoso), ids)

    def test_canal_afina(self):
        self.pago(metodo="yape")
        plin = self.pago(metodo="plin", titular="Ana Ruiz")
        self.assertEqual(self.consultar()["nivel"], "media")
        r = self.consultar(canal="plin")
        self.assertEqual((r["nivel"], r["pago"]["pago_id"]), ("alta", plin))


class TestDesempate(ConsultaBase):
    def test_codigo_operacion_desempata_entre_varios(self):
        self.pago(codigo_operacion="111111")
        objetivo = self.pago(codigo_operacion="222222", titular="Ana Ruiz")
        r = self.consultar(codigo_operacion="222222")
        self.assertEqual((r["nivel"], r["motivo"], r["pago"]["pago_id"]),
                         ("alta", "codigo_operacion", objetivo))

    def test_codigo_operacion_gana_aunque_la_hora_declarada_este_lejos(self):
        objetivo = self.pago(codigo_operacion="333333", hora=utc(20))
        r = self.consultar(codigo_operacion="333333")
        self.assertEqual((r["nivel"], r["pago"]["pago_id"]), ("alta", objetivo))

    def test_codigo_que_contradice_descarta_candidato(self):
        self.pago(codigo_operacion="999999")
        self.assertEqual(self.consultar(codigo_operacion="123456")["nivel"], "sin_coincidencia")

    def test_nombre_pagador_desempata(self):
        self.pago(titular="Ana Ruiz")
        objetivo = self.pago(titular="Juan P.", hora=utc(15, 6))
        r = self.consultar(nombre_pagador_declarado="Juan Pérez")
        self.assertEqual((r["nivel"], r["motivo"], r["pago"]["pago_id"]),
                         ("alta", "monto_ventana_nombre", objetivo))
        self.assertTrue(r["pago"]["coincide_nombre"])

    def test_nombre_no_coincide_con_candidato_unico_es_media(self):
        self.pago(titular="Ana Ruiz")
        r = self.consultar(nombre_pagador_declarado="Juan Perez")
        self.assertEqual((r["nivel"], r["motivo"]), ("media", "nombre_no_coincide"))
        self.assertFalse(r["candidatos"][0]["coincide_nombre"])

    def test_nombre_sin_titular_capturado_no_bloquea_candidato_unico(self):
        self.pago(titular="")
        r = self.consultar(nombre_pagador_declarado="Juan Perez")
        self.assertEqual((r["nivel"], r["motivo"]), ("alta", "candidato_unico"))

    def test_comparacion_de_nombres(self):
        self.assertTrue(nombre_coincide("Juan Perez Lopez", "JUAN PÉREZ"))
        self.assertTrue(nombre_coincide("Juan P.", "Juan Perez"))
        self.assertTrue(nombre_coincide("Juan Pere*", "Juan Perez Lopez"))
        self.assertFalse(nombre_coincide("Juan Gomez", "Ana Ruiz"))
        self.assertFalse(nombre_coincide("Juan Gomez", "Juan Perez"))
        self.assertIsNone(nombre_coincide("Juan", "Juan Perez"))   # solo nombre de pila
        self.assertIsNone(nombre_coincide("", "Juan Perez"))


class TestAislamientoPorEmpresa(ConsultaBase):
    def test_empresa_b_no_ve_pagos_de_a(self):
        pid_a = self.pago(codigo_operacion="777777")
        for cuerpo in ({}, {"codigo_operacion": "777777"}, {"ventana_minutos": 1440}):
            r = self.consultar(key=self.key_b, **cuerpo)
            self.assertEqual(r["nivel"], "sin_coincidencia")
            self.assertEqual(r["pagos_ya_usados"], [])
        # Y no puede marcarlo como usado (404, no 409: ni siquiera se revela que existe).
        self.assertEqual(self.marcar(pid_a, "X-1", key=self.key_b).status_code, 404)

    def test_cuerpo_no_puede_elegir_empresa(self):
        self.pago(empresa=self.emp_a)
        r = self.consultar(key=self.key_b, empresa_id=self.emp_a)  # campo extra ignorado
        self.assertEqual(r["nivel"], "sin_coincidencia")

    def test_sin_credencial_401(self):
        r = self.client.post(URL, json={"monto": "30.00", "fecha_hora_declarada": DECLARADA})
        self.assertEqual(r.status_code, 401)

    def test_validaciones_422(self):
        h = {"X-API-Key": self.key_fact}
        for malo in ({"monto": "abc"}, {"monto": "-5"}, {"canal": "bitcoin"},
                     {"ventana_minutos": 0}, {"fecha_hora_declarada": "ayer"}):
            body = {"monto": "30.00", "fecha_hora_declarada": DECLARADA, **malo}
            self.assertEqual(self.client.post(URL, json=body, headers=h).status_code, 422, malo)


class TestIdempotenciaDeUso(ConsultaBase):
    def test_marcar_usado_y_no_reutilizar(self):
        pid = self.pago()
        r = self.marcar(pid, "SUSC-001", sistema="Facturalo suscripciones")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["usado"], r.json()["ya_estaba_usado"]), (True, False))
        self.assertEqual(r.json()["sistema"], "Facturalo suscripciones")

        # Repetir con la misma referencia: idempotente.
        r2 = self.marcar(pid, "SUSC-001")
        self.assertEqual((r2.status_code, r2.json()["ya_estaba_usado"]), (200, True))

        # Misma credencial, OTRA suscripción: doble cobro → 409.
        r3 = self.marcar(pid, "SUSC-002")
        self.assertEqual(r3.status_code, 409)
        self.assertIn("SUSC-001", r3.json()["detail"])

        # Otro sistema de la misma empresa: 409.
        self.assertEqual(self.marcar(pid, "PED-9", key=self.key_qv).status_code, 409)
        self.assertEqual(self.db.query(PagoUso).count(), 1)

    def test_consulta_posterior_informa_pago_ya_usado(self):
        pid = self.pago()
        self.marcar(pid, "SUSC-001")
        for key in (self.key_fact, self.key_qv):
            r = self.consultar(key=key)
            self.assertEqual((r["nivel"], r["motivo"]), ("sin_coincidencia", "ya_usado"))
            usado = r["pagos_ya_usados"][0]
            self.assertEqual((usado["pago_id"], usado["estado"]), (pid, "usado"))
            self.assertEqual(usado["uso"]["referencia_externa"], "SUSC-001")
            self.assertEqual(usado["uso"]["sistema"], "Facturalo")

    def test_pago_usado_no_compite_con_uno_libre(self):
        usado = self.pago()
        libre = self.pago(hora=utc(15, 8), titular="Ana Ruiz")
        self.marcar(usado, "SUSC-001")
        r = self.consultar()
        self.assertEqual((r["nivel"], r["pago"]["pago_id"]), ("alta", libre))
        self.assertEqual([u["pago_id"] for u in r["pagos_ya_usados"]], [usado])

    def test_pago_reclamado_por_otra_credencial_no_es_candidato(self):
        pid = self.pago()
        r = self.client.post(f"/api/v1/pagos/{pid}/reclamar", headers={"X-API-Key": self.key_qv})
        self.assertEqual(r.status_code, 200)
        c = self.consultar()
        self.assertEqual(c["nivel"], "sin_coincidencia")
        self.assertEqual(c["pagos_ya_usados"][0]["estado"], "reclamado_por_otro_sistema")
        self.assertEqual(self.marcar(pid, "SUSC-1").status_code, 409)

    def test_marcar_usado_saca_el_pago_del_listado_de_otros(self):
        pid = self.pago()
        self.marcar(pid, "SUSC-001")
        r = self.client.get("/api/v1/pagos", headers={"X-API-Key": self.key_qv})
        self.assertNotIn(str(pid), [p["id"] for p in r.json()["pagos"]])

    def test_marcar_usado_404_y_422(self):
        self.assertEqual(self.marcar(99999, "X").status_code, 404)
        pid = self.pago()
        self.assertEqual(self.marcar(pid, "   ").status_code, 422)


if __name__ == "__main__":
    unittest.main()
