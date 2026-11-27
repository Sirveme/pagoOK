"""Calcula el signature_hash de 11 caracteres que pide Meta en supported_apps
(plantilla zero-tap / one-tap, zWeb-16). Funciona sin conexión.

Es el mismo cálculo que AppSignatureHelper de Android (SMS Retriever):
    base64( sha256("<paquete> <hex del certificado DER>")[:9] )[:11]

Uso:
    python scripts/hash_firma_app.py --paquete pro.pagook.app --cert deployment_cert.der
    python scripts/hash_firma_app.py --paquete pro.pagook.app --cert debug.pem

  - Play (clave de firma de apps, NO la de subida): Play Console > Prueba y
    publicación > Integridad de la app > Firma de apps > "Certificado de la clave
    de firma de apps" > Descargar certificado (deployment_cert.der).
  - Debug: keytool -exportcert -alias androiddebugkey
      -keystore %USERPROFILE%\\.android\\debug.keystore -storepass android > debug.der

Acepta .der o .pem. Solo lee el certificado público; no usa claves privadas.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import re
from pathlib import Path


def leer_certificado(ruta: Path) -> bytes:
    datos = ruta.read_bytes()
    m = re.search(rb"-----BEGIN CERTIFICATE-----(.+?)-----END CERTIFICATE-----", datos, re.S)
    if m:
        return base64.b64decode(b"".join(m.group(1).split()))
    return datos


def signature_hash(paquete: str, cert_der: bytes) -> str:
    info = f"{paquete} {cert_der.hex()}".encode("utf-8")
    return base64.b64encode(hashlib.sha256(info).digest()[:9]).decode("ascii")[:11]


def main() -> int:
    ap = argparse.ArgumentParser(description="signature_hash de 11 caracteres para Meta.")
    ap.add_argument("--paquete", required=True, help="applicationId, p. ej. pro.pagook.app")
    ap.add_argument("--cert", required=True, type=Path, help="Certificado .der o .pem")
    args = ap.parse_args()
    print(signature_hash(args.paquete.strip(), leer_certificado(args.cert)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
