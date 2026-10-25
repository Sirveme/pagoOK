# Deploy de pagook.pro

## Desarrollo local

Requiere Python 3.11+.

```
cd C:\pagook

# Crear venv (primera vez)
python -m venv venv

# Activar venv (cada sesion)
venv\Scripts\activate

# Instalar dependencias (primera vez)
pip install -r requirements.txt

# Copiar .env
copy .env.example .env

# Arrancar en puerto 4000 (Gestix debe correr en 8001)
uvicorn app.main:app --reload --host 0.0.0.0 --port 4000
```

Abrir http://localhost:4000

## Deploy a Railway

### Primera vez

1. Crear repo en GitHub (manual, como acostumbras)
2. Push del codigo
3. En Railway, nuevo servicio desde GitHub repo
4. Variables de entorno en Railway:
   - ENV=production
   - GESTIX_API_URL=https://gestix.pro
   - GESTIX_API_URL_PUBLIC=https://gestix.pro
   - WHATSAPP_NUMERO=51967317946
   - EMAIL_CONTACTO=info@perusistemas.pro
5. Railway detecta Python automaticamente (Procfile + requirements.txt)
6. En Settings -> Domains, agregar pagook.pro y www.pagook.pro

### Cloudflare DNS

- pagook.pro -> A record proxied a Railway
- www.pagook.pro -> CNAME a pagook.pro

### Actualizaciones

Cada git push a main dispara auto-deploy en Railway.

## Verificacion post-deploy

- [ ] https://pagook.pro carga landing
- [ ] https://pagook.pro/videos carga pagina videos
- [ ] https://pagook.pro/robots.txt responde texto
- [ ] https://pagook.pro/llms.txt responde texto
- [ ] https://pagook.pro/sitemap.xml responde XML
- [ ] https://pagook.pro/health responde status ok
- [ ] Formulario encuesta envia a Gestix
- [ ] QR tracking funciona

## Troubleshooting

### "No module named pydantic_settings"
Falta instalar dependencias:
pip install -r requirements.txt

### "Connection refused en localhost:8001"
Gestix no esta corriendo. La landing va a usar fallback de PE.
Arranca Gestix en otra terminal.

### Landing carga pero precio aparece hardcoded
No se pudo conectar a Gestix. Verifica GESTIX_API_URL en .env