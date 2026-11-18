"""Tablero de testers (zWeb-12), SOLO LECTURA. Credencial global de superadmin.

  GET /admin/testers-metricas          HTML (sesión de superadmin por cookie)
  GET /api/v1/admin/testers-metricas   JSON (cookie o X-Admin-Token)
  GET /admin/testers-metricas.csv      CSV  (cookie o X-Admin-Token)

Cálculo en app/services/metricas_testers.py. No escribe nada en la base.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from app.admin import superadmin
from app.admin.db import get_db
from app.admin.estilo_oscuro import CSS_BASE
from app.services import metricas_testers

router = APIRouter(tags=["admin-testers"])


@router.get("/api/v1/admin/testers-metricas")
def testers_json(request: Request, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
                 db: Session = Depends(get_db)):
    if not superadmin.autorizado(request, x_admin_token):
        raise HTTPException(401, "No autorizado")
    return metricas_testers.calcular(db)


@router.get("/admin/testers-metricas.csv")
def testers_csv(request: Request, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
                db: Session = Depends(get_db)):
    if not superadmin.autorizado(request, x_admin_token):
        raise HTTPException(401, "No autorizado")
    nombre = f"pagook_testers_{datetime.now(timezone.utc):%Y%m%d}.csv"
    return Response(metricas_testers.a_csv(metricas_testers.calcular(db)), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{nombre}"'})


@router.get("/admin/testers-metricas", response_class=HTMLResponse)
def testers_html(request: Request, db: Session = Depends(get_db)):
    if not superadmin.sesion_valida(request):
        return HTMLResponse(superadmin.html_login("/admin/testers-metricas", "Métricas de testers"), status_code=401)
    datos = metricas_testers.calcular(db)
    # "</" escapado: un nombre de empresa no puede cerrar el <script>.
    return HTMLResponse(_PLANTILLA.replace("__CSS_BASE__", CSS_BASE)
                        .replace("__DATOS_JSON__", json.dumps(datos).replace("</", "<\\/")))


_PLANTILLA = """<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Métricas de testers · pagoOK</title>
<style>
__CSS_BASE__
  .cont { max-width:1200px; margin:0 auto; }
  .barra { display:flex; justify-content:space-between; align-items:center; gap:10px; flex-wrap:wrap; margin:10px 0 14px; }
  .sub { font-size:13px; color:var(--txt-suave); }
  .resumen { display:flex; gap:10px; flex-wrap:wrap; margin-bottom:12px; }
  .pestanas { display:flex; gap:6px; flex-wrap:wrap; margin:14px 0 10px; }
  .pestanas button[aria-selected="true"] { background:var(--ambar); color:var(--oscuro); border-color:var(--ambar); font-weight:700; }
  td.num, th.num { text-align:right; }
  td small { display:block; color:var(--txt-mute); font-size:11px; }
  .leyenda { display:flex; gap:14px; flex-wrap:wrap; font-size:12px; color:var(--txt-suave); margin-top:10px; }
  @media (max-width:480px) { body { padding:12px; } h1 { font-size:18px; } }
</style></head>
<body><div class="cont">
  <h1>Métricas de testers</h1>
  <p class="sub" id="sub"></p>
  <div class="barra">
    <div class="resumen" id="resumen"></div>
    <span>
      <a class="boton" href="/admin/testers-metricas.csv">Exportar CSV</a>
      <a class="boton" href="/admin/dispositivos/estado">Estado de dispositivos</a>
      <a class="salir" href="/admin/salir">Salir</a>
    </span>
  </div>
  <div class="pestanas" role="tablist" id="pestanas"></div>
  <div class="tabla-scroll"><table id="tabla"></table></div>
  <div class="leyenda" id="leyenda"></div>
  <div id="notas"></div>
</div>
<script>
const datos = __DATOS_JSON__;
const VISTAS = [["dispositivo","Por dispositivo"],["marca","Por marca"],["modelo","Por modelo"],["android","Por Android"],["rubro","Por rubro"]];
let vista = "dispositivo";
function esc(s){ return String(s).replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
const SD = '<span class="mute">sin datos</span>';
function sem(color, texto){ return '<span class="sem '+color+'">'+texto+'</span>'; }
function n(v){ return v == null ? SD : esc(v); }
function pctTxt(v, color){ return v == null ? SD : sem(color, esc(v)+" %"); }
function latTxt(p50, p90, color){ return p50 == null ? SD : sem(color, esc(p50)+" s")+'<small>p90: '+esc(p90)+' s</small>'; }
function incTxt(z, a, color){ return z == null ? SD : sem(color, esc(z)+" / "+esc(a)); }
function cabecera(primera){
  return "<thead><tr>"+primera+"<th class='num'>Pagos (7 d)</th><th>Latencia mediana</th><th>Verde 24 h</th><th>Verde 7 d</th><th>Zombies / alarmas (7 d)</th>";
}
function pintar(){
  document.getElementById("pestanas").innerHTML = VISTAS.map(([k,t]) =>
    '<button type="button" role="tab" aria-selected="'+(k===vista)+'" onclick="vista=\\''+k+'\\';pintar()">'+t+'</button>').join("");
  const t = document.getElementById("tabla");
  if (vista === "dispositivo") {
    t.innerHTML = cabecera("<th>Empresa</th><th>Equipo</th><th>App</th>") + "<th>Último pago</th><th>Último heartbeat</th></tr></thead><tbody>" +
      (datos.dispositivos.length ? datos.dispositivos.map(d =>
        "<tr><td>"+esc(d.empresa)+"<small>"+esc(d.rubro || "Sin rubro")+"</small></td>" +
        "<td>"+esc([d.marca, d.modelo].filter(Boolean).join(" ") || ("Equipo "+d.dispositivo_id))+"<small>Android "+n(d.android)+" · #"+d.dispositivo_id+"</small></td>" +
        "<td>"+sem(d.semaforo_version, esc(d.app_version || "—"))+"</td>" +
        "<td class='num'>"+d.pagos_7d+"<small>total "+d.pagos_total+"</small></td>" +
        "<td>"+latTxt(d.latencia_p50_s, d.latencia_p90_s, d.semaforo_latencia)+"</td>" +
        "<td>"+pctTxt(d.verde_24h_pct, d.semaforo_verde_24h)+"</td>" +
        "<td>"+pctTxt(d.verde_7d_pct, d.semaforo_verde_7d)+"</td>" +
        "<td>"+incTxt(d.zombies_7d, d.alarmas_7d, d.semaforo_incidentes)+"</td>" +
        "<td>"+(d.ultimo_pago_lima ? esc(d.ultimo_pago_lima) : '<span class="malo">Ninguno</span>')+"</td>" +
        "<td>"+(d.ultimo_heartbeat_lima ? sem(d.semaforo_heartbeat, esc(d.ultimo_heartbeat_lima)) : '<span class="malo">Nunca</span>')+"</td></tr>"
      ).join("") : "<tr><td colspan='10' class='vacio'>No hay equipos de testers activos.</td></tr>") + "</tbody>";
  } else {
    const titulo = {marca:"Marca", modelo:"Modelo", android:"Android", rubro:"Rubro"}[vista];
    const g = datos.grupos[vista];
    t.innerHTML = cabecera("<th>"+titulo+"</th><th class='num'>Equipos</th>") + "<th class='num'>Por actualizar</th></tr></thead><tbody>" +
      (g.length ? g.map(x =>
        "<tr><td>"+esc(x.grupo)+"</td><td class='num'>"+x.dispositivos+"</td>" +
        "<td class='num'>"+x.pagos_7d+"<small>total "+x.pagos_total+"</small></td>" +
        "<td>"+latTxt(x.latencia_p50_s, x.latencia_p90_s, x.semaforo_latencia)+"</td>" +
        "<td>"+pctTxt(x.verde_24h_pct, x.semaforo_verde_24h)+"</td>" +
        "<td>"+pctTxt(x.verde_7d_pct, x.semaforo_verde_7d)+"</td>" +
        "<td>"+incTxt(x.zombies_7d, x.alarmas_7d, x.semaforo_incidentes)+"</td>" +
        "<td class='num'>"+(x.por_actualizar ? '<span class="medio">'+x.por_actualizar+'</span>' : '0')+"</td></tr>"
      ).join("") : "<tr><td colspan='8' class='vacio'>Sin datos.</td></tr>") + "</tbody>";
  }
}
const ds = datos.dispositivos, u = datos.umbrales;
document.getElementById("sub").textContent = "Solo lectura · generado " + datos.generado_lima + " (hora de Lima)" +
  (datos.version_referencia ? " · versión más nueva en uso: " + datos.version_referencia : "");
const pl = (k, uno, varios) => k + " " + (k === 1 ? uno : varios);
const nPagos = ds.reduce((s,d)=>s+d.pagos_7d,0);
const nActualizar = ds.filter(d=>d.semaforo_version!=="verde").length;
const nSinHb = ds.filter(d=>d.semaforo_heartbeat==="rojo").length;
document.getElementById("resumen").innerHTML =
  '<span class="chip c-total">'+pl(ds.length, "equipo", "equipos")+'</span>' +
  '<span class="chip c-verde">'+pl(nPagos, "pago", "pagos")+' en 7 días</span>' +
  '<span class="chip '+(nActualizar ? "c-amarillo" : "c-total")+'">'+nActualizar+' por actualizar</span>' +
  '<span class="chip '+(nSinHb ? "c-rojo" : "c-total")+'">'+nSinHb+' sin heartbeat reciente</span>';
document.getElementById("leyenda").innerHTML =
  sem("verde","Bien")+sem("amarillo","Atención")+sem("rojo","Problema")+sem("gris","Sin datos") +
  "<span>Verde: ≥ "+u.verde_pct.verde+" % de horas en verde · latencia p90 ≤ "+u.latencia_p90_s.verde+" s · 0 zombies/alarmas</span>";
const notas = [
  "Sin contar la cuenta de revisión de Google Play ni las empresas demo" + (datos.excluidas.length ? " (" + datos.excluidas.map(esc).join(", ") + ")" : "") + ".",
  "Pagos: solo ingresos capturados por el equipo, sin pagos de prueba ni dudosos. Latencia: de la hora del aviso en el celular a la llegada al servidor, últimos " + datos.dias_latencia + " días.",
  "Tiempo en verde: aproximado por horas (estado que el equipo reportó al cerrar cada hora). Una hora sin heartbeat cuenta como fuera de verde.",
];
if (!datos.incidentes_disponibles) notas.push("Zombies y alarmas: sin datos hasta que se cree la tabla de eventos (sql/incidentes_dispositivo_2026-11-17.sql).");
else notas.push("Zombies y alarmas: se cuentan desde que existe el registro de eventos; lo anterior solo tiene contadores sin fecha.");
if (!datos.rubro_disponible) notas.push("Rubro: todavía no existe el campo en la empresa (sql/empresa_rubro_2026-11.sql).");
document.getElementById("notas").innerHTML = notas.map(t => '<p class="nota">'+t+'</p>').join("");
pintar();
</script>
</body></html>"""
