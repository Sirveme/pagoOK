# pagook.pro - Frontend publico

Landing, videos demo y paginas publicas de pagoOK.

## Stack

- Python 3.11 + FastAPI
- Jinja2 templates
- Vanilla JS + CSS (sin frameworks)
- Deploy: Railway

## Arquitectura

Este repo solo sirve contenido publico. Todo el backend de pagoOK (BD, auth, logica, panel admin) vive en Gestix.

Los formularios y QRs se comunican con Gestix via:
- GESTIX_API_URL - desarrollo local
- GESTIX_API_URL_PUBLIC - produccion (ej. https://gestix.pro)

## Desarrollo local

```
# Venv
python -m venv venv
venv\Scripts\activate

# Dependencias
pip install -r requirements.txt

# Variables de entorno
copy .env.example .env

# Arrancar (Gestix deberia correr en 8001)
uvicorn app.main:app --reload --host 0.0.0.0 --port 4000
```

Abrir http://localhost:4000

## Deploy a Railway

Ver docs/deploy.md

## Estructura

```
app/         Logica del servidor FastAPI
static/      CSS, JS, imagenes, SEO
templates/   HTML (Jinja2)
docs/        Documentacion
```

## Contacto

- WhatsApp: +51 967 317 946
- Email: info@perusistemas.pro