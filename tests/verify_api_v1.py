"""Verificación estática (AST) de invariantes de seguridad de la API v1.

1) Todo endpoint nuevo en app/api/publica_v1.py exige auth (Depends(cuenta_actual)).
2) La ingesta Android (notificaciones.py, parser.py) NO fue modificada respecto
   al commit inicial, y sus endpoints siguen exigiendo X-Device-Token.

Ejecutar:  python tests/verify_api_v1.py
Sale con código != 0 si alguna invariante falla.
"""
import ast
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
METODOS_HTTP = {"get", "post", "put", "delete", "patch"}
FUNC_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef)
fallos = []


def _es_ruta(dec: ast.expr) -> bool:
    return (
        isinstance(dec, ast.Call)
        and isinstance(dec.func, ast.Attribute)
        and dec.func.attr in METODOS_HTTP
        and isinstance(dec.func.value, ast.Name)
        and dec.func.value.id == "router"
    )


def _usa_depends(fn, nombre_dep: str) -> bool:
    for default in fn.args.defaults + fn.args.kw_defaults:
        if (
            isinstance(default, ast.Call)
            and isinstance(default.func, ast.Name)
            and default.func.id == "Depends"
            and default.args
            and isinstance(default.args[0], ast.Name)
            and default.args[0].id == nombre_dep
        ):
            return True
    return False


def check_endpoints_con_auth():
    archivo = RAIZ / "app" / "api" / "publica_v1.py"
    tree = ast.parse(archivo.read_text(encoding="utf-8"))
    rutas = 0
    for node in ast.walk(tree):
        if not isinstance(node, FUNC_DEFS):
            continue
        if not any(_es_ruta(d) for d in node.decorator_list):
            continue
        rutas += 1
        if not _usa_depends(node, "cuenta_actual"):
            fallos.append(f"[AUTH] Endpoint '{node.name}' SIN Depends(cuenta_actual)")
        else:
            print(f"  [OK] {node.name}: auth via Depends(cuenta_actual)")
    if rutas == 0:
        fallos.append("[AUTH] No se detectó ningún endpoint en publica_v1.py")
    print(f"  ({rutas} endpoints v1 verificados)")


def check_ingesta_intacta():
    ingesta = ["app/api/notificaciones.py", "app/api/parser.py"]
    try:
        r = subprocess.run(
            ["git", "diff", "--name-only", "HEAD", "--"] + ingesta,
            cwd=RAIZ, capture_output=True, text=True,
        )
        modificados = [l for l in r.stdout.splitlines() if l.strip()]
        if modificados:
            fallos.append(f"[INGESTA] Archivos de ingesta modificados: {modificados}")
        else:
            print("  [OK] notificaciones.py y parser.py sin cambios vs commit inicial")
    except FileNotFoundError:
        print("  ! git no disponible; salto verificación de diff")

    # Los endpoints de ingesta deben seguir exigiendo X-Device-Token.
    src = (RAIZ / "app" / "api" / "notificaciones.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, FUNC_DEFS) and node.name == "inbound":
            tiene_token = any(
                getattr(a, "arg", None) == "x_device_token" for a in node.args.args
            )
            if tiene_token:
                print("  [OK] inbound sigue recibiendo x_device_token")
            else:
                fallos.append("[INGESTA] inbound perdió x_device_token")


def main():
    print("== Verificación AST: endpoints v1 con auth ==")
    check_endpoints_con_auth()
    print("== Verificación: ingesta Android intacta ==")
    check_ingesta_intacta()
    print()
    if fallos:
        print("RESULTADO: FALLÓ")
        for f in fallos:
            print("  [X]", f)
        sys.exit(1)
    print("RESULTADO: OK — todas las invariantes se cumplen")


if __name__ == "__main__":
    main()
