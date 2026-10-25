// PagoOK — Email Worker de Cloudflare para bancos.pagook.pro (zWeb-06)
//
// Recibe cada correo que llega a *@bancos.pagook.pro (regla "catch-all") y lo
// reenvía SIN MODIFICAR (MIME crudo) al backend, que verifica DKIM y lo procesa.
//
// Variables del Worker (Settings > Variables and Secrets):
//   PAGOOK_URL                 https://pagook.pro/api/v1/correo/entrante   (texto)
//   CORREO_ENTRANTE_SECRETO    el mismo valor que en Railway               (secreto)
//   CORREO_RESPALDO            (opcional) buzón verificado en Email Routing, p. ej.
//                              un buzón de Hostinger: recibe el correo solo si el
//                              backend no responde, para no perderlo.
//
// Nunca se rechaza el correo: en Cloudflare, setReject() es un rebote
// PERMANENTE y el cliente recibiría un error.

export default {
  async email(message, env, ctx) {
    const mime = await new Response(message.raw).arrayBuffer();
    let ok = false;
    try {
      const respuesta = await fetch(env.PAGOOK_URL, {
        method: "POST",
        headers: {
          "Content-Type": "message/rfc822",
          "X-Correo-Secreto": env.CORREO_ENTRANTE_SECRETO,
          "X-Destinatario": message.to,
        },
        body: mime,
      });
      // 2xx: registrado en PagoOK (procesado, sin formato, rechazado o descartado).
      ok = respuesta.status < 500;
      if (!respuesta.ok) console.log("PagoOK respondió", respuesta.status, "para", message.to);
    } catch (e) {
      console.log("PagoOK no respondió:", String(e));
    }
    if (!ok && env.CORREO_RESPALDO) {
      await message.forward(env.CORREO_RESPALDO);
    }
  },
};
