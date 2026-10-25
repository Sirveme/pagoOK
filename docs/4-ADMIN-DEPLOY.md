# pagoOK Admin — Despliegue paso a paso

## 1. BD nueva en Railway

1. Ya creaste el servicio PostgreSQL en Railway: ✓
2. En el panel de Railway, abre la BD y copia el valor de la variable `DATABASE_URL`
3. Pégalo en `.env` de tu proyecto local:
   ```
   DATABASE_URL=postgresql://...
   ```
4. En el servicio web (pagook), agrega la variable `DATABASE_URL` con el mismo valor

## 2. Aplicar migración v001

**Opción A — Desde PGAdmin:**
1. Conecta PGAdmin a la BD usando las credenciales del paso 1
2. Abre `Tools > Query Tool`
3. Abre el archivo `migrations/v001_init.sql` y pega su contenido
4. Ejecuta con `F5`
5. Verifica: `SELECT * FROM pais;` debe mostrar 9 países

**Opción B — Desde tu PC (si tienes psql):**
```bash
psql "postgresql://..." -f migrations/v001_init.sql
```

## 3. Crear usuario admin inicial

Desde la raíz del proyecto:

```bash
# Activar venv si lo usas
.\venv\Scripts\activate

# Instalar dependencias nuevas
pip install -r requirements.txt

# Ejecutar bootstrap
python scripts/bootstrap_admin.py
```

**Imprimirá una password aleatoria** que debes guardar (o pasarla por variable `ADMIN_BOOTSTRAP_PASSWORD`).

## 4. Entrar al admin

1. Inicia el servidor local:
   ```
   uvicorn app.main:app --reload --port 4000
   ```
2. Abre http://localhost:4000/admin/login
3. Ingresa:
   - País: Perú
   - DNI: 05393776
   - Password: (la del paso 3)

## 5. Onboarding

Te pedirá registrar tu empresa (Perú Sistemas Pro E.I.R.L., RUC 20615446565).
Luego completa el checklist:
- Datos del negocio
- Logo o ícono
- Métodos de pago
- Vendedores y locales
- Configuración de impresión

## 6. Deploy a Railway

```bash
git add .
git commit -m "feat: panel admin v1"
git push origin main
```

Railway detecta el push y deploya automáticamente.

## Próximos pasos (Entrega B)

- Conectar la PWA Caja al admin (leer config real)
- Parser IMAP para `bancos.<slug>@pagook.pro`
- Notas de crédito, débito, guías de remisión
- Reportes y exportes
