import calendar as pycalendar
import json
import random as randlib
from datetime import datetime, time, timedelta
from xml.sax.saxutils import escape

import datetime_handler
from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)


def create_web_blueprint(
    *,
    quote_store,
    ai_worker,
    services,
    uk_tz,
    edit_pin: str,
    vapid_public_key: str,
    per_page_quote_limit: int,
    support_url: str,
    support_label: str,
    sponsor_contact_url: str,
    sponsor_contact_email: str,
    affiliate_disclosure: str,
    adsense_client_id: str,
    adsense_slot_inline: str,
    adsense_slot_footer: str,
    google_adsense_account: str,
    robots_disallow_all: bool,
):
    bp = Blueprint("web", __name__)

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
            ("/all_quotes", "daily", "0.9"),
            ("/random", "daily", "0.8"),
            ("/stats", "daily", "0.7"),
            ("/search", "daily", "0.7"),
            ("/battle", "weekly", "0.6"),
            ("/timeline/{}/{}".format(datetime.now(uk_tz).year, datetime.now(uk_tz).month), "weekly", "0.6"),
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
            lastmod = datetime.fromtimestamp(quote.timestamp, tz=uk_tz).strftime("%Y-%m-%d")
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

    @bp.route("/add_quote", methods=["GET", "POST"], endpoint="add_quote")
    def add_quote():
        if request.method == "POST":
            quote_text = request.form.get("quote_text", "").strip()
            context = request.form.get("context", "").strip()
            author_raw = request.form.get("author_info", "Unknown").strip()
            quote_datetime_raw = request.form.get("quote_datetime", "").strip()

            if quote_text:
                authors = quote_store.parse_authors(author_raw)

                timestamp = datetime_handler.get_current_uk_timestamp()
                if quote_datetime_raw:
                    try:
                        selected_dt = datetime.strptime(quote_datetime_raw, "%Y-%m-%dT%H:%M")
                        selected_dt = selected_dt.replace(tzinfo=uk_tz)
                        timestamp = int(selected_dt.timestamp())
                    except ValueError:
                        current_app.logger.warning(
                            "Invalid quote_datetime value '%s'; using current time.",
                            quote_datetime_raw,
                        )

                new_quote = quote_store.add_quote(
                    quote_text=quote_text,
                    authors=authors,
                    context=context,
                    timestamp=timestamp,
                )
                services.refresh_stats_cache("quote-added")
                current_app.logger.info(
                    "Added quote %s by %s",
                    new_quote.id,
                    ", ".join(new_quote.authors),
                )
                try:
                    author_name = ", ".join(new_quote.authors) or "Unknown"
                    sent_count = services.send_push_notification(
                        "People are chatting...",
                        f"New quote by {author_name}",
                        services.build_public_url(
                            url_for("quote_by_id", quote_id=new_quote.id)
                        ),
                    )
                    current_app.logger.info("Push notifications sent: %s", sent_count)
                except Exception as exc:
                    current_app.logger.warning("Push notification failed: %s", exc)

                return redirect(url_for("index"))

        return render_template("add_quote.html")

    @bp.route("/ai", endpoint="ai")
    def ai():
        return render_template(
            "ai.html",
            ai_available=ai_worker.can_generate,
            ai_request_token=services.get_ai_request_token() if ai_worker.can_generate else "",
        )

    @bp.route("/ai_screenplay", methods=["POST"], endpoint="ai_screenplay")
    def ai_screenplay():
        if not ai_worker.can_generate:
            return (
                jsonify(
                    error="AI screenplay generation is disabled. Set OPENROUTER_KEY to enable."
                ),
                503,
            )
        data = request.get_json(silent=True) or {}
        token = data.get("token")
        if not token or token != session.get("ai_request_token"):
            return jsonify(error="Invalid AI request token."), 403
        current_app.logger.info("AI screenplay requested.")
        quotes = quote_store.get_all_quotes()
        scored_quotes = [
            (q, ai_worker.classify_funny_score(q.quote, q.authors, q.stats)) for q in quotes
        ]
        top_20 = ai_worker.get_top_20_with_cache(scored_quotes)
        resp = ai_worker.get_ai(top_20)

        return jsonify(resp=resp)

    @bp.route("/ai_screenplay_render", methods=["POST"], endpoint="ai_screenplay_render")
    def ai_screenplay_render():
        data = json.loads(request.form["data"])
        rendered_at = datetime.now().strftime("%d %b %Y, %H:%M")
        return render_template(
            "ai_screenplay.html",
            title="AI Screenplay",
            screenplay=data.get("screenplay", ""),
            rendered_at=rendered_at,
        )

    @bp.route("/battle", methods=["GET", "POST"], endpoint="battle")
    def battle():
        if request.method == "POST":
            winner_id = int(request.form["winner"])
            loser_id = int(request.form["loser"])

            winner, loser = quote_store.record_battle(winner_id, loser_id)
            if winner and loser:
                services.refresh_stats_cache("battle-recorded")
                current_app.logger.info(
                    "Battle result: winner=%s loser=%s", winner_id, loser_id
                )
            else:
                current_app.logger.warning(
                    "Battle POST with missing quote(s): winner=%s loser=%s",
                    winner_id,
                    loser_id,
                )

            return redirect(url_for("battle"))

        quotes = quote_store.get_all_quotes()
        if len(quotes) < 2:
            return "Not enough quotes for a battle", 400

        quote_a, quote_b = randlib.sample(quotes, 2)

        return render_template(
            "battle.html",
            quote_a=quote_a,
            quote_b=quote_b,
        )

    @bp.route("/random", endpoint="random")
    def random():
        q = quote_store.get_random_quote()
        if not q:
            abort(404)
        current_app.logger.info("Random quote served: %s", q.id)

        date_str, time_str = datetime_handler.format_uk_datetime_from_timestamp(q.timestamp)

        return render_template(
            "quote.html",
            quote=q.quote,
            author=", ".join(q.authors),
            date=date_str,
            time=time_str,
            id=str(q.id),
            context=q.context,
            reroll_button=True,
            quote_id=q.id,
            permalink=services.build_public_url(url_for("quote_by_id", quote_id=q.id)),
            permalink_base=services.build_public_url("/quote/"),
            edit_enabled=bool(edit_pin),
            edit_authed=bool(session.get("edit_authed")),
        )

    @bp.route("/quote/<int:quote_id>", endpoint="quote_by_id")
    def quote_by_id(quote_id):
        q = quote_store.get_quote_by_id(quote_id)
        if not q:
            current_app.logger.info("Quote not found: %s", quote_id)
            abort(404)

        date_str, time_str = datetime_handler.format_uk_datetime_from_timestamp(q.timestamp)

        return render_template(
            "quote.html",
            quote=q.quote,
            author=", ".join(q.authors),
            id=str(q.id),
            date=date_str,
            time=time_str,
            context=q.context,
            reroll_button=False,
            quote_id=quote_id,
            permalink=services.build_public_url(url_for("quote_by_id", quote_id=quote_id)),
            permalink_base=services.build_public_url("/quote/"),
            edit_enabled=bool(edit_pin),
            edit_authed=bool(session.get("edit_authed")),
        )

    @bp.route("/quote/<int:quote_id>/edit", methods=["GET", "POST"], endpoint="edit_quote")
    def edit_quote(quote_id):
        if not edit_pin:
            return (
                render_template(
                    "error.html",
                    code=503,
                    name="Edit Disabled",
                    description="Editing is disabled. Set EDIT_PIN to enable editing.",
                ),
                503,
            )

        quote = quote_store.get_quote_by_id(quote_id)
        if not quote:
            abort(404)

        pin_error = None
        edit_error = None

        if request.method == "POST":
            action = request.form.get("action", "").strip().lower()

            if action == "pin":
                pin = (request.form.get("pin") or "").strip()
                if pin == edit_pin:
                    session["edit_authed"] = True
                    session.permanent = True
                    return redirect(url_for("edit_quote", quote_id=quote_id))
                pin_error = "Incorrect PIN. Try again."

            if action == "edit":
                if not session.get("edit_authed"):
                    pin_error = "Please enter your PIN to edit."
                else:
                    quote_text = request.form.get("quote_text", "").strip()
                    context = request.form.get("context", "").strip()
                    author_raw = request.form.get("author_info", "Unknown").strip()

                    if not quote_text:
                        edit_error = "Quote text cannot be empty."
                    else:
                        authors = quote_store.parse_authors(author_raw)
                        updated = quote_store.update_quote(
                            quote_id=quote_id,
                            quote_text=quote_text,
                            authors=authors,
                            context=context,
                        )
                        if not updated:
                            abort(404)
                        return redirect(url_for("quote_by_id", quote_id=quote_id))

        return render_template(
            "edit_quote.html",
            quote=quote,
            pin_error=pin_error,
            edit_error=edit_error,
            is_authed=bool(session.get("edit_authed")),
        )

    @bp.route("/edit", methods=["GET", "POST"], endpoint="edit_index")
    def edit_index():
        if not edit_pin:
            return (
                render_template(
                    "error.html",
                    code=503,
                    name="Edit Disabled",
                    description="Editing is disabled. Set EDIT_PIN to enable editing.",
                ),
                503,
            )

        pin_error = None

        if request.method == "POST":
            action = request.form.get("action", "").strip().lower()
            if action == "pin":
                pin = (request.form.get("pin") or "").strip()
                if pin == edit_pin:
                    session["edit_authed"] = True
                    session.permanent = True
                    return redirect(url_for("edit_index"))
                pin_error = "Incorrect PIN. Try again."

        page = request.args.get("page", 1, type=int)
        quotes = []
        total_pages = 1
        if session.get("edit_authed"):
            quotes, page, total_pages = quote_store.get_quote_page(None, page, 10)

        return render_template(
            "edit_index.html",
            quotes=quotes,
            page=page,
            total_pages=total_pages,
            pin_error=pin_error,
            is_authed=bool(session.get("edit_authed")),
        )

    @bp.route("/all_quotes", endpoint="all_quotes")
    def all_quotes():
        speaker_filter = request.args.get("speaker", None)
        sort_order = (request.args.get("order") or "oldest").strip().lower()
        if sort_order not in ("oldest", "newest"):
            sort_order = "oldest"
        page = request.args.get("page", 1, type=int)

        paginated_quotes, page, total_pages = quote_store.get_quote_page(
            speaker_filter,
            page,
            per_page_quote_limit,
            sort_order,
        )
        sorted_speakers = quote_store.get_speaker_counts()

        return render_template(
            "all_quotes.html",
            quotes=paginated_quotes,
            selected_speaker=speaker_filter,
            sort_order=sort_order,
            speakers=sorted_speakers,
            page=page,
            total_pages=total_pages,
            per_page=per_page_quote_limit,
        )

    @bp.route("/search", methods=["GET", "POST"], endpoint="search")
    def search():
        results = []
        query = ""

        if request.method == "POST":
            query = request.form.get("query", "").strip()
        else:
            query = request.args.get("q", "").strip()

        if query:
            results = quote_store.search_quotes(query)
            current_app.logger.info("Search query: '%s' (%s results)", query, len(results))

        return render_template(
            "search.html",
            results=results,
            len_results=len(results),
            query=query,
        )

    @bp.route("/stats", endpoint="stats")
    def stats():
        snapshot = services.get_stats_cache_snapshot()
        return render_template("stats.html", **snapshot)

    @bp.route("/timeline/<int:year>/<int:month>", endpoint="timeline")
    def timeline(year, month):
        cal = pycalendar.Calendar(firstweekday=0)
        month_days = cal.monthdatescalendar(year, month)

        calendar_days = []
        quotes = quote_store.get_all_quotes()

        for week in month_days:
            week_days = []
            for day in week:
                day_start = datetime(day.year, day.month, day.day, tzinfo=uk_tz)
                day_end = day_start + timedelta(days=1) - timedelta(seconds=1)

                start_ts = int(day_start.timestamp())
                end_ts = int(day_end.timestamp())

                day_quotes = [q for q in quotes if start_ts <= q.timestamp <= end_ts]
                count = len(day_quotes)

                week_days.append(
                    {
                        "date": day,
                        "in_month": day.month == month,
                        "count": count,
                        "timestamp": start_ts if count > 0 else None,
                    }
                )

            calendar_days.append(week_days)

        years = sorted({datetime.fromtimestamp(q.timestamp, uk_tz).year for q in quotes})

        months = list(range(1, 13))

        return render_template(
            "calendar.html",
            year=year,
            month=month,
            years=years,
            months=months,
            calendar_days=calendar_days,
        )

    @bp.route("/timeline/day/<int:timestamp>", endpoint="quotes_by_day")
    def quotes_by_day(timestamp):
        day_dt = datetime.fromtimestamp(timestamp, tz=uk_tz)

        start_of_day = datetime.combine(
            day_dt.date(),
            time.min,
            tzinfo=uk_tz,
        )

        end_of_day = datetime.combine(
            day_dt.date(),
            time.max,
            tzinfo=uk_tz,
        )

        start_ts = int(start_of_day.timestamp())
        end_ts = int(end_of_day.timestamp())

        quotes = quote_store.get_quotes_between(start_ts, end_ts)

        return render_template(
            "quotes_by_day.html",
            quotes=quotes,
            day=day_dt.strftime("%d %B %Y"),
            year=day_dt.year,
            month=day_dt.month,
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
                "push_enabled": bool(vapid_public_key),
                "weekly_email_configured": bool(services.weekly_email_is_configured()),
            },
        )

    @bp.route("/cuppa", endpoint="cuppa")
    def cuppa():
        abort(418)

    return bp
