(() => {
  const bootstrapEl = document.getElementById("blrBootstrapData");
  let bootstrap = {};
  if (bootstrapEl?.textContent) {
    try {
      bootstrap = JSON.parse(bootstrapEl.textContent);
    } catch (_err) {
      bootstrap = {};
    }
  }

  const STORAGE_KEYS = {
    sessionCode: "blr_session_code",
    playerId: "blr_player_id",
    playerName: "blr_player_name",
  };

  const els = {
    locked: document.getElementById("blrLocked"),
    main: document.getElementById("blrMain"),
    minWords: document.getElementById("blrMinWords"),
    eligibleCount: document.getElementById("blrEligibleCount"),

    createForm: document.getElementById("blrCreateForm"),
    joinForm: document.getElementById("blrJoinForm"),
    createName: document.getElementById("blrCreateName"),
    joinName: document.getElementById("blrJoinName"),
    joinCode: document.getElementById("blrJoinCode"),
    lobbyView: document.getElementById("blrLobbyView"),
    lobbyMessage: document.getElementById("blrLobbyMessage"),

    sessionView: document.getElementById("blrSessionView"),
    sessionTitle: document.getElementById("blrSessionTitle"),
    sessionMeta: document.getElementById("blrSessionMeta"),
    sessionNotice: document.getElementById("blrSessionNotice"),
    playersList: document.getElementById("blrPlayersList"),
    puzzle: document.getElementById("blrPuzzle"),
    pdfMetaRight: document.getElementById("blrPdfMetaRight"),

    turnHeading: document.getElementById("blrTurnHeading"),
    turnStatus: document.getElementById("blrTurnStatus"),

    startBtn: document.getElementById("blrStartBtn"),
    endTurnBtn: document.getElementById("blrEndTurnBtn"),
    nextTurnBtn: document.getElementById("blrNextTurnBtn"),
    turnActions: document.getElementById("blrTurnActions"),
    mobileTip: document.getElementById("blrMobileTip"),

    redactorWrap: document.getElementById("blrRedactorWrap"),
    redactionHint: document.getElementById("blrRedactionHint"),
    redactionCount: document.getElementById("blrRedactionCount"),
    redactionWords: document.getElementById("blrRedactionWords"),
    submitRedactionBtn: document.getElementById("blrSubmitRedactionBtn"),

    guessWrap: document.getElementById("blrGuessWrap"),
    guessHint: document.getElementById("blrGuessHint"),
    guessForm: document.getElementById("blrGuessForm"),
    guessFields: document.getElementById("blrGuessFields"),
    guessSubmitBtn: document.getElementById("blrGuessSubmitBtn"),

    solverList: document.getElementById("blrSolverList"),
    revealWrap: document.getElementById("blrRevealWrap"),
    revealAnswers: document.getElementById("blrRevealAnswers"),

    endBtn: document.getElementById("blrEndBtn"),
    copyCodeBtn: document.getElementById("blrCopyCodeBtn"),
    leaveBtn: document.getElementById("blrLeaveBtn"),
  };

  const state = {
    sessionCode: "",
    playerId: "",
    playerName: "",
    pending: false,
    pollingTimer: null,
    selectedTurn: 0,
    selectedRedactions: [],
    lastState: null,
  };

  const escapeHtml = (value) =>
    String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");

  const isMobileViewport = () => window.matchMedia("(max-width: 720px)").matches;

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

  const setLobbyMessage = (message) => {
    if (!els.lobbyMessage) return;
    els.lobbyMessage.textContent = message || "";
  };

  const saveSessionIdentity = () => {
    localStorage.setItem(STORAGE_KEYS.sessionCode, state.sessionCode);
    localStorage.setItem(STORAGE_KEYS.playerId, state.playerId);
    localStorage.setItem(STORAGE_KEYS.playerName, state.playerName);
  };

  const clearSessionIdentity = () => {
    localStorage.removeItem(STORAGE_KEYS.sessionCode);
    localStorage.removeItem(STORAGE_KEYS.playerId);
    localStorage.removeItem(STORAGE_KEYS.playerName);
  };

  const renderReadyState = () => {
    const ready = Boolean(bootstrap.ready);
    if (els.minWords)
      els.minWords.textContent = String(bootstrap.min_words_for_quote || 10);
    if (els.eligibleCount)
      els.eligibleCount.textContent = String(bootstrap.eligible_quote_count || 0);
    if (els.locked) els.locked.hidden = ready;
    if (els.main) els.main.hidden = !ready;
  };

  const renderLobbyOrSession = (inSession) => {
    if (els.lobbyView) els.lobbyView.hidden = inSession;
    if (els.sessionView) els.sessionView.hidden = !inSession;
  };

  const stopPolling = () => {
    if (state.pollingTimer) {
      window.clearInterval(state.pollingTimer);
      state.pollingTimer = null;
    }
  };

  const startPolling = () => {
    stopPolling();
    state.pollingTimer = window.setInterval(() => {
      refreshSessionState();
    }, 2500);
  };

  const resetSessionState = (message) => {
    stopPolling();
    state.sessionCode = "";
    state.playerId = "";
    state.lastState = null;
    state.selectedTurn = 0;
    state.selectedRedactions = [];
    clearSessionIdentity();
    renderLobbyOrSession(false);
    if (message) setLobbyMessage(message);
  };

  const renderPlayers = (payload) => {
    if (!els.playersList) return;
    const hostId = payload.session.host_player_id;
    const redactorId = payload.session.redactor_player_id;
    const viewerId = payload.viewer.player_id;

    els.playersList.innerHTML = payload.players
      .map((player) => {
        const tags = [];
        if (player.player_id === viewerId) tags.push("You");
        if (player.player_id === hostId) tags.push("Host");
        if (player.player_id === redactorId && payload.session.status !== "waiting") {
          tags.push("Redactor");
        }
        return `
          <li class="blr-player">
            <strong>${escapeHtml(player.display_name)}<span class="tag">${escapeHtml(tags.join(" · ") || "Player")}</span></strong>
            <span>${player.score} pt${player.score === 1 ? "" : "s"}</span>
          </li>
        `;
      })
      .join("");
  };

  const renderPuzzleMarkup = (puzzleText) => {
    const escaped = escapeHtml(puzzleText || "");
    return escaped.replace(
      /\[\[([^[\]]+)\]\]/g,
      (_match, token) => `<span class="blr-redaction">${escapeHtml(token)}</span>`,
    );
  };

  const syncTurnActionsVisibility = () => {
    if (!els.turnActions) return;
    const hasVisibleAction = [els.startBtn, els.endTurnBtn, els.nextTurnBtn].some(
      (button) => button && !button.hidden,
    );
    els.turnActions.hidden = !hasVisibleAction;
  };

  const renderSolvers = (payload) => {
    if (!els.solverList) return;
    const solvers = Array.isArray(payload.turn.solvers) ? payload.turn.solvers : [];
    if (!solvers.length) {
      els.solverList.innerHTML =
        "<li><span>No correct guesses yet</span><span>—</span></li>";
      return;
    }
    els.solverList.innerHTML = solvers
      .map(
        (solver) => `
          <li>
            <span>#${solver.rank} ${escapeHtml(solver.player_name)}</span>
            <span>+${solver.points_awarded}</span>
          </li>
        `,
      )
      .join("");
  };

  const updateRedactionControls = (payload) => {
    if (!els.redactorWrap || !els.redactionWords || !els.submitRedactionBtn) return;
    const turn = payload.turn;
    const show = Boolean(turn.can_submit_redaction);
    els.redactorWrap.hidden = !show;

    if (!show) {
      els.redactionWords.innerHTML = "";
      els.submitRedactionBtn.disabled = true;
      return;
    }

    const turnNumber = Number(turn.number || 0);
    const options = Array.isArray(turn.redaction_options) ? turn.redaction_options : [];
    const allowed = Math.max(1, Number(turn.allowed_redactions || 1));

    if (state.selectedTurn !== turnNumber) {
      state.selectedTurn = turnNumber;
      state.selectedRedactions = [];
    }
    state.selectedRedactions = state.selectedRedactions.filter((index) =>
      options.some((opt) => Number(opt.index) === Number(index)),
    );
    if (state.selectedRedactions.length > allowed) {
      state.selectedRedactions = state.selectedRedactions.slice(0, allowed);
    }

    if (els.redactionHint) {
      els.redactionHint.textContent =
        "Tap words to blackline. This turn follows the one-word-per-ten rule.";
    }
    if (els.redactionCount) {
      els.redactionCount.textContent = `${state.selectedRedactions.length}/${allowed} selected`;
    }

    els.redactionWords.innerHTML = options
      .map((item) => {
        const index = Number(item.index);
        const selected = state.selectedRedactions.includes(index);
        return `
          <button type="button" class="blr-word-btn ${selected ? "is-selected" : ""}" data-word-index="${index}">
            ${escapeHtml(item.word)}
          </button>
        `;
      })
      .join("");

    els.redactionWords.querySelectorAll(".blr-word-btn").forEach((button) => {
      button.addEventListener("click", () => {
        const wordIndex = Number(button.getAttribute("data-word-index") || "-1");
        if (wordIndex < 0) return;
        const existingIdx = state.selectedRedactions.indexOf(wordIndex);
        if (existingIdx >= 0) {
          state.selectedRedactions.splice(existingIdx, 1);
        } else if (state.selectedRedactions.length < allowed) {
          state.selectedRedactions.push(wordIndex);
        }
        updateRedactionControls(payload);
      });
    });

    els.submitRedactionBtn.disabled = state.selectedRedactions.length === 0;
  };

  const renderGuessFields = (gapCount) => {
    if (!els.guessFields) return;
    const count = Math.max(0, Number(gapCount || 0));
    els.guessFields.innerHTML = Array.from({ length: count })
      .map(
        (_item, idx) => `
          <input
            type="text"
            class="blr-guess-input"
            data-gap-index="${idx}"
            placeholder="Gap ${idx + 1}"
            autocomplete="off"
            required
          />
        `,
      )
      .join("");
  };

  const updateGuessControls = (payload) => {
    if (!els.guessWrap || !els.guessForm || !els.guessHint || !els.guessSubmitBtn) {
      return;
    }

    const turn = payload.turn;
    const viewer = payload.viewer;
    const show = turn.status === "guessing" && !viewer.is_redactor;
    els.guessWrap.hidden = !show;
    if (!show) {
      els.guessForm.hidden = true;
      return;
    }

    if (turn.you_solved_rank > 0) {
      els.guessHint.textContent = `Solved at rank #${turn.you_solved_rank}. You earned ${turn.you_points_awarded} point${turn.you_points_awarded === 1 ? "" : "s"}.`;
      els.guessForm.hidden = true;
      return;
    }

    if (!turn.can_submit_guess) {
      els.guessHint.textContent = "Guessing is closed for you on this turn.";
      els.guessForm.hidden = true;
      return;
    }

    els.guessHint.textContent = `Submit ${turn.gap_count} word${turn.gap_count === 1 ? "" : "s"} in order.`;
    els.guessForm.hidden = false;

    const existingFields = els.guessFields?.querySelectorAll(".blr-guess-input").length || 0;
    if (existingFields !== Number(turn.gap_count || 0)) {
      renderGuessFields(turn.gap_count);
    }

    els.guessSubmitBtn.disabled = false;
  };

  const updateReveal = (payload) => {
    if (!els.revealWrap || !els.revealAnswers) return;
    const show = payload.turn.status === "reveal";
    els.revealWrap.hidden = !show;
    if (!show) {
      els.revealAnswers.textContent = "";
      return;
    }
    const answers = Array.isArray(payload.turn.answers) ? payload.turn.answers : [];
    if (!answers.length) {
      els.revealAnswers.textContent = "No answers recorded.";
      return;
    }
    els.revealAnswers.textContent = answers
      .map((word, idx) => `Gap ${idx + 1}: ${word}`)
      .join(" | ");
  };

  const statusTextFor = (payload) => {
    const sessionStatus = payload.session.status;
    const turn = payload.turn;
    const viewer = payload.viewer;

    if (!payload.session.is_active) {
      return payload.session.ended_reason || "Game ended.";
    }
    if (sessionStatus === "waiting") {
      return turn.can_start
        ? "Ready to start. Launch when everyone is in."
        : "Waiting for host start (minimum 2 players).";
    }
    if (sessionStatus === "redacting") {
      return viewer.is_redactor
        ? "Select words to blackline, then lock redactions."
        : `${payload.session.redactor_name} is preparing the redacted file.`;
    }
    if (sessionStatus === "guessing") {
      if (viewer.is_redactor) {
        return `${turn.solved_count}/${turn.guesser_count} guessers solved so far.`;
      }
      if (turn.you_solved_rank > 0) {
        return `Solved at rank #${turn.you_solved_rank}.`;
      }
      return "Guess the original words before everyone else does.";
    }
    if (sessionStatus === "reveal") {
      return turn.can_next_turn
        ? "Answers revealed. Start the next turn when ready."
        : "Answers revealed. Waiting for host.";
    }
    return "";
  };

  const renderRound = (payload) => {
    const turn = payload.turn;
    const session = payload.session;
    const turnNumber = Number(turn.number || 0);

    if (els.sessionTitle) els.sessionTitle.textContent = `Room ${session.code}`;
    if (els.sessionMeta) {
      els.sessionMeta.textContent =
        `${payload.players.length}/${session.max_players} players · Turn ${Math.max(turnNumber, 0)} · Redactor: ${session.redactor_name}`;
    }
    if (els.sessionNotice) {
      els.sessionNotice.textContent = session.is_active
        ? "Scoring is rank-based: first correct gets the highest points."
        : session.ended_reason || "Game ended.";
    }
    if (els.turnHeading) {
      els.turnHeading.textContent = turnNumber > 0 ? `Turn ${turnNumber}` : "Lobby";
    }
    if (els.turnStatus) {
      els.turnStatus.textContent = statusTextFor(payload);
    }
    if (els.pdfMetaRight) {
      els.pdfMetaRight.textContent =
        turnNumber > 0 ? `Turn ${turnNumber} · ${session.redactor_name}` : "Awaiting host start";
    }

    let puzzleText = "Waiting for host to start.";
    if (session.status === "redacting") {
      puzzleText = payload.viewer.is_redactor
        ? turn.source_quote || "Quote unavailable."
        : "REDACTION IN PROGRESS\n\nThe current redactor is blacklining the source quote.";
    } else if (session.status === "guessing" || session.status === "reveal") {
      puzzleText = turn.puzzle_text || "No redacted quote available.";
    } else if (!session.is_active) {
      puzzleText = session.ended_reason || "Game ended.";
    }
    if (els.puzzle) {
      if (session.status === "guessing" || session.status === "reveal") {
        els.puzzle.innerHTML = renderPuzzleMarkup(puzzleText);
      } else {
        els.puzzle.textContent = puzzleText;
      }
    }

    if (els.startBtn) els.startBtn.hidden = !turn.can_start;
    if (els.endTurnBtn) els.endTurnBtn.hidden = !turn.can_end_turn;
    if (els.nextTurnBtn) els.nextTurnBtn.hidden = !turn.can_next_turn;
    if (els.endBtn) els.endBtn.hidden = !turn.can_end_game;

    updateRedactionControls(payload);
    updateGuessControls(payload);
    renderSolvers(payload);
    updateReveal(payload);
    syncTurnActionsVisibility();

    if (els.mobileTip) {
      let tip = "";
      if (isMobileViewport()) {
        if (turn.can_submit_redaction) {
          tip = "Tap words to mark them, then lock redactions.";
        } else if (turn.can_submit_guess) {
          tip = "Fill each gap in order and submit fast for more points.";
        } else if (turn.can_next_turn) {
          tip = "Tap Next turn when everyone is ready.";
        }
      }
      els.mobileTip.textContent = tip;
      els.mobileTip.hidden = !tip;
    }
  };

  const renderSession = (payload) => {
    state.lastState = payload;
    renderLobbyOrSession(true);
    renderPlayers(payload);
    renderRound(payload);

    if (!payload.session.is_active) {
      stopPolling();
    }
  };

  const refreshSessionState = async () => {
    if (state.pending) return;
    if (!state.sessionCode || !state.playerId) return;

    state.pending = true;
    try {
      const payload = await api(
        `/api/blackline-rush/sessions/${encodeURIComponent(state.sessionCode)}?player_id=${encodeURIComponent(state.playerId)}`,
      );
      renderSession(payload);
    } catch (err) {
      resetSessionState(String(err.message || "Session disconnected."));
    } finally {
      state.pending = false;
    }
  };

  const createSession = async (playerName) => {
    const payload = await api("/api/blackline-rush/sessions", {
      method: "POST",
      body: { player_name: playerName },
    });
    state.sessionCode = payload.session_code;
    state.playerId = payload.player_id;
    state.playerName = payload.display_name;
    saveSessionIdentity();
    setLobbyMessage("");
    await refreshSessionState();
    startPolling();
  };

  const joinSession = async ({ playerName, sessionCode, playerId }) => {
    const payload = await api(
      `/api/blackline-rush/sessions/${encodeURIComponent(sessionCode)}/join`,
      {
        method: "POST",
        body: {
          player_name: playerName,
          player_id: playerId || undefined,
        },
      },
    );
    state.sessionCode = payload.session_code;
    state.playerId = payload.player_id;
    state.playerName = payload.display_name;
    saveSessionIdentity();
    setLobbyMessage("");
    await refreshSessionState();
    startPolling();
  };

  const tryResumeSession = async () => {
    const storedCode = localStorage.getItem(STORAGE_KEYS.sessionCode) || "";
    const storedPlayerId = localStorage.getItem(STORAGE_KEYS.playerId) || "";
    const storedPlayerName = localStorage.getItem(STORAGE_KEYS.playerName) || "";
    if (!storedCode || !storedPlayerId) {
      renderLobbyOrSession(false);
      return;
    }
    state.sessionCode = storedCode;
    state.playerId = storedPlayerId;
    state.playerName = storedPlayerName;
    try {
      await refreshSessionState();
      if (state.lastState?.session?.is_active) {
        startPolling();
      }
    } catch (_err) {
      resetSessionState();
    }
  };

  const bindEvents = () => {
    els.createForm?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const playerName = String(els.createName?.value || "").trim();
      if (!playerName) {
        setLobbyMessage("Enter your name to create a room.");
        return;
      }
      try {
        await createSession(playerName);
      } catch (err) {
        setLobbyMessage(String(err.message || "Unable to create room."));
      }
    });

    els.joinForm?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const playerName = String(els.joinName?.value || "").trim();
      const sessionCode = String(els.joinCode?.value || "")
        .trim()
        .toUpperCase();
      if (!playerName || !sessionCode) {
        setLobbyMessage("Enter your name and a room code.");
        return;
      }
      try {
        await joinSession({ playerName, sessionCode });
      } catch (err) {
        setLobbyMessage(String(err.message || "Unable to join room."));
      }
    });

    els.joinCode?.addEventListener("input", () => {
      if (!els.joinCode) return;
      els.joinCode.value = els.joinCode.value
        .toUpperCase()
        .replace(/[^A-Z0-9]/g, "")
        .slice(0, 6);
    });

    els.startBtn?.addEventListener("click", async () => {
      try {
        await api(
          `/api/blackline-rush/sessions/${encodeURIComponent(state.sessionCode)}/start`,
          {
            method: "POST",
            body: { player_id: state.playerId },
          },
        );
        await refreshSessionState();
      } catch (err) {
        setLobbyMessage(String(err.message || "Unable to start game."));
      }
    });

    els.submitRedactionBtn?.addEventListener("click", async () => {
      if (!state.selectedRedactions.length) return;
      try {
        await api(
          `/api/blackline-rush/sessions/${encodeURIComponent(state.sessionCode)}/submit-redaction`,
          {
            method: "POST",
            body: {
              player_id: state.playerId,
              redaction_indices: [...state.selectedRedactions].sort((a, b) => a - b),
            },
          },
        );
        state.selectedRedactions = [];
        await refreshSessionState();
      } catch (err) {
        setLobbyMessage(String(err.message || "Unable to submit redaction."));
      }
    });

    els.guessForm?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const guessInputs = Array.from(
        els.guessFields?.querySelectorAll(".blr-guess-input") || [],
      );
      const guesses = guessInputs.map((input) => String(input.value || "").trim());
      if (!guesses.length || guesses.some((item) => !item)) {
        setLobbyMessage("Enter a guess for each gap.");
        return;
      }
      if (els.guessSubmitBtn) els.guessSubmitBtn.disabled = true;
      try {
        const payload = await api(
          `/api/blackline-rush/sessions/${encodeURIComponent(state.sessionCode)}/guess`,
          {
            method: "POST",
            body: {
              player_id: state.playerId,
              guesses,
            },
          },
        );
        if (payload.correct) {
          setLobbyMessage(
            payload.solved_rank > 0
              ? `Correct. Rank #${payload.solved_rank} (+${payload.points_awarded}).`
              : "Correct.",
          );
        } else {
          setLobbyMessage("Not quite. Try again.");
        }
        await refreshSessionState();
      } catch (err) {
        setLobbyMessage(String(err.message || "Unable to submit guess."));
      } finally {
        if (els.guessSubmitBtn) els.guessSubmitBtn.disabled = false;
      }
    });

    els.endTurnBtn?.addEventListener("click", async () => {
      try {
        await api(
          `/api/blackline-rush/sessions/${encodeURIComponent(state.sessionCode)}/end-turn`,
          {
            method: "POST",
            body: { player_id: state.playerId },
          },
        );
        await refreshSessionState();
      } catch (err) {
        setLobbyMessage(String(err.message || "Unable to reveal answers."));
      }
    });

    els.nextTurnBtn?.addEventListener("click", async () => {
      try {
        await api(
          `/api/blackline-rush/sessions/${encodeURIComponent(state.sessionCode)}/next-turn`,
          {
            method: "POST",
            body: { player_id: state.playerId },
          },
        );
        await refreshSessionState();
        if (state.lastState?.session?.is_active) {
          startPolling();
        }
      } catch (err) {
        setLobbyMessage(String(err.message || "Unable to start next turn."));
      }
    });

    els.endBtn?.addEventListener("click", async () => {
      try {
        await api(
          `/api/blackline-rush/sessions/${encodeURIComponent(state.sessionCode)}/end`,
          {
            method: "POST",
            body: { player_id: state.playerId },
          },
        );
        await refreshSessionState();
      } catch (err) {
        setLobbyMessage(String(err.message || "Unable to end game."));
      }
    });

    els.copyCodeBtn?.addEventListener("click", async () => {
      if (!state.sessionCode) return;
      try {
        await navigator.clipboard.writeText(state.sessionCode);
        setLobbyMessage("Room code copied.");
      } catch (_err) {
        setLobbyMessage("Clipboard blocked. Share code manually.");
      }
    });

    els.leaveBtn?.addEventListener("click", async () => {
      if (!state.sessionCode || !state.playerId) {
        resetSessionState();
        return;
      }
      try {
        await api(
          `/api/blackline-rush/sessions/${encodeURIComponent(state.sessionCode)}/leave`,
          {
            method: "POST",
            body: { player_id: state.playerId },
          },
        );
      } catch (_err) {
        // Leave should clear local state even on server errors.
      }
      resetSessionState("You left the room.");
    });

    window.addEventListener("beforeunload", () => {
      stopPolling();
    });
  };

  const init = async () => {
    renderReadyState();
    bindEvents();

    if (!bootstrap.ready) return;
    renderLobbyOrSession(false);
    await tryResumeSession();
  };

  init();
})();
