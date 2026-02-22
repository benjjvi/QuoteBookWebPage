import hashlib
import json
import logging
import os
import re
import time

import requests
from dotenv import load_dotenv
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

import qb_formats
from PATTERNS import TABOO_GROUPS, UK_INSULT_PATTERNS

load_dotenv()

CACHE_DIR = "cache"
CACHE_FILE = os.path.join(CACHE_DIR, "top_20_cache.json")
CACHE_TTL_SECONDS = 60 * 60 * 3  # 3 hours

logger = logging.getLogger(__name__)


class AI:
    def __init__(self):
        self.OPENROUTER_KEY = os.getenv("OPENROUTER_KEY", "").strip()
        self.OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
        self.can_generate = bool(self.OPENROUTER_KEY)
        if not self.can_generate:
            logger.warning(
                "OPENROUTER_KEY not set; AI screenplay generation is disabled."
            )
        self.sentiment = SentimentIntensityAnalyzer()

        with open("profanities.json", "r", encoding="utf-8") as f:
            self.profanity_list = json.load(f)

    def build_screenplay_prompt(self, quotes):
        top20_list = quotes["data"]["top_20"]

        joined_quotes = "\n".join(
            f"- \"{q['quote']}\" — {', '.join(q['authors'])}" for q in top20_list
        )

        return f"""
    You are writing a short comedy screenplay set in the UK.

    Rules:
    - Use the quotes EXACTLY as written (do not censor, rewrite, or soften them).
    - You may add dialogue that has not been said, but you mustn't rewrite the quotes, or re-accredit them to other people.
    - If the conversation flows better with dialogue, add it.
    - Each quote should appear naturally as dialogue.
    - You may invent characters, locations, and transitions.
    - Do NOT explain the jokes.
    - Do NOT moralise or add disclaimers.
    - Add discussion between characters that doesnt exist, but do NOT rewrite the quotes.
    - The quotes should be the punchlines or key moments.
    - Tone: dry British humour, casual, slightly chaotic.
    - Setting examples: pub, kitchen, smoking area, group chat, workplace.
    - You are working as an API. Do not include any explanations or extra text. Just return the screenplay.
    - IMPORTANT: When you return the screenplay, you are working as an API. DO NOT INCLUDE ANY TEXT OTHER THAN THE SCREENPLAY.

    Quotes to include:
    {joined_quotes}

    Write a screenplay-style script with character names and dialogue.
    """

    def get_ai(self, top_20):
        os.makedirs(CACHE_DIR, exist_ok=True)
        cache_file = os.path.join(CACHE_DIR, "ai_response_cache.json")
        now = int(time.time())

        # --- Step 1: compute top_20 hash ---
        hash_input = [
            {
                "id": q["id"],
                "quote": q["quote"],
                "authors": q["authors"],
                "score": q["score"],
            }
            for q in top_20["data"]["top_20"]
        ]

        top_20_hash = hashlib.sha256(
            json.dumps(hash_input, sort_keys=True).encode("utf-8")
        ).hexdigest()

        # --- Step 2: check existing AI cache ---
        if os.path.exists(cache_file):
            try:
                with open(cache_file, "r") as f:
                    cache = json.load(f)

                if (
                    cache.get("expires_at", 0) > now
                    and cache.get("hash") == top_20_hash
                ):
                    logger.info("Using cached AI screenplay.")
                    return cache["data"]

            except (json.JSONDecodeError, KeyError):
                logger.warning("AI screenplay cache is invalid; regenerating cache.")

        # --- Step 3: regenerate cache ---
        ai_response = None
        try:
            logger.info("Generating new AI screenplay.")
            ai_response = self.generate_screenplay(top_20)
        except Exception as e:
            logger.exception("Error generating AI screenplay: %s", e)
            logger.info("Falling back to cached response if available...")
            if os.path.exists(cache_file):
                try:
                    with open(cache_file, "r") as f:
                        cache = json.load(f)

                    if cache.get("hash") == top_20_hash:
                        return cache.get("data", "")

                except (json.JSONDecodeError, KeyError):
                    raise FileNotFoundError(
                        "Can not lock cache file, and cannot generate new AI response."
                    )  # corrupted cache → cannot use
            ai_response = self.build_fallback_screenplay(top_20)
            logger.info("Using local fallback screenplay because AI generation failed.")

        # --- Step 4: save ---
        cache_payload = {
            "expires_at": now + CACHE_TTL_SECONDS,
            "hash": top_20_hash,
            "data": ai_response,
        }
        with open(cache_file, "w") as f:
            json.dump(cache_payload, f, indent=2)

        ai_response = ai_response.replace("â", "'")

        return ai_response

    def build_fallback_screenplay(self, top_20):
        quotes = (top_20 or {}).get("data", {}).get("top_20", [])[:8]
        if not quotes:
            return (
                "INT. OFFICE - DAY\n\n"
                "NARRATOR\n"
                "The room is suspiciously quiet. No quotes were available.\n"
            )

        lines = ["INT. BREAK ROOM - DAY", ""]
        for item in quotes:
            authors = item.get("authors") or ["Unknown"]
            author_line = ", ".join(str(author).strip() for author in authors if str(author).strip())
            quote_line = str(item.get("quote") or "").strip() or "(silence)"
            lines.append(author_line.upper() or "UNKNOWN")
            lines.append(quote_line)
            lines.append("")

        lines.append("NARRATOR")
        lines.append(
            "Fallback screenplay generated while the AI provider was unavailable."
        )
        return "\n".join(lines)

    def generate_screenplay(self, quotes):
        if not self.can_generate:
            raise RuntimeError("OPENROUTER_KEY not set; AI generation disabled.")
        prompt = self.build_screenplay_prompt(quotes)

        headers = {
            "Authorization": f"Bearer {self.OPENROUTER_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://benjjvi.pythonanywhere.com",  # optional but recommended
            "X-Title": "QuoteBook Screenplay Generator",
        }

        payload = {
            "model": "mistralai/mistral-7b-instruct",  # excellent for scripts
            # alternatives:
            # "openai/gpt-4o"
            # "google/gemini-pro-1.5"
            "messages": [
                {
                    "role": "system",
                    "content": "You are a professional comedy screenwriter.",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.9,
            "max_tokens": 1200,
        }

        logger.debug("Requesting screenplay from OpenRouter (%s).", payload["model"])
        response = requests.post(
            self.OPENROUTER_URL, headers=headers, json=payload, timeout=60
        )
        response.raise_for_status()

        answer = response.json()["choices"][0]["message"]["content"]
        if not answer:
            logger.warning("OpenRouter returned an empty screenplay response.")

        return answer

    def build_weekly_digest_prompt(self, digest_data):
        payload_json = json.dumps(digest_data, ensure_ascii=False, indent=2)
        return f"""
You are generating a weekly digest email for a Quote Book application.

You are working as part of an API.
Return STRICT JSON only, with no markdown and no commentary:
{{
  "subject": "short email subject",
  "body": "plain text email body"
}}

Rules:
- Keep subject under 80 characters.
- Body must be plain text (no markdown, no HTML).
- Keep the body concise and readable for friends.
- Body must be at least 2 paragraphs (separated by a blank line).
- Mention notable themes, standout quotes, and speaker highlights.
- Include a short "Top picks" section with quote IDs.
- Include a "Leagues" section using at least 3 items from `weekly_leagues`
  (for example: Funniest Quote League, Most Prolific Speaker League, Chaos Hour League).
- Keep a light, witty British tone.
- Do not include disclaimers.
- Do not include any text before or after the JSON object.

Digest data:
{payload_json}
"""

    def generate_weekly_digest(self, digest_data):
        if not self.can_generate:
            raise RuntimeError("OPENROUTER_KEY not set; AI generation disabled.")

        prompt = self.build_weekly_digest_prompt(digest_data)
        headers = {
            "Authorization": f"Bearer {self.OPENROUTER_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://benjjvi.pythonanywhere.com",
            "X-Title": "QuoteBook Weekly Digest Generator",
        }
        payload = {
            "model": "mistralai/mistral-7b-instruct",
            "messages": [
                {
                    "role": "system",
                    "content": "You produce strict JSON outputs for backend APIs.",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 900,
        }

        logger.debug("Requesting weekly digest from OpenRouter (%s).", payload["model"])
        last_error = None
        payload_variants = [
            {**payload, "response_format": {"type": "json_object"}},
            payload,
        ]
        for candidate_payload in payload_variants:
            try:
                response = requests.post(
                    self.OPENROUTER_URL,
                    headers=headers,
                    json=candidate_payload,
                    timeout=60,
                )
                response.raise_for_status()
                answer = response.json()["choices"][0]["message"]["content"]
                subject, body = self.parse_weekly_digest_response(answer)
                if self.count_paragraphs(body) < 2:
                    raise ValueError(
                        "AI weekly digest body must contain at least 2 paragraphs."
                    )
                return subject, body
            except Exception as exc:
                last_error = exc
        raise RuntimeError(f"AI weekly digest parsing failed: {last_error}")

    @staticmethod
    def count_paragraphs(text: str) -> int:
        chunks = re.split(r"\n\s*\n", (text or "").strip())
        return len([chunk for chunk in chunks if chunk.strip()])

    def parse_weekly_digest_response(self, text):
        try:
            parsed = self.extract_json_from_response(text)
            subject = str(parsed.get("subject", "")).strip()
            body = str(parsed.get("body", "")).strip()
            if subject and body:
                return subject, body
        except (json.JSONDecodeError, TypeError, AttributeError, ValueError) as exc:
            logger.debug(
                "Weekly digest JSON parse failed (%s); trying fallback parser.",
                exc,
            )

        # Fallback parser for near-JSON replies where body includes raw newlines.
        stripped = text.strip()
        if stripped.startswith("```"):
            stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
            stripped = re.sub(r"\s*```$", "", stripped)

        subject_match = re.search(
            r'"subject"\s*:\s*"(?P<subject>(?:\\.|[^"\\])*)"', stripped, flags=re.DOTALL
        )
        body_match = re.search(
            r'"body"\s*:\s*"(?P<body>[\s\S]*)"\s*}\s*$',
            stripped,
            flags=re.DOTALL,
        )
        if not subject_match or not body_match:
            raise ValueError("AI weekly digest response missing subject/body.")

        subject = subject_match.group("subject").replace('\\"', '"').strip()
        body = body_match.group("body").replace('\\"', '"').strip()
        if not subject or not body:
            raise ValueError("AI weekly digest response missing subject/body.")
        return subject, body

    def extract_json_from_response(self, text):
        """
        Handles:
        - raw JSON
        - ```json { ... } ```
        - ``` { ... } ```
        """

        text = text.strip()

        # Remove triple backticks if present
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?", "", text.strip(), flags=re.IGNORECASE)
            text = re.sub(r"```$", "", text.strip())

        return json.loads(text)

    def normalise(self, raw):
        return max(1.0, min(10.0, raw + 5))

    def classify_funny_score(self, quote, authors, stats):
        # Score quotes based on amount of profanities, general humor, and absurdity.
        score = -0.5
        stats = stats or {}

        # split the quote into words
        words = re.findall(r"\b\w+\b", quote.lower())

        profanity_score = 0
        for word in words:
            for profanity in self.profanity_list:
                if word in profanity["match"].split("|") and word not in profanity.get(
                    "exceptions", []
                ):
                    if profanity_score > 0:
                        profanity_density = profanity_score / len(words)

                        if profanity_density > 0.25:
                            score += 0.2
                    else:
                        profanity_score += profanity["severity"]

        score += min(profanity_score, 3.0)

        if re.search(r"(what|why|how|who|where|\?)", quote.lower()):
            score += 0.3

        if re.search(r"['“].+['”]", quote):
            score += 0.4

        taboo_score = 0
        for group in TABOO_GROUPS.values():
            if any(word in group["words"] for word in words):
                taboo_score += group["weight"]

        score += min(taboo_score, 2.5)

        uk_insult_hit = False
        for group in UK_INSULT_PATTERNS.values():
            if any(re.search(p, quote.lower()) for p in group["patterns"]):
                score += group["weight"]
                uk_insult_hit = True
                break

        # Deadpan insult bonus (only if NOT already UK insult)
        if not uk_insult_hit:
            if len(words) <= 8 and not re.search(r"[!?]", quote):
                if any(w in words for w in ["na", "mum", "mam", "da", "dad"]):
                    score += 0.4

        sent = self.sentiment.polarity_scores(quote)

        intensity = abs(sent["compound"])

        length_factor = min(len(words) / 8, 1.0)

        if intensity > 0.4:
            score += 0.5 * length_factor

        if sent["compound"] < -0.4:
            score += 0.3 * length_factor

        # If quote contains taboo but sentiment is strongly positive, dampen it
        if sent["compound"] > 0.6 and taboo_score > 0:
            score -= 0.4

        if len(words) > 0 and sum(len(w) for w in words) / len(words) > 6:
            score += 0.2  # long weird words

        if re.search(r"\b(but|until|except|and then|so anyway)\b", quote.lower()):
            score += 0.4

        # get battle results and add to funnyness. one score is worth +0.2 funny points
        battle_wins = stats.get("wins", 0)
        battles_fought = stats.get("battles", 0)
        if battles_fought > 0:
            win_rate = battle_wins / battles_fought
            logger.debug("Win rate: %s", win_rate)
            score += min(win_rate * 5 * 0.2, 10.0)  # cap at +10.0

            battles_lost = stats.get("losses", 0)
            loss_rate = battles_lost / battles_fought
            logger.debug("Loss rate: %s", loss_rate)
            score -= min(loss_rate * 5 * 0.2, 2.0)  # cap at -2.0

        try:
            anarchy_points = max(int(stats.get("anarchy_points", 0) or 0), 0)
        except (TypeError, ValueError):
            anarchy_points = 0
        if anarchy_points > 0:
            # Quote Anarchy winner points are a small tie-breaker in funny scoring.
            score += min(anarchy_points * 0.12, 1.8)

        return round(self.normalise(score), 2)

    def get_top_20_with_cache(self, scored_quotes):
        """
        scored_quotes: list of (quote_obj, score) tuples
        """

        os.makedirs(CACHE_DIR, exist_ok=True)
        now = int(time.time())

        # --- Step 1: sort & take top 20 ---
        top_20 = sorted(scored_quotes, key=lambda x: x[1], reverse=True)[:20]

        # --- Step 2: build deterministic hash of top 20 ---
        hash_input = [
            {
                "id": q.id,
                "quote": q.quote,
                "authors": q.authors,
                "score": score,
            }
            for q, score in top_20
        ]

        hash_str = json.dumps(hash_input, sort_keys=True)
        top_20_hash = hashlib.sha256(hash_str.encode("utf-8")).hexdigest()

        # --- Step 3: load cache if it exists ---
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, "r") as f:
                    cache = json.load(f)

                if (
                    cache.get("expires_at", 0) > now
                    and cache.get("data", {}).get("hash") == top_20_hash
                ):
                    logger.info("Using cached top 20.")
                    return cache

            except (json.JSONDecodeError, KeyError):
                logger.warning("Top-20 cache is invalid; regenerating cache.")

        # --- Step 4: save fresh cache ---
        logger.info("Regenerating top 20 cache.")
        expires_at = now + CACHE_TTL_SECONDS

        cache_payload = {
            "expires_at": expires_at,
            "data": {
                "hash": top_20_hash,
                "top_20": hash_input,
            },
        }

        with open(CACHE_FILE, "w") as f:
            json.dump(cache_payload, f, indent=2)

        return cache_payload


if __name__ == "__main__":
    ai = AI()
    qb = qb_formats.QuoteBook("qb.qbf")
    quotes = qb.quotes
    scored_quotes = [
        (q, ai.classify_funny_score(q.quote, q.authors, q.stats)) for q in quotes
    ]
    top_20 = ai.get_top_20_with_cache(scored_quotes)

    sc = ai.get_ai(top_20)
    logger.info(sc)
