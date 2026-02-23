from flask import current_app, jsonify, request

from api_errors import error_response


def register_game_api_routes(bp, context):
    quote_anarchy_service = context["quote_anarchy_service"]
    quote_blackline_service = context["quote_blackline_service"]
    quote_who_said_service = context["quote_who_said_service"]
    services = context["services"]

    def _api_error(status: int, code: str, message: str, details=None):
        return error_response(status=status, code=code, message=message, details=details)

    def _quote_anarchy_response(fn):
        try:
            payload = fn()
            return jsonify(payload)
        except Exception as exc:
            status_code = getattr(exc, "status_code", 500)
            if status_code >= 500:
                current_app.logger.error("Quote Anarchy API failure: %s", exc)
                return _api_error(
                    500,
                    "quote_anarchy_unavailable",
                    "Quote Anarchy is temporarily unavailable.",
                )
            return _api_error(
                int(status_code),
                "quote_anarchy_error",
                str(exc),
            )

    def _blackline_response(fn):
        try:
            payload = fn()
            return jsonify(payload)
        except Exception as exc:
            status_code = getattr(exc, "status_code", 500)
            if status_code >= 500:
                current_app.logger.error("Redacted Black Line Rush API failure: %s", exc)
                return _api_error(
                    500,
                    "blackline_unavailable",
                    "Redacted: Black Line Rush is temporarily unavailable.",
                )
            return _api_error(
                int(status_code),
                "blackline_error",
                str(exc),
            )

    def _who_said_response(fn):
        try:
            payload = fn()
            return jsonify(payload)
        except Exception as exc:
            status_code = getattr(exc, "status_code", 500)
            if status_code >= 500:
                current_app.logger.error("Who Said It API failure: %s", exc)
                return _api_error(
                    500,
                    "who_said_it_unavailable",
                    "Who Even Said That? is temporarily unavailable.",
                )
            return _api_error(
                int(status_code),
                "who_said_it_error",
                str(exc),
            )

    @bp.route(
        "/api/quote-anarchy/bootstrap",
        methods=["GET"],
        endpoint="api_quote_anarchy_bootstrap",
    )
    def api_quote_anarchy_bootstrap():
        return _quote_anarchy_response(quote_anarchy_service.bootstrap)

    @bp.route(
        "/api/quote-anarchy/solo/deal",
        methods=["POST"],
        endpoint="api_quote_anarchy_solo_deal",
    )
    def api_quote_anarchy_solo_deal():
        return _quote_anarchy_response(quote_anarchy_service.deal_solo_hand)

    @bp.route(
        "/api/quote-anarchy/sessions",
        methods=["POST"],
        endpoint="api_quote_anarchy_create_session",
    )
    def api_quote_anarchy_create_session():
        data = request.get_json(silent=True) or {}
        player_name = (data.get("player_name") or "").strip()
        judging_mode = (data.get("judging_mode") or "").strip()
        max_rounds = data.get("max_rounds")
        return _quote_anarchy_response(
            lambda: quote_anarchy_service.create_session(
                player_name=player_name,
                judging_mode=judging_mode,
                max_rounds=max_rounds,
            )
        )

    @bp.route(
        "/api/quote-anarchy/sessions/<string:session_code>/join",
        methods=["POST"],
        endpoint="api_quote_anarchy_join_session",
    )
    def api_quote_anarchy_join_session(session_code: str):
        data = request.get_json(silent=True) or {}
        player_name = (data.get("player_name") or "").strip()
        player_id = (data.get("player_id") or "").strip()
        return _quote_anarchy_response(
            lambda: quote_anarchy_service.join_session(
                session_code=session_code,
                player_name=player_name,
                player_id=player_id or None,
            )
        )

    @bp.route(
        "/api/quote-anarchy/sessions/<string:session_code>",
        methods=["GET"],
        endpoint="api_quote_anarchy_session_state",
    )
    def api_quote_anarchy_session_state(session_code: str):
        player_id = (request.args.get("player_id") or "").strip()
        return _quote_anarchy_response(
            lambda: quote_anarchy_service.get_state(
                session_code=session_code,
                player_id=player_id,
            )
        )

    @bp.route(
        "/api/quote-anarchy/sessions/<string:session_code>/start",
        methods=["POST"],
        endpoint="api_quote_anarchy_start_session",
    )
    def api_quote_anarchy_start_session(session_code: str):
        data = request.get_json(silent=True) or {}
        player_id = (data.get("player_id") or "").strip()
        return _quote_anarchy_response(
            lambda: quote_anarchy_service.start_session(
                session_code=session_code,
                player_id=player_id,
            )
        )

    @bp.route(
        "/api/quote-anarchy/sessions/<string:session_code>/submit",
        methods=["POST"],
        endpoint="api_quote_anarchy_submit_card",
    )
    def api_quote_anarchy_submit_card(session_code: str):
        data = request.get_json(silent=True) or {}
        player_id = (data.get("player_id") or "").strip()
        quote_id = data.get("quote_id")
        return _quote_anarchy_response(
            lambda: quote_anarchy_service.submit_card(
                session_code=session_code,
                player_id=player_id,
                quote_id=quote_id,
            )
        )

    @bp.route(
        "/api/quote-anarchy/sessions/<string:session_code>/pick-winner",
        methods=["POST"],
        endpoint="api_quote_anarchy_pick_winner",
    )
    def api_quote_anarchy_pick_winner(session_code: str):
        data = request.get_json(silent=True) or {}
        player_id = (data.get("player_id") or "").strip()
        winner_player_id = (data.get("winner_player_id") or "").strip()

        def _pick_winner():
            payload = quote_anarchy_service.pick_winner(
                session_code=session_code,
                player_id=player_id,
                winner_player_id=winner_player_id,
            )
            if payload.get("winners_recorded") or payload.get("game_completed"):
                services.refresh_stats_cache("quote-anarchy-winner-picked-api")
            return payload

        return _quote_anarchy_response(_pick_winner)

    @bp.route(
        "/api/quote-anarchy/sessions/<string:session_code>/vote",
        methods=["POST"],
        endpoint="api_quote_anarchy_vote_submission",
    )
    def api_quote_anarchy_vote_submission(session_code: str):
        data = request.get_json(silent=True) or {}
        player_id = (data.get("player_id") or "").strip()
        voted_player_id = (data.get("voted_player_id") or "").strip()

        def _vote_submission():
            payload = quote_anarchy_service.vote_submission(
                session_code=session_code,
                player_id=player_id,
                voted_player_id=voted_player_id,
            )
            if payload.get("winners_recorded") or payload.get("game_completed"):
                services.refresh_stats_cache("quote-anarchy-vote-resolved-api")
            return payload

        return _quote_anarchy_response(_vote_submission)

    @bp.route(
        "/api/quote-anarchy/sessions/<string:session_code>/next-round",
        methods=["POST"],
        endpoint="api_quote_anarchy_next_round",
    )
    def api_quote_anarchy_next_round(session_code: str):
        data = request.get_json(silent=True) or {}
        player_id = (data.get("player_id") or "").strip()
        return _quote_anarchy_response(
            lambda: quote_anarchy_service.next_round(
                session_code=session_code,
                player_id=player_id,
            )
        )

    @bp.route(
        "/api/quote-anarchy/sessions/<string:session_code>/end",
        methods=["POST"],
        endpoint="api_quote_anarchy_end_session",
    )
    def api_quote_anarchy_end_session(session_code: str):
        data = request.get_json(silent=True) or {}
        player_id = (data.get("player_id") or "").strip()

        def _end_session():
            payload = quote_anarchy_service.end_session(
                session_code=session_code,
                player_id=player_id,
            )
            if payload.get("ended"):
                services.refresh_stats_cache("quote-anarchy-session-ended-api")
            return payload

        return _quote_anarchy_response(_end_session)

    @bp.route(
        "/api/quote-anarchy/sessions/<string:session_code>/leave",
        methods=["POST"],
        endpoint="api_quote_anarchy_leave_session",
    )
    def api_quote_anarchy_leave_session(session_code: str):
        data = request.get_json(silent=True) or {}
        player_id = (data.get("player_id") or "").strip()
        return _quote_anarchy_response(
            lambda: quote_anarchy_service.leave_session(
                session_code=session_code,
                player_id=player_id,
            )
        )

    @bp.route(
        "/api/blackline-rush/bootstrap",
        methods=["GET"],
        endpoint="api_blackline_bootstrap",
    )
    def api_blackline_bootstrap():
        return _blackline_response(quote_blackline_service.bootstrap)

    @bp.route(
        "/api/blackline-rush/sessions",
        methods=["POST"],
        endpoint="api_blackline_create_session",
    )
    def api_blackline_create_session():
        data = request.get_json(silent=True) or {}
        player_name = (data.get("player_name") or "").strip()
        return _blackline_response(
            lambda: quote_blackline_service.create_session(player_name=player_name)
        )

    @bp.route(
        "/api/blackline-rush/sessions/<string:session_code>/join",
        methods=["POST"],
        endpoint="api_blackline_join_session",
    )
    def api_blackline_join_session(session_code: str):
        data = request.get_json(silent=True) or {}
        player_name = (data.get("player_name") or "").strip()
        player_id = (data.get("player_id") or "").strip()
        return _blackline_response(
            lambda: quote_blackline_service.join_session(
                session_code=session_code,
                player_name=player_name,
                player_id=player_id or None,
            )
        )

    @bp.route(
        "/api/blackline-rush/sessions/<string:session_code>",
        methods=["GET"],
        endpoint="api_blackline_session_state",
    )
    def api_blackline_session_state(session_code: str):
        player_id = (request.args.get("player_id") or "").strip()
        return _blackline_response(
            lambda: quote_blackline_service.get_state(
                session_code=session_code,
                player_id=player_id,
            )
        )

    @bp.route(
        "/api/blackline-rush/sessions/<string:session_code>/start",
        methods=["POST"],
        endpoint="api_blackline_start_session",
    )
    def api_blackline_start_session(session_code: str):
        data = request.get_json(silent=True) or {}
        player_id = (data.get("player_id") or "").strip()
        return _blackline_response(
            lambda: quote_blackline_service.start_session(
                session_code=session_code,
                player_id=player_id,
            )
        )

    @bp.route(
        "/api/blackline-rush/sessions/<string:session_code>/submit-redaction",
        methods=["POST"],
        endpoint="api_blackline_submit_redaction",
    )
    def api_blackline_submit_redaction(session_code: str):
        data = request.get_json(silent=True) or {}
        player_id = (data.get("player_id") or "").strip()
        redaction_indices = data.get("redaction_indices") or []
        return _blackline_response(
            lambda: quote_blackline_service.submit_redaction(
                session_code=session_code,
                player_id=player_id,
                redaction_indices=redaction_indices,
            )
        )

    @bp.route(
        "/api/blackline-rush/sessions/<string:session_code>/guess",
        methods=["POST"],
        endpoint="api_blackline_submit_guess",
    )
    def api_blackline_submit_guess(session_code: str):
        data = request.get_json(silent=True) or {}
        player_id = (data.get("player_id") or "").strip()
        guesses = data.get("guesses") or []
        return _blackline_response(
            lambda: quote_blackline_service.submit_guess(
                session_code=session_code,
                player_id=player_id,
                guesses=guesses,
            )
        )

    @bp.route(
        "/api/blackline-rush/sessions/<string:session_code>/end-turn",
        methods=["POST"],
        endpoint="api_blackline_end_turn",
    )
    def api_blackline_end_turn(session_code: str):
        data = request.get_json(silent=True) or {}
        player_id = (data.get("player_id") or "").strip()
        return _blackline_response(
            lambda: quote_blackline_service.end_turn(
                session_code=session_code,
                player_id=player_id,
            )
        )

    @bp.route(
        "/api/blackline-rush/sessions/<string:session_code>/next-turn",
        methods=["POST"],
        endpoint="api_blackline_next_turn",
    )
    def api_blackline_next_turn(session_code: str):
        data = request.get_json(silent=True) or {}
        player_id = (data.get("player_id") or "").strip()
        return _blackline_response(
            lambda: quote_blackline_service.next_turn(
                session_code=session_code,
                player_id=player_id,
            )
        )

    @bp.route(
        "/api/blackline-rush/sessions/<string:session_code>/end",
        methods=["POST"],
        endpoint="api_blackline_end_session",
    )
    def api_blackline_end_session(session_code: str):
        data = request.get_json(silent=True) or {}
        player_id = (data.get("player_id") or "").strip()
        return _blackline_response(
            lambda: quote_blackline_service.end_session(
                session_code=session_code,
                player_id=player_id,
            )
        )

    @bp.route(
        "/api/blackline-rush/sessions/<string:session_code>/leave",
        methods=["POST"],
        endpoint="api_blackline_leave_session",
    )
    def api_blackline_leave_session(session_code: str):
        data = request.get_json(silent=True) or {}
        player_id = (data.get("player_id") or "").strip()
        return _blackline_response(
            lambda: quote_blackline_service.leave_session(
                session_code=session_code,
                player_id=player_id,
            )
        )

    @bp.route(
        "/api/who-said-it/bootstrap",
        methods=["GET"],
        endpoint="api_who_said_bootstrap",
    )
    def api_who_said_bootstrap():
        return _who_said_response(quote_who_said_service.bootstrap)

    @bp.route(
        "/api/who-said-it/sessions",
        methods=["POST"],
        endpoint="api_who_said_create_session",
    )
    def api_who_said_create_session():
        data = request.get_json(silent=True) or {}
        player_name = (data.get("player_name") or "").strip()
        return _who_said_response(
            lambda: quote_who_said_service.create_session(player_name=player_name)
        )

    @bp.route(
        "/api/who-said-it/sessions/<string:session_code>/join",
        methods=["POST"],
        endpoint="api_who_said_join_session",
    )
    def api_who_said_join_session(session_code: str):
        data = request.get_json(silent=True) or {}
        player_name = (data.get("player_name") or "").strip()
        player_id = (data.get("player_id") or "").strip()
        return _who_said_response(
            lambda: quote_who_said_service.join_session(
                session_code=session_code,
                player_name=player_name,
                player_id=player_id or None,
            )
        )

    @bp.route(
        "/api/who-said-it/sessions/<string:session_code>",
        methods=["GET"],
        endpoint="api_who_said_session_state",
    )
    def api_who_said_session_state(session_code: str):
        player_id = (request.args.get("player_id") or "").strip()
        return _who_said_response(
            lambda: quote_who_said_service.get_state(
                session_code=session_code,
                player_id=player_id,
            )
        )

    @bp.route(
        "/api/who-said-it/sessions/<string:session_code>/start",
        methods=["POST"],
        endpoint="api_who_said_start_session",
    )
    def api_who_said_start_session(session_code: str):
        data = request.get_json(silent=True) or {}
        player_id = (data.get("player_id") or "").strip()
        return _who_said_response(
            lambda: quote_who_said_service.start_session(
                session_code=session_code,
                player_id=player_id,
            )
        )

    @bp.route(
        "/api/who-said-it/sessions/<string:session_code>/answer",
        methods=["POST"],
        endpoint="api_who_said_submit_answer",
    )
    def api_who_said_submit_answer(session_code: str):
        data = request.get_json(silent=True) or {}
        player_id = (data.get("player_id") or "").strip()
        selected_author = (data.get("selected_author") or "").strip()
        return _who_said_response(
            lambda: quote_who_said_service.submit_answer(
                session_code=session_code,
                player_id=player_id,
                selected_author=selected_author,
            )
        )

    @bp.route(
        "/api/who-said-it/sessions/<string:session_code>/end-turn",
        methods=["POST"],
        endpoint="api_who_said_end_turn",
    )
    def api_who_said_end_turn(session_code: str):
        data = request.get_json(silent=True) or {}
        player_id = (data.get("player_id") or "").strip()
        return _who_said_response(
            lambda: quote_who_said_service.end_turn(
                session_code=session_code,
                player_id=player_id,
            )
        )

    @bp.route(
        "/api/who-said-it/sessions/<string:session_code>/next-turn",
        methods=["POST"],
        endpoint="api_who_said_next_turn",
    )
    def api_who_said_next_turn(session_code: str):
        data = request.get_json(silent=True) or {}
        player_id = (data.get("player_id") or "").strip()
        return _who_said_response(
            lambda: quote_who_said_service.next_turn(
                session_code=session_code,
                player_id=player_id,
            )
        )

    @bp.route(
        "/api/who-said-it/sessions/<string:session_code>/end",
        methods=["POST"],
        endpoint="api_who_said_end_session",
    )
    def api_who_said_end_session(session_code: str):
        data = request.get_json(silent=True) or {}
        player_id = (data.get("player_id") or "").strip()
        return _who_said_response(
            lambda: quote_who_said_service.end_session(
                session_code=session_code,
                player_id=player_id,
            )
        )

    @bp.route(
        "/api/who-said-it/sessions/<string:session_code>/leave",
        methods=["POST"],
        endpoint="api_who_said_leave_session",
    )
    def api_who_said_leave_session(session_code: str):
        data = request.get_json(silent=True) or {}
        player_id = (data.get("player_id") or "").strip()
        return _who_said_response(
            lambda: quote_who_said_service.leave_session(
                session_code=session_code,
                player_id=player_id,
            )
        )
