import os
from collections import defaultdict


class QuoteBook:
    def __init__(self):
        self.filepath = "qb.qbf"
        with open(self.filepath, "r", encoding="utf-8") as f:
            raw = f.read()

        # Split quotes by blank lines (2+ newlines)
        blocks = [b.strip() for b in raw.split("\n\n") if b.strip()]

        self.quotes = []
        if not hasattr(self, "last_mtime"):
            self.last_mtime = 0

        for block in blocks:
            if "—" in block:
                quote_text, meta = block.rsplit("—", 1)
                self.quotes.append([quote_text.strip(), meta.strip()])
            else:
                # Fallback if metadata missing
                self.quotes.append([block.strip(), "Unknown"])

        # Get stats
        self.total_quotes = len(self.quotes)
        self.speaker_counts = self.get_sorted_quote_counts()

        print(
            f"Loaded {self.total_quotes} quotes from qb.qbf"
            f" with {len(self.speaker_counts)} unique speakers."
        )

    def get_random_quote(self, randomlib):
        return randomlib.choice(self.quotes)

    def get_quote_counts(self):
        """
        Returns a list of (name, count) tuples
        """
        counts = defaultdict(int)

        for quote in self.quotes:
            if len(quote) < 2:
                continue

            meta = quote[1].strip()

            # Name(s) appear before first comma
            name_part = meta.split(",", 1)[0].strip()

            # Support multiple speakers: "Ben and James"
            speakers = [n.strip() for n in name_part.split(" and ")]

            for speaker in speakers:
                if speaker:
                    counts[speaker] += 1

        return list(counts.items())

    def get_sorted_quote_counts(self):
        """
        Returns counts sorted highest → lowest
        """
        return sorted(self.get_quote_counts(), key=lambda x: x[1], reverse=True)

    def add_quote(self, quote_text, author_info="Unknown"):
        if not quote_text:
            raise ValueError("Quote text cannot be empty")

        # --- sanitise quote text for storage ---
        quote = quote_text.strip()

        # Normalise newlines (no multi-paragraph quotes)
        quote = " ".join(quote.splitlines())

        # Escape double quotes inside the quote
        quote = quote.replace('"', '\\"')

        # Wrap in speech marks
        quote = f'"{quote}"'

        # --- sanitise author ---
        author = author_info.strip() or "Unknown"
        author = author.replace("\n", " ").replace("—", "—")

        # --- write in canonical format ---
        with open("qb.qbf", "a", encoding="utf-8") as f:
            f.write(f"\n\n{quote} — {author}")

    def search_quotes(self, query):
        """Search quotes for a given query string (case-insensitive)."""
        query_lower = query.lower()
        results = []

        for quote in self.quotes:
            quote_text = quote[0].lower()
            author_info = quote[1].lower() if len(quote) > 1 else ""

            if query_lower in quote_text or query_lower in author_info:
                results.append(quote)

        return results

    def reload(self, force=False):
        """Reload quotes from disk."""
        try:
            mtime = os.path.getmtime(self.filepath)
        except FileNotFoundError:
            return 500

        if not force and mtime == self.last_mtime:
            return 304  # Not Modified

        with open(self.filepath, "r", encoding="utf-8") as f:
            raw = f.read().strip()

        self.last_mtime = mtime
        self.__init__()  # Re-initialize to reload quotes

        return 200


if __name__ == "__main__":
    exit()  # Prevent running standalone
