import os
import secrets

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


def register_social_routes(bp, context):
    quote_store = context["quote_store"]
    services = context["services"]

    social_generic_posts = [
        {
            "title": "Hallway Bulletin",
            "body": "Reminder: coffee machine diplomacy is still the fastest path to peace.",
        },
        {
            "title": "Studio Update",
            "body": "Today's vibe report says everyone is one spreadsheet away from poetry.",
        },
        {
            "title": "Community Note",
            "body": "If a quote starts with 'technically', brace for impact.",
        },
        {
            "title": "Signal Boost",
            "body": "Context lines are climbing. Future-you is grateful already.",
        },
        {
            "title": "Mood Index",
            "body": "Peak posting window remains late evening and lightly unhinged.",
        },
    ]

    def _sorted_quotes_newest_first():
        return sorted(
            quote_store.get_all_quotes(),
            key=lambda item: (item.timestamp, item.id),
            reverse=True,
        )

    def _social_author_directory():
        return [
            {"name": author, "count": count}
            for author, count in quote_store.get_speaker_counts()
        ]

    def _social_match_authors(author_directory, query: str):
        query = (query or "").strip()
        if not query:
            return []
        query_lower = query.lower()
        return [
            item for item in author_directory if query_lower in item["name"].lower()
        ]

    def _social_quote_matches(quote, query: str) -> bool:
        query = (query or "").strip()
        if not query:
            return True

        query_lower = query.lower()
        if query_lower in quote.quote.lower():
            return True
        if query_lower in (quote.context or "").lower():
            return True
        return any(query_lower in author.lower() for author in quote.authors)

    def _filter_social_quotes(quotes, query: str):
        if not query:
            return list(quotes)
        return [quote for quote in quotes if _social_quote_matches(quote, query)]

    def _build_social_feed_items(quotes):
        feed_items = []
        generic_index = 0

        for quote_index, quote in enumerate(quotes):
            primary_author = quote.authors[0] if quote.authors else "Unknown"
            feed_items.append(
                {
                    "kind": "quote",
                    "quote": quote,
                    "primary_author": primary_author,
                }
            )

            should_insert_generic = (
                social_generic_posts
                and (quote_index + 1) % 4 == 0
                and quote_index < len(quotes) - 1
            )
            if should_insert_generic:
                generic_post = social_generic_posts[
                    generic_index % len(social_generic_posts)
                ]
                feed_items.append({"kind": "generic", "post": generic_post})
                generic_index += 1

        return feed_items

    def _social_avatar_urls():
        avatar_dir = os.path.join(
            current_app.static_folder or "static",
            "assets",
            "img",
            "ui_profile_pictures",
        )
        if not os.path.isdir(avatar_dir):
            current_app.logger.warning(
                "Social profile picture directory missing: %s",
                avatar_dir,
            )
            return []

        def _avatar_sort_key(filename: str):
            stem = filename.rsplit(".", 1)[0]
            if stem.isdigit():
                return (0, int(stem))
            return (1, stem.lower())

        allowed_exts = (".jpg", ".jpeg", ".png", ".webp", ".gif")
        filenames = [
            name
            for name in os.listdir(avatar_dir)
            if name.lower().endswith(allowed_exts)
        ]
        filenames.sort(key=_avatar_sort_key)
        return [f"/static/assets/img/ui_profile_pictures/{name}" for name in filenames]

    def _resolve_author_name(author_directory, raw_name: str):
        raw_name = (raw_name or "").strip()
        if not raw_name:
            return None
        for entry in author_directory:
            if entry["name"].casefold() == raw_name.casefold():
                return entry["name"]
        return None

    social_reactions = services.get_social_reaction_catalog()
    social_device_cookie_key = "qb_social_device_id"

    def _social_notice_pop():
        notice = session.pop("social_notice", None)
        if not isinstance(notice, dict):
            return None
        kind = str(notice.get("kind") or "").strip().lower()
        text = str(notice.get("text") or "").strip()
        if not text:
            return None
        if kind not in {"success", "error", "info"}:
            kind = "info"
        return {"kind": kind, "text": text}

    def _social_set_notice(kind: str, text: str):
        text = (text or "").strip()
        if not text:
            return
        kind = (kind or "info").strip().lower()
        if kind not in {"success", "error", "info"}:
            kind = "info"
        session["social_notice"] = {"kind": kind, "text": text}

    def _social_get_device_id():
        raw = (request.cookies.get(social_device_cookie_key) or "").strip()
        if raw and len(raw) <= 255:
            return raw, False
        return secrets.token_urlsafe(24), True

    def _social_apply_device_cookie(response, device_id: str):
        if not response or not device_id:
            return response
        response.set_cookie(
            social_device_cookie_key,
            device_id,
            max_age=365 * 24 * 60 * 60,
            samesite="Lax",
            secure=bool(services.config.is_prod),
        )
        return response

    @bp.route("/social", endpoint="social_feed")
    def social_feed():
        device_id, _ = _social_get_device_id()
        query = (request.args.get("q") or "").strip()
        quotes = _sorted_quotes_newest_first()
        author_directory = _social_author_directory()
        filtered_quotes = _filter_social_quotes(quotes, query)
        matched_authors = _social_match_authors(author_directory, query)

        response = make_response(
            render_template(
                "social.html",
                social_mode="feed",
                query=query,
                active_author="",
                feed_items=_build_social_feed_items(filtered_quotes),
                feed_quote_count=len(filtered_quotes),
                matched_authors=matched_authors,
                author_directory=author_directory[:32],
                all_authors=[entry["name"] for entry in author_directory],
                avatar_urls=_social_avatar_urls(),
                profile_meta={},
            )
        )
        return _social_apply_device_cookie(response, device_id)

    @bp.route("/social/author/<path:author_name>", endpoint="social_author")
    def social_author(author_name):
        device_id, _ = _social_get_device_id()
        query = (request.args.get("q") or "").strip()
        author_directory = _social_author_directory()
        canonical_author = _resolve_author_name(author_directory, author_name)
        if not canonical_author:
            abort(404)

        quotes = _sorted_quotes_newest_first()
        author_quotes = [
            quote
            for quote in quotes
            if any(
                canonical_author.casefold() == author.casefold()
                for author in quote.authors
            )
        ]
        filtered_author_quotes = _filter_social_quotes(author_quotes, query)
        matched_authors = _social_match_authors(author_directory, query)

        co_author_counts = {}
        for quote in author_quotes:
            for author in quote.authors:
                if author.casefold() == canonical_author.casefold():
                    continue
                co_author_counts[author] = co_author_counts.get(author, 0) + 1

        top_coauthors = sorted(
            co_author_counts.items(),
            key=lambda item: (-item[1], item[0].casefold()),
        )[:3]

        response = make_response(
            render_template(
                "social.html",
                social_mode="author",
                query=query,
                active_author=canonical_author,
                feed_items=_build_social_feed_items(filtered_author_quotes),
                feed_quote_count=len(filtered_author_quotes),
                matched_authors=matched_authors,
                author_directory=author_directory[:32],
                all_authors=[entry["name"] for entry in author_directory],
                avatar_urls=_social_avatar_urls(),
                profile_meta={
                    "quote_count": len(author_quotes),
                    "latest_timestamp": (
                        author_quotes[0].timestamp if author_quotes else 0
                    ),
                    "oldest_timestamp": (
                        author_quotes[-1].timestamp if author_quotes else 0
                    ),
                    "top_coauthors": top_coauthors,
                },
            )
        )
        return _social_apply_device_cookie(response, device_id)

    @bp.route("/social/quote/<int:quote_id>", endpoint="social_quote_post")
    def social_quote_post(quote_id: int):
        quote = quote_store.get_quote_by_id(quote_id)
        if not quote:
            abort(404)

        device_id, _ = _social_get_device_id()
        reactions = services.get_social_reactions_for_quote(
            quote_id=quote_id,
            device_id=device_id,
        )
        comments = services.get_social_comments_for_quote(quote_id=quote_id, limit=300)
        notice = _social_notice_pop()
        author_directory = _social_author_directory()
        related_quotes = []
        if quote.authors:
            primary_author = quote.authors[0]
            for item in _sorted_quotes_newest_first():
                if item.id == quote_id:
                    continue
                if any(
                    primary_author.casefold() == name.casefold() for name in item.authors
                ):
                    related_quotes.append(item)
                if len(related_quotes) >= 6:
                    break

        response = make_response(
            render_template(
                "social_post.html",
                quote=quote,
                social_notice=notice,
                reaction_catalog=social_reactions,
                reaction_counts=reactions["counts"],
                reaction_total=reactions["total"],
                user_reaction=reactions["user_reaction"],
                comments=comments,
                comment_name_max=services.SOCIAL_COMMENT_NAME_MAX,
                comment_text_max=services.SOCIAL_COMMENT_TEXT_MAX,
                related_quotes=related_quotes,
                all_authors=[entry["name"] for entry in author_directory],
                avatar_urls=_social_avatar_urls(),
            )
        )
        return _social_apply_device_cookie(response, device_id)

    @bp.route(
        "/social/quote/<int:quote_id>/react",
        methods=["POST"],
        endpoint="social_quote_react",
    )
    def social_quote_react(quote_id: int):
        quote = quote_store.get_quote_by_id(quote_id)
        if not quote:
            abort(404)

        reaction_key = (request.form.get("reaction") or "").strip()
        device_id, _ = _social_get_device_id()
        if reaction_key not in {item["key"] for item in social_reactions}:
            _social_set_notice("error", "Unknown reaction selected.")
        else:
            ok = services.record_social_reaction(
                quote_id=quote_id,
                device_id=device_id,
                reaction_type=reaction_key,
            )
            if not ok:
                _social_set_notice(
                    "error",
                    "Couldn't save your reaction. Try again in a moment.",
                )

        response = make_response(redirect(url_for("social_quote_post", quote_id=quote_id)))
        return _social_apply_device_cookie(response, device_id)

    @bp.route(
        "/social/quote/<int:quote_id>/comment",
        methods=["POST"],
        endpoint="social_quote_comment",
    )
    def social_quote_comment(quote_id: int):
        quote = quote_store.get_quote_by_id(quote_id)
        if not quote:
            abort(404)

        commenter_name = (request.form.get("commenter_name") or "").strip()
        comment_text = (request.form.get("comment_text") or "").strip()
        if not commenter_name or not comment_text:
            _social_set_notice("error", "Please add a name and a comment.")
        elif len(" ".join(commenter_name.split())) > services.SOCIAL_COMMENT_NAME_MAX:
            _social_set_notice(
                "error",
                f"Name is too long. Max {services.SOCIAL_COMMENT_NAME_MAX} characters.",
            )
        elif len(comment_text) > services.SOCIAL_COMMENT_TEXT_MAX:
            _social_set_notice(
                "error",
                (
                    "Comment is too long. "
                    f"Max {services.SOCIAL_COMMENT_TEXT_MAX} characters."
                ),
            )
        else:
            ok = services.add_social_comment(
                quote_id=quote_id,
                display_name=commenter_name,
                comment_text=comment_text,
            )
            if not ok:
                _social_set_notice(
                    "error",
                    "Couldn't post that comment. Please try again.",
                )

        response = make_response(redirect(url_for("social_quote_post", quote_id=quote_id)))
        device_id, _ = _social_get_device_id()
        return _social_apply_device_cookie(response, device_id)
