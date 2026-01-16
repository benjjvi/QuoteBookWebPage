import hashlib
import json
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


class AI:
    def __init__(self):
        self.OPENROUTER_KEY = os.getenv("OPENROUTER_KEY", "")
        self.OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
        if self.OPENROUTER_KEY == "":
            raise ValueError("OPENROUTER_KEY not set in environment variables")
        self.sentiment = SentimentIntensityAnalyzer()

        with open("profanities.json", "r") as f:
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
                    print("Using cached AI screenplay...")
                    return json.dumps(cache["data"])

            except (json.JSONDecodeError, KeyError):
                pass  # corrupted cache → regenerate

        # --- Step 3: regenerate cache ---
        try:
            print("Generating new AI screenplay...")
            ai_response = self.generate_screenplay(top_20)
        except Exception as e:
            print(f"Error generating AI screenplay: {e}")
            print("Falling back to cached response if available...")
            if os.path.exists(cache_file):
                try:
                    with open(cache_file, "r") as f:
                        cache = json.load(f)

                    if cache.get("data", {}).get("hash") == top_20_hash:
                        return json.dumps(cache["data"])

                except (json.JSONDecodeError, KeyError):
                    raise FileNotFoundError(
                        "Can not lock cache file, and cannot generate new AI response."
                    )  # corrupted cache → cannot use

        # --- Step 4: save ---
        cache_payload = {
            "expires_at": now + CACHE_TTL_SECONDS,
            "hash": top_20_hash,
            "data": ai_response,
        }
        with open(cache_file, "w") as f:
            json.dump(cache_payload, f, indent=2)

        return ai_response

    def generate_screenplay(self, quotes):
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

        response = requests.post(
            self.OPENROUTER_URL, headers=headers, json=payload, timeout=60
        )
        response.raise_for_status()

        answer = response.json()["choices"][0]["message"]["content"]

        # save

        return answer

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

    def classify_funny_score(self, quote, authors):
        # Score quotes based on amount of profanities, general humor, and absurdity.
        score = -0.5

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
                    print("Using cached top 20...")
                    return cache

            except (json.JSONDecodeError, KeyError):
                pass  # corrupted cache → regenerate

        # --- Step 4: save fresh cache ---
        print("Regenerating top 20 cache...")
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
    scored_quotes = [(q, ai.classify_funny_score(q.quote, q.authors)) for q in quotes]
    top_20 = AI.get_top_20_with_cache(scored_quotes)

    sc = ai.get_ai(top_20)
    print(sc)
