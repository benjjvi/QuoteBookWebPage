import json
from datetime import datetime
from xml.sax.saxutils import escape

from flask import (
    Response,
    abort,
    current_app,
    jsonify,
    render_template,
    request,
    session,
    send_from_directory,
    url_for,
)

from api_errors import error_response


def register_core_routes(bp, context):
    quote_store = context["quote_store"]
    ai_worker = context["ai_worker"]
    services = context["services"]
    quote_anarchy_service = context["quote_anarchy_service"]
    quote_blackline_service = context["quote_blackline_service"]
    quote_who_said_service = context["quote_who_said_service"]
    uk_tz = context["uk_tz"]
    edit_pin = context["edit_pin"]
    vapid_public_key = context["vapid_public_key"]
    support_url = context["support_url"]
    support_label = context["support_label"]
    sponsor_contact_url = context["sponsor_contact_url"]
    sponsor_contact_email = context["sponsor_contact_email"]
    affiliate_disclosure = context["affiliate_disclosure"]
    adsense_client_id = context["adsense_client_id"]
    adsense_slot_inline = context["adsense_slot_inline"]
    adsense_slot_footer = context["adsense_slot_footer"]
    google_adsense_account = context["google_adsense_account"]
    robots_disallow_all = context["robots_disallow_all"]

    @bp.app_context_processor
    def inject_marketing_context():
        configured_base = (services.config.public_base_url or "").strip().rstrip("/")
        fallback_base = request.url_root.rstrip("/")
        site_base_url = configured_base or fallback_base
        return {
            "site_base_url": site_base_url,
            "canonical_url": request.base_url,
            "support_url": support_url,
            "support_label": support_label,
            "sponsor_contact_url": sponsor_contact_url,
            "sponsor_contact_email": sponsor_contact_email,
            "affiliate_disclosure": affiliate_disclosure,
            "adsense_client_id": adsense_client_id,
            "adsense_slot_inline": adsense_slot_inline,
            "adsense_slot_footer": adsense_slot_footer,
            "google_adsense_account": google_adsense_account,
            "robots_disallow_all": robots_disallow_all,
        }

    @bp.route("/sw.js", endpoint="sw_js")
    def sw_js():
        return send_from_directory("static", "sw.js", mimetype="application/javascript")

    @bp.route("/manifest.webmanifest", endpoint="manifest_webmanifest")
    def manifest_webmanifest():
        return send_from_directory(
            "static", "manifest.webmanifest", mimetype="application/manifest+json"
        )

    @bp.route("/offline", endpoint="offline_page")
    def offline_page():
        return send_from_directory("static", "offline.html", mimetype="text/html")

    @bp.route("/pwa", endpoint="pwa_diag")
    def pwa_diag():
        return render_template("pwa.html")

    @bp.route("/robots.txt", endpoint="robots_txt")
    def robots_txt():
        sitemap_url = services.build_public_url("/sitemap.xml")
        if robots_disallow_all:
            content = "\n".join(
                [
                    "User-agent: *",
                    "Disallow: /",
                    f"Sitemap: {sitemap_url}",
                ]
            )
        else:
            content = "\n".join(
                [
                    "User-agent: *",
                    "Allow: /",
                    f"Sitemap: {sitemap_url}",
                ]
            )
        return Response(content, mimetype="text/plain")

    @bp.route("/sitemap.xml", endpoint="sitemap_xml")
    def sitemap_xml():
        now = datetime.now(uk_tz).strftime("%Y-%m-%d")
        static_pages = [
            ("/", "daily", "1.0"),
            ("/social", "daily", "0.9"),
            ("/all_quotes", "daily", "0.9"),
            ("/random", "daily", "0.8"),
            ("/stats", "daily", "0.7"),
            ("/search", "daily", "0.7"),
            ("/battle", "weekly", "0.6"),
            ("/games", "weekly", "0.7"),
            ("/games/blackline-rush", "weekly", "0.7"),
            ("/games/who-said-it", "weekly", "0.7"),
            ("/quote-anarchy", "weekly", "0.7"),
            ("/mailbox", "weekly", "0.6"),
            ("/unsubscribe", "monthly", "0.3"),
            (
                "/timeline/{}/{}".format(
                    datetime.now(uk_tz).year, datetime.now(uk_tz).month
                ),
                "weekly",
                "0.6",
            ),
            ("/credits", "monthly", "0.3"),
            ("/privacy", "monthly", "0.3"),
            ("/advertise", "monthly", "0.5"),
            ("/support", "monthly", "0.5"),
        ]

        url_entries = []
        for path, changefreq, priority in static_pages:
            loc = services.build_public_url(path)
            url_entries.append(
                (
                    "<url>"
                    f"<loc>{escape(loc)}</loc>"
                    f"<lastmod>{now}</lastmod>"
                    f"<changefreq>{changefreq}</changefreq>"
                    f"<priority>{priority}</priority>"
                    "</url>"
                )
            )

        for quote in quote_store.get_all_quotes():
            loc = services.build_public_url(url_for("web.quote_by_id", quote_id=quote.id))
            lastmod = datetime.fromtimestamp(quote.timestamp, tz=uk_tz).strftime(
                "%Y-%m-%d"
            )
            url_entries.append(
                (
                    "<url>"
                    f"<loc>{escape(loc)}</loc>"
                    f"<lastmod>{lastmod}</lastmod>"
                    "<changefreq>monthly</changefreq>"
                    "<priority>0.5</priority>"
                    "</url>"
                )
            )

        payload = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"{''.join(url_entries)}"
            "</urlset>"
        )
        return Response(payload, mimetype="application/xml")

    @bp.route("/support", endpoint="support_page")
    def support_page():
        return render_template("support.html")

    @bp.route("/advertise", endpoint="advertise_page")
    def advertise_page():
        return render_template("advertise.html")

    @bp.route("/", endpoint="index")
    def index():
        return render_template(
            "index.html",
            total_quotes=quote_store.get_total_quotes(),
            speaker_counts=quote_store.get_speaker_counts(),
            now=datetime.now(uk_tz),
            edit_enabled=bool(edit_pin),
            vapid_public_key=vapid_public_key,
            push_subscribe_token=services.get_push_subscribe_token(),
            email_subscribe_token=services.get_email_subscribe_token(),
        )

    @bp.route("/ai", endpoint="ai")
    def ai():
        return render_template(
            "ai.html",
            ai_available=ai_worker.can_generate,
            ai_request_token=(
                services.get_ai_request_token() if ai_worker.can_generate else ""
            ),
        )

    @bp.route("/ai_screenplay", methods=["POST"], endpoint="ai_screenplay")
    def ai_screenplay():
        if not ai_worker.can_generate:
            return error_response(
                status=503,
                code="ai_unavailable",
                message=(
                    "AI screenplay generation is disabled. "
                    "Set OPENROUTER_KEY to enable."
                ),
            )
        data = request.get_json(silent=True) or {}
        token = data.get("token")
        if not token or token != session.get("ai_request_token"):
            return error_response(
                status=403,
                code="ai_token_invalid",
                message="Invalid AI request token.",
            )
        current_app.logger.info("AI screenplay requested.")
        quotes = quote_store.get_all_quotes()
        scored_quotes = [
            (q, ai_worker.classify_funny_score(q.quote, q.authors, q.stats))
            for q in quotes
        ]
        top_20 = ai_worker.get_top_20_with_cache(scored_quotes)
        resp = ai_worker.get_ai(top_20)

        return jsonify(resp=resp)

    @bp.route(
        "/ai_screenplay_render", methods=["POST"], endpoint="ai_screenplay_render"
    )
    def ai_screenplay_render():
        data = json.loads(request.form["data"])
        rendered_at = datetime.now().strftime("%d %b %Y, %H:%M")
        return render_template(
            "ai_screenplay.html",
            title="AI Screenplay",
            screenplay=data.get("screenplay", ""),
            rendered_at=rendered_at,
        )

    @bp.route("/quote-anarchy", endpoint="quote_anarchy")
    def quote_anarchy():
        return render_template(
            "quote_anarchy.html",
            quote_anarchy_bootstrap=quote_anarchy_service.bootstrap(),
        )

    @bp.route("/games", endpoint="games")
    def games():
        return render_template(
            "games.html",
            quote_anarchy_bootstrap=quote_anarchy_service.bootstrap(),
            blackline_bootstrap=quote_blackline_service.bootstrap(),
            who_said_bootstrap=quote_who_said_service.bootstrap(),
        )

    @bp.route("/games/blackline-rush", endpoint="blackline_rush")
    def blackline_rush():
        return render_template(
            "blackline_rush.html",
            blackline_bootstrap=quote_blackline_service.bootstrap(),
        )

    @bp.route("/games/who-said-it", endpoint="who_said_it")
    def who_said_it():
        return render_template(
            "who_said_it.html",
            who_said_bootstrap=quote_who_said_service.bootstrap(),
        )

    @bp.route("/credits", endpoint="credits")
    def credits():
        return render_template("credits.html")

    @bp.route("/privacy", endpoint="privacy")
    def privacy():
        return render_template("privacy.html")

    @bp.route("/health", endpoint="health")
    def health():
        return jsonify(status="ok")

    @bp.route("/health/details", endpoint="health_details")
    def health_details():
        return jsonify(
            status="ok",
            metrics=services.get_runtime_metrics(),
            features={
                "ai_enabled": bool(ai_worker.can_generate),
                "edit_enabled": bool(edit_pin),
                "push_enabled": bool(
                    services.config.vapid_public_key and services.config.vapid_private_key
                ),
                "weekly_email_configured": bool(services.weekly_email_is_configured()),
                "weekly_scheduler_mode": services.resolve_weekly_scheduler_mode(),
            },
        )

    @bp.route("/cuppa", endpoint="cuppa")
    def cuppa():
        abort(418)
