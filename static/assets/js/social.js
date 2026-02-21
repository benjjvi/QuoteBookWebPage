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
  if (!avatarUrls.length) return;

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

  let avatarOrder = parseStoredArray(readStorage(ORDER_KEY)).filter((avatarPath) =>
    avatarUrls.includes(avatarPath)
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

  const assignAvatar = (authorName) => {
    const normalized = normalizeAuthor(authorName);
    if (!normalized) return avatarOrder[0];
    if (avatarMap[normalized]) {
      return avatarMap[normalized];
    }

    const nextAvatar = avatarOrder[cursor % avatarOrder.length];
    avatarMap[normalized] = nextAvatar;
    cursor += 1;
    return nextAvatar;
  };

  allAuthors.forEach((authorName) => assignAvatar(authorName));

  document.querySelectorAll("[data-social-author]").forEach((node) => {
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

  writeStorage(MAP_KEY, JSON.stringify(avatarMap));
  writeStorage(CURSOR_KEY, String(cursor));
})();
