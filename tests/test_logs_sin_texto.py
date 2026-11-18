"""zWeb-12: ningún log contiene el texto, el título ni datos sacados de un aviso.

Cubre los endpoints de entrada desde el celular (inbound y no-reconocido) en
todos sus caminos: parseado, tipo incierto, sin match, error del parser y aviso
no reconocido. Más una revisión del código fuente para que no vuelva a pasar.

SQLite en memoria. Ejecutar:  python -m unittest tests.test_logs_sin_texto
"""
import os
import re
import sys
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DATABASE_URL", "postgresql://dummy:dummy@localhost/dummy")
os.environ.setdefault("ADMIN_TOKEN", "token-admin-de-prueba")
os.environ["PAGOOK_ENTORNO"] = "desarrollo"

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.admin.db import Base, get_db  # noqa: E402
import app.admin.models  # noqa: E402,F401
from app.admin.models import Empresa  # noqa: E402
from app.api.models_webhook import Dispositivo  # noqa: E402
from app.main import app  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
TITULO = "TITULOSECRETO Yape!"
TEXTO = "Zacarias Quispilema te yapeó S/ 15.00"
# Nada de esto puede aparecer en ningún log.
PROHIBIDO = ("TITULOSECRETO", "Zacarias", "Quispilema", "te yapeó", "15.00", "Z. Quispilema")


class LogsSinTextoTest(unittest.TestCase):
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
            emp = Empresa(pais_codigo="PE", id_fiscal="20100000001", razon_social="PSP",
                          nombre_comercial="PSP", slug="perusistemas")
            db.add(emp)
            db.flush()
            db.add(Dispositivo(empresa_id=emp.id, token="tok_log", nombre="moto", activo=True))
            db.commit()
        finally:
            db.close()

    def tearDown(self):
        app.dependency_overrides.clear()
        Base.metadata.drop_all(self.engine)

    def _inbound(self, texto=TEXTO, sbn=None):
        body = {"package": "com.bcp.innovacxion.yapeapp", "titulo": TITULO, "texto": texto}
        if sbn:
            body["sbn_key"] = sbn
        with self.assertLogs("pagook", "DEBUG") as logs:
            r = self.client.post("/api/v1/notificaciones/inbound", json=body, headers={"X-Device-Token": "tok_log"})
        self.assertEqual(r.status_code, 200)
        return r, "\n".join(logs.output)

    def _sin_contenido(self, salida):
        for prohibido in PROHIBIDO:
            self.assertNotIn(prohibido, salida)

    def test_inbound_parseado(self):
        r, salida = self._inbound()
        self.assertIsNotNone(r.json()["pago_id"])
        self._sin_contenido(salida)
        self.assertIn("Aviso parseado", salida)
        self.assertIn(f"longitud_texto={len(TEXTO)}", salida)
        self.assertIn("paquete=com.bcp.innovacxion.yapeapp", salida)
        self.assertRegex(salida, r"dispositivo=\d+ empresa=\d+")

    def test_inbound_tipo_incierto(self):
        falso = {"metodo": "yape", "monto": Decimal("15.00"), "titular": "Zacarias Quispilema",
                 "titular_corto": "Z. Quispilema", "tipo": "ingreso", "tipo_incierto": True}
        with mock.patch("app.api.notificaciones.parsear", return_value=falso):
            _, salida = self._inbound()
        self._sin_contenido(salida)
        self.assertIn("tipo incierto", salida)

    def test_inbound_sin_match(self):
        _, salida = self._inbound(texto="Zacarias Quispilema: tu resumen semanal está listo")
        self._sin_contenido(salida)
        self.assertIn("estado=ignorada", salida)

    def test_inbound_error_del_parser_no_loguea_el_mensaje(self):
        # El mensaje de la excepción trae el texto: no debe llegar al log.
        with mock.patch("app.api.notificaciones.parsear", side_effect=ValueError(f"no entendí {TEXTO}")):
            _, salida = self._inbound()
        self._sin_contenido(salida)
        self.assertIn("estado=fallida", salida)
        self.assertIn("ValueError", salida)

    def test_inbound_duplicado(self):
        self._inbound(sbn="k-1")
        _, salida = self._inbound(sbn="k-1")
        self._sin_contenido(salida)

    def test_no_reconocido(self):
        with self.assertLogs("pagook", "DEBUG") as logs:
            r = self.client.post("/api/v1/device/no-reconocido", headers={"X-Device-Token": "tok_log"},
                                 json={"package": "com.bcp.innovacxion.yapeapp", "titulo": TITULO, "texto": TEXTO})
        self.assertEqual(r.status_code, 200)
        salida = "\n".join(logs.output)
        self._sin_contenido(salida)
        self.assertIn("estado=no_reconocido", salida)
        self.assertIn(f"longitud_texto={len(TEXTO)}", salida)

    def test_paquete_con_texto_se_sanea(self):
        from app.services.log_seguro import paquete_seguro
        self.assertEqual(paquete_seguro("com.app Zacarias te yapeó"), "invalido")
        self.assertEqual(paquete_seguro("Zacarias Quispilema"), "invalido")
        self.assertEqual(paquete_seguro("pe.com.interbank.mobilebanking"), "pe.com.interbank.mobilebanking")
        self.assertEqual(paquete_seguro(None), "-")

    def test_codigo_fuente_sin_contenido_en_logs(self):
        """Revisión estática: ninguna llamada a logger interpola título, texto,
        titular, cuerpo o asunto (también en llamadas de varias líneas)."""
        prohibidas = re.compile(r"\{[^}]*\b(texto|titulo|titular\w*|cuerpo\w*|asunto|body|parsed)\b[^}]*\}")
        culpables = []
        for archivo in (RAIZ / "app").rglob("*.py"):
            fuente = archivo.read_text(encoding="utf-8")
            for m in re.finditer(r"logger\.(?:debug|info|warning|error|exception|critical)\(", fuente):
                # Toma la llamada completa hasta cerrar paréntesis (simple, suficiente aquí).
                nivel, i = 1, m.end()
                while nivel and i < len(fuente):
                    nivel += {"(": 1, ")": -1}.get(fuente[i], 0)
                    i += 1
                llamada = fuente[m.end():i]
                if prohibidas.search(llamada):
                    culpables.append(f"{archivo.relative_to(RAIZ)}: {llamada[:90]!r}")
        self.assertEqual(culpables, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
