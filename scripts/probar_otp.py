"""Diagnóstico del envío del OTP por WhatsApp (Meta Cloud API).

Manda la plantilla OTP con un código DE PRUEBA al celular indicado, usando la
MISMA función que el login (app.services.otp.WhatsAppEnviador), e imprime si Meta
aceptó el envío o el detalle del error (code / error_subcode / message /
fbtrace_id). Nunca imprime ni registra el token.

Pensado para correr en Railway, donde están las variables WA_*:

    railway run python scripts/probar_otp.py --celular +51987654321

El código enviado es ficticio (no sirve para iniciar sesión); el objetivo es solo
ver si el canal funciona. Tras ver el error, usa la guía que imprime el script.
No toca la base de datos.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import otp  # noqa: E402

# Guía por código de error de Meta (los más comunes en plantillas AUTHENTICATION).
GUIA_ERRORES = {
    190: (
        "TOKEN (190): el WA_ACCESS_TOKEN está vencido o es inválido.\n"
        "   Arreglo: genera un token PERMANENTE de usuario del sistema con el\n"
        "   permiso whatsapp_business_messaging (Business Manager > Usuarios del\n"
        "   sistema > Generar token) y cárgalo en Railway como WA_ACCESS_TOKEN.\n"
        "   Los tokens temporales caducan en ~24 h o ~60 días; por eso 'dejó de\n"
        "   llegar' sin tocar nada."
    ),
    132: (
        "PLANTILLA (132): la plantilla no coincide (nombre, idioma o parámetros).\n"
        "   Revisa en WhatsApp Manager que WA_OTP_PLANTILLA y WA_OTP_IDIOMA sean\n"
        "   EXACTAMENTE los de la plantilla aprobada, y que el nº de variables {{ }}\n"
        "   del body (y el botón 'Copiar código') coincidan con lo que manda la app."
    ),
    133: (
        "PLANTILLA (133): la plantilla no está disponible (p. ej. no aprobada o\n"
        "   pausada para ese idioma). Revisa su estado en WhatsApp Manager."
    ),
    135: (
        "PLANTILLA (135): error de parámetros de la plantilla. Revisa el número y\n"
        "   tipo de variables que espera la plantilla AUTHENTICATION en Meta."
    ),
}


def _guia(code, subcode) -> str:
    if code in GUIA_ERRORES:
        return GUIA_ERRORES[code]
    if code == 131:
        return ("LÍMITE/NÚMERO (131): problema con el número emisor o un límite de\n"
                "   la cuenta de WhatsApp Business. Revisa estado y saldo de la WABA.")
    return (f"Código de Meta {code} (subcode {subcode}). Búscalo en la referencia de\n"
            "   errores de la Cloud API: developers.facebook.com/docs/whatsapp/cloud-api/support/error-codes")


def main() -> int:
    ap = argparse.ArgumentParser(description="Prueba el envío del OTP por WhatsApp.")
    ap.add_argument("--celular", required=True,
                    help="Destino en formato internacional, p. ej. +51987654321")
    ap.add_argument("--codigo", default="123456",
                    help="Código de prueba a enviar (ficticio, default 123456)")
    args = ap.parse_args()

    enviador = otp.WhatsAppEnviador()
    print("Configuración WhatsApp:")
    print(f"  plantilla:       {enviador.plantilla or '(vacía)'}")
    print(f"  idioma:          {enviador.idioma}")
    print(f"  con botón:       {enviador.con_boton}")
    print(f"  phone_number_id: {'sí' if enviador.phone_number_id else 'NO'}")
    print(f"  access_token:    {'presente' if enviador.token else 'AUSENTE'}")
    print()

    if not enviador.configurado():
        print("✗ Falta configuración (token, phone_number_id o plantilla). "
              "Revisa WA_ACCESS_TOKEN, WA_PHONE_NUMBER_ID y WA_OTP_PLANTILLA en Railway.")
        return 2

    destino = (args.celular or "").strip()
    print(f"Enviando plantilla '{enviador.plantilla}' a {destino} (código ficticio)...")
    res = enviador.enviar_detalle(destino, args.codigo)

    if res.get("ok"):
        print(f"\n✓ Meta aceptó el envío (HTTP {res.get('status')}). "
              "Revisa que el WhatsApp del destino reciba el mensaje.")
        return 0

    err = res.get("error") or {}
    print(f"\n✗ Meta rechazó el envío (HTTP {res.get('status')}).")
    print(f"  code:         {err.get('code')}")
    print(f"  error_subcode:{err.get('error_subcode')}")
    print(f"  message:      {err.get('message')}")
    print(f"  fbtrace_id:   {err.get('fbtrace_id')}")
    print()
    print("Qué hacer:")
    print("   " + _guia(err.get("code"), err.get("error_subcode")))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
