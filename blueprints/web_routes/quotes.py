import calendar as pycalendar
import random as randlib
from datetime import datetime, time, timedelta

from flask import (
    abort,
    current_app,
    make_response,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

import datetime_handler


def register_quote_routes(bp, context):
    quote_store = context["quote_store"]
    ai_worker = context["ai_worker"]
    services = context["services"]
    uk_tz = context["uk_tz"]
    edit_pin = context["edit_pin"]
    vapid_public_key = context["vapid_public_key"]
    per_page_quote_limit = context["per_page_quote_limit"]

    def _normalise_email(raw_email: str) -> str:
        return (raw_email or "").strip().lower()

    def _suggest_tags_if_needed(quote_text: str, context_text: str, authors, tags):
        if tags:
            return tags
        suggest_fn = getattr(ai_worker, "suggest_tags", None)
        if not callable(suggest_fn):
            return []
        try:
            return suggest_fn(quote_text, context_text, authors)
        except Exception as exc:
            current_app.logger.warning("AI tag suggestion failed: %s", exc)
            return []

    @bp.route("/add_quote", methods=["GET", "POST"], endpoint="add_quote")
    def add_quote():
        if request.method == "POST":
            quote_text = request.form.get("quote_text", "").strip()
            context = request.form.get("context", "").strip()
            author_raw = request.form.get("author_info", "Unknown").strip()
            quote_datetime_raw = request.form.get("quote_datetime", "").strip()
            tags_raw = request.form.get("tags", "")

            if quote_text:
                authors = quote_store.parse_authors(author_raw)
                tags = quote_store.parse_tags(tags_raw)
                tags = _suggest_tags_if_needed(quote_text, context, authors, tags)

                timestamp = datetime_handler.get_current_uk_timestamp()
                if quote_datetime_raw:
                    try:
                        selected_dt = datetime.strptime(
                            quote_datetime_raw, "%Y-%m-%dT%H:%M"
                        )
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
                    tags=tags,
                )
                services.refresh_stats_cache("quote-added")
                current_app.logger.info(
                    "Added quote %s by %s tags=%s",
                    new_quote.id,
                    ", ".join(new_quote.authors),
                    ",".join(new_quote.tags),
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

    @bp.route("/battle", methods=["GET", "POST"], endpoint="battle")
    def battle():
        if request.method == "POST":
            winner_raw = (request.form.get("winner") or "").strip()
            loser_raw = (request.form.get("loser") or "").strip()
            try:
                winner_id = int(winner_raw)
                loser_id = int(loser_raw)
            except (TypeError, ValueError):
                current_app.logger.warning(
                    "Battle POST with invalid IDs: winner=%r loser=%r",
                    winner_raw,
                    loser_raw,
                )
                return redirect(url_for("battle"))

            if winner_id <= 0 or loser_id <= 0 or winner_id == loser_id:
                current_app.logger.warning(
                    "Battle POST rejected: winner=%s loser=%s",
                    winner_id,
                    loser_id,
                )
                return redirect(url_for("battle"))

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

    @bp.route("/mailbox", methods=["GET", "POST"], endpoint="mailbox")
    def mailbox():
        email_value = _normalise_email((request.values.get("email") or ""))
        form_message = ""
        form_kind = "info"
        cookie_email_to_set = ""
        clear_subscription_cookie = False

        if request.method == "POST":
            action = (request.form.get("action") or "").strip().lower()
            if action not in {"subscribe", "unsubscribe"}:
                form_message = "Unknown mailbox action."
                form_kind = "error"
            elif not services.is_valid_email_address(email_value):
                form_message = "Please enter a valid email address."
                form_kind = "error"
            elif action == "subscribe":
                created = services.add_weekly_email_recipient(email_value)
                if created:
                    form_message = f"Subscribed {email_value} to weekly digest emails."
                    form_kind = "success"
                    cookie_email_to_set = email_value
                elif services.is_weekly_email_recipient(email_value):
                    form_message = f"{email_value} is already subscribed."
                    cookie_email_to_set = email_value
                else:
                    form_message = "Unable to subscribe right now."
                    form_kind = "error"
            elif action == "unsubscribe":
                removed = services.remove_weekly_email_recipient(email_value)
                if removed:
                    form_message = f"Unsubscribed {email_value}."
                    form_kind = "success"
                    clear_subscription_cookie = True
                elif services.is_weekly_email_recipient(email_value):
                    form_message = "Unable to unsubscribe right now."
                    form_kind = "error"
                else:
                    form_message = f"{email_value} was not subscribed."

        cookie_flag = (request.cookies.get("qb_email_subscribed") or "").strip().lower()
        cookie_email = _normalise_email(request.cookies.get("qb_email_address") or "")
        has_subscribed_cookie = cookie_flag in {"1", "true", "yes", "y"} and bool(
            cookie_email
        )
        cookie_has_valid_subscription = (
            has_subscribed_cookie and services.is_weekly_email_recipient(cookie_email)
        )

        digests = services.get_weekly_digest_archive(limit=25)
        previous_digests = digests[1:] if len(digests) > 1 else []
        mailbox_show_all = bool(cookie_has_valid_subscription)
        visible_digests = previous_digests if mailbox_show_all else previous_digests[:1]

        response = make_response(
            render_template(
                "mailbox.html",
                mailbox_digests=visible_digests,
                mailbox_show_all=mailbox_show_all,
                mailbox_cookie_email=cookie_email,
                mailbox_email=email_value,
                mailbox_message=form_message,
                mailbox_message_kind=form_kind,
                weekly_email_enabled=bool(services.config.weekly_email_enabled),
            )
        )

        if cookie_email_to_set:
            response.set_cookie(
                "qb_email_subscribed",
                "true",
                max_age=365 * 24 * 60 * 60,
                samesite="Lax",
                secure=bool(services.config.is_prod),
            )
            response.set_cookie(
                "qb_email_address",
                cookie_email_to_set,
                max_age=365 * 24 * 60 * 60,
                samesite="Lax",
                secure=bool(services.config.is_prod),
            )

        if clear_subscription_cookie or (
            has_subscribed_cookie and not cookie_has_valid_subscription
        ):
            response.delete_cookie("qb_email_subscribed")
            response.delete_cookie("qb_email_address")

        return response

    @bp.route("/unsubscribe", methods=["GET", "POST"], endpoint="unsubscribe_page")
    def unsubscribe_page():
        email_value = _normalise_email((request.values.get("email") or ""))
        message = ""
        message_kind = "info"
        clear_subscription_cookie = False

        if request.method == "POST":
            if not services.is_valid_email_address(email_value):
                message = "Please enter a valid email address."
                message_kind = "error"
            else:
                removed = services.remove_weekly_email_recipient(email_value)
                if removed:
                    message = f"{email_value} has been unsubscribed."
                    message_kind = "success"
                    clear_subscription_cookie = True
                else:
                    message = "That email is not currently subscribed."

        response = make_response(
            render_template(
                "unsubscribe.html",
                unsubscribe_email=email_value,
                unsubscribe_message=message,
                unsubscribe_message_kind=message_kind,
            )
        )
        if clear_subscription_cookie:
            response.delete_cookie("qb_email_subscribed")
            response.delete_cookie("qb_email_address")
        return response

    @bp.route("/random", endpoint="random")
    def random():
        q = quote_store.get_random_quote()
        if not q:
            abort(404)
        current_app.logger.info("Random quote served: %s", q.id)

        date_str, time_str = datetime_handler.format_uk_datetime_from_timestamp(
            q.timestamp
        )

        return render_template(
            "quote.html",
            quote=q.quote,
            author=", ".join(q.authors),
            date=date_str,
            time=time_str,
            id=str(q.id),
            context=q.context,
            tags=q.tags,
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

        date_str, time_str = datetime_handler.format_uk_datetime_from_timestamp(
            q.timestamp
        )

        return render_template(
            "quote.html",
            quote=q.quote,
            author=", ".join(q.authors),
            id=str(q.id),
            date=date_str,
            time=time_str,
            context=q.context,
            tags=q.tags,
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
                    tags_raw = request.form.get("tags", "")
                    tags = quote_store.parse_tags(tags_raw)

                    if not quote_text:
                        edit_error = "Quote text cannot be empty."
                    else:
                        authors = quote_store.parse_authors(author_raw)
                        updated = quote_store.update_quote(
                            quote_id=quote_id,
                            quote_text=quote_text,
                            authors=authors,
                            context=context,
                            tags=tags,
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
        tag_filter = request.args.get("tag", "")
        sort_order = (request.args.get("order") or "oldest").strip().lower()
        if sort_order not in ("oldest", "newest"):
            sort_order = "oldest"
        page = request.args.get("page", 1, type=int)

        paginated_quotes, page, total_pages = quote_store.get_quote_page(
            speaker_filter,
            page,
            per_page_quote_limit,
            sort_order,
            tag_filter,
        )
        sorted_speakers = quote_store.get_speaker_counts()
        sorted_tags = quote_store.get_tag_counts()

        return render_template(
            "all_quotes.html",
            quotes=paginated_quotes,
            selected_speaker=speaker_filter,
            selected_tag=(quote_store.parse_tags(tag_filter) or [""])[0],
            sort_order=sort_order,
            speakers=sorted_speakers,
            tags=sorted_tags,
            page=page,
            total_pages=total_pages,
            per_page=per_page_quote_limit,
        )

    @bp.route("/search", methods=["GET", "POST"], endpoint="search")
    def search():
        results = []
        tag_filter = ""

        if request.method == "POST":
            query = request.form.get("query", "").strip()
            tag_filter = request.form.get("tag", "").strip()
        else:
            query = request.args.get("q", "").strip()
            tag_filter = request.args.get("tag", "").strip()

        normalized_tag = (quote_store.parse_tags(tag_filter) or [""])[0]

        if query:
            results = quote_store.search_quotes(query, tag=normalized_tag)
            current_app.logger.info(
                "Search query: '%s' (%s results)", query, len(results)
            )
        elif normalized_tag:
            filtered, _, _ = quote_store.get_quote_page(
                speaker=None,
                page=1,
                per_page=500,
                order="newest",
                tag=normalized_tag,
            )
            results = filtered

        return render_template(
            "search.html",
            results=results,
            len_results=len(results),
            query=query,
            selected_tag=normalized_tag,
            tags=quote_store.get_tag_counts(),
        )

    @bp.route("/stats", endpoint="stats")
    def stats():
        snapshot = services.get_stats_cache_snapshot()
        return render_template("stats.html", **snapshot)

    @bp.route("/timeline/<int:year>/<int:month>", endpoint="timeline")
    def timeline(year, month):
        if year < 1 or year > 9999 or month < 1 or month > 12:
            abort(404)

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
        try:
            day_dt = datetime.fromtimestamp(timestamp, tz=uk_tz)
        except (OverflowError, OSError, ValueError):
            abort(404)

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
