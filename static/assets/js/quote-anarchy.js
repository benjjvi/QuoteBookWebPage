(() => {
  const bootstrapEl = document.getElementById("qaBootstrapData");
  let bootstrap = {};
  if (bootstrapEl?.textContent) {
    try {
      bootstrap = JSON.parse(bootstrapEl.textContent);
    } catch (_err) {
      bootstrap = {};
    }
  }

  const STORAGE_KEYS = {
    sessionCode: "qa_session_code",
    playerId: "qa_player_id",
    playerName: "qa_player_name",
  };

  const els = {
    locked: document.getElementById("qaLocked"),
    main: document.getElementById("qaMain"),
    lockMin: document.getElementById("qaLockMin"),
    lockCurrent: document.getElementById("qaLockCurrent"),

    modeSoloBtn: document.getElementById("qaModeSoloBtn"),
    modeMultiBtn: document.getElementById("qaModeMultiBtn"),
    soloMode: document.getElementById("qaSoloMode"),
    multiMode: document.getElementById("qaMultiMode"),

    soloBlackCard: document.getElementById("qaSoloBlackCard"),
    soloDealBtn: document.getElementById("qaSoloDealBtn"),
    soloHand: document.getElementById("qaSoloHand"),
    soloHint: document.getElementById("qaSoloHint"),
    soloResult: document.getElementById("qaSoloResult"),
    soloMatchText: document.getElementById("qaSoloMatchText"),
    soloCopyBtn: document.getElementById("qaSoloCopyBtn"),

    createForm: document.getElementById("qaCreateForm"),
    joinForm: document.getElementById("qaJoinForm"),
    createName: document.getElementById("qaCreateName"),
    createMode: document.getElementById("qaCreateMode"),
    createMaxRounds: document.getElementById("qaCreateMaxRounds"),
    joinName: document.getElementById("qaJoinName"),
    joinCode: document.getElementById("qaJoinCode"),
    lobbyView: document.getElementById("qaLobbyView"),
    lobbyMessage: document.getElementById("qaLobbyMessage"),

    sessionView: document.getElementById("qaSessionView"),
    sessionTitle: document.getElementById("qaSessionTitle"),
    sessionMeta: document.getElementById("qaSessionMeta"),
    sessionNotice: document.getElementById("qaSessionNotice"),
    copyCodeBtn: document.getElementById("qaCopyCodeBtn"),
    endBtn: document.getElementById("qaEndBtn"),
    leaveBtn: document.getElementById("qaLeaveBtn"),

    playersList: document.getElementById("qaPlayersList"),

    roundHeading: document.getElementById("qaRoundHeading"),
    roundStatus: document.getElementById("qaRoundStatus"),
    roundBlackCard: document.getElementById("qaRoundBlackCard"),

    startBtn: document.getElementById("qaStartBtn"),
    submitBtn: document.getElementById("qaSubmitBtn"),
    nextRoundBtn: document.getElementById("qaNextRoundBtn"),

    handWrap: document.getElementById("qaHandWrap"),
    roundHand: document.getElementById("qaRoundHand"),
    judgeWrap: document.getElementById("qaJudgeWrap"),
    judgeHeading: document.getElementById("qaJudgeHeading"),
    judgeSubmissions: document.getElementById("qaJudgeSubmissions"),
    revealWrap: document.getElementById("qaRevealWrap"),
    revealText: document.getElementById("qaRevealText"),
  };

  const state = {
    mode: "solo",
    solo: {
      blackCard: "",
      hand: [],
      selectedQuote: null,
      shareText: "",
    },
    multi: {
      sessionCode: "",
      playerId: "",
      playerName: "",
      pollingTimer: null,
      pending: false,
      lastState: null,
      selectedQuoteId: null,
      selectionRound: 0,
    },
  };

  const setLobbyMessage = (message) => {
    if (!els.lobbyMessage) return;
    els.lobbyMessage.textContent = message || "";
  };

  const formatAuthors = (authors) => {
    if (!Array.isArray(authors) || !authors.length) return "Unknown";
    return authors.join(", ");
  };

  const escapeHtml = (value) =>
    String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\"/g, "&quot;")
      .replace(/'/g, "&#39;");

  const modeLabel = (mode) =>
    mode === "all_vote" ? "Everyone Votes" : "Classic Judge";

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

  const saveSessionIdentity = () => {
    localStorage.setItem(STORAGE_KEYS.sessionCode, state.multi.sessionCode);
    localStorage.setItem(STORAGE_KEYS.playerId, state.multi.playerId);
    localStorage.setItem(STORAGE_KEYS.playerName, state.multi.playerName);
  };

  const clearSessionIdentity = () => {
    localStorage.removeItem(STORAGE_KEYS.sessionCode);
    localStorage.removeItem(STORAGE_KEYS.playerId);
    localStorage.removeItem(STORAGE_KEYS.playerName);
  };

  const switchMode = (mode) => {
    state.mode = mode === "multi" ? "multi" : "solo";

    const solo = state.mode === "solo";
    els.modeSoloBtn?.classList.toggle("is-active", solo);
    els.modeSoloBtn?.setAttribute("aria-selected", solo ? "true" : "false");
    els.modeMultiBtn?.classList.toggle("is-active", !solo);
    els.modeMultiBtn?.setAttribute("aria-selected", solo ? "false" : "true");

    if (els.soloMode) els.soloMode.hidden = !solo;
    if (els.multiMode) els.multiMode.hidden = solo;
  };

  const renderLockState = () => {
    const unlocked = Boolean(bootstrap.unlocked);
    if (els.lockMin) els.lockMin.textContent = String(bootstrap.min_quotes_required || 50);
    if (els.lockCurrent) els.lockCurrent.textContent = String(bootstrap.total_quotes || 0);
    if (els.locked) els.locked.hidden = unlocked;
    if (els.main) els.main.hidden = !unlocked;

    if (els.createMaxRounds) {
      const defaultRounds = Number(bootstrap.default_max_rounds || 8);
      const maxLimit = Number(bootstrap.max_rounds_limit || 30);
      els.createMaxRounds.value = String(defaultRounds);
      els.createMaxRounds.max = String(maxLimit);
    }
  };

  const renderSoloHand = () => {
    if (!els.soloHand) return;
    if (!state.solo.hand.length) {
      els.soloHand.innerHTML = "";
      if (els.soloResult) els.soloResult.hidden = true;
      return;
    }

    const selectedId = state.solo.selectedQuote?.id;
    els.soloHand.innerHTML = state.solo.hand
      .map(
        (card) => `
          <button type="button" class="qa-card ${selectedId === card.id ? "is-selected" : ""}" data-quote-id="${card.id}">
            <p class="quote">${escapeHtml(card.quote)}</p>
            <p class="meta">${escapeHtml(formatAuthors(card.authors))}</p>
          </button>
        `,
      )
      .join("");

    els.soloHand.querySelectorAll(".qa-card").forEach((button) => {
      button.addEventListener("click", () => {
        const quoteId = Number(button.getAttribute("data-quote-id") || "0");
        const selected = state.solo.hand.find((item) => item.id === quoteId);
        if (!selected) return;
        state.solo.selectedQuote = selected;
        const blackCard = state.solo.blackCard || "";
        state.solo.shareText = `${blackCard}\n\n${selected.quote}\n- ${formatAuthors(selected.authors)}`;

        if (els.soloMatchText) {
          els.soloMatchText.textContent = state.solo.shareText;
        }
        if (els.soloResult) {
          els.soloResult.hidden = false;
        }
        renderSoloHand();
      });
    });
  };

  const dealSoloRound = async () => {
    if (!bootstrap.unlocked) return;
    if (!els.soloDealBtn) return;

    els.soloDealBtn.disabled = true;
    els.soloDealBtn.textContent = "Dealing...";
    try {
      const payload = await api("/api/quote-anarchy/solo/deal", { method: "POST" });
      state.solo.blackCard = payload.black_card || "The best response is ____.";
      state.solo.hand = (payload.hand || []).map((item) => ({
        id: Number(item.id),
        quote: item.quote,
        authors: item.authors || [],
      }));
      state.solo.selectedQuote = null;
      state.solo.shareText = "";

      if (els.soloBlackCard) {
        els.soloBlackCard.textContent = state.solo.blackCard;
      }
      if (els.soloHint) {
        els.soloHint.textContent = "Pick your funniest quote card to complete the prompt.";
      }
      if (els.soloResult) {
        els.soloResult.hidden = true;
      }
      renderSoloHand();
    } catch (err) {
      if (els.soloHint) {
        els.soloHint.textContent = String(err.message || "Unable to deal a solo round.");
      }
    } finally {
      els.soloDealBtn.disabled = false;
      els.soloDealBtn.textContent = "Deal Solo Round";
    }
  };

  const renderLobbyOrSession = (inSession) => {
    if (els.lobbyView) els.lobbyView.hidden = inSession;
    if (els.sessionView) els.sessionView.hidden = !inSession;
  };

  const stopPolling = () => {
    if (state.multi.pollingTimer) {
      window.clearInterval(state.multi.pollingTimer);
      state.multi.pollingTimer = null;
    }
  };

  const startPolling = () => {
    stopPolling();
    state.multi.pollingTimer = window.setInterval(() => {
      refreshSessionState();
    }, 2500);
  };

  const resetSessionState = (message) => {
    stopPolling();
    state.multi.sessionCode = "";
    state.multi.playerId = "";
    state.multi.lastState = null;
    state.multi.selectedQuoteId = null;
    state.multi.selectionRound = 0;
    clearSessionIdentity();
    renderLobbyOrSession(false);
    if (message) setLobbyMessage(message);
  };

  const renderPlayers = (payload) => {
    if (!els.playersList) return;
    const viewerId = payload.viewer.player_id;
    const judgeId = payload.session.judge_player_id;
    const hostId = payload.session.host_player_id;
    const isJudgeMode = payload.session.judging_mode === "judge";

    els.playersList.innerHTML = payload.players
      .map((player) => {
        const tags = [];
        if (player.player_id === viewerId) tags.push("You");
        if (player.player_id === hostId) tags.push("Host");
        if (isJudgeMode && player.player_id === judgeId && payload.session.status !== "waiting") {
          tags.push("Judge");
        }
        return `
          <li class="qa-player-item">
            <div class="left">
              <strong>
                <img class="chip" src="/static/assets/quote-anarchy/player-chip-${Math.min(4, Number(player.seat) || 1)}.svg" alt="" />
                ${escapeHtml(player.display_name)}
              </strong>
              <span class="tag">${escapeHtml(tags.join(" · ") || "Player")}</span>
            </div>
            <span>${player.score} pt${player.score === 1 ? "" : "s"}</span>
          </li>
        `;
      })
      .join("");
  };

  const renderRoundHand = (payload) => {
    if (!els.roundHand || !els.handWrap || !els.submitBtn) return;

    const round = payload.round;
    const mode = payload.session.judging_mode;
    const isCollecting = round.status === "collecting";
    const shouldShow = isCollecting && (mode === "all_vote" || !payload.viewer.is_judge);

    els.handWrap.hidden = !shouldShow;
    if (!shouldShow) {
      els.roundHand.innerHTML = "";
      els.submitBtn.hidden = true;
      els.submitBtn.disabled = true;
      return;
    }

    const roundNumber = Number(round.number || 0);
    if (state.multi.selectionRound !== roundNumber) {
      state.multi.selectedQuoteId = null;
      state.multi.selectionRound = roundNumber;
    }

    const submitted = Boolean(round.you_submitted);
    const cards = Array.isArray(round.hand) ? round.hand : [];

    els.roundHand.innerHTML = cards
      .map(
        (card) => `
          <button
            type="button"
            class="qa-card ${state.multi.selectedQuoteId === card.quote_id ? "is-selected" : ""}"
            data-quote-id="${card.quote_id}"
            ${submitted ? "disabled" : ""}
          >
            <p class="quote">${escapeHtml(card.quote)}</p>
            <p class="meta">${escapeHtml(formatAuthors(card.authors))}</p>
          </button>
        `,
      )
      .join("");

    els.roundHand.querySelectorAll(".qa-card").forEach((button) => {
      button.addEventListener("click", () => {
        if (submitted) return;
        const quoteId = Number(button.getAttribute("data-quote-id") || "0");
        state.multi.selectedQuoteId = quoteId;
        renderRoundHand(payload);
      });
    });

    els.submitBtn.hidden = submitted;
    els.submitBtn.disabled = submitted || !state.multi.selectedQuoteId;
  };

  const renderJudgeSubmissions = (payload) => {
    if (!els.judgeWrap || !els.judgeSubmissions || !els.judgeHeading) return;

    const round = payload.round;
    const session = payload.session;
    const mode = session.judging_mode;

    const showJudge = round.status === "judging" && (round.can_pick_winner || mode === "all_vote");
    els.judgeWrap.hidden = !showJudge;
    if (!showJudge) {
      els.judgeSubmissions.innerHTML = "";
      return;
    }

    const submissions = round.submissions || [];
    if (mode === "all_vote") {
      els.judgeHeading.textContent = "Vote for your favorite";
    } else {
      els.judgeHeading.textContent = "Pick winner";
    }

    const votedPlayerId = String(round.voted_player_id || "");

    els.judgeSubmissions.innerHTML = submissions
      .map((item) => {
        const targetId = String(item.player_id || "");
        const selected = mode === "all_vote" && votedPlayerId === targetId;
        const disabled = mode === "all_vote" && !round.can_vote;
        return `
          <button
            type="button"
            class="qa-card ${selected ? "is-selected" : ""}"
            data-target-player-id="${escapeHtml(targetId)}"
            ${disabled ? "disabled" : ""}
          >
            <p class="quote">${escapeHtml(item.quote)}</p>
            <p class="meta">${escapeHtml(formatAuthors(item.authors))}</p>
          </button>
        `;
      })
      .join("");

    els.judgeSubmissions.querySelectorAll(".qa-card").forEach((button) => {
      button.addEventListener("click", async () => {
        const targetId = button.getAttribute("data-target-player-id") || "";
        if (!targetId) return;

        try {
          if (mode === "all_vote") {
            if (!round.can_vote) return;
            await api(
              `/api/quote-anarchy/sessions/${encodeURIComponent(state.multi.sessionCode)}/vote`,
              {
                method: "POST",
                body: {
                  player_id: state.multi.playerId,
                  voted_player_id: targetId,
                },
              },
            );
          } else {
            await api(
              `/api/quote-anarchy/sessions/${encodeURIComponent(state.multi.sessionCode)}/pick-winner`,
              {
                method: "POST",
                body: {
                  player_id: state.multi.playerId,
                  winner_player_id: targetId,
                },
              },
            );
          }
          await refreshSessionState();
        } catch (err) {
          setLobbyMessage(String(err.message || "Unable to complete this action."));
        }
      });
    });
  };

  const renderReveal = (payload) => {
    if (!els.revealWrap || !els.revealText) return;
    const showReveal = payload.round.status === "reveal" && payload.round.result;
    els.revealWrap.hidden = !showReveal;
    if (!showReveal) {
      els.revealText.textContent = "";
      return;
    }

    const result = payload.round.result;
    const winners = Array.isArray(result.winners) ? result.winners : [];

    if (winners.length > 1) {
      const names = winners.map((winner) => winner.player_name).join(" and ");
      const lines = winners.map(
        (winner) =>
          `${winner.player_name}: “${winner.quote}” - ${formatAuthors(winner.authors)}${winner.vote_count ? ` (${winner.vote_count} votes)` : ""}`,
      );
      els.revealText.textContent = `Tie round. Winners: ${names}. ${lines.join(" | ")}`;
      return;
    }

    els.revealText.textContent = `${result.winner_name} wins with: “${result.quote}” - ${formatAuthors(result.authors)}`;
  };

  const renderRound = (payload) => {
    if (!els.roundHeading || !els.roundStatus || !els.roundBlackCard) return;

    const round = payload.round;
    const session = payload.session;
    const mode = session.judging_mode;
    const status = session.status;
    const isActive = Boolean(session.is_active);

    const roundNumber = Number(round.number || 0);
    els.sessionTitle.textContent = `Room ${session.code}`;
    els.sessionMeta.textContent = `${payload.players.length}/${session.max_players} players · ${modeLabel(mode)} · Round ${Math.max(roundNumber, 0)}/${session.max_rounds}`;

    if (els.sessionNotice) {
      if (!isActive && session.ended_reason) {
        els.sessionNotice.textContent = session.ended_reason;
      } else {
        els.sessionNotice.textContent = `Game ends after ${session.max_rounds} rounds.`;
      }
    }

    if (els.endBtn) {
      els.endBtn.hidden = !round.can_end_game;
    }

    els.roundHeading.textContent = roundNumber > 0 ? `Round ${roundNumber}` : "Lobby";
    els.roundBlackCard.textContent = round.black_card || "Waiting for the host to start.";

    let statusText = "";
    if (!isActive) {
      statusText = session.ended_reason || "Game ended.";
    } else if (status === "waiting") {
      statusText = round.can_start
        ? "Ready to start. Begin when everyone is in."
        : "Waiting for host to start the game (minimum 2 players).";
    } else if (status === "collecting") {
      if (mode === "judge" && payload.viewer.is_judge) {
        statusText = `${round.submitted_count}/${round.required_submissions} submissions in. You are judging this round.`;
      } else if (round.you_submitted) {
        statusText = `${round.submitted_count}/${round.required_submissions} submitted. Waiting for the rest.`;
      } else {
        statusText = mode === "all_vote"
          ? "Submit one white card. Everyone submits in this mode."
          : "Pick one white card and submit it.";
      }
    } else if (status === "judging") {
      if (mode === "all_vote") {
        if (round.can_vote) {
          statusText = `All cards are in. Vote for your favorite (${round.votes_submitted_count}/${round.required_votes} votes).`;
        } else {
          statusText = `Vote locked. Waiting for everyone (${round.votes_submitted_count}/${round.required_votes} votes).`;
        }
      } else {
        statusText = round.can_pick_winner
          ? "All cards are in. Pick the winning quote."
          : "Judge is choosing the winner.";
      }
    } else if (status === "reveal") {
      if (!isActive) {
        statusText = session.ended_reason || "Game ended.";
      } else {
        statusText = round.can_advance
          ? "Winner revealed. Start the next round when ready."
          : "Winner revealed. Waiting for host to continue.";
      }
    }

    els.roundStatus.textContent = statusText;

    els.startBtn.hidden = !round.can_start;
    els.nextRoundBtn.hidden = !round.can_advance;

    renderRoundHand(payload);
    renderJudgeSubmissions(payload);
    renderReveal(payload);
  };

  const renderSession = (payload) => {
    state.multi.lastState = payload;
    renderLobbyOrSession(true);
    renderPlayers(payload);
    renderRound(payload);

    if (!payload.session.is_active) {
      stopPolling();
    }
  };

  const refreshSessionState = async () => {
    if (state.multi.pending) return;
    if (!state.multi.sessionCode || !state.multi.playerId) return;

    state.multi.pending = true;
    try {
      const payload = await api(
        `/api/quote-anarchy/sessions/${encodeURIComponent(state.multi.sessionCode)}?player_id=${encodeURIComponent(state.multi.playerId)}`,
      );
      renderSession(payload);
    } catch (err) {
      resetSessionState(String(err.message || "Session disconnected."));
    } finally {
      state.multi.pending = false;
    }
  };

  const createSession = async (playerName, judgingMode, maxRounds) => {
    const payload = await api("/api/quote-anarchy/sessions", {
      method: "POST",
      body: {
        player_name: playerName,
        judging_mode: judgingMode,
        max_rounds: maxRounds,
      },
    });

    state.multi.sessionCode = payload.session_code;
    state.multi.playerId = payload.player_id;
    state.multi.playerName = payload.display_name;
    saveSessionIdentity();
    setLobbyMessage("");
    await refreshSessionState();
    startPolling();
  };

  const joinSession = async ({ playerName, sessionCode, playerId }) => {
    const payload = await api(
      `/api/quote-anarchy/sessions/${encodeURIComponent(sessionCode)}/join`,
      {
        method: "POST",
        body: {
          player_name: playerName,
          player_id: playerId || undefined,
        },
      },
    );
    state.multi.sessionCode = payload.session_code;
    state.multi.playerId = payload.player_id;
    state.multi.playerName = payload.display_name;
    saveSessionIdentity();
    setLobbyMessage("");
    await refreshSessionState();
    startPolling();
  };

  const handleCreateSubmit = async (event) => {
    event.preventDefault();
    const playerName = String(els.createName?.value || "").trim();
    const judgingMode = String(els.createMode?.value || "judge").trim();
    const rawRounds = Number(els.createMaxRounds?.value || bootstrap.default_max_rounds || 8);
    const maxRoundsLimit = Number(bootstrap.max_rounds_limit || 30);
    const maxRounds = Math.max(1, Math.min(maxRoundsLimit, Math.floor(rawRounds || 0) || 8));

    if (!playerName) {
      setLobbyMessage("Enter your name to create a session.");
      return;
    }

    try {
      await createSession(playerName, judgingMode, maxRounds);
    } catch (err) {
      setLobbyMessage(String(err.message || "Unable to create session."));
    }
  };

  const handleJoinSubmit = async (event) => {
    event.preventDefault();
    const playerName = String(els.joinName?.value || "").trim();
    const sessionCode = String(els.joinCode?.value || "").trim().toUpperCase();
    if (!playerName || !sessionCode) {
      setLobbyMessage("Enter your name and a session code.");
      return;
    }
    try {
      await joinSession({ playerName, sessionCode });
    } catch (err) {
      setLobbyMessage(String(err.message || "Unable to join session."));
    }
  };

  const tryResumeSession = async () => {
    const storedCode = localStorage.getItem(STORAGE_KEYS.sessionCode) || "";
    const storedPlayerId = localStorage.getItem(STORAGE_KEYS.playerId) || "";
    const storedPlayerName = localStorage.getItem(STORAGE_KEYS.playerName) || "";
    if (!storedCode || !storedPlayerId) {
      renderLobbyOrSession(false);
      return;
    }

    state.multi.sessionCode = storedCode;
    state.multi.playerId = storedPlayerId;
    state.multi.playerName = storedPlayerName;

    try {
      await refreshSessionState();
      if (state.multi.lastState?.session?.is_active) {
        startPolling();
      }
    } catch (_err) {
      resetSessionState();
    }
  };

  const bindEvents = () => {
    els.modeSoloBtn?.addEventListener("click", () => switchMode("solo"));
    els.modeMultiBtn?.addEventListener("click", () => switchMode("multi"));

    els.soloDealBtn?.addEventListener("click", dealSoloRound);
    els.soloCopyBtn?.addEventListener("click", async () => {
      if (!state.solo.shareText) return;
      try {
        await navigator.clipboard.writeText(state.solo.shareText);
        if (els.soloHint) {
          els.soloHint.textContent = "Match copied. Send it to your friends.";
        }
      } catch (_err) {
        if (els.soloHint) {
          els.soloHint.textContent = "Clipboard blocked. Copy manually from the result card.";
        }
      }
    });

    els.createForm?.addEventListener("submit", handleCreateSubmit);
    els.joinForm?.addEventListener("submit", handleJoinSubmit);
    els.joinCode?.addEventListener("input", () => {
      if (!els.joinCode) return;
      els.joinCode.value = els.joinCode.value.toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, 6);
    });

    els.startBtn?.addEventListener("click", async () => {
      try {
        await api(`/api/quote-anarchy/sessions/${encodeURIComponent(state.multi.sessionCode)}/start`, {
          method: "POST",
          body: { player_id: state.multi.playerId },
        });
        await refreshSessionState();
      } catch (err) {
        setLobbyMessage(String(err.message || "Unable to start session."));
      }
    });

    els.submitBtn?.addEventListener("click", async () => {
      if (!state.multi.selectedQuoteId) return;
      try {
        await api(`/api/quote-anarchy/sessions/${encodeURIComponent(state.multi.sessionCode)}/submit`, {
          method: "POST",
          body: {
            player_id: state.multi.playerId,
            quote_id: state.multi.selectedQuoteId,
          },
        });
        await refreshSessionState();
      } catch (err) {
        setLobbyMessage(String(err.message || "Unable to submit card."));
      }
    });

    els.nextRoundBtn?.addEventListener("click", async () => {
      try {
        await api(`/api/quote-anarchy/sessions/${encodeURIComponent(state.multi.sessionCode)}/next-round`, {
          method: "POST",
          body: { player_id: state.multi.playerId },
        });
        await refreshSessionState();
        if (state.multi.lastState?.session?.is_active) {
          startPolling();
        }
      } catch (err) {
        setLobbyMessage(String(err.message || "Unable to start next round."));
      }
    });

    els.endBtn?.addEventListener("click", async () => {
      try {
        await api(`/api/quote-anarchy/sessions/${encodeURIComponent(state.multi.sessionCode)}/end`, {
          method: "POST",
          body: { player_id: state.multi.playerId },
        });
        await refreshSessionState();
      } catch (err) {
        setLobbyMessage(String(err.message || "Unable to end game."));
      }
    });

    els.copyCodeBtn?.addEventListener("click", async () => {
      if (!state.multi.sessionCode) return;
      try {
        await navigator.clipboard.writeText(state.multi.sessionCode);
        setLobbyMessage("Session code copied.");
      } catch (_err) {
        setLobbyMessage("Clipboard blocked. Share code manually.");
      }
    });

    els.leaveBtn?.addEventListener("click", async () => {
      if (!state.multi.sessionCode || !state.multi.playerId) {
        resetSessionState();
        return;
      }
      try {
        await api(`/api/quote-anarchy/sessions/${encodeURIComponent(state.multi.sessionCode)}/leave`, {
          method: "POST",
          body: { player_id: state.multi.playerId },
        });
      } catch (_err) {
        // Leave should still clear local state even if server reports an error.
      }
      resetSessionState("You left the session.");
    });

    window.addEventListener("beforeunload", () => {
      stopPolling();
    });
  };

  const init = async () => {
    renderLockState();
    switchMode("solo");
    bindEvents();

    if (!bootstrap.unlocked) return;
    renderLobbyOrSession(false);
    await tryResumeSession();
  };

  init();
})();
