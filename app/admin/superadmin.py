"""Sesión de superadministrador para los paneles GLOBALES.

Antes, /admin/dispositivos/estado y /admin/empresas/nuevas recibían el
ADMIN_TOKEN como `?token=` en la URL: eso queda escrito en los logs del
servidor, en el historial del navegador y en la cabecera Referer. Cualquiera
con acceso a los logs se quedaba con la llave del reino.

Ahora el token viaja UNA sola vez por POST (/admin/acceso) y la sesión se
mantiene con una cookie httponly FIRMADA (HMAC-SHA256 con el propio ADMIN_TOKEN
como clave). Ventajas:
  - El token nunca aparece en una URL ni en los logs.
  - La cookie no guarda el token, solo una firma + expiración, así que un
    volcado de cookies no revela la credencial.
  - Rotar ADMIN_TOKEN invalida de golpe la credencial Y todas las cookies ya
    emitidas (cambia la clave de firma).

Es una credencial global de operaciones (ve todas las empresas), aparte de la
sesión por-empresa del panel (app/admin/deps.py y app/api/deps_empresa.py).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time

from fastapi import Request, Response

COOKIE_SUPERADMIN = "pagook_superadmin"
DURACION_SEG = 12 * 3600  # 12 h


def token_admin_valido(token: str | None) -> bool:
    """Compara el token recibido con ADMIN_TOKEN en tiempo constante."""
    admin_token = os.getenv("ADMIN_TOKEN", "").strip()
    return bool(admin_token) and secrets.compare_digest(
        (token or "").encode("utf-8"), admin_token.encode("utf-8")
    )


def _clave() -> bytes:
    return os.getenv("ADMIN_TOKEN", "").strip().encode("utf-8")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(texto: str) -> bytes:
    return base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))


def _firma(expira_raw: bytes) -> str:
    return _b64(hmac.new(_clave(), expira_raw, hashlib.sha256).digest())


def _crear_valor() -> str:
    """Valor de la cookie: '<exp_b64>.<firma_b64>' con exp = epoch de caducidad."""
    expira_raw = str(int(time.time()) + DURACION_SEG).encode("ascii")
    return f"{_b64(expira_raw)}.{_firma(expira_raw)}"


def _valor_valido(valor: str | None) -> bool:
    if not valor or "." not in valor or not _clave():
        return False
    exp_b64, firma = valor.split(".", 1)
    try:
        expira_raw = _unb64(exp_b64)
    except Exception:
        return False
    if not hmac.compare_digest(firma, _firma(expira_raw)):
        return False
    try:
        return int(expira_raw) >= int(time.time())
    except ValueError:
        return False


def sesion_valida(request: Request) -> bool:
    """True si la petición trae una cookie de superadmin firmada y vigente."""
    return _valor_valido(request.cookies.get(COOKIE_SUPERADMIN))


def autorizado(request: Request, x_admin_token: str | None) -> bool:
    """Admin global por header X-Admin-Token (scripts) O por la cookie de
    sesión de superadmin (los paneles web, tras entrar por POST /admin/acceso)."""
    return token_admin_valido(x_admin_token) or sesion_valida(request)


def _cookie_segura() -> bool:
    return os.getenv("PAGOOK_ENTORNO", "produccion").strip().lower() != "desarrollo"


def poner_cookie(response: Response) -> None:
    response.set_cookie(
        COOKIE_SUPERADMIN, _crear_valor(), max_age=DURACION_SEG,
        httponly=True, secure=_cookie_segura(), samesite="lax", path="/",
    )


def borrar_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_SUPERADMIN, path="/")


def destino_seguro(destino: str | None, por_defecto: str = "/admin/dispositivos/estado") -> str:
    """Evita el open-redirect: solo rutas locales que empiezan en /admin/."""
    if destino and destino.startswith("/admin/") and "//" not in destino[1:] and "\\" not in destino:
        return destino
    return por_defecto


def html_login(destino: str, titulo: str = "Panel de administración",
               subtitulo: str = "Ingresa el token de administrador para continuar.") -> str:
    """Formulario de acceso que envía el token por POST a /admin/acceso."""
    destino = destino_seguro(destino)
    return f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{titulo} · pagoOK</title>
<style>
  body {{ font-family:'Inter',system-ui,sans-serif; background:#faf6ef; color:#1c1f26; display:flex; align-items:center; justify-content:center; min-height:100vh; margin:0; }}
  .caja {{ background:#fff; border-radius:16px; padding:28px; box-shadow:0 2px 14px rgba(28,31,38,.1); max-width:360px; width:90%; }}
  h1 {{ font-size:18px; color:#0f766e; margin:0 0 6px; }}
  p {{ font-size:13px; color:#4b5563; margin:0 0 16px; }}
  input {{ width:100%; box-sizing:border-box; border:1px solid #ddd; border-radius:8px; padding:10px 12px; font-size:14px; }}
  button {{ width:100%; margin-top:12px; background:#0f766e; color:#fff; border:none; border-radius:8px; padding:11px; font-weight:700; cursor:pointer; }}
</style></head>
<body><div class="caja">
  <h1>{titulo}</h1>
  <p>{subtitulo}</p>
  <form method="post" action="/admin/acceso">
    <input type="hidden" name="destino" value="{destino}">
    <input type="password" name="token" placeholder="Token de administrador" autofocus autocomplete="current-password">
    <button type="submit">Entrar</button>
  </form>
</div></body></html>"""
