"""Standalone Quote API service backed by SQLite."""

import logging
import os

from dotenv import load_dotenv
from flask import Flask, jsonify, request

import datetime_handler
import qb_formats

load_dotenv()

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)

DB_PATH = os.getenv("QUOTEBOOK_DB", "qb.db")
qb = qb_formats.QuoteBook(DB_PATH)


def quote_to_dict(quote: qb_formats.Quote) -> dict:
    return {
        "id": quote.id,
        "quote": quote.quote,
        "authors": quote.authors,
        "timestamp": quote.timestamp,
        "context": quote.context,
        "stats": quote.stats,
    }


@app.before_request
def refresh_quotes():
    qb.reload()


@app.after_request
def add_cors_headers(response):
    origin = os.getenv("CORS_ORIGIN", "*")
    response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    return response


@app.route("/health")
def health():
    return jsonify(status="ok")


@app.route("/api/<path:_path>", methods=["OPTIONS"])
def api_options(_path):
    return ("", 204)


@app.route("/api/latest")
def api_latest():
    if not qb.quotes:
        return jsonify({"error": "No quotes found"}), 404

    newest = max(qb.quotes, key=lambda q: q.id)
    return jsonify(quote_to_dict(newest))


@app.route("/api/speakers")
def api_speakers():
    return jsonify(
        speakers=[{"speaker": s, "count": c} for s, c in qb.speaker_counts]
    )


@app.route("/api/quotes")
def api_quotes():
    speaker = request.args.get("speaker")
    page = request.args.get("page", type=int)
    per_page = request.args.get("per_page", type=int)

    quotes = qb.quotes
    if speaker:
        speaker_lower = speaker.lower()
        quotes = [
            q
            for q in quotes
            if any(speaker_lower == author.lower() for author in q.authors)
        ]

    total = len(quotes)

    if page and per_page:
        page = max(1, page)
        total_pages = max(1, (total + per_page - 1) // per_page)
        page = min(page, total_pages)
        start = (page - 1) * per_page
        end = start + per_page
        quotes = quotes[start:end]
    else:
        total_pages = 1

    return jsonify(
        quotes=[quote_to_dict(q) for q in quotes],
        total=total,
        page=page or 1,
        per_page=per_page or total,
        total_pages=total_pages,
    )


@app.route("/api/quotes/random")
def api_random():
    if not qb.quotes:
        return jsonify({"error": "No quotes found"}), 404

    return jsonify(quote_to_dict(qb.get_random_quote()))


@app.route("/api/quotes/<int:quote_id>")
def api_quote_by_id(quote_id: int):
    quote = qb.get_quote_by_id(quote_id)
    if not quote:
        return jsonify({"error": "Quote not found"}), 404
    return jsonify(quote_to_dict(quote))


@app.route("/api/quotes/between")
def api_quotes_between():
    start_ts = request.args.get("start_ts", type=int)
    end_ts = request.args.get("end_ts", type=int)
    if start_ts is None or end_ts is None:
        return jsonify({"error": "start_ts and end_ts are required"}), 400

    quotes = qb.get_quotes_between(start_ts, end_ts)
    return jsonify(quotes=[quote_to_dict(q) for q in quotes])


@app.route("/api/search")
def api_search():
    query = request.args.get("query", "").strip()
    if not query:
        return jsonify(quotes=[], query=query)

    results = qb.search_quotes(query)
    return jsonify(quotes=[quote_to_dict(q) for q in results], query=query)


@app.route("/api/quotes", methods=["POST"])
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
        authors = qb.parse_authors(str(authors_raw or "Unknown"))

    timestamp = data.get("timestamp")
    if timestamp is None:
        timestamp = datetime_handler.get_current_uk_timestamp()

    new_quote = qb_formats.Quote(
        id=qb.next_id(),
        quote=quote_text,
        authors=authors,
        timestamp=int(timestamp),
        context=context,
    )

    qb.add_quote(new_quote)
    logger.info("Added quote %s via API", new_quote.id)
    return jsonify(quote_to_dict(new_quote)), 201


@app.route("/api/battles", methods=["POST"])
def api_battle():
    data = request.get_json(silent=True) or {}
    winner_id = data.get("winner_id")
    loser_id = data.get("loser_id")

    if winner_id is None or loser_id is None:
        return jsonify({"error": "winner_id and loser_id are required"}), 400

    winner = qb.get_quote_by_id(int(winner_id))
    loser = qb.get_quote_by_id(int(loser_id))
    if not winner or not loser:
        return jsonify({"error": "Quote not found"}), 404

    winner.stats["wins"] += 1
    winner.stats["battles"] += 1
    winner.stats["score"] += 1

    loser.stats["losses"] += 1
    loser.stats["battles"] += 1

    qb._save()
    logger.info("Battle recorded: winner=%s loser=%s", winner_id, loser_id)

    return jsonify({"winner": quote_to_dict(winner), "loser": quote_to_dict(loser)})


if __name__ == "__main__":
    host = os.getenv("API_HOST", "127.0.0.1")
    port = int(os.getenv("API_PORT", "8050"))
    app.run(debug=False, host=host, port=port)
