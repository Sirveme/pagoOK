// ============================================================
// pagoOK Caja - Service Worker (Nivel 1: shell mínimo)
// ============================================================
// Versión simple: cachea archivos estáticos para arranque rápido,
// pero deja que las llamadas a la API vayan siempre a la red.

const CACHE_NAME = 'pagook-caja-v1';
const SHELL_FILES = [
  './',
  './index.html',
  './caja.css',
  './caja.js',
  './manifest.json',
  './icon.svg',
];

// Instalación: cachear el shell
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(SHELL_FILES).catch((err) => {
        console.warn('SW: algunos archivos no se cachearon:', err);
      });
    })
  );
  self.skipWaiting();
});

// Activación: limpiar caches viejos
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((names) => {
      return Promise.all(
        names
          .filter((name) => name !== CACHE_NAME)
          .map((name) => caches.delete(name))
      );
    })
  );
  self.clients.claim();
});

// Fetch: estrategia network-first para API, cache-first para shell
self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // Las llamadas a API siempre van a la red (no se cachean)
  if (url.pathname.includes('/api/')) {
    event.respondWith(fetch(event.request));
    return;
  }

  // Para el shell: intenta cache primero, si no está → red
  event.respondWith(
    caches.match(event.request).then((cached) => {
      if (cached) {
        return cached;
      }
      return fetch(event.request).then((response) => {
        // Solo cachear respuestas exitosas del mismo origen
        if (response.ok && url.origin === location.origin) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
        }
        return response;
      }).catch(() => {
        // Sin red ni caché: devolver index para SPA-like behavior
        return caches.match('./index.html');
      });
    })
  );
});