const CACHE_VERSION = "v13";
const PRECACHE = `qb-precache-${CACHE_VERSION}`;
const RUNTIME = `qb-runtime-${CACHE_VERSION}`;
const OFFLINE_URL = "/offline";

const PRECACHE_URLS = [
  "/",
  "/all_quotes",
  "/random",
  "/quote-anarchy",
  "/mailbox",
  "/unsubscribe",
  OFFLINE_URL,
  "/manifest.webmanifest",
  "/static/favicon.ico",
  "/static/favicon.png",
  "/static/icons/icon-180.png",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
  "/static/assets/css/main.css",
  "/static/assets/css/footer.css",
  "/static/assets/css/index.css",
  "/static/assets/css/quote-anarchy.css",
  "/static/assets/css/mailbox.css",
  "/static/assets/js/background.js",
  "/static/assets/js/pwa-sync.js",
  "/static/assets/js/quote-anarchy.js",
  "/static/assets/quote-anarchy/icon.svg",
  "/static/assets/quote-anarchy/card-back.svg",
  "/static/assets/quote-anarchy/table-bg.svg",
  "/static/assets/quote-anarchy/player-chip-1.svg",
  "/static/assets/quote-anarchy/player-chip-2.svg",
  "/static/assets/quote-anarchy/player-chip-3.svg",
  "/static/assets/quote-anarchy/player-chip-4.svg",
  "/static/assets/quote-anarchy/black-cards.json",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(PRECACHE)
      .then((cache) => cache.addAll(PRECACHE_URLS))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((key) => ![PRECACHE, RUNTIME].includes(key))
            .map((key) => caches.delete(key)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (request.mode === "navigate") {
    event.respondWith(networkFirst(request, RUNTIME, OFFLINE_URL));
    return;
  }

  if (url.pathname.startsWith("/api/")) {
    event.respondWith(networkFirst(request, RUNTIME));
    return;
  }

  if (
    url.pathname.startsWith("/static/") ||
    url.pathname === "/manifest.webmanifest"
  ) {
    event.respondWith(cacheFirst(request, PRECACHE));
    return;
  }

  event.respondWith(staleWhileRevalidate(request, RUNTIME, OFFLINE_URL));
});

self.addEventListener("push", (event) => {
  let payload = {};
  if (event.data) {
    try {
      payload = event.data.json();
    } catch (err) {
      payload = { body: event.data.text() };
    }
  }

  const title = payload.title || "People are chatting...";
  const options = {
    body: payload.body || "New quote",
    icon: "/static/icons/icon-192.png",
    badge: "/static/icons/icon-192.png",
    data: {
      url: payload.url || "/",
    },
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const targetUrl = event.notification?.data?.url || "/";
  event.waitUntil(
    clients
      .matchAll({ type: "window", includeUncontrolled: true })
      .then((clientList) => {
        for (const client of clientList) {
          if (client.url === targetUrl && "focus" in client) {
            return client.focus();
          }
        }
        if (clients.openWindow) {
          return clients.openWindow(targetUrl);
        }
        return null;
      })
  );
});

async function cacheFirst(request, cacheName) {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(request);
  if (cached) return cached;

  const response = await fetch(request);
  if (response && response.status === 200) {
    cache.put(request, response.clone());
  }
  return response;
}

async function getFallbackResponse(fallbackUrl) {
  if (!fallbackUrl) return null;

  const runtimeCache = await caches.open(RUNTIME);
  let fallback = await runtimeCache.match(fallbackUrl);
  if (fallback) return fallback;

  const precache = await caches.open(PRECACHE);
  fallback = await precache.match(fallbackUrl);
  if (fallback) return fallback;

  return null;
}

async function networkFirst(request, cacheName, fallbackUrl) {
  const cache = await caches.open(cacheName);
  try {
    const response = await fetch(request);
    if (response && response.status === 200) {
      cache.put(request, response.clone());
    }
    return response;
  } catch (err) {
    const cached = await cache.match(request);
    if (cached) return cached;

    const fallback = await getFallbackResponse(fallbackUrl);
    if (fallback) return fallback;

    return new Response("Offline", {
      status: 503,
      headers: { "Content-Type": "text/plain" },
    });
  }
}

async function staleWhileRevalidate(request, cacheName, fallbackUrl) {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(request);

  const fetchPromise = fetch(request)
    .then((response) => {
      if (response && response.status === 200) {
        cache.put(request, response.clone());
      }
      return response;
    })
    .catch(() => null);

  if (cached) {
    fetchPromise.then(() => undefined);
    return cached;
  }

  const networkResponse = await fetchPromise;
  if (networkResponse) return networkResponse;

  const fallback = await getFallbackResponse(fallbackUrl);
  if (fallback) return fallback;

  return new Response("Offline", {
    status: 503,
    headers: { "Content-Type": "text/plain" },
  });
}
