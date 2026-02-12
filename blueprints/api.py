import secrets

import datetime_handler
from flask import Blueprint, current_app, jsonify, request, session, url_for


def _normalize_order(raw_order):
    order = (raw_order or "oldest").strip().lower()
    if order not in ("oldest", "newest", "desc", "reverse"):
        order = "oldest"
    return order, order in ("newest", "desc", "reverse")


def create_api_blueprint(*, quote_store, services, vapid_public_key: str, vapid_private_key: str):
    bp = Blueprint("api", __name__)

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
            speakers=[{"speaker": s, "count": c} for s, c in quote_store.get_speaker_counts()]
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

    @bp.route("/api/quotes/<int:quote_id>", methods=["PUT"], endpoint="api_update_quote")
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
                services.build_public_url(url_for("quote_by_id", quote_id=new_quote.id)),
            )
            current_app.logger.info("Push notifications sent after API add: %s", sent_count)
        except Exception as exc:
            current_app.logger.warning("Push notification failed after API add: %s", exc)

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
        current_app.logger.info("Battle recorded via API: winner=%s loser=%s", winner_id, loser_id)
        return jsonify(
            {"winner": services.quote_to_dict(winner), "loser": services.quote_to_dict(loser)}
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
        return jsonify(
            ok=True,
            already_subscribed=not created,
        )

    @bp.route("/api/push/unsubscribe", methods=["POST"], endpoint="api_push_unsubscribe")
    def api_push_unsubscribe():
        data = request.get_json(silent=True) or {}
        endpoint = data.get("endpoint")
        if not endpoint:
            return jsonify(error="Missing endpoint."), 400
        services.delete_push_subscription(endpoint)
        return jsonify(ok=True)

    return bp
