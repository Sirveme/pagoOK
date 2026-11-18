"""zWeb-12: tablero /admin/testers-metricas (solo lectura).

SQLite en memoria. Ejecutar:  python -m unittest tests.test_testers_metricas
"""
import csv
import io
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DATABASE_URL", "postgresql://dummy:dummy@localhost/dummy")
os.environ["ADMIN_TOKEN"] = "token-admin-de-prueba"
os.environ["PAGOOK_ENTORNO"] = "desarrollo"
os.environ.pop("SLUGS_DEMO", None)          # por defecto, la demo es perusistemas

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.admin.db import Base, get_db  # noqa: E402
import app.admin.models  # noqa: E402,F401
from app.admin.models import Empresa  # noqa: E402
from app.api.models_device import DeviceEstadoActual, DeviceEstadoHistorico  # noqa: E402
from app.api.models_incidente import DispositivoIncidente  # noqa: E402
from app.api.models_webhook import Dispositivo, NotificacionRaw, PagoDetectado  # noqa: E402
from app.main import app  # noqa: E402

ADMIN = {"X-Admin-Token": "token-admin-de-prueba"}
URL = "/api/v1/admin/testers-metricas"


class TestersMetricasTest(unittest.TestCase):
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
        self.ahora = datetime.now(timezone.utc)
        self._seed()

    def tearDown(self):
        app.dependency_overrides.clear()
        Base.metadata.drop_all(self.engine)

    # ------------------------------------------------------------ datos
    def _empresa(self, db, slug, nombre, **kw):
        e = Empresa(pais_codigo="PE", id_fiscal=slug.upper()[:20], razon_social=nombre, nombre_comercial=nombre,
                    slug=slug, **kw)
        db.add(e)
        db.flush()
        return e

    def _equipo(self, db, emp, marca, modelo, android, version):
        d = Dispositivo(empresa_id=emp.id, token=f"tok_{emp.slug}_{modelo}", nombre=modelo, activo=True,
                        creado_en=(self.ahora - timedelta(days=30)).replace(tzinfo=None))
        db.add(d)
        db.flush()
        db.add(DeviceEstadoActual(dispositivo_id=d.id, estado="verde", manufacturer=marca, modelo=modelo,
                                  android_version=android, app_version=version, listener_conectado=True,
                                  ultimo_heartbeat=self.ahora - timedelta(minutes=2)))
        return d

    def _pago(self, db, disp, hace, latencia_s=2.0, **kw):
        srv = (self.ahora - hace).replace(tzinfo=None)
        raw = NotificacionRaw(empresa_id=disp.empresa_id, dispositivo_id=disp.id, package_app="com.bcp.innovacxion.yapeapp",
                              titulo="Yape", texto="x", estado_parseo="parseada", timestamp_servidor=srv,
                              timestamp_celular=int(((self.ahora - hace).timestamp() - latencia_s) * 1000))
        db.add(raw)
        db.flush()
        db.add(PagoDetectado(empresa_id=disp.empresa_id, notificacion_raw_id=raw.id, metodo="yape",
                             monto=Decimal("10.00"), titular="A", recibido_en=srv, **kw))

    def _seed(self):
        db = self.Session()
        try:
            tienda = self._empresa(db, "tienda", "=HYPERLINK(\"x\")")     # nombre malicioso para el CSV
            otra = self._empresa(db, "botica", "Botica Sol")
            demo = self._empresa(db, "perusistemas", "Perú Sistemas")
            rev = self._empresa(db, "revision-play", "Revisión Play", es_cuenta_revision=True)
            self.a = self._equipo(db, tienda, "motorola", "moto g06", "15", "1.7.1")
            self.b = self._equipo(db, otra, "Xiaomi", "Redmi 13", "14", "1.5.1")
            self.c = self._equipo(db, tienda, "motorola", "moto e6", "9", "1.6.2")
            demo_d = self._equipo(db, demo, "motorola", "moto e6 plus", "9", "1.5.1")
            rev_d = self._equipo(db, rev, "google", "pixel", "15", "1.7.1")
            # A: 2 pagos reales (1 en 7 días), + prueba, dudoso y egreso que NO cuentan.
            self._pago(db, self.a, timedelta(days=10), latencia_s=4.0)
            self._pago(db, self.a, timedelta(days=1), latencia_s=2.0)
            self._pago(db, self.a, timedelta(hours=3), es_prueba=True)
            self._pago(db, self.a, timedelta(hours=2), tipo_incierto=True)
            self._pago(db, self.a, timedelta(hours=1), tipo="egreso")
            # Demo y revisión con pagos: no deben aparecer.
            self._pago(db, demo_d, timedelta(hours=5))
            self._pago(db, rev_d, timedelta(hours=5))
            # A: 24 horas en verde en el histórico.
            for k in range(1, 25):
                db.add(DeviceEstadoHistorico(dispositivo_id=self.a.id,
                                             hora_inicio=self.ahora - timedelta(hours=k) + timedelta(minutes=1),
                                             hora_fin=self.ahora - timedelta(hours=k - 1), estado_predominante="verde"))
            # C: 12 h en verde y 12 h en rojo.
            for k in range(1, 25):
                db.add(DeviceEstadoHistorico(dispositivo_id=self.c.id,
                                             hora_inicio=self.ahora - timedelta(hours=k) + timedelta(minutes=1),
                                             hora_fin=self.ahora - timedelta(hours=k - 1),
                                             estado_predominante="verde" if k % 2 else "rojo"))
            # A: un zombie (x2) y una alarma en 7 días + una alarma vieja que no cuenta.
            for tipo, cant, hace in (("zombie", 2, timedelta(days=2)), ("alarma", 1, timedelta(days=1)),
                                     ("alarma", 1, timedelta(days=20))):
                db.add(DispositivoIncidente(dispositivo_id=self.a.id, empresa_id=tienda.id, tipo=tipo, cantidad=cant,
                                            mensaje="m", ocurrido_en=self.ahora - hace, activa=False))
            db.commit()
            self.ids = {"a": self.a.id, "b": self.b.id, "c": self.c.id, "demo": demo_d.id, "rev": rev_d.id}
        finally:
            db.close()

    def _datos(self):
        r = self.client.get(URL, headers=ADMIN)
        self.assertEqual(r.status_code, 200)
        return r.json()

    def _fila(self, datos, clave):
        return next(d for d in datos["dispositivos"] if d["dispositivo_id"] == self.ids[clave])

    # ------------------------------------------------------------ tests
    def test_excluye_revision_y_demos(self):
        j = self._datos()
        ids = {d["dispositivo_id"] for d in j["dispositivos"]}
        self.assertEqual(ids, {self.ids["a"], self.ids["b"], self.ids["c"]})
        self.assertEqual(j["excluidas"], ["perusistemas", "revision-play"])

    def test_pagos_sin_pruebas_ni_dudosos_ni_egresos(self):
        a = self._fila(self._datos(), "a")
        self.assertEqual((a["pagos_total"], a["pagos_7d"]), (2, 1))

    def test_latencia_mediana_y_p90(self):
        j = self._datos()
        a = self._fila(j, "a")
        # 5 avisos de A en 30 días: 4 s, 2 s y tres de 2 s -> mediana 2, p90 3.2
        self.assertEqual(a["latencia_muestras"], 5)
        self.assertEqual(a["latencia_p50_s"], 2.0)
        self.assertEqual(a["semaforo_latencia"], "verde")
        b = self._fila(j, "b")
        self.assertIsNone(b["latencia_p50_s"])                       # sin avisos: sin datos
        self.assertEqual(b["semaforo_latencia"], "gris")

    def test_tiempo_en_verde(self):
        j = self._datos()
        a, b, c = self._fila(j, "a"), self._fila(j, "b"), self._fila(j, "c")
        self.assertEqual((a["verde_24h_pct"], a["semaforo_verde_24h"]), (100.0, "verde"))
        self.assertEqual(a["verde_7d_pct"], round(100 * 24 / 168, 1))
        self.assertEqual((c["verde_24h_pct"], c["semaforo_verde_24h"]), (50.0, "rojo"))
        self.assertEqual((b["verde_24h_pct"], b["semaforo_verde_24h"]), (0.0, "rojo"))   # sin horas = no verde

    def test_zombies_y_alarmas_7_dias(self):
        j = self._datos()
        a = self._fila(j, "a")
        self.assertEqual((a["zombies_7d"], a["alarmas_7d"], a["semaforo_incidentes"]), (2, 1, "rojo"))
        self.assertEqual(self._fila(j, "b")["zombies_7d"], 0)
        self.assertTrue(j["incidentes_disponibles"])

    def test_version_de_la_app(self):
        j = self._datos()
        self.assertEqual(j["version_referencia"], "1.7.1")
        self.assertEqual([self._fila(j, k)["semaforo_version"] for k in ("a", "c", "b")],
                         ["verde", "amarillo", "rojo"])                 # 1.5.1 no informa el lector

    def test_agrupaciones(self):
        g = self._datos()["grupos"]
        marcas = {x["grupo"]: x for x in g["marca"]}
        self.assertEqual(set(marcas), {"Motorola", "Xiaomi"})
        self.assertEqual((marcas["Motorola"]["dispositivos"], marcas["Motorola"]["pagos_total"]), (2, 2))
        self.assertEqual(marcas["Motorola"]["por_actualizar"], 1)
        self.assertEqual({x["grupo"] for x in g["android"]}, {"15", "14", "9"})
        self.assertEqual({x["grupo"] for x in g["modelo"]}, {"moto g06", "Redmi 13", "moto e6"})
        self.assertEqual([(x["grupo"], x["dispositivos"]) for x in g["rubro"]], [("Sin rubro", 3)])

    def test_rubro_cuando_existe_la_columna(self):
        with self.engine.begin() as c:
            c.execute(text("ALTER TABLE empresa ADD COLUMN rubro VARCHAR(40)"))
            c.execute(text("UPDATE empresa SET rubro = 'farmacia' WHERE slug = 'botica'"))
        j = self._datos()
        self.assertTrue(j["rubro_disponible"])
        self.assertEqual({x["grupo"]: x["dispositivos"] for x in j["grupos"]["rubro"]},
                         {"Sin rubro": 2, "farmacia": 1})

    def test_sin_tabla_de_incidentes_muestra_sin_datos(self):
        DispositivoIncidente.__table__.drop(self.engine)
        try:
            j = self._datos()
            self.assertFalse(j["incidentes_disponibles"])
            self.assertIsNone(self._fila(j, "a")["zombies_7d"])
            self.assertEqual(self._fila(j, "a")["semaforo_incidentes"], "gris")
            self.assertEqual(self._fila(j, "a")["pagos_total"], 2)        # lo demás sigue calculándose
        finally:
            DispositivoIncidente.__table__.create(self.engine)

    def test_csv(self):
        r = self.client.get("/admin/testers-metricas.csv", headers=ADMIN)
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment", r.headers["content-disposition"])
        filas = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))
        self.assertEqual(filas[0][:3], ["Empresa", "Rubro", "Dispositivo"])
        self.assertEqual(len(filas), 4)                                 # cabecera + 3 equipos
        self.assertTrue(all(not f[0].startswith("=") for f in filas[1:]))  # sin inyección de fórmulas
        self.assertIn("sin datos", filas[-1] + filas[-2] + filas[-3])

    def test_permisos(self):
        self.assertEqual(self.client.get(URL).status_code, 401)
        self.assertEqual(self.client.get(URL, headers={"X-Admin-Token": "malo"}).status_code, 401)
        self.assertEqual(self.client.get("/admin/testers-metricas.csv").status_code, 401)
        r = self.client.get("/admin/testers-metricas")
        self.assertEqual(r.status_code, 401)
        self.assertIn("Ingresa el token", r.text)

    def test_pagina_html_oscura_y_sin_token(self):
        r = self.client.post("/admin/acceso", data={"token": "token-admin-de-prueba",
                                                    "destino": "/admin/testers-metricas"})
        self.assertEqual(r.status_code, 200)
        html = r.text
        for texto in ("Métricas de testers", "Exportar CSV", "Por marca", "Por rubro", "sin datos"):
            self.assertIn(texto, html)
        self.assertNotIn("token-admin-de-prueba", html)
        for blanco in ("#ffffff", "#fff;", "#fff ", "background:white"):
            self.assertNotIn(blanco, html.lower())
        self.assertNotIn('</script>"', html)
        # Con la cookie de sesión, el JSON y el CSV también responden.
        self.assertEqual(self.client.get(URL).status_code, 200)
        self.assertEqual(self.client.get("/admin/testers-metricas.csv").status_code, 200)

    def test_es_solo_lectura(self):
        db = self.Session()
        try:
            antes = [db.query(m).count() for m in (Empresa, Dispositivo, PagoDetectado, DispositivoIncidente,
                                                   DeviceEstadoHistorico)]
        finally:
            db.close()
        self._datos()
        self.client.get("/admin/testers-metricas.csv", headers=ADMIN)
        db = self.Session()
        try:
            despues = [db.query(m).count() for m in (Empresa, Dispositivo, PagoDetectado, DispositivoIncidente,
                                                     DeviceEstadoHistorico)]
        finally:
            db.close()
        self.assertEqual(antes, despues)


if __name__ == "__main__":
    unittest.main(verbosity=2)
