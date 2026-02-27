from __future__ import annotations

from flask import current_app, jsonify, request, url_for

import datetime_handler
from api_errors import error_response
from social_feed import build_social_feed_items


def _normalize_order(raw_order):
    order = (raw_order or "oldest").strip().lower()
    if order not in ("oldest", "newest", "desc", "reverse"):
        order = "oldest"
    return order, order in ("newest", "desc", "reverse")


def _extract_tags(quote_store, raw_tags):
    if raw_tags is None:
        return None
    if isinstance(raw_tags, list):
        if hasattr(quote_store, "_local") and quote_store._local is not None:
            return quote_store._local.normalize_tags([str(tag) for tag in raw_tags])
        return [str(tag).strip().lower() for tag in raw_tags if str(tag).strip()]
    return quote_store.parse_tags(str(raw_tags))


def _normalized_tag(quote_store, raw_tag: str) -> str:
    tags = _extract_tags(quote_store, [raw_tag] if raw_tag else [])
    return tags[0] if tags else ""


def _quote_has_tag(quote, tag_value: str) -> bool:
    if not tag_value:
        return True
    tags = [str(tag or "").strip().lower() for tag in (getattr(quote, "tags", []) or [])]
    return tag_value in tags


def _paginate(items, page: int, per_page: int):
    total = len(items)
    total_pages = max(1, (total + per_page - 1) // per_page)
    safe_page = max(1, min(page, total_pages))
    start = (safe_page - 1) * per_page
    end = start + per_page
    return items[start:end], safe_page, total_pages, total



def register_quote_api_routes(bp, context):
    quote_store = context["quote_store"]
    ai_worker = context["ai_worker"]
    services = context["services"]

    def _suggest_tags_if_needed(quote_text: str, context_text: str, authors, tags):
        if tags is None:
            return None
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

    @bp.route("/api/latest", endpoint="api_latest_quote")
    def api_latest_quote():
        newest_quote = quote_store.get_latest_quote()
        if not newest_quote:
            current_app.logger.warning("API latest requested with no quotes.")
            return error_response(
                status=404,
                code="no_quotes",
                message="No quotes found.",
            )
        current_app.logger.info("API latest quote served: %s", newest_quote.id)
        return jsonify(services.quote_to_dict(newest_quote))

    @bp.route("/api/speakers", endpoint="api_speakers")
    def api_speakers():
        return jsonify(
            speakers=[
                {"speaker": speaker, "count": count}
                for speaker, count in quote_store.get_speaker_counts()
            ]
        )

    @bp.route("/api/tags", endpoint="api_tags")
    def api_tags():
        return jsonify(
            tags=[{"tag": tag, "count": count} for tag, count in quote_store.get_tag_counts()]
        )

    @bp.route("/api/quotes", endpoint="api_quotes")
    def api_quotes():
        speaker = (request.args.get("speaker") or "").strip()
        tag = _normalized_tag(quote_store, request.args.get("tag", ""))
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
        if tag:
            quotes = [q for q in quotes if _quote_has_tag(q, tag)]

        quotes = sorted(quotes, key=lambda q: (q.timestamp, q.id), reverse=reverse_sort)
        total = len(quotes)

        if page and per_page and per_page > 0:
            quotes, page, total_pages, _ = _paginate(quotes, page, per_page)
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
            speaker=speaker,
            tag=tag,
        )

    @bp.route("/api/social/feed", endpoint="api_social_feed")
    def api_social_feed():
        query = (request.args.get("q") or "").strip()
        author = (request.args.get("author") or "").strip()
        tag = _normalized_tag(quote_store, request.args.get("tag", ""))
        page = max(1, request.args.get("page", 1, type=int))
        per_page = request.args.get("per_page", 12, type=int)
        per_page = max(1, min(per_page, 30))

        if query:
            quotes = quote_store.search_quotes(query, tag=tag)
            if author:
                author_lower = author.lower()
                quotes = [
                    quote
                    for quote in quotes
                    if any(author_lower == name.lower() for name in quote.authors)
                ]
        else:
            quotes = quote_store.get_all_quotes()
            if author:
                author_lower = author.lower()
                quotes = [
                    quote
                    for quote in quotes
                    if any(author_lower == name.lower() for name in quote.authors)
                ]
            if tag:
                quotes = [quote for quote in quotes if _quote_has_tag(quote, tag)]
            quotes = sorted(quotes, key=lambda q: (q.timestamp, q.id), reverse=True)

        paged_quotes, page, total_pages, total = _paginate(quotes, page, per_page)
        feed_items = build_social_feed_items(
            paged_quotes,
            offset=(page - 1) * per_page,
            total_quotes=total,
        )

        serialized_items = []
        for item in feed_items:
            if item["kind"] == "quote":
                serialized_items.append(
                    {
                        "kind": "quote",
                        "primary_author": item["primary_author"],
                        "quote": services.quote_to_dict(item["quote"]),
                    }
                )
            else:
                serialized_items.append({"kind": "generic", "post": dict(item["post"])})

        return jsonify(
            items=serialized_items,
            total=total,
            page=page,
            per_page=per_page,
            total_pages=total_pages,
            has_more=page < total_pages,
            next_page=(page + 1) if page < total_pages else None,
            query=query,
            author=author,
            tag=tag,
        )

    @bp.route("/api/quotes/random", endpoint="api_random")
    def api_random():
        random_quote = quote_store.get_random_quote()
        if not random_quote:
            return error_response(status=404, code="no_quotes", message="No quotes found.")
        return jsonify(services.quote_to_dict(random_quote))

    @bp.route("/api/quotes/<int:quote_id>", endpoint="api_quote_by_id")
    def api_quote_by_id(quote_id: int):
        quote = quote_store.get_quote_by_id(quote_id)
        if not quote:
            return error_response(status=404, code="quote_not_found", message="Quote not found.")
        return jsonify(services.quote_to_dict(quote))

    @bp.route("/api/quotes/<int:quote_id>", methods=["PUT"], endpoint="api_update_quote")
    def api_update_quote(quote_id: int):
        data = request.get_json(silent=True) or {}
        quote_text = (data.get("quote") or "").strip()
        context_text = (data.get("context") or "").strip()
        authors_raw = data.get("authors")
        tags = _extract_tags(quote_store, data.get("tags"))

        if not quote_text:
            return error_response(
                status=400,
                code="quote_required",
                message="quote is required",
            )

        if isinstance(authors_raw, list):
            authors = [str(value).strip() for value in authors_raw if str(value).strip()]
        else:
            authors = quote_store.parse_authors(str(authors_raw or "Unknown"))

        if tags is not None:
            tags = _suggest_tags_if_needed(quote_text, context_text, authors, tags)

        updated = quote_store.update_quote(
            quote_id=quote_id,
            quote_text=quote_text,
            authors=authors,
            context=context_text,
            tags=tags,
        )
        if not updated:
            return error_response(
                status=404,
                code="quote_not_found",
                message="Quote not found.",
            )

        services.refresh_stats_cache("quote-updated-api")
        current_app.logger.info("Updated quote %s via API", quote_id)
        return jsonify(services.quote_to_dict(updated))

    @bp.route("/api/quotes/between", endpoint="api_quotes_between")
    def api_quotes_between():
        start_ts = request.args.get("start_ts", type=int)
        end_ts = request.args.get("end_ts", type=int)
        if start_ts is None or end_ts is None:
            return error_response(
                status=400,
                code="range_required",
                message="start_ts and end_ts are required",
            )
        quotes = quote_store.get_quotes_between(start_ts, end_ts)
        return jsonify(quotes=[services.quote_to_dict(q) for q in quotes])

    @bp.route("/api/search", endpoint="api_search")
    def api_search():
        query = (request.args.get("query") or "").strip()
        tag = _normalized_tag(quote_store, request.args.get("tag", ""))
        if not query:
            if not tag:
                return jsonify(quotes=[], query=query, tag=tag)
            matches = [
                quote
                for quote in quote_store.get_all_quotes()
                if _quote_has_tag(quote, tag)
            ]
            matches = sorted(matches, key=lambda q: (q.timestamp, q.id), reverse=True)
            return jsonify(
                quotes=[services.quote_to_dict(q) for q in matches],
                query=query,
                tag=tag,
            )

        results = quote_store.search_quotes(query, tag=tag)
        return jsonify(
            quotes=[services.quote_to_dict(q) for q in results],
            query=query,
            tag=tag,
        )

    @bp.route("/api/quotes", methods=["POST"], endpoint="api_add_quote")
    def api_add_quote():
        data = request.get_json(silent=True) or {}
        quote_text = (data.get("quote") or "").strip()
        context_text = (data.get("context") or "").strip()
        authors_raw = data.get("authors")
        tags = _extract_tags(quote_store, data.get("tags"))

        if not quote_text:
            return error_response(
                status=400,
                code="quote_required",
                message="quote is required",
            )

        if isinstance(authors_raw, list):
            authors = [str(value).strip() for value in authors_raw if str(value).strip()]
        else:
            authors = quote_store.parse_authors(str(authors_raw or "Unknown"))

        timestamp_raw = data.get("timestamp")
        if timestamp_raw is None:
            timestamp = datetime_handler.get_current_uk_timestamp()
        else:
            try:
                timestamp = int(timestamp_raw)
            except (TypeError, ValueError):
                return error_response(
                    status=400,
                    code="timestamp_invalid",
                    message="timestamp must be an integer",
                )

        tags = _suggest_tags_if_needed(quote_text, context_text, authors, tags or [])

        new_quote = quote_store.add_quote(
            quote_text=quote_text,
            authors=authors,
            context=context_text,
            timestamp=timestamp,
            tags=tags,
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
            return error_response(
                status=400,
                code="battle_ids_required",
                message="winner_id and loser_id are required",
            )

        try:
            winner_id = int(winner_id)
            loser_id = int(loser_id)
        except (TypeError, ValueError):
            return error_response(
                status=400,
                code="battle_ids_invalid",
                message="winner_id and loser_id must be integers",
            )

        if winner_id <= 0 or loser_id <= 0:
            return error_response(
                status=400,
                code="battle_ids_positive",
                message="winner_id and loser_id must be positive",
            )

        if winner_id == loser_id:
            return error_response(
                status=400,
                code="battle_ids_distinct",
                message="winner_id and loser_id must be different",
            )

        winner, loser = quote_store.record_battle(winner_id, loser_id)
        if not winner or not loser:
            return error_response(
                status=404,
                code="quote_not_found",
                message="Quote not found.",
            )

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
        "/api/quote-anarchy-wins",
        methods=["POST"],
        endpoint="api_quote_anarchy_wins",
    )
    def api_quote_anarchy_wins():
        data = request.get_json(silent=True) or {}
        quote_ids_raw = data.get("quote_ids")
        if not isinstance(quote_ids_raw, list):
            return error_response(
                status=400,
                code="quote_ids_invalid",
                message="quote_ids must be a list",
            )

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
            return error_response(
                status=400,
                code="quote_ids_empty",
                message="No valid quote_ids provided",
            )

        updated_quotes = quote_store.record_quote_anarchy_wins(quote_ids)
        if not updated_quotes:
            return error_response(
                status=404,
                code="quote_ids_not_found",
                message="No matching quotes found",
            )

        services.refresh_stats_cache("quote-anarchy-wins-api")
        return jsonify(
            {
                "quotes": [services.quote_to_dict(quote) for quote in updated_quotes],
                "updated_count": len(updated_quotes),
            }
        )
