# Propuesta de cambio a la Política de Privacidad: correos del banco

> Propuesta para que Duilio la revise y decida. **No está aplicada:**
> `docs/legal/privacidad.md` no se modificó. Si se aprueba, conviene publicarla
> como versión nueva (por ejemplo `2026-11-v1`), porque la app envía la versión
> como `consentimiento_version`.

## Dónde va

- Un párrafo nuevo en **"4. Qué datos se envían a nuestros servidores"** (una fila en la tabla).
- Una línea en **"8. Cuánto tiempo conservamos los datos"**.
- Un apartado corto nuevo, después de la sección 3: **"3 bis. Correos de alerta de tu banco (opcional)"**.

## Texto propuesto

### 3 bis. Correos de alerta de tu banco (opcional)

Algunas operaciones no generan un aviso en tu celular, por ejemplo los pagos que haces desde Yape, pero tu banco sí te las informa por correo. Si **tú decides** reenviar esos correos a la dirección que PagoOK te da en el panel (por ejemplo, `egresos+abcd2345@bancos.pagook.pro`), los usamos para registrar esas operaciones.

- **Solo se procesan los correos de tu banco.** Comprobamos la firma digital de cada correo (DKIM) y solo aceptamos los firmados por los dominios de los bancos configurados. Cualquier otro correo se guarda como rechazado y no se usa.
- **De cada correo tomamos** el monto, la moneda, la fecha y hora, el comercio o la persona a quien enviaste el dinero tal como lo muestra el banco, el número de operación y **solo los últimos 4 dígitos** de la tarjeta o cuenta.
- **Tú controlas el reenvío.** Lo configuras en tu correo (Gmail, Outlook) y puedes desactivarlo cuando quieras. También puedes cambiar tu dirección de PagoOK desde el panel: la anterior deja de funcionar al instante.
- **No leemos tu bandeja de entrada.** Solo recibimos los correos que tú reenvías a esa dirección.

### Fila nueva en la tabla de la sección 4

| Origen | Datos | Ejemplo |
| --- | --- | --- |
| Correos de tu banco que tú reenvías (opcional) | Remitente, asunto y texto del correo; de ahí, monto, fecha, comercio o destinatario, número de operación y últimos 4 dígitos de tarjeta o cuenta | "Realizaste un consumo de 20.00 en SUPERMERCADO…", "**** 1234" |

### Línea nueva en la tabla de la sección 8

| Dato | Plazo |
| --- | --- |
| Texto completo de los correos del banco que reenvías | 90 días; luego se borra automáticamente (se conserva solo la operación registrada) |
