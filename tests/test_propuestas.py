"""Tests offline de zWeb-07: propuestas comerciales en /propuestas/<codigo>.

Reusa la base de datos de prueba de tests/test_multiempresa.py (SQLite en memoria).
Usa una carpeta temporal de propuestas: no depende de docs/propuestas/.

Ejecutar:  python -m unittest tests.test_propuestas
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.test_multiempresa import Base_  # noqa: E402

from app.api import router_propuestas  # noqa: E402
from app.api.models_propuestas import PropuestaVisita  # noqa: E402
from app.api.router_propuestas import resumir_user_agent  # noqa: E402

CODIGO = "clinica-demo-k7p4qx"
PROPUESTA = """---
titulo: Propuesta PagoOK para Clínica Demo
cliente: Clínica Demo S.A.C.
fecha: 2026-10-26
---

# Propuesta PagoOK para Clínica Demo

Estimado señor, le presentamos **nuestra propuesta**.

**Para:** Contador
**De:** Perú Sistemas Pro

## Inversión

| Plan | Sedes | Precio mensual |
|------|-------|----------------|
| Básico | 1 | S/ 49 |
| Clínica | 3 | S/ 129 |
"""
UA_ANDROID = ("Mozilla/5.0 (Linux; Android 13; SM-A145M) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/129.0.0.0 Mobile Safari/537.36")
UA_WHATSAPP = "WhatsApp/2.24.20.79 A"


class BasePropuestas(Base_):
    def setUp(self):
        super().setUp()
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        (self.dir / "clinica-demo.md").write_text(PROPUESTA, encoding="utf-8")
        (self.dir / "indice.yml").write_text(f"{CODIGO}: clinica-demo.md\n", encoding="utf-8")
        p = mock.patch.object(router_propuestas, "RUTA_PROPUESTAS", self.dir)
        p.start()
        self.addCleanup(p.stop)
        router_propuestas._cache.clear()
        self.addCleanup(router_propuestas._cache.clear)

    def ver(self, codigo=CODIGO, ua=UA_ANDROID):
        return self.cliente().get(f"/propuestas/{codigo}", headers={"user-agent": ua})


class TestPaginaPropuesta(BasePropuestas):
    def test_pagina_con_encabezado_y_texto_tal_cual(self):
        r = self.ver()
        self.assertEqual(r.status_code, 200)
        self.assertIn("Clínica Demo S.A.C.", r.text)
        self.assertIn("26 de octubre de 2026", r.text)
        self.assertIn("Perú Sistemas Pro E.I.R.L.", r.text)
        self.assertIn("/static/img/logo_pagook.png", r.text)
        self.assertIn("Estimado señor, le presentamos <strong>nuestra propuesta</strong>.", r.text)
        self.assertIn("<table>", r.text)
        self.assertIn("<strong>Para:</strong> Contador<br />", r.text)   # membrete: cada línea en su línea
        self.assertNotIn("cliente: Clínica", r.text)            # el YAML no se muestra como texto
        self.assertIn("967 317 946", r.text)
        self.assertIn("info@perusistemas.pro", r.text)

    def test_noindex_en_meta_y_cabecera(self):
        r = self.ver()
        self.assertIn('<meta name="robots" content="noindex, nofollow">', r.text)
        self.assertEqual(r.headers["x-robots-tag"], "noindex, nofollow")

    def test_open_graph_y_botones(self):
        t = self.ver().text
        self.assertIn('property="og:title" content="Propuesta PagoOK para Clínica Demo"', t)
        self.assertIn(f'property="og:url" content="https://pagook.pro/propuestas/{CODIGO}"', t)
        self.assertIn('property="og:image" content="https://pagook.pro/static/img/pagook-icon-512.png"', t)
        self.assertIn('property="og:description" content="Propuesta de Perú Sistemas Pro para Clínica Demo S.A.C."', t)
        self.assertIn("window.print()", t)
        self.assertIn("https://wa.me/?text=Propuesta%20PagoOK%20para%20Cl%C3%ADnica%20Demo%0A"
                      f"https%3A//pagook.pro/propuestas/{CODIGO}", t)

    def test_codigo_inexistente_404(self):
        self.assertEqual(self.ver("clinica-demo-zzzzzz").status_code, 404)
        self.assertEqual(self.ver("clinica-demo").status_code, 404)
        self.assertEqual(self.cliente().get("/propuestas").status_code, 404)   # no la captura /{slug}
        self.assertEqual(self.db.query(PropuestaVisita).count(), 0)

    def test_archivo_faltante_404_con_log(self):
        (self.dir / "clinica-demo.md").unlink()
        with self.assertLogs("pagook", "ERROR") as logs:
            self.assertEqual(self.ver().status_code, 404)
        self.assertIn("clinica-demo.md", logs.output[0])

    def test_indice_no_sale_de_la_carpeta(self):
        (self.dir / "indice.yml").write_text("fuera-abc123: ../../app/config.py\n", encoding="utf-8")
        with self.assertLogs("pagook", "ERROR"):
            self.assertEqual(self.ver("fuera-abc123").status_code, 404)

    def test_tema_oscuro_sin_blanco_puro(self):
        for ruta in (f"/propuestas/{CODIGO}", "/static/propuestas/propuesta.css"):
            texto = self.cliente().get(ruta).text.lower()
            self.assertNotIn("#ffffff", texto, ruta)
            self.assertNotIn("#fff;", texto, ruta)
        css = self.cliente().get("/static/propuestas/propuesta.css").text
        self.assertIn("@media print", css)
        self.assertIn("page-break-inside: avoid", css)
        self.assertIn("overflow-x: auto", css)                   # tablas desplazables en celular


class TestVisitas(BasePropuestas):
    def test_registra_visita_sin_ip_en_claro(self):
        self.ver()
        v = self.db.query(PropuestaVisita).one()
        self.assertEqual((v.codigo, v.user_agent, v.vista_previa), (CODIGO, "Android · Chrome", False))
        self.assertEqual(len(v.ip_hash), 64)
        self.assertNotIn("testclient", v.ip_hash)
        self.assertIsNotNone(v.visto_en)

    def test_vista_previa_de_whatsapp_se_marca(self):
        self.ver(ua=UA_WHATSAPP)
        v = self.db.query(PropuestaVisita).one()
        self.assertEqual((v.user_agent, v.vista_previa), ("WhatsApp (vista previa)", True))

    def test_falla_de_bd_no_rompe_la_pagina(self):
        with mock.patch.object(router_propuestas, "PropuestaVisita", side_effect=RuntimeError("bd caída")), \
                self.assertLogs("pagook", "ERROR"):
            self.assertEqual(self.ver().status_code, 200)

    def test_resumen_de_user_agent(self):
        casos = {
            UA_ANDROID: ("Android · Chrome", False),
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
            "Version/17.5 Mobile/15E148 Safari/604.1": ("iPhone · Safari", False),
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 "
            "Safari/537.36 Edg/129.0": ("Windows · Edge", False),
            "facebookexternalhit/1.1": ("Facebook (vista previa)", True),
            "Googlebot/2.1": ("Robot", True),
            None: ("Otro", False),
        }
        for ua, esperado in casos.items():
            self.assertEqual(resumir_user_agent(ua), esperado, ua)


class TestAdminPropuestas(BasePropuestas):
    def test_exige_token(self):
        with mock.patch.dict(os.environ, {"ADMIN_TOKEN": "adm"}):
            self.assertEqual(self.cliente().get("/admin/propuestas").status_code, 401)
            self.assertEqual(self.cliente().get("/admin/propuestas?token=otro").status_code, 401)
        with mock.patch.dict(os.environ, {"ADMIN_TOKEN": ""}):
            self.assertEqual(self.cliente().get("/admin/propuestas?token=").status_code, 401)

    def test_cuenta_aperturas_y_ultima(self):
        self.ver()
        self.ver()
        self.ver(ua=UA_WHATSAPP)
        with mock.patch.dict(os.environ, {"ADMIN_TOKEN": "adm"}):
            r = self.cliente().get("/admin/propuestas?token=adm")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Propuesta PagoOK para Clínica Demo", r.text)
        self.assertIn(f"https://pagook.pro/propuestas/{CODIGO}", r.text)
        fila = r.text.split("Propuesta PagoOK para Clínica Demo", 1)[1].split("</tr>", 1)[0]
        celdas = [c.split("</td>")[0].strip() for c in fila.split("<td>")[1:]]
        self.assertEqual(celdas[:3], ["2", "1", "1"])          # aperturas, conexiones distintas, vistas previas
        self.assertRegex(celdas[3], r"\d{2}/\d{2}/\d{4} \d{2}:\d{2}")
        self.assertIn("WhatsApp (vista previa)", r.text)


if __name__ == "__main__":
    unittest.main()
