"""Tests de /terminos (Condiciones del Servicio) — zWeb-10.

Página pública, mismo diseño que /privacidad, versión leída del archivo y
fallback controlado (503, nunca 500) si el archivo no existe.

Ejecutar:  python -m unittest tests.test_terminos
"""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DATABASE_URL", "postgresql://dummy:dummy@localhost/dummy")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.api import router_terminos  # noqa: E402

RUTA = Path(__file__).resolve().parent.parent / "docs" / "legal" / "terminos.md"


class TestTerminos(unittest.TestCase):
    def setUp(self):
        router_terminos._cache_terminos.clear()
        self.addCleanup(router_terminos._cache_terminos.clear)

    def cliente(self):
        return TestClient(app)

    def test_pagina_200_titulo_indice_y_version(self):
        r = self.cliente().get("/terminos")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("<title>Condiciones del Servicio · PagoOK</title>", r.text)
        self.assertIn("Condiciones del Servicio de PagoOK", r.text)   # título del documento
        self.assertIn('class="toc"', r.text)                          # índice de secciones
        self.assertNotIn("#ffffff", r.text.lower())                   # tema oscuro

    def test_version_leida_del_archivo(self):
        # La versión que muestra la página sale del propio terminos.md.
        fuente = RUTA.read_text(encoding="utf-8")
        import re
        esperada = re.search(r"Versi[oó]n\s+(\d{4}-\d{2}-v\d+)", fuente).group(1)
        self.assertEqual(router_terminos.version_terminos(), esperada)
        self.assertIn(esperada, self.cliente().get("/terminos").text)

    def test_texto_plano(self):
        r = self.cliente().get("/terminos.txt")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.headers["content-type"].startswith("text/plain"))
        self.assertNotIn("**", r.text)                                # marcado quitado
        self.assertIn("PERU SISTEMAS PRO E.I.R.L.", r.text)

    def test_sin_archivo_responde_controlado_no_500(self):
        with mock.patch.object(router_terminos, "RUTA_TERMINOS", Path("no/existe.md")), \
                self.assertLogs("pagook", "ERROR") as logs:
            c = self.cliente()
            self.assertEqual(c.get("/terminos").status_code, 503)
            self.assertEqual(c.get("/terminos.txt").status_code, 503)
        self.assertIn("terminos.md", logs.output[0])


if __name__ == "__main__":
    unittest.main()
