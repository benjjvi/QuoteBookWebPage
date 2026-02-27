from flask import current_app, jsonify, request

from api_errors import error_response


def register_game_api_routes(bp, context):
    quote_anarchy_service = context["quote_anarchy_service"]
    quote_blackline_service = context["quote_blackline_service"]
    quote_who_said_service = context["quote_who_said_service"]
    services = context["services"]

    def _api_error(status: int, code: str, message: str, details=None):
        return error_response(status=status, code=code, message=message, details=details)

    def _build_responder(
        *,
        log_label: str,
        unavailable_code: str,
        unavailable_message: str,
        game_error_code: str,
    ):
        def _respond(fn):
            try:
                payload = fn()
                return jsonify(payload)
            except Exception as exc:  # pragma: no cover - handled in integration tests
                status_code = int(getattr(exc, "status_code", 500))
                if status_code >= 500:
                    current_app.logger.error("%s API failure: %s", log_label, exc)
                    return _api_error(500, unavailable_code, unavailable_message)
                return _api_error(status_code, game_error_code, str(exc))

        return _respond

    _quote_anarchy_response = _build_responder(
        log_label="Quote Anarchy",
        unavailable_code="quote_anarchy_unavailable",
        unavailable_message="Quote Anarchy is temporarily unavailable.",
        game_error_code="quote_anarchy_error",
    )
    _blackline_response = _build_responder(
        log_label="Redacted Black Line Rush",
        unavailable_code="blackline_unavailable",
        unavailable_message="Redacted: Black Line Rush is temporarily unavailable.",
        game_error_code="blackline_error",
    )
    _who_said_response = _build_responder(
        log_label="Who Said It",
        unavailable_code="who_said_it_unavailable",
        unavailable_message="Who Even Said That? is temporarily unavailable.",
        game_error_code="who_said_it_error",
    )

    def _register_shared_room_routes(
        *,
        url_prefix: str,
        endpoint_prefix: str,
        service,
        responder,
        create_kwargs_builder,
        on_end=None,
    ) -> None:
        def _bootstrap():
            return responder(service.bootstrap)

        def _create_session():
            data = request.get_json(silent=True) or {}
            kwargs = create_kwargs_builder(data)
            return responder(lambda: service.create_session(**kwargs))

        def _join_session(session_code: str):
            data = request.get_json(silent=True) or {}
            player_name = (data.get("player_name") or "").strip()
            player_id = (data.get("player_id") or "").strip()
            return responder(
                lambda: service.join_session(
                    session_code=session_code,
                    player_name=player_name,
                    player_id=player_id or None,
                )
            )

        def _session_state(session_code: str):
            player_id = (request.args.get("player_id") or "").strip()
            return responder(
                lambda: service.get_state(
                    session_code=session_code,
                    player_id=player_id,
                )
            )

        def _start_session(session_code: str):
            data = request.get_json(silent=True) or {}
            player_id = (data.get("player_id") or "").strip()
            return responder(
                lambda: service.start_session(
                    session_code=session_code,
                    player_id=player_id,
                )
            )

        def _end_session(session_code: str):
            data = request.get_json(silent=True) or {}
            player_id = (data.get("player_id") or "").strip()

            def _run():
                payload = service.end_session(
                    session_code=session_code,
                    player_id=player_id,
                )
                if on_end:
                    on_end(payload)
                return payload

            return responder(_run)

        def _leave_session(session_code: str):
            data = request.get_json(silent=True) or {}
            player_id = (data.get("player_id") or "").strip()
            return responder(
                lambda: service.leave_session(
                    session_code=session_code,
                    player_id=player_id,
                )
            )

        bp.add_url_rule(
            f"/api/{url_prefix}/bootstrap",
            endpoint=f"api_{endpoint_prefix}_bootstrap",
            view_func=_bootstrap,
            methods=["GET"],
        )
        bp.add_url_rule(
            f"/api/{url_prefix}/sessions",
            endpoint=f"api_{endpoint_prefix}_create_session",
            view_func=_create_session,
            methods=["POST"],
        )
        bp.add_url_rule(
            f"/api/{url_prefix}/sessions/<string:session_code>/join",
            endpoint=f"api_{endpoint_prefix}_join_session",
            view_func=_join_session,
            methods=["POST"],
        )
        bp.add_url_rule(
            f"/api/{url_prefix}/sessions/<string:session_code>",
            endpoint=f"api_{endpoint_prefix}_session_state",
            view_func=_session_state,
            methods=["GET"],
        )
        bp.add_url_rule(
            f"/api/{url_prefix}/sessions/<string:session_code>/start",
            endpoint=f"api_{endpoint_prefix}_start_session",
            view_func=_start_session,
            methods=["POST"],
        )
        bp.add_url_rule(
            f"/api/{url_prefix}/sessions/<string:session_code>/end",
            endpoint=f"api_{endpoint_prefix}_end_session",
            view_func=_end_session,
            methods=["POST"],
        )
        bp.add_url_rule(
            f"/api/{url_prefix}/sessions/<string:session_code>/leave",
            endpoint=f"api_{endpoint_prefix}_leave_session",
            view_func=_leave_session,
            methods=["POST"],
        )

    def _quote_anarchy_create_kwargs(data: dict) -> dict:
        return {
            "player_name": (data.get("player_name") or "").strip(),
            "judging_mode": (data.get("judging_mode") or "").strip(),
            "max_rounds": data.get("max_rounds"),
        }

    def _default_create_kwargs(data: dict) -> dict:
        return {"player_name": (data.get("player_name") or "").strip()}

    def _on_quote_anarchy_end(payload: dict) -> None:
        if payload.get("ended"):
            services.refresh_stats_cache("quote-anarchy-session-ended-api")

    _register_shared_room_routes(
        url_prefix="quote-anarchy",
        endpoint_prefix="quote_anarchy",
        service=quote_anarchy_service,
        responder=_quote_anarchy_response,
        create_kwargs_builder=_quote_anarchy_create_kwargs,
        on_end=_on_quote_anarchy_end,
    )
    _register_shared_room_routes(
        url_prefix="blackline-rush",
        endpoint_prefix="blackline",
        service=quote_blackline_service,
        responder=_blackline_response,
        create_kwargs_builder=_default_create_kwargs,
    )
    _register_shared_room_routes(
        url_prefix="who-said-it",
        endpoint_prefix="who_said",
        service=quote_who_said_service,
        responder=_who_said_response,
        create_kwargs_builder=_default_create_kwargs,
    )

    @bp.route(
        "/api/quote-anarchy/solo/deal",
        methods=["POST"],
        endpoint="api_quote_anarchy_solo_deal",
    )
    def api_quote_anarchy_solo_deal():
        return _quote_anarchy_response(quote_anarchy_service.deal_solo_hand)

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
