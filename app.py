import os
import random
import secrets
import string
from datetime import datetime
from zoneinfo import ZoneInfo  # Python 3.9+

from dotenv import load_dotenv
from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.exceptions import HTTPException

import qbformats

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", secrets.token_hex(32))
qb = qbformats.QuoteBook()

# Load the .env file
load_dotenv()

# Define the character set: uppercase, lowercase, digits
chars = string.ascii_letters + string.digits

IS_PROD = os.getenv("IS_PROD", "False").lower() in ("true", "1", "t")

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,  # prevents JS from reading cookie
    SESSION_COOKIE_SECURE=True,  # only send cookie over HTTPS
    SESSION_COOKIE_SAMESITE="Lax",  # protects against CSRF
)


@app.before_request
def refresh_qb():
    status = qb.reload()
    if status != 200 and status != 304:
        if status < 400 or status > 600:
            abort(500)


@app.route("/robots.txt")
def robots_txt():
    content = """
    User-agent: *
    Disallow: /
    """.strip()
    return Response(content, mimetype="text/plain")


@app.route("/")
def index():
    try:
        return render_template(
            "index.html", total_quotes=qb.total_quotes, speaker_counts=qb.speaker_counts
        )
    except Exception as e:
        abort(500)


@app.route("/add_quote", methods=["GET", "POST"])
def add_quote():
    if request.method == "POST":
        quote_text = request.form.get("quote_text", "").strip()
        context = request.form.get("context", "").strip()
        author_raw = request.form.get("author_info", "Unknown").strip()
        time = request.form.get("time", "").strip()
        if time != "":
            timestamp = time
        else:
            now = datetime.now(ZoneInfo("Europe/London"))

            day = now.day
            suffix = (
                "th"
                if 11 <= day <= 13
                else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
            )
            timestamp = now.strftime(f"{day}{suffix} %B, %H:%M")

        if quote_text:
            authors = [a.strip() for a in author_raw.split(" and ") if a.strip()]

            new_quote = qbformats.Quote(
                id=qb.next_id(),
                quote=quote_text,
                authors=authors,
                date=timestamp.split(",")[0],
                time=timestamp.split(",")[1].strip() if "," in timestamp else "",
                context=context,
            )

            qb.add_quote(new_quote)

            # Reload quotes
            status = qb.reload()
            if status != 200 and status != 304:
                # give nicely formatted error page. follow template ERRXXX.html
                if status < 400 or status > 600:
                    abort(500)
                else:
                    abort(status)
            else:
                return redirect(url_for("index"))

    return render_template("add_quote.html")


@app.route("/random_quote")
def random_quote():
    q = qb.get_random_quote()

    return render_template(
        "quote.html",
        quote=q.quote,
        author=", ".join(q.authors),
        date=q.date,
        time=q.time,
        context=q.context,
        reroll_button=True,
        quote_id=q.id,
    )


@app.route("/quote/<int:quote_id>")
def quote_by_id(quote_id):
    q = qb.get_quote_by_id(quote_id)
    if not q:
        abort(404)

    return render_template(
        "quote.html",
        quote=q.quote,
        author=", ".join(q.authors),
        date=q.date,
        time=q.time,
        context=q.context,
        reroll_button=False,
        quote_id=quote_id,
    )


@app.route("/all_quotes")
def all_quotes():
    # Get the speaker from query parameter
    speaker_filter = request.args.get("speaker", None)

    # Filter quotes if a speaker is selected
    if speaker_filter:
        speaker_lower = speaker_filter.lower()

        filtered_quotes = [
            q
            for q in qb.quotes
            if any(speaker_lower == author.lower() for author in q.authors)
        ]
    else:
        filtered_quotes = qb.quotes

    # Sort speakers by count (most common first)
    sorted_speakers = sorted(qb.speaker_counts, key=lambda x: x[1], reverse=True)

    return render_template(
        "all_quotes.html",
        quotes=filtered_quotes,
        speaker=speaker_filter,
        speakers=sorted_speakers,
    )


@app.route("/search", methods=["GET", "POST"])
def search():
    try:
        results = []
        query = ""

        if request.method == "POST":
            query = request.form.get("query", "").strip()
            if query:
                results = qb.search_quotes(query)

        return render_template(
            "search.html",
            results=results,  # List[Quote]
            len_results=len(results),
            query=query,
        )
    except Exception as e:
        print(e)
        abort(500)


@app.route("/credits")
def credits():
    return render_template("credits.html")


@app.route("/health")
def health():
    return jsonify(status="ok")


@app.errorhandler(Exception)
def handle_all_errors(e):
    if isinstance(e, HTTPException):
        code = e.code
        name = e.name
        description = e.description
    else:
        code = 500
        name = "Internal Server Error"
        description = "The server encountered an internal error and was unable to complete your request. Either the server is overloaded or there is an error in the application."

    return (
        render_template("error.html", code=code, name=name, description=description),
        code,
    )


if __name__ == "__main__":
    app.run()
