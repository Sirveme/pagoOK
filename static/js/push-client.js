// Cliente JS para suscribirse a Web Push

function urlBase64ToUint8Array(base64String) {
    const padding = '='.repeat((4 - base64String.length % 4) % 4);
    const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
    const rawData = window.atob(base64);
    return new Uint8Array([...rawData].map(c => c.charCodeAt(0)));
}

async function suscribirseAPagoOK(codigo, nombreReceptor, vapidPublicKey) {
    // Verificar soporte
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
        throw new Error('Tu navegador no soporta notificaciones push');
    }

    // Pedir permiso
    const permiso = await Notification.requestPermission();
    if (permiso !== 'granted') {
        throw new Error('Permiso de notificaciones denegado');
    }

    // Registrar Service Worker
    const registration = await navigator.serviceWorker.register('/static/js/sw.js', {
        scope: '/'
    });
    await navigator.serviceWorker.ready;

    // Suscribir al push manager
    let subscription = await registration.pushManager.getSubscription();
    if (!subscription) {
        subscription = await registration.pushManager.subscribe({
            userVisibleOnly: true,
            applicationServerKey: urlBase64ToUint8Array(vapidPublicKey)
        });
    }

    // Enviar al backend
    const resp = await fetch('/api/v1/push/suscribir', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            codigo: codigo,
            nombre_receptor: nombreReceptor,
            subscription: subscription.toJSON(),
            user_agent: navigator.userAgent
        })
    });

    if (!resp.ok) {
        const txt = await resp.text();
        throw new Error('Error al registrar: ' + txt);
    }

    return await resp.json();
}

window.pagoOKPush = { suscribirseAPagoOK };
