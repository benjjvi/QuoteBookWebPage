(() => {
  const root = document.documentElement;
  const storageKey = "qb_theme_preference";
  const colorSchemeMedia = window.matchMedia
    ? window.matchMedia("(prefers-color-scheme: dark)")
    : null;

  const themeMeta = document.querySelector('meta[name="theme-color"]');
  const colorSchemeMeta = document.querySelector('meta[name="color-scheme"]');

  const isValidTheme = (value) => value === "light" || value === "dark";

  const getStoredTheme = () => {
    try {
      return localStorage.getItem(storageKey);
    } catch (_err) {
      return null;
    }
  };

  const setStoredTheme = (theme) => {
    try {
      if (isValidTheme(theme)) {
        localStorage.setItem(storageKey, theme);
      } else {
        localStorage.removeItem(storageKey);
      }
    } catch (_err) {
      // Ignore localStorage errors in private browsing modes.
    }
  };

  const getSystemTheme = () => (colorSchemeMedia?.matches ? "dark" : "light");

  const getThemeSource = () =>
    isValidTheme(getStoredTheme()) ? "manual" : "system";

  const resolveTheme = () => {
    const stored = getStoredTheme();
    return isValidTheme(stored) ? stored : getSystemTheme();
  };

  const readThemeColor = () => {
    const cssColor = getComputedStyle(root)
      .getPropertyValue("--qb-theme-color")
      .trim();
    if (cssColor) return cssColor;
    return root.dataset.theme === "dark" ? "#22140d" : "#eef8ff";
  };

  const syncThemeMeta = () => {
    const theme = root.dataset.theme === "dark" ? "dark" : "light";
    root.style.colorScheme = theme;
    if (colorSchemeMeta) {
      colorSchemeMeta.setAttribute("content", theme);
    }
    if (themeMeta) {
      themeMeta.setAttribute("content", readThemeColor());
    }
  };

  const syncThemeToggles = () => {
    const theme = root.dataset.theme === "dark" ? "dark" : "light";
    const source = root.dataset.themeSource || getThemeSource();

    document.querySelectorAll("[data-theme-toggle]").forEach((toggle) => {
      const nextTheme = theme === "dark" ? "light" : "dark";
      const stateEl = toggle.querySelector("[data-theme-toggle-state]");
      const sourceEl = toggle.querySelector("[data-theme-toggle-source]");

      if (stateEl) {
        stateEl.textContent = theme === "dark" ? "Dark mode" : "Light mode";
      }
      if (sourceEl) {
        sourceEl.textContent = source === "manual" ? "Manual" : "System";
      }

      toggle.setAttribute("aria-label", `Switch to ${nextTheme} mode`);
      toggle.setAttribute("aria-pressed", String(theme === "dark"));
    });
  };

  const announceThemeChange = () => {
    document.dispatchEvent(
      new CustomEvent("qb-theme-change", {
        detail: {
          theme: root.dataset.theme,
          source: root.dataset.themeSource,
        },
      }),
    );
  };

  const applyTheme = (theme, options = {}) => {
    const normalized = theme === "dark" ? "dark" : "light";
    if (options.persist) {
      setStoredTheme(normalized);
    }

    root.dataset.theme = normalized;
    root.dataset.themeSource = getThemeSource();

    syncThemeMeta();
    syncThemeToggles();
    announceThemeChange();
  };

  const cycleTheme = () => {
    const current = root.dataset.theme === "dark" ? "dark" : "light";
    const nextTheme = current === "dark" ? "light" : "dark";
    applyTheme(nextTheme, { persist: true });
  };

  const resetThemeToSystem = () => {
    setStoredTheme(null);
    applyTheme(getSystemTheme());
  };

  const bindThemeToggles = () => {
    document.querySelectorAll("[data-theme-toggle]").forEach((toggle) => {
      if (toggle.dataset.themeBound === "true") return;
      toggle.dataset.themeBound = "true";
      toggle.addEventListener("click", (event) => {
        if (event.altKey || event.shiftKey || event.metaKey) {
          resetThemeToSystem();
          return;
        }
        cycleTheme();
      });
    });
  };

  if (!isValidTheme(root.dataset.theme)) {
    root.dataset.theme = resolveTheme();
  }
  root.dataset.themeSource = getThemeSource();

  bindThemeToggles();
  syncThemeMeta();
  syncThemeToggles();

  if (colorSchemeMedia && typeof colorSchemeMedia.addEventListener === "function") {
    colorSchemeMedia.addEventListener("change", () => {
      if (getThemeSource() === "system") {
        applyTheme(getSystemTheme());
      }
    });
  } else if (colorSchemeMedia && typeof colorSchemeMedia.addListener === "function") {
    colorSchemeMedia.addListener(() => {
      if (getThemeSource() === "system") {
        applyTheme(getSystemTheme());
      }
    });
  }
})();
