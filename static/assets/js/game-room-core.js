(() => {
  const parseBootstrapData = (elementId) => {
    const el = document.getElementById(elementId);
    if (!el?.textContent) return {};
    try {
      return JSON.parse(el.textContent);
    } catch (_err) {
      return {};
    }
  };

  const api = async (path, { method = "GET", body } = {}) => {
    const options = {
      method,
      credentials: "same-origin",
      headers: {},
    };
    if (body !== undefined) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    const response = await fetch(path, options);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload?.error || "Request failed.");
    }
    return payload;
  };

  const sanitizeSessionCode = (value) =>
    String(value || "")
      .toUpperCase()
      .replace(/[^A-Z0-9]/g, "")
      .slice(0, 6);

  const bindSessionCodeInput = (inputEl) => {
    if (!inputEl) return;
    inputEl.addEventListener("input", () => {
      inputEl.value = sanitizeSessionCode(inputEl.value);
    });
  };

  const copyText = async (text) => {
    try {
      await navigator.clipboard.writeText(String(text || ""));
      return true;
    } catch (_err) {
      return false;
    }
  };

  const createRoomController = ({
    storageKeys,
    fetchState,
    requestCreate,
    requestJoin,
    requestLeave,
    renderLobbyOrSession,
    renderSession,
    onReset,
    onIdentityChange,
    onIdentityLoaded,
    pollIntervalMs = 2500,
  }) => {
    const state = {
      sessionCode: "",
      playerId: "",
      playerName: "",
      lastPayload: null,
      refreshPending: false,
      actionPending: false,
      pollingTimer: null,
    };

    const saveIdentity = () => {
      try {
        localStorage.setItem(storageKeys.sessionCode, state.sessionCode);
        localStorage.setItem(storageKeys.playerId, state.playerId);
        localStorage.setItem(storageKeys.playerName, state.playerName);
      } catch (_err) {
        // Ignore storage failures (private mode/quota/etc.).
      }
    };

    const clearIdentity = () => {
      try {
        localStorage.removeItem(storageKeys.sessionCode);
        localStorage.removeItem(storageKeys.playerId);
        localStorage.removeItem(storageKeys.playerName);
      } catch (_err) {
        // Ignore storage failures.
      }
    };

    const stopPolling = () => {
      if (!state.pollingTimer) return;
      window.clearInterval(state.pollingTimer);
      state.pollingTimer = null;
    };

    const startPolling = () => {
      stopPolling();
      state.pollingTimer = window.setInterval(() => {
        refresh();
      }, pollIntervalMs);
    };

    const applyIdentity = (payload) => {
      state.sessionCode = String(payload?.session_code || "").trim();
      state.playerId = String(payload?.player_id || "").trim();
      state.playerName = String(payload?.display_name || "").trim();
      saveIdentity();
      if (onIdentityChange) {
        onIdentityChange({
          sessionCode: state.sessionCode,
          playerId: state.playerId,
          playerName: state.playerName,
        });
      }
    };

    const reset = (message = "") => {
      stopPolling();
      state.sessionCode = "";
      state.playerId = "";
      state.playerName = "";
      state.lastPayload = null;
      clearIdentity();
      if (renderLobbyOrSession) renderLobbyOrSession(false);
      if (onIdentityChange) {
        onIdentityChange({ sessionCode: "", playerId: "", playerName: "" });
      }
      if (onReset) onReset(message);
    };

    const refresh = async () => {
      if (state.refreshPending) return state.lastPayload;
      if (!state.sessionCode || !state.playerId) return state.lastPayload;
      state.refreshPending = true;
      try {
        const payload = await fetchState({
          sessionCode: state.sessionCode,
          playerId: state.playerId,
        });
        state.lastPayload = payload;
        if (renderLobbyOrSession) renderLobbyOrSession(true);
        if (renderSession) renderSession(payload);
        if (payload?.session && payload.session.is_active === false) {
          stopPolling();
        }
        return payload;
      } catch (err) {
        reset(String(err?.message || "Session disconnected."));
        throw err;
      } finally {
        state.refreshPending = false;
      }
    };

    const create = async ({ playerName, ...options }) => {
      const payload = await requestCreate({
        playerName: String(playerName || "").trim(),
        ...options,
      });
      applyIdentity(payload);
      if (renderLobbyOrSession) renderLobbyOrSession(true);
      await refresh();
      startPolling();
      return payload;
    };

    const join = async ({ sessionCode, playerName, playerId, ...options }) => {
      const payload = await requestJoin({
        sessionCode: String(sessionCode || "").trim(),
        playerName: String(playerName || "").trim(),
        playerId: String(playerId || "").trim() || undefined,
        ...options,
      });
      applyIdentity(payload);
      if (renderLobbyOrSession) renderLobbyOrSession(true);
      await refresh();
      startPolling();
      return payload;
    };

    const tryResume = async () => {
      let savedCode = "";
      let savedPlayerId = "";
      let savedPlayerName = "";
      try {
        savedCode = String(localStorage.getItem(storageKeys.sessionCode) || "").trim();
        savedPlayerId = String(localStorage.getItem(storageKeys.playerId) || "").trim();
        savedPlayerName = String(localStorage.getItem(storageKeys.playerName) || "").trim();
      } catch (_err) {
        if (renderLobbyOrSession) renderLobbyOrSession(false);
        return false;
      }

      if (!savedCode || !savedPlayerId) {
        if (renderLobbyOrSession) renderLobbyOrSession(false);
        return false;
      }

      state.sessionCode = savedCode;
      state.playerId = savedPlayerId;
      state.playerName = savedPlayerName;
      if (onIdentityLoaded) {
        onIdentityLoaded({
          sessionCode: savedCode,
          playerId: savedPlayerId,
          playerName: savedPlayerName,
        });
      }

      try {
        await refresh();
        if (state.lastPayload?.session?.is_active !== false) {
          startPolling();
        }
        return true;
      } catch (_err) {
        return false;
      }
    };

    const leave = async ({ message = "You left the room.", swallowErrors = true } = {}) => {
      if (state.sessionCode && state.playerId && requestLeave) {
        try {
          await requestLeave({
            sessionCode: state.sessionCode,
            playerId: state.playerId,
          });
        } catch (err) {
          if (!swallowErrors) {
            throw err;
          }
        }
      }
      reset(message);
    };

    const withPending = async (fn) => {
      if (state.actionPending) return;
      state.actionPending = true;
      try {
        return await fn();
      } finally {
        state.actionPending = false;
      }
    };

    return {
      getState: () => ({ ...state }),
      refresh,
      create,
      join,
      tryResume,
      leave,
      reset,
      startPolling,
      stopPolling,
      withPending,
    };
  };

  window.GameRoomCore = {
    parseBootstrapData,
    api,
    sanitizeSessionCode,
    bindSessionCodeInput,
    copyText,
    createRoomController,
  };
})();
