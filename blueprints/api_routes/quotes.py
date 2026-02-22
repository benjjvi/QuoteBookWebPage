from flask import current_app, jsonify, request, url_for

import datetime_handler


def _normalize_order(raw_order):
    order = (raw_order or "oldest").strip().lower()
    if order not in ("oldest", "newest", "desc", "reverse"):
        order = "oldest"
    return order, order in ("newest", "desc", "reverse")


def register_quote_api_routes(bp, context):
    quote_store = context["quote_store"]
    services = context["services"]

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
        current_app.logger.info(
            "Battle recorded via API: winner=%s loser=%s", winner_id, loser_id
        )
        return jsonify(
            {
                "winner": services.quote_to_dict(winner),
                "loser": services.quote_to_dict(loser),
            }
        )

    @bp.route("/api/quote-anarchy-wins", methods=["POST"], endpoint="api_quote_anarchy_wins")
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
