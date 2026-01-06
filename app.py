import os
import random
import secrets
import string
from datetime import datetime
from zoneinfo import ZoneInfo  # Python 3.9+

from dotenv import load_dotenv
from flask import (
    Flask,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
    Response
)
from werkzeug.exceptions import HTTPException

import qbformats as quote_book

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", secrets.token_hex(32))
qb = quote_book.QuoteBook()

# Load the .env file
load_dotenv()

# Define the character set: uppercase, lowercase, digits
chars = string.ascii_letters + string.digits

ADMIN_PASSWORD = (
    os.getenv("ADMIN_PASSWORD")
    if os.getenv("ADMIN_PASSWORD")
    else "".join(secrets.choice(chars) for _ in range(12))
)  # generates a random, secure password if there is none supplied.

IS_PROD = os.getenv("IS_PROD", "False").lower() in ("true", "1", "t")

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,  # prevents JS from reading cookie
    SESSION_COOKIE_SECURE=True,    # only send cookie over HTTPS
    SESSION_COOKIE_SAMESITE="Lax"  # protects against CSRF
)



@app.before_request
def refresh_qb():
    status = qb.reload()
    if status != 200 and status != 304:
        # give nicely formatted error page. follow template ERRXXX.html
        if status < 400 or status > 600:
            abort(500)

@app.route("/robots.txt")
def robots_txt():
    content = """
    User-agent: *
    Disallow: /
    """.strip()
    return Response(content, mimetype="text/plain")

@app.route("/admin_login", methods=["GET", "POST"])
def admin_login():
    error = None
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["admin"] = True
            return redirect(url_for("admin_queue"))
        else:
            error = "Incorrect password."
    return render_template("admin_login.html", error=error)


@app.route("/admin_logout")
def admin_logout():
    session.pop("admin", None)
    return redirect(url_for("admin_login"))


@app.route("/admin_queue", methods=["GET", "POST"])
def admin_queue():
    # Require login
    if not session.get("admin"):
        return redirect(url_for("admin_login"))

    # Load current queue
    qb.load_queue()

    if request.method == "POST":
        index = int(request.form.get("index"))
        action = request.form.get("action")

        if action == "approve":
            qb.approve_quote(index)
        elif action == "reject":
            qb.reject_quote(index)

        return redirect(url_for("admin_queue"))

    # Pass queue to template
    return render_template("admin_queue.html", queue=qb.queue)


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

        if context:
            extras = f"{author_raw}, {timestamp}, {context}"
        else:
            extras = f"{author_raw}, {timestamp}"

        if quote_text:
            qb.add_quote_to_queue(quote_text, extras)
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
    random_quote = qb.get_random_quote(random)
    quote_text = random_quote[0].strip()
    author_info = random_quote[1].strip() if len(random_quote) > 1 else "Unknown"
    return render_template("quote.html", quote=quote_text, author=author_info)


@app.route("/all_quotes")
def all_quotes():
    # Get the speaker from query parameter
    speaker_filter = request.args.get("speaker", None)

    # Filter quotes if a speaker is selected
    if speaker_filter:
        filtered_quotes = [
            q
            for q in qb.quotes
            if q[1].split(",")[0].strip().lower() == speaker_filter.lower()
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
    results = []
    query = ""
    if request.method == "POST":
        query = request.form.get("query", "").strip()
        if query:
            results = qb.search_quotes(query)

    return render_template(
        "search.html", results=results, len_results=len(results), query=query
    )


@app.route("/credits")
def credits():
    return render_template("credits.html")


@app.route("/admin")
def admin():
    return render_template("admin.html")


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
    app.run(debug=True if not IS_PROD else False)
