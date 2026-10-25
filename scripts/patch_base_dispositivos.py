"""Aplica patch al base.html del admin para agregar Dispositivos al sidebar."""
from pathlib import Path
import sys

archivo = Path("templates/admin/base.html")
if not archivo.exists():
    print(f"ERROR: no encuentro {archivo}")
    sys.exit(1)

contenido = archivo.read_text(encoding="utf-8")

marca_buscar = '<a href="/admin/impresion"'
nuevo_link = '''<a href="/admin/dispositivos" class="nav-item {% if '/dispositivos' in request.url.path %}activo{% endif %}">
      <span class="nav-icono">📱</span>
      <span class="nav-texto">Dispositivos (App)</span>
    </a>
    <a href="/admin/impresion"'''

if 'dispositivos' in contenido:
    print("OK: el link Dispositivos ya estaba.")
else:
    if marca_buscar in contenido:
        contenido = contenido.replace(marca_buscar, nuevo_link, 1)
        archivo.write_text(contenido, encoding="utf-8")
        print(f"OK: link Dispositivos agregado a {archivo}")
    else:
        print(f"WARN: no encontré la marca '{marca_buscar}'. Agrega manualmente el link al sidebar.")
