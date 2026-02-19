import secrets

from flask import (
    Blueprint,
    current_app,
    jsonify,
    make_response,
    request,
    session,
    url_for,
)

import datetime_handler


def _normalize_order(raw_order):
    order = (raw_order or "oldest").strip().lower()
    if order not in ("oldest", "newest", "desc", "reverse"):
        order = "oldest"
    return order, order in ("newest", "desc", "reverse")


def create_api_blueprint(
    *,
    quote_store,
    services,
    quote_anarchy_service,
    quote_blackline_service,
    vapid_public_key: str,
    vapid_private_key: str,
):
    bp = Blueprint("api", __name__)

    def _quote_anarchy_response(fn):
        try:
            payload = fn()
            return jsonify(payload)
        except Exception as exc:
            status_code = getattr(exc, "status_code", 500)
            if status_code >= 500:
                current_app.logger.error("Quote Anarchy API failure: %s", exc)
                return jsonify(error="Quote Anarchy is temporarily unavailable."), 500
            return jsonify(error=str(exc)), status_code

    def _blackline_response(fn):
        try:
            payload = fn()
            return jsonify(payload)
        except Exception as exc:
            status_code = getattr(exc, "status_code", 500)
            if status_code >= 500:
                current_app.logger.error("Blackline Rush API failure: %s", exc)
                return jsonify(error="Blackline Rush is temporarily unavailable."), 500
            return jsonify(error=str(exc)), status_code

    @bp.route("/api/latest", endpoint="api_latest_quote")
    def api_latest_quote():
        newest_quote = quote_store.get_latest_quote()
        if not newest_quote:
            current_app.logger.warning("API latest requested with no quotes.")
            return jsonify({"error": "No quotes found"}), 404
        current_app.logger.info("API latest quote served: %s", newest_quote.id)
        return jsonify(services.quote_to_dict(newest_quote))

    @bp.route("/api/speakers", endpoint="api_speakers")
    def api_speakers():
        return jsonify(
            speakers=[
                {"speaker": s, "count": c} for s, c in quote_store.get_speaker_counts()
            ]
        )

    @bp.route("/api/quotes", endpoint="api_quotes")
    def api_quotes():
        speaker = request.args.get("speaker")
        page = request.args.get("page", type=int)
        per_page = request.args.get("per_page", type=int)
        normalized_order, reverse_sort = _normalize_order(request.args.get("order"))

        quotes = quote_store.get_all_quotes()
        if speaker:
            speaker_lower = speaker.lower()
            quotes = [
                q
                for q in quotes
                if any(speaker_lower == author.lower() for author in q.authors)
            ]
        quotes = sorted(quotes, key=lambda q: (q.timestamp, q.id), reverse=reverse_sort)

        total = len(quotes)
        if page and per_page and per_page > 0:
            page = max(1, page)
            total_pages = max(1, (total + per_page - 1) // per_page)
            page = min(page, total_pages)
            start = (page - 1) * per_page
            end = start + per_page
            quotes = quotes[start:end]
            effective_per_page = per_page
        else:
            page = 1
            total_pages = 1
            effective_per_page = total

        return jsonify(
            quotes=[services.quote_to_dict(q) for q in quotes],
            total=total,
            page=page,
            per_page=effective_per_page,
            total_pages=total_pages,
            order=normalized_order,
        )

    @bp.route("/api/quotes/random", endpoint="api_random")
    def api_random():
        random_quote = quote_store.get_random_quote()
        if not random_quote:
            return jsonify({"error": "No quotes found"}), 404
        return jsonify(services.quote_to_dict(random_quote))

    @bp.route("/api/quotes/<int:quote_id>", endpoint="api_quote_by_id")
    def api_quote_by_id(quote_id: int):
        quote = quote_store.get_quote_by_id(quote_id)
        if not quote:
            return jsonify({"error": "Quote not found"}), 404
        return jsonify(services.quote_to_dict(quote))

    @bp.route(
        "/api/quotes/<int:quote_id>", methods=["PUT"], endpoint="api_update_quote"
    )
    def api_update_quote(quote_id: int):
        data = request.get_json(silent=True) or {}
        quote_text = (data.get("quote") or "").strip()
        context = (data.get("context") or "").strip()
        authors_raw = data.get("authors")

        if not quote_text:
            return jsonify({"error": "quote is required"}), 400

        if isinstance(authors_raw, list):
            authors = [str(a).strip() for a in authors_raw if str(a).strip()]
        else:
            authors = quote_store.parse_authors(str(authors_raw or "Unknown"))

        updated = quote_store.update_quote(
            quote_id=quote_id,
            quote_text=quote_text,
            authors=authors,
            context=context,
        )
        if not updated:
            return jsonify({"error": "Quote not found"}), 404

        services.refresh_stats_cache("quote-updated-api")
        current_app.logger.info("Updated quote %s via API", quote_id)
        return jsonify(services.quote_to_dict(updated))

    @bp.route("/api/quotes/between", endpoint="api_quotes_between")
    def api_quotes_between():
        start_ts = request.args.get("start_ts", type=int)
        end_ts = request.args.get("end_ts", type=int)
        if start_ts is None or end_ts is None:
            return jsonify({"error": "start_ts and end_ts are required"}), 400
        quotes = quote_store.get_quotes_between(start_ts, end_ts)
        return jsonify(quotes=[services.quote_to_dict(q) for q in quotes])

    @bp.route("/api/search", endpoint="api_search")
    def api_search():
        query = request.args.get("query", "").strip()
        if not query:
            return jsonify(quotes=[], query=query)
        results = quote_store.search_quotes(query)
        return jsonify(quotes=[services.quote_to_dict(q) for q in results], query=query)

    @bp.route("/api/quotes", methods=["POST"], endpoint="api_add_quote")
    def api_add_quote():
        data = request.get_json(silent=True) or {}
        quote_text = (data.get("quote") or "").strip()
        context = (data.get("context") or "").strip()
        authors_raw = data.get("authors")

        if not quote_text:
            return jsonify({"error": "quote is required"}), 400

        if isinstance(authors_raw, list):
            authors = [str(a).strip() for a in authors_raw if str(a).strip()]
        else:
            authors = quote_store.parse_authors(str(authors_raw or "Unknown"))

        timestamp = data.get("timestamp")
        if timestamp is None:
            timestamp = datetime_handler.get_current_uk_timestamp()

        new_quote = quote_store.add_quote(
            quote_text=quote_text,
            authors=authors,
            context=context,
            timestamp=int(timestamp),
        )

        services.refresh_stats_cache("quote-added-api")
        try:
            sent_count = services.send_push_notification(
                "People are chatting...",
                f"New quote by {', '.join(new_quote.authors) or 'Unknown'}",
                services.build_public_url(
                    url_for("quote_by_id", quote_id=new_quote.id)
                ),
            )
            current_app.logger.info(
                "Push notifications sent after API add: %s", sent_count
            )
        except Exception as exc:
            current_app.logger.warning(
                "Push notification failed after API add: %s", exc
            )

        current_app.logger.info("Added quote %s via API", new_quote.id)
        return jsonify(services.quote_to_dict(new_quote)), 201

    @bp.route("/api/battles", methods=["POST"], endpoint="api_battle")
    def api_battle():
        data = request.get_json(silent=True) or {}
        winner_id = data.get("winner_id")
        loser_id = data.get("loser_id")

        if winner_id is None or loser_id is None:
            return jsonify({"error": "winner_id and loser_id are required"}), 400

        winner, loser = quote_store.record_battle(int(winner_id), int(loser_id))
        if not winner or not loser:
            return jsonify({"error": "Quote not found"}), 404

        services.refresh_stats_cache("battle-recorded-api")
        current_app.logger.info(
            "Battle recorded via API: winner=%s loser=%s", winner_id, loser_id
        )
        return jsonify(
            {
                "winner": services.quote_to_dict(winner),
                "loser": services.quote_to_dict(loser),
            }
        )

    @bp.route(
        "/api/quote-anarchy-wins", methods=["POST"], endpoint="api_quote_anarchy_wins"
    )
    def api_quote_anarchy_wins():
        data = request.get_json(silent=True) or {}
        quote_ids_raw = data.get("quote_ids")
        if not isinstance(quote_ids_raw, list):
            return jsonify({"error": "quote_ids must be a list"}), 400

        quote_ids = []
        seen_ids = set()
        for raw_id in quote_ids_raw:
            try:
                quote_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            if quote_id <= 0 or quote_id in seen_ids:
                continue
            seen_ids.add(quote_id)
            quote_ids.append(quote_id)

        if not quote_ids:
            return jsonify({"error": "No valid quote_ids provided"}), 400

        updated_quotes = quote_store.record_quote_anarchy_wins(quote_ids)
        if not updated_quotes:
            return jsonify({"error": "No matching quotes found"}), 404

        services.refresh_stats_cache("quote-anarchy-wins-api")
        return jsonify(
            {
                "quotes": [services.quote_to_dict(quote) for quote in updated_quotes],
                "updated_count": len(updated_quotes),
            }
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

    @bp.route("/api/ops/metrics", methods=["GET"], endpoint="api_ops_metrics")
    def api_ops_metrics():
        return jsonify(metrics=services.get_runtime_metrics())

    @bp.route("/api/push/subscribe", methods=["POST"], endpoint="api_push_subscribe")
    def api_push_subscribe():
        if not vapid_public_key or not vapid_private_key:
            return jsonify(error="Push notifications are not configured."), 503

        data = request.get_json(silent=True) or {}
        token = data.get("token")
        if not token or token != session.get("push_subscribe_token"):
            return jsonify(error="Invalid subscription token."), 403
        subscription = data.get("subscription") or data
        user_agent = data.get("userAgent") or request.headers.get("User-Agent", "")

        if not isinstance(subscription, dict) or not subscription.get("endpoint"):
            return jsonify(error="Invalid subscription payload."), 400

        saved = services.save_push_subscription(subscription, user_agent)
        return jsonify(ok=bool(saved))

    @bp.route("/api/push/token", methods=["GET"], endpoint="api_push_token")
    def api_push_token():
        if not vapid_public_key or not vapid_private_key:
            return jsonify(error="Push notifications are not configured."), 503
        return jsonify(token=services.get_push_subscribe_token())

    @bp.route("/api/email/token", methods=["GET"], endpoint="api_email_token")
    def api_email_token():
        return jsonify(token=services.get_email_subscribe_token())

    @bp.route("/api/email/subscribe", methods=["POST"], endpoint="api_email_subscribe")
    def api_email_subscribe():
        if not services.ensure_weekly_email_recipients_table():
            return jsonify(error="Email subscriptions are unavailable right now."), 503

        data = request.get_json(silent=True) or {}
        token = data.get("token")
        if not token or token != session.get("email_subscribe_token"):
            return jsonify(error="Invalid subscription token."), 403

        email = (data.get("email") or "").strip().lower()
        if not services.is_valid_email_address(email):
            return jsonify(error="Please enter a valid email address."), 400

        created = services.add_weekly_email_recipient(email)
        session["email_subscribe_token"] = secrets.token_urlsafe(24)
        response = make_response(
            jsonify(
                ok=True,
                already_subscribed=not created,
            )
        )
        response.set_cookie(
            "qb_email_subscribed",
            "true",
            max_age=365 * 24 * 60 * 60,
            samesite="Lax",
            secure=bool(services.config.is_prod),
        )
        response.set_cookie(
            "qb_email_address",
            email,
            max_age=365 * 24 * 60 * 60,
            samesite="Lax",
            secure=bool(services.config.is_prod),
        )
        return response

    @bp.route(
        "/api/email/unsubscribe", methods=["POST"], endpoint="api_email_unsubscribe"
    )
    def api_email_unsubscribe():
        if not services.ensure_weekly_email_recipients_table():
            return jsonify(error="Email subscriptions are unavailable right now."), 503

        data = request.get_json(silent=True) or {}
        token = data.get("token")
        if not token or token != session.get("email_subscribe_token"):
            return jsonify(error="Invalid subscription token."), 403

        email = (data.get("email") or "").strip().lower()
        if not services.is_valid_email_address(email):
            return jsonify(error="Please enter a valid email address."), 400

        removed = services.remove_weekly_email_recipient(email)
        session["email_subscribe_token"] = secrets.token_urlsafe(24)
        response = make_response(jsonify(ok=True, removed=bool(removed)))
        response.delete_cookie("qb_email_subscribed")
        response.delete_cookie("qb_email_address")
        return response

    @bp.route(
        "/api/push/unsubscribe", methods=["POST"], endpoint="api_push_unsubscribe"
    )
    def api_push_unsubscribe():
        data = request.get_json(silent=True) or {}
        endpoint = data.get("endpoint")
        if not endpoint:
            return jsonify(error="Missing endpoint."), 400
        services.delete_push_subscription(endpoint)
        return jsonify(ok=True)

    return bp
