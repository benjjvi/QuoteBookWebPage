(() => {
  const bootstrap = window.GameRoomCore.parseBootstrapData("wsiBootstrapData");

  const STORAGE_KEYS = {
    sessionCode: "wsi_session_code",
    playerId: "wsi_player_id",
    playerName: "wsi_player_name",
  };

  const els = {
    locked: document.getElementById("wsiLocked"),
    main: document.getElementById("wsiMain"),
    eligibleCount: document.getElementById("wsiEligibleCount"),
    authorPool: document.getElementById("wsiAuthorPool"),

    createForm: document.getElementById("wsiCreateForm"),
    joinForm: document.getElementById("wsiJoinForm"),
    createName: document.getElementById("wsiCreateName"),
    joinName: document.getElementById("wsiJoinName"),
    joinCode: document.getElementById("wsiJoinCode"),
    lobbyView: document.getElementById("wsiLobbyView"),
    lobbyMessage: document.getElementById("wsiLobbyMessage"),

    sessionView: document.getElementById("wsiSessionView"),
    sessionTitle: document.getElementById("wsiSessionTitle"),
    sessionMeta: document.getElementById("wsiSessionMeta"),
    sessionNotice: document.getElementById("wsiSessionNotice"),
    playersList: document.getElementById("wsiPlayersList"),

    turnHeading: document.getElementById("wsiTurnHeading"),
    turnStatus: document.getElementById("wsiTurnStatus"),
    quoteMetaRight: document.getElementById("wsiQuoteMetaRight"),
    quoteText: document.getElementById("wsiQuoteText"),
    turnActions: document.getElementById("wsiTurnActions"),
    turnHint: document.getElementById("wsiTurnHint"),

    startBtn: document.getElementById("wsiStartBtn"),
    endTurnBtn: document.getElementById("wsiEndTurnBtn"),
    nextTurnBtn: document.getElementById("wsiNextTurnBtn"),
    endBtn: document.getElementById("wsiEndBtn"),
    copyCodeBtn: document.getElementById("wsiCopyCodeBtn"),
    leaveBtn: document.getElementById("wsiLeaveBtn"),

    answerWrap: document.getElementById("wsiAnswerWrap"),
    answerForm: document.getElementById("wsiAnswerForm"),
    optionList: document.getElementById("wsiOptionList"),
    answerSubmitBtn: document.getElementById("wsiAnswerSubmitBtn"),

    turnBoard: document.getElementById("wsiTurnBoard"),
    revealWrap: document.getElementById("wsiRevealWrap"),
    revealAnswer: document.getElementById("wsiRevealAnswer"),
  };

  const state = {
    selectedAuthor: "",
    selectedTurn: 0,
    lastNotice: "",
    lastState: null,
  };

  const escapeHtml = (value) =>
    String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");

  const api = window.GameRoomCore.api;

  const setLobbyMessage = (message) => {
    if (!els.lobbyMessage) return;
    els.lobbyMessage.textContent = message || "";
  };

  const setSessionNotice = (message) => {
    state.lastNotice = message || "";
    if (els.sessionNotice) {
      els.sessionNotice.textContent = state.lastNotice;
    }
  };

  const renderReadyState = () => {
    const ready = Boolean(bootstrap.ready);
    if (els.eligibleCount) {
      els.eligibleCount.textContent = String(bootstrap.eligible_quote_count || 0);
    }
    if (els.authorPool) {
      els.authorPool.textContent = String(bootstrap.author_pool_count || 0);
    }
    if (els.locked) els.locked.hidden = ready;
    if (els.main) els.main.hidden = !ready;
  };

  const renderLobbyOrSession = (inSession) => {
    if (els.lobbyView) els.lobbyView.hidden = inSession;
    if (els.sessionView) els.sessionView.hidden = !inSession;
  };

  const statusLabel = (status) => {
    if (status === "waiting") return "Waiting";
    if (status === "guessing") return "Live Round";
    if (status === "reveal") return "Reveal";
    return "Session";
  };

  const syncTurnActionsVisibility = () => {
    if (!els.turnActions) return;
    const hasVisibleAction = [els.startBtn, els.endTurnBtn, els.nextTurnBtn].some(
      (button) => button && !button.hidden,
    );
    els.turnActions.hidden = !hasVisibleAction;
  };

  const renderPlayers = (payload) => {
    if (!els.playersList) return;
    const hostId = payload.session.host_player_id;
    const viewerId = payload.viewer.player_id;
    els.playersList.innerHTML = payload.players
      .map((player) => {
        const tags = [];
        if (player.player_id === viewerId) tags.push("You");
        if (player.player_id === hostId) tags.push("Host");
        return `
          <li class="wsi-player">
            <strong>${escapeHtml(player.display_name)}<span class="tag">${escapeHtml(tags.join(" · ") || "Player")}</span></strong>
            <span>${player.score} pt${player.score === 1 ? "" : "s"}</span>
          </li>
        `;
      })
      .join("");
  };

  const renderTurnBoard = (payload) => {
    if (!els.turnBoard) return;
    const turn = payload.turn;
    const answers = Array.isArray(turn.answers) ? turn.answers : [];

    if (!answers.length) {
      els.turnBoard.innerHTML = "<li><span>No responses yet</span><span>—</span></li>";
      return;
    }

    if (payload.session.status === "reveal") {
      els.turnBoard.innerHTML = answers
        .map((row) => {
          if (!row.answered) {
            return `
              <li>
                <span>${escapeHtml(row.player_name)}</span>
                <span>No answer</span>
              </li>
            `;
          }

          const outcome = row.is_correct ? "Correct" : "Wrong";
          const rank = row.answer_order > 0 ? `#${row.answer_order}` : "—";
          return `
            <li>
              <span>${escapeHtml(row.player_name)} · ${escapeHtml(row.selected_author)} · ${outcome}</span>
              <span>${rank} · +${row.points_awarded}</span>
            </li>
          `;
        })
        .join("");
      return;
    }

    els.turnBoard.innerHTML = answers
      .map(
        (row) => `
          <li>
            <span>${escapeHtml(row.player_name)}</span>
            <span>${row.answered ? "Answered" : "Waiting"}</span>
          </li>
        `,
      )
      .join("");
  };

  const setOptionButtons = (payload) => {
    if (!els.optionList) return;
    const turn = payload.turn;
    const options = Array.isArray(turn.option_authors) ? turn.option_authors : [];
    const canSubmit = Boolean(turn.can_submit_answer);

    if (state.selectedTurn !== Number(turn.number || 0)) {
      state.selectedTurn = Number(turn.number || 0);
      state.selectedAuthor = "";
    }
    if (turn.you_answered && turn.your_selected_author) {
      state.selectedAuthor = String(turn.your_selected_author);
    }
    if (
      state.selectedAuthor &&
      !options.some((option) => option === state.selectedAuthor)
    ) {
      state.selectedAuthor = "";
    }

    if (!options.length) {
      els.optionList.innerHTML = "";
      if (els.answerSubmitBtn) els.answerSubmitBtn.disabled = true;
      return;
    }

    els.optionList.innerHTML = options
      .map((author) => {
        const isSelected = author === state.selectedAuthor;
        return `
          <button
            type="button"
            class="wsi-option-btn ${isSelected ? "is-selected" : ""}"
            data-author="${escapeHtml(author)}"
            ${canSubmit ? "" : "disabled"}
          >
            ${escapeHtml(author)}
          </button>
        `;
      })
      .join("");

    els.optionList.querySelectorAll(".wsi-option-btn").forEach((button) => {
      button.addEventListener("click", () => {
        if (!canSubmit) return;
        state.selectedAuthor = button.dataset.author || "";
        setOptionButtons(payload);
      });
    });

    if (els.answerSubmitBtn) {
      els.answerSubmitBtn.disabled = !canSubmit || !state.selectedAuthor;
    }
  };

  const renderTurn = (payload) => {
    const session = payload.session;
    const turn = payload.turn;
    const playerCount = Array.isArray(payload.players) ? payload.players.length : 0;

    if (els.sessionTitle) {
      els.sessionTitle.textContent = `Room ${session.code}`;
    }
    if (els.sessionMeta) {
      els.sessionMeta.textContent = `Turn ${session.turn_number || 0} · ${statusLabel(session.status)}`;
    }

    if (els.turnHeading) {
      els.turnHeading.textContent =
        session.turn_number > 0 ? `Round ${session.turn_number}` : "Round";
    }

    if (els.turnStatus) {
      if (session.status === "waiting") {
        els.turnStatus.textContent = `Need ${session.min_players} players to start. ${playerCount} joined.`;
      } else if (session.status === "guessing") {
        els.turnStatus.textContent = `${turn.answered_count}/${turn.total_players} locked answers.`;
      } else if (session.status === "reveal") {
        els.turnStatus.textContent = `${turn.correct_count} correct this round.`;
      } else {
        els.turnStatus.textContent = "";
      }
    }

    if (els.quoteMetaRight) {
      if (session.status === "waiting") {
        els.quoteMetaRight.textContent = "Waiting for host start";
      } else {
        els.quoteMetaRight.textContent = `${(turn.option_authors || []).length} options`;
      }
    }

    if (els.quoteText) {
      if (session.status === "waiting") {
        els.quoteText.textContent = "Waiting for host to start.";
      } else if (turn.source_quote) {
        els.quoteText.textContent = `"${turn.source_quote}"`;
      } else {
        els.quoteText.textContent = "Loading quote...";
      }
    }

    if (els.startBtn) els.startBtn.hidden = !turn.can_start;
    if (els.endTurnBtn) els.endTurnBtn.hidden = !turn.can_end_turn;
    if (els.nextTurnBtn) els.nextTurnBtn.hidden = !turn.can_next_turn;
    if (els.endBtn) els.endBtn.hidden = !turn.can_end_game;
    syncTurnActionsVisibility();

    const showAnswerForm = Boolean(turn.can_submit_answer);
    if (els.answerWrap) els.answerWrap.hidden = !showAnswerForm;
    setOptionButtons(payload);

    if (els.turnHint) {
      if (session.status === "guessing" && turn.can_submit_answer) {
        els.turnHint.textContent = "Pick quickly. Correct answers are ranked by speed.";
      } else if (session.status === "guessing" && turn.you_answered) {
        els.turnHint.textContent = `Answer locked: ${turn.your_selected_author || "—"}. Waiting for others.`;
      } else if (session.status === "reveal" && turn.you_answered) {
        const correctText = turn.your_is_correct
          ? `Correct (+${turn.your_points_awarded})`
          : "Wrong";
        els.turnHint.textContent = `Your answer: ${turn.your_selected_author || "—"} · ${correctText}`;
      } else {
        els.turnHint.textContent = "";
      }
    }

    const showReveal = session.status === "reveal" && Boolean(turn.correct_author);
    if (els.revealWrap) els.revealWrap.hidden = !showReveal;
    if (els.revealAnswer) {
      els.revealAnswer.textContent = showReveal ? turn.correct_author : "";
    }

    renderTurnBoard(payload);
  };

  const renderSessionState = (payload) => {
    state.lastState = payload;
    if (payload.session.ended_reason) {
      setSessionNotice(payload.session.ended_reason);
    } else if (state.lastNotice) {
      setSessionNotice(state.lastNotice);
    } else {
      setSessionNotice("");
    }

    renderPlayers(payload);
    renderTurn(payload);
  };

  const room = window.GameRoomCore.createRoomController({
    storageKeys: STORAGE_KEYS,
    fetchState: ({ sessionCode, playerId }) =>
      api(
        `/api/who-said-it/sessions/${encodeURIComponent(sessionCode)}?player_id=${encodeURIComponent(playerId)}`,
      ),
    requestCreate: ({ playerName }) =>
      api("/api/who-said-it/sessions", {
        method: "POST",
        body: { player_name: playerName },
      }),
    requestJoin: ({ playerName, sessionCode, playerId }) =>
      api(`/api/who-said-it/sessions/${encodeURIComponent(sessionCode)}/join`, {
        method: "POST",
        body: { player_name: playerName, player_id: playerId || undefined },
      }),
    requestLeave: ({ sessionCode, playerId }) =>
      api(`/api/who-said-it/sessions/${encodeURIComponent(sessionCode)}/leave`, {
        method: "POST",
        body: { player_id: playerId },
      }),
    renderLobbyOrSession,
    renderSession: renderSessionState,
    onIdentityLoaded: ({ sessionCode, playerName }) => {
      if (els.createName && playerName) els.createName.value = playerName;
      if (els.joinName && playerName) els.joinName.value = playerName;
      if (els.joinCode) els.joinCode.value = sessionCode;
    },
    onReset: (message) => {
      state.selectedAuthor = "";
      state.selectedTurn = 0;
      state.lastState = null;
      setSessionNotice("");
      if (message) setLobbyMessage(message);
    },
  });

  const refreshSessionState = async () => room.refresh();

  const afterJoin = () => {
    state.selectedAuthor = "";
    state.selectedTurn = 0;
    renderLobbyOrSession(true);
    setLobbyMessage("");
    setSessionNotice("");
  };

  const withPending = room.withPending;
  const currentIdentity = () => room.getState();

  const handleCreate = () => {
    if (!els.createForm || !els.createName) return;
    els.createForm.addEventListener("submit", (event) => {
      event.preventDefault();
      withPending(async () => {
        const playerName = els.createName.value.trim();
        if (!playerName) {
          setLobbyMessage("Enter your name first.");
          return;
        }
        setLobbyMessage("");
        await room.create({ playerName });
        afterJoin();
      }).catch((err) => {
        setLobbyMessage(err.message || "Unable to create room.");
      });
    });
  };

  const handleJoin = () => {
    if (!els.joinForm || !els.joinName || !els.joinCode) return;
    els.joinForm.addEventListener("submit", (event) => {
      event.preventDefault();
      withPending(async () => {
        const playerName = els.joinName.value.trim();
        const sessionCode = els.joinCode.value.trim().toUpperCase();
        if (!playerName || !sessionCode) {
          setLobbyMessage("Enter your name and room code.");
          return;
        }
        setLobbyMessage("");
        await room.join({ playerName, sessionCode });
        afterJoin();
      }).catch((err) => {
        setLobbyMessage(err.message || "Unable to join room.");
      });
    });
  };

  const wireButtons = () => {
    if (els.startBtn) {
      els.startBtn.addEventListener("click", () => {
        withPending(async () => {
          const { sessionCode, playerId } = currentIdentity();
          if (!sessionCode || !playerId) return;
          await api(
            `/api/who-said-it/sessions/${encodeURIComponent(sessionCode)}/start`,
            {
              method: "POST",
              body: { player_id: playerId },
            },
          );
          setSessionNotice("");
          await refreshSessionState();
        }).catch((err) => setSessionNotice(err.message || "Unable to start game."));
      });
    }

    if (els.endTurnBtn) {
      els.endTurnBtn.addEventListener("click", () => {
        withPending(async () => {
          const { sessionCode, playerId } = currentIdentity();
          if (!sessionCode || !playerId) return;
          await api(
            `/api/who-said-it/sessions/${encodeURIComponent(sessionCode)}/end-turn`,
            {
              method: "POST",
              body: { player_id: playerId },
            },
          );
          await refreshSessionState();
        }).catch((err) => setSessionNotice(err.message || "Unable to reveal answers."));
      });
    }

    if (els.nextTurnBtn) {
      els.nextTurnBtn.addEventListener("click", () => {
        withPending(async () => {
          const { sessionCode, playerId } = currentIdentity();
          if (!sessionCode || !playerId) return;
          await api(
            `/api/who-said-it/sessions/${encodeURIComponent(sessionCode)}/next-turn`,
            {
              method: "POST",
              body: { player_id: playerId },
            },
          );
          state.selectedAuthor = "";
          await refreshSessionState();
        }).catch((err) => setSessionNotice(err.message || "Unable to start next turn."));
      });
    }

    if (els.endBtn) {
      els.endBtn.addEventListener("click", () => {
        withPending(async () => {
          const { sessionCode, playerId } = currentIdentity();
          if (!sessionCode || !playerId) return;
          await api(
            `/api/who-said-it/sessions/${encodeURIComponent(sessionCode)}/end`,
            {
              method: "POST",
              body: { player_id: playerId },
            },
          );
          await refreshSessionState();
        }).catch((err) => setSessionNotice(err.message || "Unable to end game."));
      });
    }

    if (els.leaveBtn) {
      els.leaveBtn.addEventListener("click", () => {
        withPending(async () => {
          await room.leave({ message: "You left the room.", swallowErrors: false });
        }).catch((err) => {
          room.reset(err.message || "You left the room.");
        });
      });
    }

    if (els.copyCodeBtn) {
      els.copyCodeBtn.addEventListener("click", async () => {
        const { sessionCode } = currentIdentity();
        const code = sessionCode || "";
        if (!code) return;
        const copied = await window.GameRoomCore.copyText(code);
        if (copied) {
          setSessionNotice(`Copied room code ${code}.`);
        } else {
          setSessionNotice(`Room code: ${code}`);
        }
      });
    }

    if (els.answerForm) {
      els.answerForm.addEventListener("submit", (event) => {
        event.preventDefault();
        withPending(async () => {
          const { sessionCode, playerId } = currentIdentity();
          if (!sessionCode || !playerId) return;
          if (!state.selectedAuthor) {
            setSessionNotice("Pick an author first.");
            return;
          }
          const payload = await api(
            `/api/who-said-it/sessions/${encodeURIComponent(sessionCode)}/answer`,
            {
              method: "POST",
              body: {
                player_id: playerId,
                selected_author: state.selectedAuthor,
              },
            },
          );
          if (payload.already_answered) {
            setSessionNotice("Answer already locked this round.");
          } else if (payload.is_correct) {
            setSessionNotice(
              `Correct. +${payload.points_awarded} point${payload.points_awarded === 1 ? "" : "s"}.`,
            );
          } else {
            setSessionNotice("Not this time.");
          }
          await refreshSessionState();
        }).catch((err) => setSessionNotice(err.message || "Unable to submit answer."));
      });
    }
  };

  const init = async () => {
    renderReadyState();
    renderLobbyOrSession(false);
    window.GameRoomCore.bindSessionCodeInput(els.joinCode);
    handleCreate();
    handleJoin();
    wireButtons();
    await room.tryResume();
  };

  init();
})();
