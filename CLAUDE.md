# PagoOK — guía para Claude Code

## Idioma y trato
- Responde en español. **Usa tuteo (tú), nunca voseo (vos).** Ej.: "revisa", "corres", "te dejo" (no "revisá", "corrés", "te dejo vos").
- Español neutro peruano en los textos visibles del producto.

## Despliegue (SQL-primero)
- `C:\pagook` es un clon de GitHub. Hacer **push a `main` despliega en Railway**.
- La base de datos vive en Railway; **no** hay `create_all`/Alembic en producción.
- Los cambios de esquema van en un archivo `sql/` y se corren **a mano en Railway ANTES del push**. El orden siempre es: SQL → confirmar → push → verificar.
- No hagas commit ni push salvo que el usuario lo pida.

## Tests
- La suite usa **unittest**, no pytest (pytest no está en el venv):
  `.\venv\Scripts\python.exe -m unittest discover -s tests -t .`
- SQLite en memoria + FastAPI TestClient. Las cookies `Secure` solo se guardan en TestClient si `PAGOOK_ENTORNO=desarrollo` (ya se setea en los tests que usan sesión).

## Lectura de producción (solo lectura)
- `scripts._conexion.abrir_sesion(solo_lectura=True)` con `PYTHONIOENCODING=utf-8`
  (la consola cp1252 falla con los emojis de los avisos).

## Credenciales de administración global
- `ADMIN_TOKEN`: credencial de superadmin (paneles que cruzan empresas).
- Los paneles globales (`/admin/dispositivos/estado`, `/admin/empresas/nuevas`, `/admin/bancos`, `/admin/eliminaciones`, `/admin/propuestas`) usan **sesión de superadmin** (cookie firmada HMAC con `ADMIN_TOKEN`), vía `POST /admin/acceso`. El token **no** viaja en la URL. Rotar `ADMIN_TOKEN` invalida la credencial y todas las cookies.
- Los endpoints JSON `/api/v1/admin/...` aceptan la cookie de sesión **o** el header `X-Admin-Token` (scripts).

## Aislamiento multi-empresa
- `empresa_id` SIEMPRE sale de la credencial (X-Device-Token, X-API-Key o cookie de sesión), nunca del cuerpo del cliente.
