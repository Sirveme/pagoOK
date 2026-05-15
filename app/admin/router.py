"""Router del panel admin pagoOK."""
from fastapi import APIRouter, Request, Depends, Form, Cookie, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import or_
from datetime import datetime
import re

from app.admin.db import get_db
from app.admin.models import (
    Pais, Tributo, RegimenTributario, Persona, Empresa,
    Local, VendedorLocal, MetodoPago, CuentaBancaria, ConfigImpresion
)
from app.admin.auth import (
    autenticar_duenio, crear_sesion, cerrar_sesion, obtener_sesion,
    hash_password, verify_password,
)
from app.admin.deps import current_persona, current_empresa, NoAutenticado, SESION_COOKIE

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="templates")


# =============================================================
# HELPERS
# =============================================================

ICONOS_SECTORIALES = [
    {"codigo": "polleria",   "nombre": "Pollería",          "rubro": "Comida"},
    {"codigo": "restaurante","nombre": "Restaurante",       "rubro": "Comida"},
    {"codigo": "chifa",      "nombre": "Chifa",             "rubro": "Comida"},
    {"codigo": "pizzeria",   "nombre": "Pizzería",          "rubro": "Comida"},
    {"codigo": "parrilla",   "nombre": "Anticuchería/Parrilla", "rubro": "Comida"},
    {"codigo": "cafeteria",  "nombre": "Cafetería/Panadería","rubro": "Comida"},
    {"codigo": "heladeria",  "nombre": "Heladería",         "rubro": "Comida"},
    {"codigo": "jugueria",   "nombre": "Juguería",          "rubro": "Comida"},
    {"codigo": "bodega",     "nombre": "Bodega/Minimarket", "rubro": "Comercio"},
    {"codigo": "ferreteria", "nombre": "Ferretería",        "rubro": "Comercio"},
    {"codigo": "lubricentro","nombre": "Lubricentro",       "rubro": "Comercio"},
    {"codigo": "repuestera", "nombre": "Repuestera",        "rubro": "Comercio"},
    {"codigo": "ropa",       "nombre": "Ropa y zapatería",  "rubro": "Comercio"},
    {"codigo": "bazar",      "nombre": "Bazar/Juguetería",  "rubro": "Comercio"},
    {"codigo": "abarrotes",  "nombre": "Abarrotes",         "rubro": "Comercio"},
    {"codigo": "farmacia",   "nombre": "Farmacia/Botica",   "rubro": "Servicios"},
    {"codigo": "peluqueria", "nombre": "Peluquería",        "rubro": "Servicios"},
    {"codigo": "lavanderia", "nombre": "Lavandería",        "rubro": "Servicios"},
    {"codigo": "stecnico",   "nombre": "Servicio técnico",  "rubro": "Servicios"},
    {"codigo": "veterinaria","nombre": "Veterinaria",       "rubro": "Servicios"},
    {"codigo": "grifo",      "nombre": "Grifo",             "rubro": "Otros"},
    {"codigo": "floreria",   "nombre": "Florería",          "rubro": "Otros"},
    {"codigo": "lubricantes","nombre": "Lubricantes",       "rubro": "Otros"},
    {"codigo": "generico",   "nombre": "Genérico",          "rubro": "Otros"},
]

BANCOS_PE = ["BCP", "BBVA", "Interbank", "Scotiabank", "BanBif", "Pichincha", "Banco de la Nación", "Otro"]


def slugify(texto: str) -> str:
    """Convierte texto a slug seguro para URL/email."""
    s = texto.lower().strip()
    s = re.sub(r"[áàäâã]", "a", s)
    s = re.sub(r"[éèëê]", "e", s)
    s = re.sub(r"[íìïî]", "i", s)
    s = re.sub(r"[óòöôõ]", "o", s)
    s = re.sub(r"[úùüû]", "u", s)
    s = re.sub(r"ñ", "n", s)
    s = re.sub(r"[^a-z0-9]+", "", s)
    return s[:30]


def generar_slug_empresa(db: Session, nombre_comercial: str) -> str:
    base = slugify(nombre_comercial) or "empresa"
    slug = base
    i = 2
    while db.query(Empresa).filter(Empresa.slug == slug).first():
        slug = f"{base}{i}"
        i += 1
    return slug


def ctx_base(request: Request, persona: Persona = None, empresa: Empresa = None) -> dict:
    return {
        "request": request,
        "persona": persona,
        "empresa": empresa,
        "iconos_sectoriales": ICONOS_SECTORIALES,
        "bancos_pe": BANCOS_PE,
    }


# =============================================================
# LOGIN / LOGOUT
# =============================================================

@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request, db: Session = Depends(get_db),
                     pagook_admin_sesion: str | None = Cookie(default=None)):
    # Si ya está logueado, redirige al dashboard
    sesion = obtener_sesion(db, pagook_admin_sesion)
    if sesion:
        return RedirectResponse(url="/admin/", status_code=303)
    paises = db.query(Pais).filter(Pais.activo == True).order_by(Pais.orden).all()
    return templates.TemplateResponse("admin/login.html", {
        "request": request,
        "paises": paises,
        "error": None,
    })


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    pais_codigo: str = Form(...),
    id_fiscal: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    persona = autenticar_duenio(db, id_fiscal, password)
    if not persona:
        paises = db.query(Pais).filter(Pais.activo == True).order_by(Pais.orden).all()
        return templates.TemplateResponse("admin/login.html", {
            "request": request,
            "paises": paises,
            "error": "DNI o contraseña incorrectos",
            "pais_codigo": pais_codigo,
            "id_fiscal": id_fiscal,
        }, status_code=200)
    # Buscar empresa del dueño
    empresa = db.query(Empresa).filter(
        Empresa.duenio_id == persona.id, Empresa.activa == True
    ).first()
    empresa_id = empresa.id if empresa else None
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent", "")[:300]
    token = crear_sesion(db, persona.id, empresa_id, ip, ua)
    response = RedirectResponse(url="/admin/", status_code=303)
    response.set_cookie(
        key=SESION_COOKIE,
        value=token,
        max_age=8 * 3600,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    return response


@router.post("/logout")
async def logout(
    request: Request,
    db: Session = Depends(get_db),
    pagook_admin_sesion: str | None = Cookie(default=None),
):
    if pagook_admin_sesion:
        cerrar_sesion(db, pagook_admin_sesion)
    response = RedirectResponse(url="/admin/login", status_code=303)
    response.delete_cookie(SESION_COOKIE)
    return response


# =============================================================
# DASHBOARD
# =============================================================

@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    db: Session = Depends(get_db),
    persona: Persona = Depends(current_persona),
    empresa: Empresa = Depends(current_empresa),
):
    if not empresa:
        return RedirectResponse(url="/admin/empresa/nueva", status_code=303)
    # Stats
    n_locales = db.query(Local).filter(Local.empresa_id == empresa.id, Local.activo == True).count()
    n_vendedores = db.query(VendedorLocal).join(Local).filter(
        Local.empresa_id == empresa.id, VendedorLocal.activo == True
    ).count()
    n_metodos = db.query(MetodoPago).filter(
        MetodoPago.empresa_id == empresa.id, MetodoPago.activo == True
    ).count()
    n_cuentas = db.query(CuentaBancaria).filter(
        CuentaBancaria.empresa_id == empresa.id, CuentaBancaria.activa == True
    ).count()
    # Checklist de onboarding
    checklist = {
        "datos_negocio": bool(empresa.domicilio_fiscal and empresa.regimen_tributario_id),
        "logo": bool(empresa.logo_url or empresa.icono_sectorial),
        "metodos_pago": n_metodos > 0,
        "vendedores": n_vendedores > 0,
        "impresion": bool(empresa.config_impresion),
    }
    ctx = ctx_base(request, persona, empresa)
    ctx.update({
        "n_locales": n_locales,
        "n_vendedores": n_vendedores,
        "n_metodos": n_metodos,
        "n_cuentas": n_cuentas,
        "checklist": checklist,
        "completado": sum(1 for v in checklist.values() if v),
        "total_checklist": len(checklist),
    })
    return templates.TemplateResponse("admin/dashboard.html", ctx)


# =============================================================
# EMPRESA NUEVA (onboarding) y EDICIÓN
# =============================================================

@router.get("/empresa/nueva", response_class=HTMLResponse)
async def empresa_nueva_form(
    request: Request,
    db: Session = Depends(get_db),
    persona: Persona = Depends(current_persona),
):
    paises = db.query(Pais).filter(Pais.activo == True).order_by(Pais.orden).all()
    regimenes_pe = db.query(RegimenTributario).filter(
        RegimenTributario.pais_codigo == "PE"
    ).order_by(RegimenTributario.orden).all()
    ctx = ctx_base(request, persona, None)
    ctx.update({"paises": paises, "regimenes_pe": regimenes_pe})
    return templates.TemplateResponse("admin/empresa_nueva.html", ctx)


@router.post("/empresa/nueva", response_class=HTMLResponse)
async def empresa_nueva_submit(
    request: Request,
    pais_codigo: str = Form(...),
    id_fiscal: str = Form(...),
    razon_social: str = Form(...),
    nombre_comercial: str = Form(...),
    domicilio_fiscal: str = Form(""),
    ciudad: str = Form(""),
    regimen_tributario_id: int = Form(None),
    db: Session = Depends(get_db),
    persona: Persona = Depends(current_persona),
):
    id_fiscal = id_fiscal.strip()
    # Validar duplicado
    existe = db.query(Empresa).filter(
        Empresa.pais_codigo == pais_codigo, Empresa.id_fiscal == id_fiscal
    ).first()
    if existe:
        paises = db.query(Pais).filter(Pais.activo == True).order_by(Pais.orden).all()
        regimenes_pe = db.query(RegimenTributario).filter(
            RegimenTributario.pais_codigo == "PE"
        ).order_by(RegimenTributario.orden).all()
        ctx = ctx_base(request, persona, None)
        ctx.update({
            "paises": paises, "regimenes_pe": regimenes_pe,
            "error": f"Ya existe una empresa con ese {pais_codigo} {id_fiscal}",
        })
        return templates.TemplateResponse("admin/empresa_nueva.html", ctx)
    slug = generar_slug_empresa(db, nombre_comercial)
    email_notif = f"bancos.{slug}@pagook.pro"
    empresa = Empresa(
        pais_codigo=pais_codigo,
        id_fiscal=id_fiscal,
        razon_social=razon_social.strip().upper(),
        nombre_comercial=nombre_comercial.strip(),
        domicilio_fiscal=domicilio_fiscal.strip() or None,
        ciudad=ciudad.strip() or None,
        regimen_tributario_id=regimen_tributario_id,
        slug=slug,
        email_notif_bancos=email_notif,
        duenio_id=persona.id,
    )
    db.add(empresa)
    db.flush()
    # Crear config_impresion default
    config = ConfigImpresion(empresa_id=empresa.id)
    db.add(config)
    db.commit()
    # Actualizar sesión
    request.state.sesion.empresa_id = empresa.id
    db.commit()
    return RedirectResponse(url="/admin/", status_code=303)


@router.get("/empresa", response_class=HTMLResponse)
async def empresa_editar(
    request: Request,
    db: Session = Depends(get_db),
    persona: Persona = Depends(current_persona),
    empresa: Empresa = Depends(current_empresa),
):
    if not empresa:
        return RedirectResponse(url="/admin/empresa/nueva", status_code=303)
    paises = db.query(Pais).filter(Pais.activo == True).order_by(Pais.orden).all()
    regimenes_pe = db.query(RegimenTributario).filter(
        RegimenTributario.pais_codigo == empresa.pais_codigo
    ).order_by(RegimenTributario.orden).all()
    ctx = ctx_base(request, persona, empresa)
    ctx.update({"paises": paises, "regimenes_pe": regimenes_pe})
    return templates.TemplateResponse("admin/empresa.html", ctx)


@router.post("/empresa", response_class=HTMLResponse)
async def empresa_actualizar(
    request: Request,
    razon_social: str = Form(...),
    nombre_comercial: str = Form(...),
    domicilio_fiscal: str = Form(""),
    ciudad: str = Form(""),
    regimen_tributario_id: int = Form(None),
    forma_pago_default: str = Form("CONTADO"),
    es_zona_especial: bool = Form(False),
    zona_especial_nombre: str = Form(""),
    db: Session = Depends(get_db),
    persona: Persona = Depends(current_persona),
    empresa: Empresa = Depends(current_empresa),
):
    if not empresa:
        return RedirectResponse(url="/admin/empresa/nueva", status_code=303)
    empresa.razon_social = razon_social.strip().upper()
    empresa.nombre_comercial = nombre_comercial.strip()
    empresa.domicilio_fiscal = domicilio_fiscal.strip() or None
    empresa.ciudad = ciudad.strip() or None
    empresa.regimen_tributario_id = regimen_tributario_id
    empresa.forma_pago_default = forma_pago_default
    empresa.es_zona_especial = es_zona_especial
    empresa.zona_especial_nombre = zona_especial_nombre.strip() or None
    empresa.actualizada_en = datetime.utcnow()
    db.commit()
    return templates.TemplateResponse("admin/partials/empresa_guardado.html", {
        "request": request, "empresa": empresa,
    })


# =============================================================
# LOGO
# =============================================================

@router.get("/logo", response_class=HTMLResponse)
async def logo_view(
    request: Request,
    db: Session = Depends(get_db),
    persona: Persona = Depends(current_persona),
    empresa: Empresa = Depends(current_empresa),
):
    if not empresa:
        return RedirectResponse(url="/admin/empresa/nueva", status_code=303)
    return templates.TemplateResponse("admin/logo.html", ctx_base(request, persona, empresa))


@router.post("/logo/icono", response_class=HTMLResponse)
async def logo_icono(
    request: Request,
    codigo: str = Form(...),
    db: Session = Depends(get_db),
    empresa: Empresa = Depends(current_empresa),
):
    if not empresa:
        return Response(status_code=403)
    codigos_validos = {i["codigo"] for i in ICONOS_SECTORIALES}
    if codigo not in codigos_validos and codigo != "":
        codigo = "generico"
    empresa.icono_sectorial = codigo or None
    empresa.logo_url = None  # icono sectorial sobreescribe logo subido
    db.commit()
    return templates.TemplateResponse("admin/partials/logo_seleccionado.html", {
        "request": request, "empresa": empresa,
    })


@router.post("/logo/quitar", response_class=HTMLResponse)
async def logo_quitar(
    request: Request,
    db: Session = Depends(get_db),
    empresa: Empresa = Depends(current_empresa),
):
    if not empresa:
        return Response(status_code=403)
    empresa.logo_url = None
    empresa.icono_sectorial = None
    db.commit()
    return templates.TemplateResponse("admin/partials/logo_seleccionado.html", {
        "request": request, "empresa": empresa,
    })


# =============================================================
# MÉTODOS DE PAGO
# =============================================================

@router.get("/pagos", response_class=HTMLResponse)
async def pagos_view(
    request: Request,
    db: Session = Depends(get_db),
    persona: Persona = Depends(current_persona),
    empresa: Empresa = Depends(current_empresa),
):
    if not empresa:
        return RedirectResponse(url="/admin/empresa/nueva", status_code=303)
    metodos = {m.tipo: m for m in empresa.metodos_pago}
    cuentas = sorted(empresa.cuentas_bancarias, key=lambda c: c.orden)
    ctx = ctx_base(request, persona, empresa)
    ctx.update({"metodos": metodos, "cuentas": cuentas})
    return templates.TemplateResponse("admin/pagos.html", ctx)


@router.post("/pagos/yape-plin", response_class=HTMLResponse)
async def guardar_yape_plin(
    request: Request,
    yape_celular: str = Form(""),
    yape_titular: str = Form(""),
    yape_activo: bool = Form(False),
    plin_celular: str = Form(""),
    plin_titular: str = Form(""),
    plin_activo: bool = Form(False),
    db: Session = Depends(get_db),
    empresa: Empresa = Depends(current_empresa),
):
    if not empresa:
        return Response(status_code=403)
    for tipo, celular, titular, activo in [
        ("yape", yape_celular, yape_titular, yape_activo),
        ("plin", plin_celular, plin_titular, plin_activo),
    ]:
        existente = db.query(MetodoPago).filter(
            MetodoPago.empresa_id == empresa.id, MetodoPago.tipo == tipo
        ).first()
        if existente:
            existente.celular = celular.strip() or None
            existente.titular = titular.strip() or None
            existente.activo = activo
        else:
            db.add(MetodoPago(
                empresa_id=empresa.id, tipo=tipo,
                celular=celular.strip() or None,
                titular=titular.strip() or None,
                activo=activo,
            ))
    db.commit()
    return templates.TemplateResponse("admin/partials/pagos_yape_plin_ok.html", {
        "request": request,
    })


@router.post("/pagos/tarjeta", response_class=HTMLResponse)
async def guardar_tarjeta(
    request: Request,
    tarjeta_activo: bool = Form(False),
    voucher_obligatorio: bool = Form(False),
    db: Session = Depends(get_db),
    empresa: Empresa = Depends(current_empresa),
):
    if not empresa:
        return Response(status_code=403)
    existente = db.query(MetodoPago).filter(
        MetodoPago.empresa_id == empresa.id, MetodoPago.tipo == "tarjeta"
    ).first()
    if existente:
        existente.activo = tarjeta_activo
        existente.voucher_obligatorio = voucher_obligatorio
    else:
        db.add(MetodoPago(
            empresa_id=empresa.id, tipo="tarjeta",
            activo=tarjeta_activo, voucher_obligatorio=voucher_obligatorio,
        ))
    db.commit()
    return templates.TemplateResponse("admin/partials/pagos_tarjeta_ok.html", {
        "request": request,
    })


@router.post("/pagos/efectivo", response_class=HTMLResponse)
async def guardar_efectivo(
    request: Request,
    efectivo_activo: bool = Form(True),
    db: Session = Depends(get_db),
    empresa: Empresa = Depends(current_empresa),
):
    if not empresa:
        return Response(status_code=403)
    existente = db.query(MetodoPago).filter(
        MetodoPago.empresa_id == empresa.id, MetodoPago.tipo == "efectivo"
    ).first()
    if existente:
        existente.activo = efectivo_activo
    else:
        db.add(MetodoPago(empresa_id=empresa.id, tipo="efectivo", activo=efectivo_activo))
    db.commit()
    return templates.TemplateResponse("admin/partials/pagos_efectivo_ok.html", {
        "request": request,
    })


# Cuentas bancarias
@router.post("/cuenta-bancaria", response_class=HTMLResponse)
async def cuenta_crear(
    request: Request,
    banco: str = Form(...),
    tipo_cuenta: str = Form(...),
    numero: str = Form(...),
    cci: str = Form(""),
    titular: str = Form(""),
    db: Session = Depends(get_db),
    empresa: Empresa = Depends(current_empresa),
):
    if not empresa:
        return Response(status_code=403)
    n_cuentas = db.query(CuentaBancaria).filter(
        CuentaBancaria.empresa_id == empresa.id, CuentaBancaria.activa == True
    ).count()
    if n_cuentas >= 5:
        return templates.TemplateResponse("admin/partials/cuenta_error.html", {
            "request": request, "error": "Máximo 5 cuentas bancarias",
        })
    cuenta = CuentaBancaria(
        empresa_id=empresa.id,
        banco=banco.strip(),
        tipo_cuenta=tipo_cuenta.strip() or None,
        numero=numero.strip(),
        cci=cci.strip() or None,
        titular=titular.strip() or None,
        orden=n_cuentas,
    )
    db.add(cuenta)
    db.commit()
    db.refresh(cuenta)
    return templates.TemplateResponse("admin/partials/cuenta_fila.html", {
        "request": request, "cuenta": cuenta,
    })


@router.delete("/cuenta-bancaria/{cuenta_id}", response_class=HTMLResponse)
async def cuenta_eliminar(
    cuenta_id: int,
    db: Session = Depends(get_db),
    empresa: Empresa = Depends(current_empresa),
):
    if not empresa:
        return Response(status_code=403)
    cuenta = db.query(CuentaBancaria).filter(
        CuentaBancaria.id == cuenta_id, CuentaBancaria.empresa_id == empresa.id,
    ).first()
    if cuenta:
        db.delete(cuenta)
        db.commit()
    return Response(status_code=200)


# =============================================================
# VENDEDORES Y LOCALES
# =============================================================

@router.get("/vendedores", response_class=HTMLResponse)
async def vendedores_view(
    request: Request,
    db: Session = Depends(get_db),
    persona: Persona = Depends(current_persona),
    empresa: Empresa = Depends(current_empresa),
):
    if not empresa:
        return RedirectResponse(url="/admin/empresa/nueva", status_code=303)
    locales = db.query(Local).filter(
        Local.empresa_id == empresa.id, Local.activo == True
    ).order_by(Local.creado_en).all()
    ctx = ctx_base(request, persona, empresa)
    ctx.update({"locales": locales})
    return templates.TemplateResponse("admin/vendedores.html", ctx)


@router.post("/local", response_class=HTMLResponse)
async def local_crear(
    request: Request,
    nombre: str = Form(...),
    direccion: str = Form(""),
    es_anexo: bool = Form(False),
    codigo_anexo: str = Form(""),
    serie_base: int = Form(None),
    db: Session = Depends(get_db),
    empresa: Empresa = Depends(current_empresa),
):
    if not empresa:
        return Response(status_code=403)
    local = Local(
        empresa_id=empresa.id,
        nombre=nombre.strip(),
        direccion=direccion.strip() or None,
        es_anexo=es_anexo,
        codigo_anexo=codigo_anexo.strip() or None,
        serie_base=serie_base,
    )
    db.add(local)
    db.commit()
    db.refresh(local)
    return templates.TemplateResponse("admin/partials/local_card.html", {
        "request": request, "local": local,
    })


@router.delete("/local/{local_id}", response_class=HTMLResponse)
async def local_eliminar(
    local_id: int,
    db: Session = Depends(get_db),
    empresa: Empresa = Depends(current_empresa),
):
    if not empresa:
        return Response(status_code=403)
    local = db.query(Local).filter(
        Local.id == local_id, Local.empresa_id == empresa.id
    ).first()
    if local:
        local.activo = False
        db.commit()
    return Response(status_code=200)


@router.post("/vendedor", response_class=HTMLResponse)
async def vendedor_crear(
    request: Request,
    local_id: int = Form(...),
    pais_codigo: str = Form(...),
    id_fiscal: str = Form(...),
    nombre_completo: str = Form(...),
    pin: str = Form(...),
    serie_b: str = Form(...),
    serie_f: str = Form(...),
    serie_p: str = Form(""),
    db: Session = Depends(get_db),
    empresa: Empresa = Depends(current_empresa),
):
    if not empresa:
        return Response(status_code=403)
    if len(pin) != 4 or not pin.isdigit():
        return templates.TemplateResponse("admin/partials/vendedor_error.html", {
            "request": request, "error": "El PIN debe ser exactamente 4 dígitos",
        })
    local = db.query(Local).filter(
        Local.id == local_id, Local.empresa_id == empresa.id
    ).first()
    if not local:
        return Response(status_code=404)
    # Buscar persona existente o crear
    persona_vend = db.query(Persona).filter(
        Persona.pais_codigo == pais_codigo, Persona.id_fiscal == id_fiscal.strip()
    ).first()
    if not persona_vend:
        persona_vend = Persona(
            pais_codigo=pais_codigo,
            id_fiscal=id_fiscal.strip(),
            nombre_completo=nombre_completo.strip(),
            pin_hash=hash_password(pin),
            es_duenio=False,
        )
        db.add(persona_vend)
        db.flush()
    else:
        persona_vend.pin_hash = hash_password(pin)
        if not persona_vend.nombre_completo:
            persona_vend.nombre_completo = nombre_completo.strip()
    # Verificar serie no duplicada
    existe_serie = db.query(VendedorLocal).filter(
        VendedorLocal.local_id == local_id,
        or_(VendedorLocal.serie_b == serie_b.strip(), VendedorLocal.serie_f == serie_f.strip()),
    ).first()
    if existe_serie:
        return templates.TemplateResponse("admin/partials/vendedor_error.html", {
            "request": request, "error": f"La serie {serie_b}/{serie_f} ya está usada en este local",
        })
    vend = VendedorLocal(
        persona_id=persona_vend.id,
        local_id=local_id,
        serie_b=serie_b.strip().upper(),
        serie_f=serie_f.strip().upper(),
        serie_p=serie_p.strip().upper() or None,
    )
    db.add(vend)
    db.commit()
    db.refresh(vend)
    return templates.TemplateResponse("admin/partials/vendedor_fila.html", {
        "request": request, "vend": vend, "persona": persona_vend,
    })


@router.delete("/vendedor/{vend_id}", response_class=HTMLResponse)
async def vendedor_eliminar(
    vend_id: int,
    db: Session = Depends(get_db),
    empresa: Empresa = Depends(current_empresa),
):
    if not empresa:
        return Response(status_code=403)
    vend = db.query(VendedorLocal).join(Local).filter(
        VendedorLocal.id == vend_id, Local.empresa_id == empresa.id
    ).first()
    if vend:
        vend.activo = False
        db.commit()
    return Response(status_code=200)


# =============================================================
# CONFIGURACIÓN DE IMPRESIÓN
# =============================================================

@router.get("/impresion", response_class=HTMLResponse)
async def impresion_view(
    request: Request,
    db: Session = Depends(get_db),
    persona: Persona = Depends(current_persona),
    empresa: Empresa = Depends(current_empresa),
):
    if not empresa:
        return RedirectResponse(url="/admin/empresa/nueva", status_code=303)
    config = empresa.config_impresion
    if not config:
        config = ConfigImpresion(empresa_id=empresa.id)
        db.add(config)
        db.commit()
        db.refresh(empresa)
    ctx = ctx_base(request, persona, empresa)
    ctx.update({"config": config})
    return templates.TemplateResponse("admin/impresion.html", ctx)


@router.post("/impresion", response_class=HTMLResponse)
async def impresion_guardar(
    request: Request,
    formato_default: str = Form("58mm"),
    comprobante_default: str = Form("boleta"),
    imprime_boleta: bool = Form(False),
    imprime_factura: bool = Form(False),
    imprime_proforma: bool = Form(False),
    imprime_nota_credito: bool = Form(False),
    imprime_nota_debito: bool = Form(False),
    imprime_guia_remision: bool = Form(False),
    imprime_ticket_simple: bool = Form(False),
    db: Session = Depends(get_db),
    empresa: Empresa = Depends(current_empresa),
):
    if not empresa:
        return Response(status_code=403)
    config = empresa.config_impresion
    if not config:
        config = ConfigImpresion(empresa_id=empresa.id)
        db.add(config)
    config.formato_default = formato_default
    config.comprobante_default = comprobante_default
    config.imprime_boleta = imprime_boleta
    config.imprime_factura = imprime_factura
    config.imprime_proforma = imprime_proforma
    config.imprime_nota_credito = imprime_nota_credito
    config.imprime_nota_debito = imprime_nota_debito
    config.imprime_guia_remision = imprime_guia_remision
    config.imprime_ticket_simple = imprime_ticket_simple
    db.commit()
    return templates.TemplateResponse("admin/partials/impresion_ok.html", {
        "request": request,
    })


# =============================================================
# CAMBIAR CONTRASEÑA
# =============================================================

@router.get("/perfil", response_class=HTMLResponse)
async def perfil_view(
    request: Request,
    persona: Persona = Depends(current_persona),
    empresa: Empresa = Depends(current_empresa),
):
    return templates.TemplateResponse("admin/perfil.html", ctx_base(request, persona, empresa))


@router.post("/perfil/password", response_class=HTMLResponse)
async def perfil_password(
    request: Request,
    password_actual: str = Form(...),
    password_nuevo: str = Form(...),
    password_confirm: str = Form(...),
    db: Session = Depends(get_db),
    persona: Persona = Depends(current_persona),
):
    if not verify_password(password_actual, persona.password_hash):
        return templates.TemplateResponse("admin/partials/password_error.html", {
            "request": request, "error": "Contraseña actual incorrecta",
        })
    if len(password_nuevo) < 6:
        return templates.TemplateResponse("admin/partials/password_error.html", {
            "request": request, "error": "La nueva contraseña debe tener al menos 6 caracteres",
        })
    if password_nuevo != password_confirm:
        return templates.TemplateResponse("admin/partials/password_error.html", {
            "request": request, "error": "Las contraseñas no coinciden",
        })
    persona.password_hash = hash_password(password_nuevo)
    db.commit()
    return templates.TemplateResponse("admin/partials/password_ok.html", {
        "request": request,
    })
