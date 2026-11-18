"""CSS compartido de los paneles globales de superadmin (zWeb-12).

Misma paleta que /static/panel/panel.css y /static/admin/admin.css: vino +
ámbar, tema oscuro. Nunca blanco puro: el texto claro es crema (#fef3d7).
Lo usan /admin/dispositivos/estado y /admin/testers-metricas (HTML
autocontenido: no dependen de archivos estáticos).
"""

CSS_BASE = """
  :root {
    --bg:#1a0303; --bg-2:#2a0404; --bg-3:#3d0606;
    --card:rgba(254,243,215,.05); --card-2:rgba(254,243,215,.09); --borde:rgba(254,243,215,.10);
    --txt:#fef3d7; --txt-suave:rgba(254,243,215,.72); --txt-mute:rgba(254,243,215,.45);
    --ambar:#f5a623; --ambar-claro:#ffc14f; --oscuro:#1a0303;
    --verde:#10b981; --rojo:#ef4444; --azul:#60a5fa; --violeta:#a78bfa;
    --verde-fondo:rgba(16,185,129,.14); --ambar-fondo:rgba(245,166,35,.14); --rojo-fondo:rgba(239,68,68,.14);
  }
  * { box-sizing:border-box; margin:0; padding:0; }
  body { font-family:'Inter',system-ui,-apple-system,sans-serif; background:var(--bg); color:var(--txt); padding:16px; line-height:1.45; }
  a { color:var(--ambar-claro); }
  h1 { font-size:20px; color:var(--ambar-claro); margin-bottom:8px; }
  h2 { font-size:15px; color:var(--ambar-claro); margin:22px 0 8px; }
  .chip { display:inline-block; border-radius:999px; padding:6px 14px; font-size:13px; font-weight:600; border:1px solid var(--borde); background:var(--card); color:var(--txt); }
  .c-verde { background:var(--verde-fondo); color:var(--verde); border-color:rgba(16,185,129,.35); }
  .c-amarillo { background:var(--ambar-fondo); color:var(--ambar-claro); border-color:rgba(245,166,35,.35); }
  .c-rojo { background:var(--rojo-fondo); color:var(--rojo); border-color:rgba(239,68,68,.35); }
  .c-total, .c-sin { background:var(--card-2); color:var(--txt-suave); }
  table { width:100%; border-collapse:collapse; background:var(--card); border:1px solid var(--borde); border-radius:12px; overflow:hidden; }
  th, td { text-align:left; padding:9px 12px; font-size:13px; border-bottom:1px solid var(--borde); vertical-align:top; }
  th { background:var(--bg-2); color:var(--txt-mute); font-weight:600; font-size:12px; text-transform:uppercase; letter-spacing:.4px; }
  .tabla-scroll { overflow-x:auto; border-radius:12px; }
  .badge { display:inline-block; border-radius:999px; padding:2px 9px; font-size:11px; font-weight:700; text-transform:capitalize; color:var(--oscuro); }
  .b-verde { background:var(--verde); } .b-amarillo { background:var(--ambar); }
  .b-rojo { background:var(--rojo); } .b-inactivo, .b-gris { background:var(--txt-mute); }
  button, .boton { border:1px solid var(--borde); background:var(--card-2); color:var(--txt); border-radius:8px; padding:6px 12px; font-size:13px; cursor:pointer; text-decoration:none; display:inline-block; }
  button:hover, .boton:hover { border-color:var(--ambar); }
  input, select { border:1px solid var(--borde); background:var(--bg-3); color:var(--txt); border-radius:8px; padding:6px 9px; font-size:13px; }
  .salir { margin-left:8px; font-size:13px; color:var(--ambar-claro); text-decoration:none; }
  .vacio { font-size:13px; color:var(--txt-suave); padding:10px 0; }
  .mute { color:var(--txt-mute); }
  .malo { color:var(--rojo); } .bueno { color:var(--verde); } .medio { color:var(--ambar-claro); }
  .nota { font-size:12px; color:var(--txt-suave); background:var(--card); border:1px solid var(--borde); border-radius:8px; padding:8px 10px; margin-top:8px; }
  /* Semáforo: punto de color + valor */
  .sem { display:inline-flex; align-items:center; gap:6px; white-space:nowrap; }
  .sem::before { content:""; width:10px; height:10px; border-radius:50%; background:var(--txt-mute); flex-shrink:0; }
  .sem.verde::before { background:var(--verde); } .sem.amarillo::before { background:var(--ambar); }
  .sem.rojo::before { background:var(--rojo); } .sem.gris { color:var(--txt-mute); }
"""
