"""Prueba el webhook simulando notificaciones de Yape/Plin/banco.

Uso:
  python scripts/probar_webhook.py <URL_BASE> <TOKEN>

Ejemplos:
  python scripts/probar_webhook.py http://localhost:4000 tok_xxx
  python scripts/probar_webhook.py https://pagook.pro tok_xxx
"""
import sys
import json
import time
from urllib import request as urlreq, error as urlerr


CASOS = [
    {
        "nombre": "Yape de Juan",
        "package": "com.bcp.innovacxion.yapeapp",
        "titulo": "Yape!",
        "texto": "Juan Carlos Perez Lopez te yapeó S/ 15.00 con código 12345678",
    },
    {
        "nombre": "Plin de María",
        "package": "com.bbva.plin",
        "titulo": "Plin",
        "texto": "Recibiste S/ 30.00 de MARIA GONZALES código: ABC12345",
    },
    {
        "nombre": "Transferencia BCP",
        "package": "pe.com.bcp.bcppersonas",
        "titulo": "BCP Notifica",
        "texto": "Se realizó un abono en tu cuenta soles ***1234 por S/ 200.00",
    },
    {
        "nombre": "WhatsApp (ignorada)",
        "package": "com.whatsapp",
        "titulo": "WhatsApp",
        "texto": "Pedro: Hola, ¿estás en la oficina?",
    },
]


def enviar(base_url: str, token: str, payload: dict) -> tuple[int, str]:
    url = base_url.rstrip("/") + "/api/v1/notificaciones/inbound"
    data = json.dumps({
        **payload,
        "timestamp_celular": int(time.time() * 1000),
        "sbn_key": f"test|{payload['package']}|{int(time.time() * 1000)}",
    }).encode("utf-8")
    req = urlreq.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "X-Device-Token": token,
        },
    )
    try:
        with urlreq.urlopen(req, timeout=15) as r:
            return r.status, r.read().decode("utf-8")
    except urlerr.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="ignore")
    except Exception as e:
        return -1, str(e)


def consultar(base_url: str, token: str) -> tuple[int, str]:
    url = base_url.rstrip("/") + "/api/v1/pagos/recientes?limit=10"
    req = urlreq.Request(url, headers={"X-Device-Token": token})
    try:
        with urlreq.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode("utf-8")
    except urlerr.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="ignore")
    except Exception as e:
        return -1, str(e)


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    base_url = sys.argv[1]
    token = sys.argv[2]

    print(f"\nProbando webhook en: {base_url}")
    print(f"Token: {token[:20]}...{token[-6:] if len(token) > 26 else ''}\n")

    # 1) Ping
    try:
        with urlreq.urlopen(base_url.rstrip("/") + "/api/v1/ping", timeout=10) as r:
            print(f"[Ping] {r.status} {r.read().decode()}")
    except Exception as e:
        print(f"[Ping] ERROR: {e}")
        sys.exit(1)

    # 2) Enviar casos
    print("\n--- Enviando notificaciones de prueba ---")
    for caso in CASOS:
        codigo, body = enviar(base_url, token, caso)
        flag = "✓" if codigo == 200 else "✗"
        print(f"{flag} [{codigo}] {caso['nombre']}")
        print(f"     resp: {body[:200]}")
        time.sleep(0.3)

    # 3) Consultar
    print("\n--- Últimos pagos detectados ---")
    codigo, body = consultar(base_url, token)
    print(f"[{codigo}]")
    try:
        data = json.loads(body)
        for p in data.get("pagos", []):
            print(f"  - {p['metodo'].upper():<14} S/ {p['monto']:>8.2f}  {p.get('titular_corto', '')[:30]}")
        if not data.get("pagos"):
            print("  (sin pagos detectados)")
    except Exception:
        print(body[:500])

    print("\nListo. Revisa también el admin: /admin/dispositivos")


if __name__ == "__main__":
    main()
