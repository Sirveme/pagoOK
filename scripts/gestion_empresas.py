"""Gestión de empresas multi-empresa (sin UI). Lo usa Duilio en los pilotos.

Por defecto NO escribe nada: muestra lo que haría (simulación).
Con --aplicar guarda en DATABASE_URL. No emite webhooks.

Ejemplos:
  python scripts/gestion_empresas.py listar
  python scripts/gestion_empresas.py crear --pais PE --id-fiscal 20601234567 \
      --dueno-nombre "Ana Torres" --dueno-celular +51987654321
  python scripts/gestion_empresas.py crear --pais CO --id-fiscal 900123456 \
      --razon-social "CLINICA EJEMPLO SAS" --dueno-nombre "..." --dueno-celular +57... --aplicar
  python scripts/gestion_empresas.py activar --empresa clinica-ejemplo --aplicar
  python scripts/gestion_empresas.py asignar-dueno --empresa duilio       --dueno-nombre "Duilio Restuccia" --dueno-celular +51967317946 --aplicar
  python scripts/gestion_empresas.py suspender --empresa clinica-ejemplo --aplicar
  python scripts/gestion_empresas.py eliminar --empresa clinica-ejemplo --solicitud 3 --aplicar

ELIMINAR es IRREVERSIBLE: borra todos los datos de la empresa (misma función
que el autoservicio de /panel/eliminar). Nunca sobre perusistemas ni SLUGS_DEMO.
Sin --aplicar muestra cuántas filas borraría y no toca nada.

Perú: el RUC se valida con la API de RUC del ecosistema (apis.net.pe v2,
APIS_NET_PE_TOKEN) y de ahí sale la razón social. Si la API no responde,
--razon-social es obligatorio. Otros países: --razon-social obligatorio.
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

from scripts._conexion import abrir_sesion, explicar_error  # noqa: E402
from app.admin.models import Empresa, Pais  # noqa: E402
from app.admin.router import slugify  # noqa: E402
from app.api.models_multiempresa import Membresia, Usuario  # noqa: E402
from app.api.models_webhook import Dispositivo, PagoDetectado  # noqa: E402
from app.services import cuenta_empresa  # noqa: E402
from app.services.ruc import consultar_ruc, ruc_valido_formato  # noqa: E402
from app.services.secretos import normalizar_celular  # noqa: E402

SLUGS_RESERVADOS = {
    "admin", "api", "static", "panel", "unirse", "demo", "docs", "health", "login", "logout",
    "testers", "videos", "qr", "caja", "flujo-caja", "recibir", "privacidad", "eliminar-cuenta",
}
TIPO_ID_POR_PAIS = {"PE": "RUC", "MX": "RFC", "CO": "NIT", "CL": "RUT", "EC": "RUC",
                    "BO": "NIT", "AR": "CUIT", "VE": "RIF", "ES": "NIF"}


class ErrorGestion(Exception):
    pass


def _slug_libre(db, base: str) -> str:
    base = (slugify(base) or "empresa")[:50]
    slug, i = base, 2
    while slug in SLUGS_RESERVADOS or db.query(Empresa).filter(Empresa.slug == slug).first():
        slug, i = f"{base}{i}", i + 1
    return slug


def crear_empresa(db, *, pais: str, id_fiscal: str, razon_social: str | None, dueno_nombre: str,
                  dueno_celular: str, slug: str | None = None, consultar=consultar_ruc) -> list[str]:
    """Crea empresa en prueba + usuario dueño + membresía (sin commit)."""
    log = []
    pais = pais.upper().strip()
    id_fiscal = id_fiscal.strip()
    celular = normalizar_celular(dueno_celular)
    if celular is None:
        raise ErrorGestion(f"Celular inválido: {dueno_celular!r} (usa E.164, ej. +51987654321)")
    if not dueno_nombre.strip():
        raise ErrorGestion("Falta --dueno-nombre")
    pais_row = db.get(Pais, pais)
    if pais_row is None:
        raise ErrorGestion(f"El país {pais} no está en la tabla pais")
    if db.query(Empresa).filter(Empresa.pais_codigo == pais, Empresa.id_fiscal == id_fiscal).first():
        raise ErrorGestion(f"Ya existe una empresa con {pais} {id_fiscal}")

    if pais == "PE":
        if not ruc_valido_formato(id_fiscal):
            raise ErrorGestion(f"RUC con formato inválido: {id_fiscal}")
        datos = consultar(id_fiscal)
        if datos:
            log.append(f"RUC validado: {datos['razon_social']} · {datos['estado']} / {datos['condicion']}")
            if razon_social and razon_social.strip().upper() != datos["razon_social"].upper():
                log.append(f"  (se usa la razón social de SUNAT, no {razon_social!r})")
            razon_social = datos["razon_social"]
        elif not razon_social:
            raise ErrorGestion("La API de RUC no respondió: indica --razon-social")
        else:
            log.append("AVISO: la API de RUC no respondió; se usa --razon-social sin validar")
    elif not razon_social:
        raise ErrorGestion(f"Para {pais}, --razon-social es obligatorio")

    slug_final = slug.strip().lower() if slug else _slug_libre(db, razon_social)
    if slug_final in SLUGS_RESERVADOS or db.query(Empresa).filter(Empresa.slug == slug_final).first():
        raise ErrorGestion(f"El slug {slug_final!r} está reservado o ya existe")

    empresa = Empresa(
        pais_codigo=pais, id_fiscal=id_fiscal, id_fiscal_tipo=pais_row.nombre_id_fiscal or TIPO_ID_POR_PAIS.get(pais),
        razon_social=razon_social.strip().upper(), nombre_comercial=razon_social.strip().upper(),
        slug=slug_final, activa=True,
        visible_en_testers=False,      # los pilotos no salen en la grilla pública /testers
        acepta_autoregistro=False,     # sus celulares entran solo con código de vinculación
    )
    cuenta_empresa.iniciar_prueba(empresa)
    db.add(empresa)
    db.flush()

    log += _hacer_dueno(db, empresa, celular, dueno_nombre)
    log.append(f"Empresa: {empresa.razon_social} · {empresa.id_fiscal_tipo} {id_fiscal} · slug={slug_final} · "
               f"prueba hasta {empresa.prueba_fin:%Y-%m-%d}")
    log.append(f"Panel: https://pagook.pro/panel/login (ingresa con {celular})")
    return log


def _hacer_dueno(db, empresa: Empresa, celular: str, nombre: str, escribir: bool = True) -> list[str]:
    """Usuario (por celular) + membresía de dueño en la empresa (sin commit).
    Con escribir=False solo consulta y dice qué haría (sirve en solo lectura)."""
    log = []
    usuario = db.query(Usuario).filter(Usuario.celular == celular).first()
    if usuario is None:
        log.append(f"Usuario dueño nuevo: {nombre.strip()[:150]} ({celular})")
        if escribir:
            usuario = Usuario(nombre=nombre.strip()[:150], celular=celular, activo=True)
            db.add(usuario)
            db.flush()
    else:
        log.append(f"Usuario dueño ya existía: {usuario.nombre} ({celular})"
                   + ("" if usuario.activo else " · estaba inactivo: se reactiva"))
        if escribir:
            usuario.activo = True
    m = None if usuario is None or usuario.id is None else (
        db.query(Membresia).filter(Membresia.usuario_id == usuario.id, Membresia.empresa_id == empresa.id).first())
    if m is None:
        log.append(f"Membresía nueva: dueño de {empresa.slug}")
        if escribir:
            db.add(Membresia(usuario_id=usuario.id, empresa_id=empresa.id, rol="dueno", activo=True))
    elif m.activo and m.rol == "dueno":
        raise ErrorGestion(f"{celular} ya es dueño de {empresa.slug}")
    else:
        log.append(f"Membresía existente ({m.rol}, {'activa' if m.activo else 'revocada'}) → dueño activo")
        if escribir:
            m.rol, m.activo, m.revocado_en = "dueno", True, None
    if escribir:
        db.flush()
    return log


def asignar_dueno(db, slug: str, dueno_nombre: str, dueno_celular: str, escribir: bool = True) -> list[str]:
    """Hace dueño de una empresa existente a un celular (misma lógica que crear)."""
    celular = normalizar_celular(dueno_celular)
    if celular is None:
        raise ErrorGestion(f"Celular inválido: {dueno_celular!r} (usa E.164, ej. +51987654321)")
    if not dueno_nombre.strip():
        raise ErrorGestion("Falta --dueno-nombre")
    e = _empresa(db, slug)
    log = [f"Empresa: {e.razon_social} · slug={e.slug} · estado={e.estado_cuenta}"]
    log += _hacer_dueno(db, e, celular, dueno_nombre, escribir)
    log.append(f"Panel: https://pagook.pro/panel/login (ingresa con {celular})")
    return log


def _empresa(db, slug: str) -> Empresa:
    e = db.query(Empresa).filter(Empresa.slug == slug.strip().lower()).first()
    if e is None:
        raise ErrorGestion(f"No existe la empresa {slug!r}")
    return e


def activar_empresa(db, slug: str) -> list[str]:
    e = _empresa(db, slug)
    cuenta_empresa.activar(e)
    return [f"{e.slug}: activa (sin fecha de fin)"]


def suspender_empresa(db, slug: str) -> list[str]:
    e = _empresa(db, slug)
    try:
        cuenta_empresa.suspender(e)
    except cuenta_empresa.CuentaError as exc:
        raise ErrorGestion(str(exc))
    return [f"{e.slug}: suspendida (la captura sigue guardando pagos)"]


def eliminar_empresa_cmd(db, slug: str, solicitud_id: int | None = None) -> list[str]:
    """Elimina la empresa (sin commit: main decide según --aplicar)."""
    from app.api.models_privacidad import SolicitudEliminacion
    from app.services import eliminacion

    e = _empresa(db, slug)
    solicitud = None
    if solicitud_id is not None:
        solicitud = db.get(SolicitudEliminacion, solicitud_id)
        if solicitud is None:
            raise ErrorGestion(f"No existe la solicitud {solicitud_id}")
        if solicitud.estado != "pendiente":
            raise ErrorGestion(f"La solicitud {solicitud_id} ya está {solicitud.estado}")
        if (solicitud.pais, solicitud.id_fiscal) != (e.pais_codigo, e.id_fiscal):
            raise ErrorGestion(f"La solicitud {solicitud_id} es de {solicitud.pais} {solicitud.id_fiscal}, "
                               f"no de {e.slug} ({e.pais_codigo} {e.id_fiscal})")
    razon = e.razon_social
    try:
        conteos = eliminacion.eliminar_empresa(db, e, "solicitud", solicitud=solicitud)
    except eliminacion.EliminacionProhibida as exc:
        raise ErrorGestion(str(exc))
    lineas = [f"Eliminación de {slug} ({razon}):"]
    lineas += [f"  {tabla:<28} {n:>6}" for tabla, n in conteos.items() if n]
    lineas.append(f"  {'TOTAL':<28} {sum(conteos.values()):>6} filas")
    return lineas


def listar_empresas(db) -> list[str]:
    filas = [f"{'slug':<22} {'estado':<11} {'días':>5} {'disp.':>6} {'pagos':>7}  razón social"]
    for e in db.query(Empresa).order_by(Empresa.id).all():
        estado = cuenta_empresa.refrescar_estado(None, e)   # sin escribir
        dias = cuenta_empresa.dias_restantes(e)
        n_disp = db.query(Dispositivo).filter(Dispositivo.empresa_id == e.id, Dispositivo.activo == True).count()  # noqa: E712
        n_pagos = db.query(PagoDetectado).filter(PagoDetectado.empresa_id == e.id).count()
        filas.append(f"{e.slug:<22} {estado:<11} {('' if dias is None else dias):>5} {n_disp:>6} {n_pagos:>7}  "
                     f"{e.razon_social}")
    return filas


def main() -> None:
    p = argparse.ArgumentParser(description="Gestión de empresas de PagoOK")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("crear")
    c.add_argument("--pais", required=True)
    c.add_argument("--id-fiscal", required=True)
    c.add_argument("--razon-social")
    c.add_argument("--dueno-nombre", required=True)
    c.add_argument("--dueno-celular", required=True)
    c.add_argument("--slug")
    for nombre in ("activar", "suspender", "eliminar"):
        s = sub.add_parser(nombre)
        s.add_argument("--empresa", required=True, help="slug de la empresa")
        if nombre == "eliminar":
            s.add_argument("--solicitud", type=int, help="id de solicitud_eliminacion que se atiende")
    d = sub.add_parser("asignar-dueno")
    d.add_argument("--empresa", required=True, help="slug de la empresa")
    d.add_argument("--dueno-nombre", required=True)
    d.add_argument("--dueno-celular", required=True)
    sub.add_parser("listar")
    for s in sub.choices.values():
        s.add_argument("--aplicar", action="store_true", help="guardar (sin esto, solo simula)")
    args = p.parse_args()

    # "Conectando…" + 15 s de límite. `listar` no escribe: va en solo lectura.
    # Las simulaciones de crear/activar/suspender/eliminar hacen los cambios en
    # una transacción y luego ROLLBACK (por eso no pueden ser de solo lectura).
    # La simulación de asignar-dueno solo consulta: también va en solo lectura.
    db = abrir_sesion(solo_lectura=args.cmd == "listar" or (args.cmd == "asignar-dueno" and not args.aplicar))
    try:
        if args.cmd == "listar":
            print("Leyendo empresas…", flush=True)
            print("\n".join(listar_empresas(db)))
            return
        if args.cmd == "crear":
            if args.pais.strip().upper() == "PE":
                print("Consultando el RUC en SUNAT (apis.net.pe)…", flush=True)
            log = crear_empresa(db, pais=args.pais, id_fiscal=args.id_fiscal, razon_social=args.razon_social,
                                dueno_nombre=args.dueno_nombre, dueno_celular=args.dueno_celular, slug=args.slug)
        elif args.cmd == "asignar-dueno":
            log = asignar_dueno(db, args.empresa, args.dueno_nombre, args.dueno_celular, escribir=args.aplicar)
        elif args.cmd == "activar":
            log = activar_empresa(db, args.empresa)
        elif args.cmd == "eliminar":
            print(f"Calculando qué se borraría de {args.empresa}…", flush=True)
            log = eliminar_empresa_cmd(db, args.empresa, args.solicitud)
        else:
            log = suspender_empresa(db, args.empresa)
        print("\n".join(log), flush=True)
        if args.aplicar:
            print("Guardando…", flush=True)
            db.commit()
            print("\nGuardado.")
        else:
            db.rollback()
            print("\nSimulación: no se guardó nada. Usa --aplicar para guardar.")
    except ErrorGestion as exc:
        db.rollback()
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    except SystemExit:
        raise
    except Exception as exc:
        db.rollback()
        print(f"ERROR: {explicar_error(exc)}\nROLLBACK: no se guardó nada.", file=sys.stderr)
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
