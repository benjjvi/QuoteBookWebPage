(() => {
  const DB_NAME = "quote_book";
  const DB_VERSION = 1;
  const QUOTES_STORE = "quotes";
  const META_STORE = "meta";
  const SYNC_LOCK_KEY = "qb_sync_lock";
  const SYNC_LOCK_TTL_MS = 2 * 60 * 1000;
  const SYNC_STALE_MS = 24 * 60 * 60 * 1000;
  const PER_PAGE = 100;
  const SYNC_DELAY_MS = 300;

  let inMemorySync = false;

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const tokenize = (value) => String(value || "").match(/\b\w+\b/g) || [];

  const getConnection = () =>
    navigator.connection ||
    navigator.mozConnection ||
    navigator.webkitConnection;

  const shouldSkipForNetwork = () => {
    const connection = getConnection();
    if (connection?.saveData) return true;
    const slowTypes = ["slow-2g", "2g"];
    if (
      connection?.effectiveType &&
      slowTypes.includes(connection.effectiveType)
    ) {
      return true;
    }
    return false;
  };

  const getLock = () => {
    const raw = localStorage.getItem(SYNC_LOCK_KEY);
    if (!raw) return null;
    const ts = Number(raw);
    if (!Number.isFinite(ts)) return null;
    if (Date.now() - ts > SYNC_LOCK_TTL_MS) {
      localStorage.removeItem(SYNC_LOCK_KEY);
      return null;
    }
    return ts;
  };

  const setLock = () => localStorage.setItem(SYNC_LOCK_KEY, String(Date.now()));
  const clearLock = () => localStorage.removeItem(SYNC_LOCK_KEY);

  const openDb = () =>
    new Promise((resolve, reject) => {
      if (!("indexedDB" in window)) {
        reject(new Error("IndexedDB not supported"));
        return;
      }

      const request = indexedDB.open(DB_NAME, DB_VERSION);
      request.onupgradeneeded = () => {
        const db = request.result;
        if (!db.objectStoreNames.contains(QUOTES_STORE)) {
          db.createObjectStore(QUOTES_STORE, { keyPath: "id" });
        }
        if (!db.objectStoreNames.contains(META_STORE)) {
          db.createObjectStore(META_STORE, { keyPath: "key" });
        }
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });

  const withStore = (storeName, mode, callback) =>
    openDb().then(
      (db) =>
        new Promise((resolve, reject) => {
          const tx = db.transaction(storeName, mode);
          const store = tx.objectStore(storeName);
          const result = callback(store, tx);
          tx.oncomplete = () => resolve(result);
          tx.onerror = () => reject(tx.error);
          tx.onabort = () => reject(tx.error);
        }),
    );

  const getMeta = (key) =>
    withStore(
      META_STORE,
      "readonly",
      (store) =>
        new Promise((resolve, reject) => {
          const req = store.get(key);
          req.onsuccess = () => resolve(req.result ? req.result.value : null);
          req.onerror = () => reject(req.error);
        }),
    );

  const setMeta = (key, value) =>
    withStore(META_STORE, "readwrite", (store) => {
      store.put({ key, value });
    });

  const countQuotes = () =>
    withStore(
      QUOTES_STORE,
      "readonly",
      (store) =>
        new Promise((resolve, reject) => {
          const req = store.count();
          req.onsuccess = () => resolve(req.result || 0);
          req.onerror = () => reject(req.error);
        }),
    );

  const getAllQuotes = () =>
    withStore(
      QUOTES_STORE,
      "readonly",
      (store) =>
        new Promise((resolve, reject) => {
          const req = store.getAll();
          req.onsuccess = () => resolve(req.result || []);
          req.onerror = () => reject(req.error);
        }),
    );

  const getQuoteById = (id) =>
    withStore(
      QUOTES_STORE,
      "readonly",
      (store) =>
        new Promise((resolve, reject) => {
          const req = store.get(Number(id));
          req.onsuccess = () => resolve(req.result || null);
          req.onerror = () => reject(req.error);
        }),
    );

  const putQuotes = (quotes) =>
    withStore(QUOTES_STORE, "readwrite", (store) => {
      quotes.forEach((quote) => store.put(quote));
    });

  const clearQuotesStore = () =>
    withStore(QUOTES_STORE, "readwrite", (store) => store.clear());

  const resetSyncMeta = async () => {
    await Promise.all([
      setMeta("sync_total", null),
      setMeta("sync_total_pages", null),
      setMeta("sync_last_page", 0),
      setMeta("sync_complete", false),
      setMeta("sync_last_error", null),
    ]);
  };

  const fetchPage = async (page) => {
    const url = `/api/quotes?page=${page}&per_page=${PER_PAGE}&order=oldest`;
    const response = await fetch(url, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    if (!response.ok) {
      throw new Error("Failed to fetch quotes");
    }
    return response.json();
  };

  const runSync = async () => {
    if (!navigator.onLine || shouldSkipForNetwork()) return;
    if (inMemorySync || getLock()) return;

    inMemorySync = true;
    setLock();

    try {
      await openDb();

      let localCount = await countQuotes();
      let lastPage = (await getMeta("sync_last_page")) || 0;
      const lastRun = Number((await getMeta("sync_last_run")) || 0);
      const cacheIsStale = lastRun
        ? Date.now() - lastRun > SYNC_STALE_MS
        : false;

      await setMeta("sync_complete", false);
      await setMeta("sync_last_error", null);

      if (cacheIsStale && localCount > 0) {
        await clearQuotesStore();
        await resetSyncMeta();
        localCount = 0;
        lastPage = 0;
      }

      const firstPayload = await fetchPage(1);
      const totalPages = Number(firstPayload.total_pages || 1);
      const totalQuotes = Number(firstPayload.total || 0);

      await putQuotes(firstPayload.quotes || []);
      await setMeta("sync_total", totalQuotes);
      await setMeta("sync_total_pages", totalPages);
      await setMeta("sync_last_page", Math.max(lastPage, 1));
      await setMeta("sync_last_run", Date.now());

      if (totalPages <= lastPage) {
        if (localCount >= totalQuotes) {
          await setMeta("sync_complete", true);
          return;
        }

        const pageToRefresh = Math.max(1, totalPages);
        setLock();
        await sleep(SYNC_DELAY_MS);
        const payload = await fetchPage(pageToRefresh);
        await putQuotes(payload.quotes || []);
        await setMeta("sync_last_page", pageToRefresh);
        await setMeta("sync_last_run", Date.now());

        const refreshedCount = await countQuotes();
        if (refreshedCount >= totalQuotes) {
          await setMeta("sync_complete", true);
          return;
        }

        await clearQuotesStore();
        await resetSyncMeta();

        await putQuotes(firstPayload.quotes || []);
        await setMeta("sync_total", totalQuotes);
        await setMeta("sync_total_pages", totalPages);
        await setMeta("sync_last_page", 1);
        await setMeta("sync_last_run", Date.now());

        for (let page = 2; page <= totalPages; page += 1) {
          setLock();
          await sleep(SYNC_DELAY_MS);
          const pagePayload = await fetchPage(page);
          await putQuotes(pagePayload.quotes || []);
          await setMeta("sync_last_page", page);
          await setMeta("sync_last_run", Date.now());
        }

        await setMeta("sync_complete", true);
        return;
      }

      let startPage = Math.max(2, lastPage + 1);
      if (lastPage < 1) startPage = 2;

      for (let page = startPage; page <= totalPages; page += 1) {
        setLock();
        await sleep(SYNC_DELAY_MS);
        const payload = await fetchPage(page);
        await putQuotes(payload.quotes || []);
        await setMeta("sync_last_page", page);
        await setMeta("sync_last_run", Date.now());
      }

      await setMeta("sync_complete", true);
    } catch (err) {
      try {
        await setMeta(
          "sync_last_error",
          err?.message ? String(err.message) : "Sync failed",
        );
      } catch (_writeErr) {
        // Ignore IndexedDB write failures; sync can retry on next load.
      }
    } finally {
      clearLock();
      inMemorySync = false;
    }
  };

  const scheduleSync = () => {
    if (!navigator.onLine || shouldSkipForNetwork()) return;
    if ("requestIdleCallback" in window) {
      requestIdleCallback(() => runSync());
    } else {
      setTimeout(() => runSync(), 1500);
    }
  };

  const getLocalStats = async () => {
    try {
      const [count, complete, total, lastRun, lastError] = await Promise.all([
        countQuotes(),
        getMeta("sync_complete"),
        getMeta("sync_total"),
        getMeta("sync_last_run"),
        getMeta("sync_last_error"),
      ]);
      return {
        count,
        complete: Boolean(complete),
        total: total ? Number(total) : null,
        lastRun: lastRun ? Number(lastRun) : null,
        stale: lastRun ? Date.now() - Number(lastRun) > SYNC_STALE_MS : false,
        lastError: lastError ? String(lastError) : "",
      };
    } catch (err) {
      return null;
    }
  };

  const getOfflineQuotePage = async ({
    speaker,
    tag,
    order,
    page,
    perPage,
  } = {}) => {
    try {
      const normalizedOrder = (order || "oldest").trim().toLowerCase();
      const reverseSort = normalizedOrder === "newest";
      const normalizedSpeaker = (speaker || "").trim().toLowerCase();
      const normalizedTag = (tag || "").trim().toLowerCase();
      const pageSize = Number(perPage) > 0 ? Number(perPage) : 9;

      let quotes = await getAllQuotes();
      if (normalizedSpeaker) {
        quotes = quotes.filter((q) =>
          Array.isArray(q.authors)
            ? q.authors.some(
                (author) =>
                  String(author).trim().toLowerCase() === normalizedSpeaker,
              )
            : false,
        );
      }
      if (normalizedTag) {
        quotes = quotes.filter((q) =>
          Array.isArray(q.tags)
            ? q.tags.some(
                (item) => String(item).trim().toLowerCase() === normalizedTag,
              )
            : false,
        );
      }

      quotes.sort((a, b) => {
        const aKey = Number(a.timestamp) || 0;
        const bKey = Number(b.timestamp) || 0;
        if (aKey === bKey) {
          const aId = Number(a.id) || 0;
          const bId = Number(b.id) || 0;
          return reverseSort ? bId - aId : aId - bId;
        }
        return reverseSort ? bKey - aKey : aKey - bKey;
      });

      const total = quotes.length;
      const totalPages = Math.max(1, Math.ceil(total / pageSize));
      const safePage = Math.min(Math.max(Number(page) || 1, 1), totalPages);
      const start = (safePage - 1) * pageSize;
      const end = start + pageSize;

      return {
        quotes: quotes.slice(start, end),
        total,
        totalPages,
        page: safePage,
        perPage: pageSize,
      };
    } catch (err) {
      return null;
    }
  };

  const getOfflineQuoteById = async (id) => {
    try {
      if (!Number.isFinite(Number(id))) return null;
      const quote = await getQuoteById(Number(id));
      return quote || null;
    } catch (err) {
      return null;
    }
  };

  const getRandomOfflineQuote = async () => {
    try {
      const quotes = await getAllQuotes();
      if (!quotes.length) return null;
      const index = Math.floor(Math.random() * quotes.length);
      return quotes[index] || null;
    } catch (err) {
      return null;
    }
  };

  const searchOfflineQuotes = async (query, { limit, tag } = {}) => {
    try {
      const normalizedQuery = String(query || "").trim().toLowerCase();
      const normalizedTag = String(tag || "").trim().toLowerCase();
      if (!normalizedQuery && !normalizedTag) return [];

      const queryTokens = tokenize(normalizedQuery);
      const queryTokenSet = new Set(queryTokens);
      const quotes = await getAllQuotes();
      const scoredResults = [];

      quotes.forEach((quote) => {
        if (
          normalizedTag &&
          (!Array.isArray(quote.tags) ||
            !quote.tags.some(
              (item) => String(item).trim().toLowerCase() === normalizedTag,
            ))
        ) {
          return;
        }

        const quoteText = String(quote.quote || "").toLowerCase();
        const authorsText = Array.isArray(quote.authors)
          ? quote.authors.join(" ").toLowerCase()
          : "";
        const contextText = String(quote.context || "").toLowerCase();
        const tagsText = Array.isArray(quote.tags)
          ? quote.tags.join(" ").toLowerCase()
          : "";

        let score = 0;

        if (normalizedQuery) {
          if (quoteText.includes(normalizedQuery)) score += 8;
          if (authorsText.includes(normalizedQuery)) score += 10;
          if (contextText.includes(normalizedQuery)) score += 5;
          if (tagsText.includes(normalizedQuery)) score += 6;
        }

        const quoteTokens = new Set(tokenize(quoteText));
        const authorTokens = new Set(tokenize(authorsText));
        const contextTokens = new Set(tokenize(contextText));
        const tagTokens = new Set(tokenize(tagsText));

        if (queryTokens.length) {
          queryTokens.forEach((token) => {
            if (quoteTokens.has(token)) score += 2;
            if (authorTokens.has(token)) score += 3;
            if (contextTokens.has(token)) score += 1;
            if (tagTokens.has(token)) score += 2;
          });
        }

        if (
          queryTokenSet.size &&
          Array.from(queryTokenSet).every(
            (token) =>
              quoteTokens.has(token) ||
              authorTokens.has(token) ||
              contextTokens.has(token) ||
              tagTokens.has(token),
          )
        ) {
          score += 3;
        }

        if (!normalizedQuery && normalizedTag) {
          score += 1;
        }

        if (score > 0) scoredResults.push([score, quote]);
      });

      scoredResults.sort((a, b) => {
        if (b[0] !== a[0]) return b[0] - a[0];
        const tsA = Number(a[1]?.timestamp) || 0;
        const tsB = Number(b[1]?.timestamp) || 0;
        if (tsB !== tsA) return tsB - tsA;
        const idA = Number(a[1]?.id) || 0;
        const idB = Number(b[1]?.id) || 0;
        return idA - idB;
      });

      const matches = scoredResults.map(([, quote]) => quote);
      const maxResults = Number(limit);
      if (Number.isFinite(maxResults) && maxResults > 0) {
        return matches.slice(0, maxResults);
      }
      return matches;
    } catch (err) {
      return [];
    }
  };

  window.qbPwa = {
    syncQuotes: runSync,
    getLocalStats,
    getOfflineQuotePage,
    getOfflineQuoteById,
    getRandomOfflineQuote,
    searchOfflineQuotes,
  };

  window.addEventListener("online", scheduleSync);
  if (
    document.readyState === "complete" ||
    document.readyState === "interactive"
  ) {
    scheduleSync();
  } else {
    window.addEventListener("load", scheduleSync);
  }
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) scheduleSync();
  });
})();
