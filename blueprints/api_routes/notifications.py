import secrets

from flask import jsonify, make_response, request, session

from api_errors import error_response


def register_notification_api_routes(bp, context):
    services = context["services"]
    vapid_public_key = context["vapid_public_key"]
    vapid_private_key = context["vapid_private_key"]

    @bp.route("/api/ops/metrics", methods=["GET"], endpoint="api_ops_metrics")
    def api_ops_metrics():
        return jsonify(metrics=services.get_runtime_metrics())

    @bp.route("/api/push/subscribe", methods=["POST"], endpoint="api_push_subscribe")
    def api_push_subscribe():
        if not vapid_public_key or not vapid_private_key:
            return error_response(
                status=503,
                code="push_not_configured",
                message="Push notifications are not configured.",
            )

        data = request.get_json(silent=True) or {}
        token = data.get("token")
        if not token or token != session.get("push_subscribe_token"):
            return error_response(
                status=403,
                code="invalid_subscription_token",
                message="Invalid subscription token.",
            )
        subscription = data.get("subscription") or data
        user_agent = data.get("userAgent") or request.headers.get("User-Agent", "")

        if not isinstance(subscription, dict) or not subscription.get("endpoint"):
            return error_response(
                status=400,
                code="invalid_subscription_payload",
                message="Invalid subscription payload.",
            )

        saved = services.save_push_subscription(subscription, user_agent)
        return jsonify(ok=bool(saved))

    @bp.route("/api/push/token", methods=["GET"], endpoint="api_push_token")
    def api_push_token():
        if not vapid_public_key or not vapid_private_key:
            return error_response(
                status=503,
                code="push_not_configured",
                message="Push notifications are not configured.",
            )
        return jsonify(token=services.get_push_subscribe_token())

    @bp.route("/api/email/token", methods=["GET"], endpoint="api_email_token")
    def api_email_token():
        return jsonify(token=services.get_email_subscribe_token())

    @bp.route("/api/email/subscribe", methods=["POST"], endpoint="api_email_subscribe")
    def api_email_subscribe():
        if not services.ensure_weekly_email_recipients_table():
            return error_response(
                status=503,
                code="email_unavailable",
                message="Email subscriptions are unavailable right now.",
            )

        data = request.get_json(silent=True) or {}
        token = data.get("token")
        if not token or token != session.get("email_subscribe_token"):
            return error_response(
                status=403,
                code="invalid_subscription_token",
                message="Invalid subscription token.",
            )

        email = (data.get("email") or "").strip().lower()
        if not services.is_valid_email_address(email):
            return error_response(
                status=400,
                code="email_invalid",
                message="Please enter a valid email address.",
            )

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
            return error_response(
                status=503,
                code="email_unavailable",
                message="Email subscriptions are unavailable right now.",
            )

        data = request.get_json(silent=True) or {}
        token = data.get("token")
        if not token or token != session.get("email_subscribe_token"):
            return error_response(
                status=403,
                code="invalid_subscription_token",
                message="Invalid subscription token.",
            )

        email = (data.get("email") or "").strip().lower()
        if not services.is_valid_email_address(email):
            return error_response(
                status=400,
                code="email_invalid",
                message="Please enter a valid email address.",
            )

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
            return error_response(
                status=400,
                code="endpoint_missing",
                message="Missing endpoint.",
            )
        services.delete_push_subscription(endpoint)
        return jsonify(ok=True)
