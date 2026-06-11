// Service Worker para Web Push de pagoOK

self.addEventListener('install', (event) => {
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    event.waitUntil(self.clients.claim());
});

self.addEventListener('push', (event) => {
    if (!event.data) return;

    let data;
    try {
        data = event.data.json();
    } catch (e) {
        data = {
            titulo: 'pagoOK',
            cuerpo: event.data.text() || 'Nuevo evento'
        };
    }

    const opciones = {
        body: data.cuerpo || '',
        icon: data.icon || '/static/img/pagook-icon-192.png',
        badge: data.badge || '/static/img/pagook-badge-72.png',
        vibrate: [200, 100, 200],
        tag: 'pagook-' + (data.datos?.pago_id || Date.now()),
        renotify: true,
        requireInteraction: false,
        data: data.datos || {},
        actions: [
            { action: 'ver', title: 'Ver detalle' }
        ]
    };

    event.waitUntil(
        self.registration.showNotification(data.titulo || 'pagoOK', opciones)
    );
});

self.addEventListener('notificationclick', (event) => {
    event.notification.close();

    const urlDestino = event.notification.data?.url_destino || '/';

    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true })
            .then((clientList) => {
                // Si ya hay ventana abierta de pagoOK, enfocarla
                for (const client of clientList) {
                    if (client.url.includes(urlDestino) && 'focus' in client) {
                        return client.focus();
                    }
                }
                // Sino, abrir nueva
                if (clients.openWindow) {
                    return clients.openWindow(urlDestino);
                }
            })
    );
});
