const CACHE_VERSION = "v23";
const PRECACHE = `qb-precache-${CACHE_VERSION}`;
const RUNTIME = `qb-runtime-${CACHE_VERSION}`;
const OFFLINE_URL = "/offline";

const PRECACHE_ROUTES = [
  "/",
  "/all_quotes",
  "/random",
  "/search",
  "/stats",
  "/mailbox",
  "/unsubscribe",
  "/support",
  "/advertise",
  "/credits",
  "/privacy",
  "/ai",
  "/add_quote",
  "/edit",
  "/games",
  "/battle",
  "/quote-anarchy",
  "/games/blackline-rush",
  "/games/who-said-it",
  "/social",
  "/pwa",
  OFFLINE_URL,
];

const PRECACHE_ASSETS = [
  "/manifest.webmanifest",
  "/static/favicon.ico",
  "/static/favicon.png",
  "/static/icons/icon-180.png",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
  "/static/assets/css/main.css",
  "/static/assets/css/footer.css",
  "/static/assets/css/design-system.css",
  "/static/assets/css/index.css",
  "/static/assets/css/all-quotes.css",
  "/static/assets/css/quote.css",
  "/static/assets/css/search.css",
  "/static/assets/css/stats.css",
  "/static/assets/css/mailbox.css",
  "/static/assets/css/calendar.css",
  "/static/assets/css/quotes-by-day.css",
  "/static/assets/css/add-quote.css",
  "/static/assets/css/ai.css",
  "/static/assets/css/ai-screenplay.css",
  "/static/assets/css/battle.css",
  "/static/assets/css/games.css",
  "/static/assets/css/quote-anarchy.css",
  "/static/assets/css/blackline-rush.css",
  "/static/assets/css/who-said-it.css",
  "/static/assets/css/edit-quote.css",
  "/static/assets/css/social.css",
  "/static/assets/css/support.css",
  "/static/assets/css/advertise.css",
  "/static/assets/css/monetize.css",
  "/static/assets/css/credits.css",
  "/static/assets/css/privacy.css",
  "/static/assets/css/pwa.css",
  "/static/assets/css/error.css",
  "/static/assets/js/background.js",
  "/static/assets/js/theme.js",
  "/static/assets/js/index.js",
  "/static/assets/js/pwa-sync.js",
  "/static/assets/js/social.js",
  "/static/assets/js/quote-anarchy.js",
  "/static/assets/js/blackline-rush.js",
  "/static/assets/js/who-said-it.js",
  "/static/assets/img/book.svg",
  "/static/assets/img/dice.svg",
  "/static/assets/img/search.svg",
  "/static/assets/img/mailbox.svg",
  "/static/assets/img/stats.svg",
  "/static/assets/img/calendar.svg",
  "/static/assets/img/robot.svg",
  "/static/assets/img/add.svg",
  "/static/assets/img/edit.svg",
  "/static/assets/img/battle.svg",
  "/static/assets/img/games.svg",
  "/static/assets/img/support.svg",
  "/static/assets/img/loading.svg",
];

const PRECACHE_URLS = [...PRECACHE_ROUTES, ...PRECACHE_ASSETS];

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
    event.respondWith(
      networkFirst(request, RUNTIME, OFFLINE_URL, { navigation: true }),
    );
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
      }),
  );
});

async function cacheFirst(request, cacheName) {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(request);
  if (cached) return cached;

  const response = await fetch(request);
  if (response && response.status === 200) {
    await safePut(cache, request, response);
  }
  return response;
}

async function safePut(cache, request, response) {
  try {
    await cache.put(request, response.clone());
  } catch (_err) {
    return;
  }
}

function getNavigationCandidates(request) {
  const url = new URL(request.url);
  const candidates = [request, url.pathname];

  if (url.pathname !== "/" && url.pathname.endsWith("/")) {
    candidates.push(url.pathname.slice(0, -1));
  } else if (url.pathname !== "/") {
    candidates.push(`${url.pathname}/`);
  }

  return candidates;
}

async function getCachedResponse(request, options = {}) {
  const { navigation = false } = options;
  const runtimeCache = await caches.open(RUNTIME);
  const precache = await caches.open(PRECACHE);
  const candidates = navigation ? getNavigationCandidates(request) : [request];
  const matchOptions = navigation ? { ignoreSearch: true } : undefined;

  for (const candidate of candidates) {
    let cached = await runtimeCache.match(candidate, matchOptions);
    if (cached) return cached;
    cached = await precache.match(candidate, matchOptions);
    if (cached) return cached;
  }

  return null;
}

async function getFallbackResponse(fallbackUrl) {
  if (!fallbackUrl) return null;
  const fallbackRequest = new Request(
    new URL(fallbackUrl, self.location.origin).toString(),
  );
  return getCachedResponse(fallbackRequest, { navigation: true });
}

async function networkFirst(request, cacheName, fallbackUrl, options = {}) {
  const { navigation = false } = options;
  const cache = await caches.open(cacheName);
  try {
    const response = await fetch(request);
    if (response && response.status === 200) {
      await safePut(cache, request, response);
    }
    return response;
  } catch (err) {
    const cached = await getCachedResponse(request, { navigation });
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
  const cached = await getCachedResponse(request);

  const fetchPromise = fetch(request)
    .then((response) => {
      if (response && response.status === 200) {
        safePut(cache, request, response);
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
