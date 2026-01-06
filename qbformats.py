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

    def add_quote_to_queue(self, quote_text, author_info="Unknown"):
        """Add quote to a temporary queue file for later processing."""
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

        # --- write in canonical format to queue file ---
        with open("qb_queue.qbf", "a", encoding="utf-8") as f:
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

    # -----------------------------
    # Admin queue management
    # -----------------------------
    def load_queue(self):
        """Load the queue from qb_queue.qbf (text format, two newlines per quote)"""
        self.queue = []
        try:
            with open("qb_queue.qbf", "r", encoding="utf-8") as f:
                raw = f.read().strip()
                if raw:
                    # Split quotes by two newlines
                    entries = raw.split("\n\n")
                    for entry in entries:
                        # Split quote and info by " — " delimiter
                        if " — " in entry:
                            quote, info = entry.split(" — ", 1)
                            self.queue.append([quote.strip(), info.strip()])
        except FileNotFoundError:
            self.queue = []

    def save_queue(self):
        """Save the current queue back to qb_queue.qbf"""
        with open("qb_queue.qbf", "w", encoding="utf-8") as f:
            # Join each quote with " — " and separate by two newlines
            lines = [f"{quote} — {info}" for quote, info in self.queue]
            f.write("\n\n".join(lines))

    def approve_quote(self, index):
        """
        Move a quote from the queue into the main quotes list.
        Automatically saves both queue and main quotes.
        Strips any wrapping speech marks from the queued quote.
        """
        if 0 <= index < len(self.queue):
            quote, author_info = self.queue.pop(index)

            # Strip wrapping quotes if present
            quote = quote.strip()
            if (quote.startswith('"') and quote.endswith('"')) or (
                quote.startswith("“") and quote.endswith("”")
            ):
                quote = quote[1:-1].strip()

            # Add to main quotes (add_quote will wrap in quotes)
            self.add_quote(quote, author_info)

            # Save updated queue
            self.save_queue()
            return True

        return False

    def reject_quote(self, index):
        """Remove a quote from the queue without adding it."""
        if 0 <= index < len(self.queue):
            self.queue.pop(index)
            self.save_queue()
            return True
        return False


if __name__ == "__main__":
    exit()  # Prevent running standalone
