from __future__ import annotations

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

SOCIAL_GENERIC_POSTS = [
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


def register_social_routes(bp, context):
    quote_store = context["quote_store"]
    services = context["services"]
    social_reactions = services.get_social_reaction_catalog()
    social_device_cookie_key = "qb_social_device_id"
    per_page = 12

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

    def _social_tag_directory():
        return [
            {"tag": tag, "count": count}
            for tag, count in quote_store.get_tag_counts()
        ]

    def _normalized_tag(raw_tag: str) -> str:
        tags = quote_store.parse_tags(raw_tag or "")
        return tags[0] if tags else ""

    def _social_match_authors(author_directory, query: str):
        query = (query or "").strip()
        if not query:
            return []
        query_lower = query.lower()
        return [
            item for item in author_directory if query_lower in item["name"].lower()
        ]

    def _social_quote_matches_author(quote, author_name: str) -> bool:
        author_name = (author_name or "").strip()
        if not author_name:
            return True
        author_lower = author_name.casefold()
        return any(author_lower == author.casefold() for author in quote.authors)

    def _social_quote_matches_tag(quote, tag_value: str) -> bool:
        if not tag_value:
            return True
        tags = [str(tag or "").strip().lower() for tag in (quote.tags or [])]
        return tag_value in tags

    def _collect_social_quotes(*, query: str, author_name: str, tag: str):
        query = (query or "").strip()
        if query:
            quotes = quote_store.search_quotes(query, tag=tag)
        else:
            quotes = _sorted_quotes_newest_first()
            if tag:
                quotes = [quote for quote in quotes if _social_quote_matches_tag(quote, tag)]

        if author_name:
            quotes = [
                quote
                for quote in quotes
                if _social_quote_matches_author(quote, author_name)
            ]
        return quotes

    def _paginate_quotes(quotes, page_number: int):
        total = len(quotes)
        total_pages = max(1, (total + per_page - 1) // per_page)
        page = max(1, min(page_number, total_pages))
        start = (page - 1) * per_page
        end = start + per_page
        return quotes[start:end], page, total_pages, total

    def _build_social_feed_items(quotes, *, offset: int, total_quotes: int):
        feed_items = []
        for quote_index, quote in enumerate(quotes):
            absolute_index = offset + quote_index
            primary_author = quote.authors[0] if quote.authors else "Unknown"
            feed_items.append(
                {
                    "kind": "quote",
                    "quote": quote,
                    "primary_author": primary_author,
                }
            )

            should_insert_generic = (
                SOCIAL_GENERIC_POSTS
                and (absolute_index + 1) % 4 == 0
                and absolute_index < total_quotes - 1
            )
            if should_insert_generic:
                generic_index = ((absolute_index + 1) // 4 - 1) % len(SOCIAL_GENERIC_POSTS)
                feed_items.append(
                    {"kind": "generic", "post": SOCIAL_GENERIC_POSTS[generic_index]}
                )

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
        selected_tag = _normalized_tag(request.args.get("tag", ""))
        page = request.args.get("page", 1, type=int)

        author_directory = _social_author_directory()
        tag_directory = _social_tag_directory()
        all_quotes = _collect_social_quotes(
            query=query,
            author_name="",
            tag=selected_tag,
        )
        paginated_quotes, page, total_pages, total_quotes = _paginate_quotes(all_quotes, page)
        matched_authors = _social_match_authors(author_directory, query)
        feed_items = _build_social_feed_items(
            paginated_quotes,
            offset=(page - 1) * per_page,
            total_quotes=total_quotes,
        )

        response = make_response(
            render_template(
                "social.html",
                social_mode="feed",
                query=query,
                selected_tag=selected_tag,
                active_author="",
                feed_items=feed_items,
                feed_quote_count=total_quotes,
                matched_authors=matched_authors,
                author_directory=author_directory[:32],
                tag_directory=tag_directory[:32],
                all_authors=[entry["name"] for entry in author_directory],
                avatar_urls=_social_avatar_urls(),
                profile_meta={},
                social_feed_meta={
                    "page": page,
                    "per_page": per_page,
                    "total_pages": total_pages,
                    "has_more": page < total_pages,
                    "next_page": page + 1 if page < total_pages else None,
                    "query": query,
                    "author": "",
                    "tag": selected_tag,
                },
            )
        )
        return _social_apply_device_cookie(response, device_id)

    @bp.route("/social/author/<path:author_name>", endpoint="social_author")
    def social_author(author_name):
        device_id, _ = _social_get_device_id()
        query = (request.args.get("q") or "").strip()
        selected_tag = _normalized_tag(request.args.get("tag", ""))
        page = request.args.get("page", 1, type=int)
        author_directory = _social_author_directory()
        tag_directory = _social_tag_directory()
        canonical_author = _resolve_author_name(author_directory, author_name)
        if not canonical_author:
            abort(404)

        author_quotes = _collect_social_quotes(
            query="",
            author_name=canonical_author,
            tag="",
        )
        filtered_quotes = _collect_social_quotes(
            query=query,
            author_name=canonical_author,
            tag=selected_tag,
        )
        paginated_quotes, page, total_pages, total_quotes = _paginate_quotes(
            filtered_quotes, page
        )
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

        feed_items = _build_social_feed_items(
            paginated_quotes,
            offset=(page - 1) * per_page,
            total_quotes=total_quotes,
        )

        response = make_response(
            render_template(
                "social.html",
                social_mode="author",
                query=query,
                selected_tag=selected_tag,
                active_author=canonical_author,
                feed_items=feed_items,
                feed_quote_count=total_quotes,
                matched_authors=matched_authors,
                author_directory=author_directory[:32],
                tag_directory=tag_directory[:32],
                all_authors=[entry["name"] for entry in author_directory],
                avatar_urls=_social_avatar_urls(),
                profile_meta={
                    "quote_count": len(author_quotes),
                    "latest_timestamp": (author_quotes[0].timestamp if author_quotes else 0),
                    "oldest_timestamp": (author_quotes[-1].timestamp if author_quotes else 0),
                    "top_coauthors": top_coauthors,
                },
                social_feed_meta={
                    "page": page,
                    "per_page": per_page,
                    "total_pages": total_pages,
                    "has_more": page < total_pages,
                    "next_page": page + 1 if page < total_pages else None,
                    "query": query,
                    "author": canonical_author,
                    "tag": selected_tag,
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
