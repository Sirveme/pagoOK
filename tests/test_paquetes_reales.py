"""SQL de paquetes reales de bancos peruanos (sin tocar la base)."""
import re
import unittest
from pathlib import Path

SQL = Path(__file__).resolve().parent.parent / "sql" / "paquetes_reales_pe_2026-11.sql"

ESPERADOS = {
    ("bcp", "com.bcp.bank.bcp"),
    ("bbva", "com.bbva.nxt_peru"),
    ("scotiabank", "pe.com.scotiabank.blpm.android.client"),
    ("banco_nacion", "pe.com.bn.app.bancodelanacion"),
    ("banbif", "pe.com.banbif.pnappmobile"),
    ("banbif", "pe.com.banbif.pjappmobile"),
    ("pichincha", "pe.pichincha.bm"),
}
# Mismo formato que valida la app (CerebroBancos.esPaqueteValido).
FORMATO_PAQUETE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$")


class TestSqlPaquetesReales(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        texto = SQL.read_text(encoding="utf-8")
        cls.sql = "\n".join(l for l in texto.splitlines() if not l.startswith("--"))
        cls.updates = re.findall(
            r"UPDATE banco_config\s+SET paquetes_android = COALESCE\(paquetes_android, '\[\]'::jsonb\) "
            r"\|\| '\[\"([\w.]+)\"\]'::jsonb\s+WHERE pais = 'PE' AND clave = '(\w+)'\s+"
            r"AND NOT COALESCE\(paquetes_android, '\[\]'::jsonb\) \? '([\w.]+)';",
            cls.sql,
        )

    def test_agrega_cada_paquete_a_su_banco_de_forma_idempotente(self):
        self.assertEqual({(clave, p) for p, clave, _ in self.updates}, ESPERADOS)
        self.assertEqual(len(self.updates), len(ESPERADOS))
        for paquete, _, condicion in self.updates:
            self.assertEqual(paquete, condicion)
            self.assertRegex(paquete, FORMATO_PAQUETE)

    def test_transaccion_propia_y_verificacion_al_final(self):
        self.assertEqual(self.sql.count("BEGIN;"), 1)
        self.assertEqual(self.sql.count("COMMIT;"), 1)
        self.assertLess(self.sql.index("BEGIN;"), self.sql.index("UPDATE"))
        self.assertLess(self.sql.rindex("UPDATE"), self.sql.index("COMMIT;"))
        self.assertGreater(self.sql.rindex("SELECT"), self.sql.index("COMMIT;"))

    def test_no_quita_paquetes_ni_incluye_el_de_tunki_sin_confirmar(self):
        self.assertNotRegex(self.sql, r"(?i)\bdelete\b|\bdrop\b|\btruncate\b| - '")
        self.assertNotIn("mpay.customer", self.sql)


if __name__ == "__main__":
    unittest.main()
