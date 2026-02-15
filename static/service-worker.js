const CACHE_VERSION = 'vortex-v2-20260215-001'; // Bump this to force update
const CACHE_NAME = `vortex-pwa-${CACHE_VERSION}`;
const STATIC_ASSETS = [
  '/',
  '/static/css/style.css',
  '/static/js/script.js',
  '/static/manifest.json',
  '/static/assets/brand/vortex-ultra-final.png'
];

// Install: Cache core assets
self.addEventListener('install', event => {
  self.skipWaiting(); // Force new SW to take over
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => {
      console.log('[ServiceWorker] Caching app shell:', CACHE_NAME);
      return cache.addAll(STATIC_ASSETS);
    })
  );
});

// Activate: Delete old caches
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(cacheNames => {
      return Promise.all(
        cacheNames.map(cache => {
          if (cache !== CACHE_NAME) {
            console.log('[ServiceWorker] Deleting old cache:', cache);
            return caches.delete(cache);
          }
        })
      );
    }).then(() => {
        console.log('[ServiceWorker] Claiming clients');
        return self.clients.claim();
    })
  );
});

// Fetch: Network First for API, Stale-While-Revalidate for Static
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  
  // API: Network Only (or Network First)
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(
      fetch(event.request).catch(() => {
        return new Response(JSON.stringify({ status: 'offline', message: 'No internet connection' }), {
            headers: { 'Content-Type': 'application/json' }
        });
      })
    );
    return;
  }

  // Static/Pages: Network First (to ensure fresh content), fallback to Cache
  // "New assets fetched fresh from Render backend" -> Network First
  event.respondWith(
    fetch(event.request)
      .then(response => {
        // Check if valid response
        if (!response || response.status !== 200 || response.type !== 'basic') {
          return response;
        }
        // Update cache with new version
        const responseToCache = response.clone();
        caches.open(CACHE_NAME).then(cache => {
          cache.put(event.request, responseToCache);
        });
        return response;
      })
      .catch(() => {
        // Fallback to cache if network fails
        return caches.match(event.request);
      })
  );
});
