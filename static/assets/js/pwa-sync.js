(() => {
  const DB_NAME = "quote_book";
  const DB_VERSION = 1;
  const QUOTES_STORE = "quotes";
  const META_STORE = "meta";
  const SYNC_LOCK_KEY = "qb_sync_lock";
  const SYNC_LOCK_TTL_MS = 2 * 60 * 1000;
  const PER_PAGE = 100;
  const SYNC_DELAY_MS = 300;

  let inMemorySync = false;

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

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

      const localCount = await countQuotes();
      const lastPage = (await getMeta("sync_last_page")) || 0;

      await setMeta("sync_complete", false);

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
      // Swallow errors; sync will retry on next load.
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
      const [count, complete, total, lastRun] = await Promise.all([
        countQuotes(),
        getMeta("sync_complete"),
        getMeta("sync_total"),
        getMeta("sync_last_run"),
      ]);
      return {
        count,
        complete: Boolean(complete),
        total: total ? Number(total) : null,
        lastRun: lastRun ? Number(lastRun) : null,
      };
    } catch (err) {
      return null;
    }
  };

  const getOfflineQuotePage = async ({
    speaker,
    order,
    page,
    perPage,
  } = {}) => {
    try {
      const normalizedOrder = (order || "oldest").trim().toLowerCase();
      const reverseSort = normalizedOrder === "newest";
      const normalizedSpeaker = (speaker || "").trim().toLowerCase();
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

  window.qbPwa = {
    syncQuotes: runSync,
    getLocalStats,
    getOfflineQuotePage,
    getOfflineQuoteById,
    getRandomOfflineQuote,
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
