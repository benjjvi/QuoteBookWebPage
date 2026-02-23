(() => {
  const configElement = document.getElementById("socialConfig");
  if (!configElement) return;

  let config = {};
  try {
    config = JSON.parse(configElement.textContent || "{}");
  } catch (_error) {
    config = {};
  }

  const avatarUrls = Array.isArray(config.avatarUrls)
    ? config.avatarUrls.filter((value) => typeof value === "string" && value.length > 0)
    : [];
  const allAuthors = Array.isArray(config.allAuthors)
    ? config.allAuthors.filter((value) => typeof value === "string" && value.trim().length > 0)
    : [];
  const feedMeta = config.feed && typeof config.feed === "object" ? { ...config.feed } : null;
  const hasAvatars = avatarUrls.length > 0;

  const MAP_KEY = "qb_social_avatar_map_v1";
  const ORDER_KEY = "qb_social_avatar_order_v1";
  const CURSOR_KEY = "qb_social_avatar_cursor_v1";

  const readStorage = (key) => {
    try {
      return window.sessionStorage.getItem(key);
    } catch (_error) {
      return null;
    }
  };

  const writeStorage = (key, value) => {
    try {
      window.sessionStorage.setItem(key, value);
    } catch (_error) {
      return;
    }
  };

  const normalizeAuthor = (author) => (author || "").trim().toLowerCase();

  const shuffle = (items) => {
    const cloned = items.slice();
    for (let index = cloned.length - 1; index > 0; index -= 1) {
      const swapIndex = Math.floor(Math.random() * (index + 1));
      const temp = cloned[index];
      cloned[index] = cloned[swapIndex];
      cloned[swapIndex] = temp;
    }
    return cloned;
  };

  const parseStoredObject = (rawValue) => {
    if (!rawValue) return {};
    try {
      const parsed = JSON.parse(rawValue);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        return {};
      }
      return parsed;
    } catch (_error) {
      return {};
    }
  };

  const parseStoredArray = (rawValue) => {
    if (!rawValue) return [];
    try {
      const parsed = JSON.parse(rawValue);
      return Array.isArray(parsed) ? parsed : [];
    } catch (_error) {
      return [];
    }
  };

  let assignAvatar = () => null;
  if (hasAvatars) {
    let avatarOrder = parseStoredArray(readStorage(ORDER_KEY)).filter((avatarPath) =>
      avatarUrls.includes(avatarPath),
    );
    if (avatarOrder.length !== avatarUrls.length) {
      avatarOrder = shuffle(avatarUrls);
      writeStorage(ORDER_KEY, JSON.stringify(avatarOrder));
    }

    const storedMap = parseStoredObject(readStorage(MAP_KEY));
    const avatarMap = {};
    Object.entries(storedMap).forEach(([authorKey, avatarPath]) => {
      if (typeof avatarPath === "string" && avatarOrder.includes(avatarPath)) {
        avatarMap[authorKey] = avatarPath;
      }
    });

    let cursor = Number.parseInt(readStorage(CURSOR_KEY) || "0", 10);
    if (!Number.isFinite(cursor) || cursor < 0) {
      cursor = 0;
    }

    assignAvatar = (authorName) => {
      const normalized = normalizeAuthor(authorName);
      if (!normalized) return avatarOrder[0];
      if (avatarMap[normalized]) return avatarMap[normalized];

      const nextAvatar = avatarOrder[cursor % avatarOrder.length];
      avatarMap[normalized] = nextAvatar;
      cursor += 1;
      return nextAvatar;
    };

    allAuthors.forEach((authorName) => assignAvatar(authorName));

    const persist = () => {
      writeStorage(MAP_KEY, JSON.stringify(avatarMap));
      writeStorage(CURSOR_KEY, String(cursor));
    };
    persist();

    const applyAvatars = (root = document) => {
      root.querySelectorAll("[data-social-author]").forEach((node) => {
        const authorName = node.getAttribute("data-social-author") || "";
        const avatarPath = assignAvatar(authorName);
        if (!avatarPath) return;

        if (node.tagName === "IMG") {
          node.src = avatarPath;
          if (!node.alt) {
            node.alt = authorName ? `${authorName} profile picture` : "Profile picture";
          }
          return;
        }

        node.style.backgroundImage = `url("${avatarPath}")`;
      });
      persist();
    };

    applyAvatars(document);
    window.qbApplySocialAvatars = applyAvatars;
  }

  const isInteractiveTarget = (target) => {
    if (!(target instanceof Element)) return false;
    return Boolean(target.closest("a, button, input, textarea, select, label, form"));
  };

  document.addEventListener("click", (event) => {
    const card = event.target.closest(".post-card-clickable[data-post-url]");
    if (!card || isInteractiveTarget(event.target)) return;
    const targetUrl = card.getAttribute("data-post-url");
    if (!targetUrl) return;
    window.location.assign(targetUrl);
  });

  document.addEventListener("keydown", (event) => {
    const card = event.target.closest(".post-card-clickable[data-post-url]");
    if (!card) return;
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    const targetUrl = card.getAttribute("data-post-url");
    if (!targetUrl) return;
    window.location.assign(targetUrl);
  });

  const feedList = document.getElementById("socialFeedList");
  const feedSentinel = document.getElementById("socialFeedSentinel");
  const feedStatus = document.getElementById("socialFeedStatus");
  if (!feedMeta || !feedList || !feedSentinel || !feedStatus) return;

  let currentPage = Number(feedMeta.page || 1);
  const perPage = Number(feedMeta.per_page || 12);
  let hasMore = Boolean(feedMeta.has_more);
  let loading = false;

  const escapeHtml = (value) => {
    const div = document.createElement("div");
    div.textContent = value ?? "";
    return div.innerHTML;
  };

  const formatDate = (epochSeconds) => {
    const date = new Date((Number(epochSeconds) || 0) * 1000);
    return new Intl.DateTimeFormat("en-GB", {
      timeZone: "Europe/London",
      day: "2-digit",
      month: "long",
      year: "numeric",
    }).format(date);
  };

  const formatTime = (epochSeconds) => {
    const date = new Date((Number(epochSeconds) || 0) * 1000);
    return new Intl.DateTimeFormat("en-GB", {
      timeZone: "Europe/London",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(date);
  };

  const socialBasePath = feedMeta.author
    ? `/social/author/${encodeURIComponent(String(feedMeta.author))}`
    : "/social";

  const buildFeedQuery = ({ page, tag }) => {
    const params = new URLSearchParams();
    if (feedMeta.query) params.set("q", String(feedMeta.query));
    if (feedMeta.author) params.set("author", String(feedMeta.author));
    if (tag) params.set("tag", String(tag));
    params.set("page", String(page));
    params.set("per_page", String(perPage));
    return params;
  };

  const renderTagRow = (tags) => {
    if (!Array.isArray(tags) || !tags.length) return "";
    return `
      <div class="tag-row">
        ${tags
          .map((tag) => {
            const params = new URLSearchParams();
            if (feedMeta.query) params.set("q", String(feedMeta.query));
            params.set("tag", String(tag));
            return `<a href="${socialBasePath}?${params.toString()}" class="tag-chip">#${escapeHtml(tag)}</a>`;
          })
          .join("")}
      </div>
    `;
  };

  const renderFeedItem = (item) => {
    if (!item || typeof item !== "object") return "";
    if (item.kind === "generic" && item.post) {
      return `
        <article class="post-card post-generic">
          <span class="generic-label">${escapeHtml(item.post.title || "Post")}</span>
          <p>${escapeHtml(item.post.body || "")}</p>
        </article>
      `;
    }

    if (item.kind !== "quote" || !item.quote) return "";
    const quote = item.quote;
    const authors = Array.isArray(quote.authors) ? quote.authors : [];
    const authorLinks = authors
      .map(
        (author) =>
          `<a href="/social/author/${encodeURIComponent(String(author))}">${escapeHtml(author)}</a>`,
      )
      .join(", ");
    const context = quote.context
      ? `<p class="quote-context">${escapeHtml(quote.context)}</p>`
      : "";
    const tags = renderTagRow(quote.tags);

    return `
      <article
        class="post-card post-quote post-card-clickable"
        data-post-url="/social/quote/${encodeURIComponent(String(quote.id))}"
        role="link"
        tabindex="0"
        aria-label="Open social post for quote ${encodeURIComponent(String(quote.id))}"
      >
        <header class="post-head">
          <img
            class="avatar"
            src="/static/favicon.png"
            data-social-author="${escapeHtml(item.primary_author || "Unknown")}"
            alt="${escapeHtml(item.primary_author || "Unknown")} profile picture"
          />
          <div class="post-author-wrap">
            <div class="author-links">${authorLinks}</div>
            <p class="post-meta">${formatDate(quote.timestamp)} at ${formatTime(quote.timestamp)}</p>
          </div>
        </header>

        <blockquote class="quote-text">"${escapeHtml(quote.quote || "")}"</blockquote>
        ${context}
        ${tags}

        <footer class="post-foot">
          <a href="/social/quote/${encodeURIComponent(String(quote.id))}">Open social post</a>
          <a href="/quote/${encodeURIComponent(String(quote.id))}">Original quote #${encodeURIComponent(String(quote.id))}</a>
          <a href="/timeline/day/${encodeURIComponent(String(quote.timestamp || 0))}">View day</a>
        </footer>
      </article>
    `;
  };

  const appendFeedItems = (items) => {
    if (!Array.isArray(items) || !items.length) return;
    const html = items.map((item) => renderFeedItem(item)).join("");
    const wrapper = document.createElement("div");
    wrapper.innerHTML = html;
    while (wrapper.firstChild) {
      feedList.appendChild(wrapper.firstChild);
    }
    if (typeof window.qbApplySocialAvatars === "function") {
      window.qbApplySocialAvatars(feedList);
    }
  };

  const updateStatus = () => {
    if (loading) {
      feedStatus.textContent = "Loading more posts...";
      return;
    }
    if (hasMore) {
      feedStatus.textContent = "Scroll for more posts";
      return;
    }
    feedStatus.textContent = "End of stream.";
    feedStatus.dataset.complete = "true";
  };
  updateStatus();

  const loadNextPage = async () => {
    if (!hasMore || loading) return;
    loading = true;
    updateStatus();

    try {
      const params = buildFeedQuery({
        page: currentPage + 1,
        tag: feedMeta.tag || "",
      });
      const response = await fetch(`/api/social/feed?${params.toString()}`, {
        headers: { Accept: "application/json" },
        cache: "no-store",
      });
      if (!response.ok) {
        throw new Error(`Social feed request failed (${response.status})`);
      }
      const payload = await response.json();
      appendFeedItems(payload.items || []);
      currentPage = Number(payload.page || currentPage + 1);
      hasMore = Boolean(payload.has_more);
    } catch (_error) {
      hasMore = false;
      feedStatus.textContent = "Unable to load more posts right now.";
    } finally {
      loading = false;
      updateStatus();
    }
  };

  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        loadNextPage();
      });
    },
    { rootMargin: "350px 0px" },
  );

  observer.observe(feedSentinel);
})();
