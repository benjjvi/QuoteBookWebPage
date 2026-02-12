import secrets

from flask import Blueprint, current_app, jsonify, request, session


def create_api_blueprint(*, quote_store, services, vapid_public_key: str, vapid_private_key: str):
    bp = Blueprint("api", __name__)

    @bp.route("/api/latest", endpoint="api_latest_quote")
    def api_latest_quote():
        newest_quote = quote_store.get_latest_quote()
        if not newest_quote:
            current_app.logger.warning("API latest requested with no quotes.")
            return jsonify({"error": "No quotes found"}), 404
        current_app.logger.info("API latest quote served: %s", newest_quote.id)

        return jsonify(
            {
                "id": newest_quote.id,
                "quote": newest_quote.quote,
                "authors": newest_quote.authors,
                "timestamp": newest_quote.timestamp,
            }
        )

    @bp.route("/api/quotes", endpoint="api_quotes")
    def api_quotes():
        speaker = request.args.get("speaker")
        page = request.args.get("page", type=int)
        per_page = request.args.get("per_page", type=int)
        order = (request.args.get("order") or "oldest").strip().lower()
        if order not in ("oldest", "newest", "desc", "reverse"):
            order = "oldest"
        reverse_sort = order in ("newest", "desc", "reverse")

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
        else:
            total_pages = 1

        return jsonify(
            quotes=[services.quote_to_dict(q) for q in quotes],
            total=total,
            page=page or 1,
            per_page=per_page or total,
            total_pages=total_pages,
        )

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
