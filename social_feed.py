from __future__ import annotations

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


def build_social_feed_items(quotes, *, offset: int, total_quotes: int):
    """Interleave generic social cards after every 4th quote item."""
    items = []
    for index, quote in enumerate(quotes):
        absolute_index = offset + index
        primary_author = quote.authors[0] if quote.authors else "Unknown"
        items.append(
            {
                "kind": "quote",
                "quote": quote,
                "primary_author": primary_author,
            }
        )

        should_insert_generic = (
            SOCIAL_GENERIC_POSTS
            and (absolute_index + 1) % 4 == 0
            and absolute_index < (total_quotes - 1)
        )
        if should_insert_generic:
            generic_index = ((absolute_index + 1) // 4 - 1) % len(SOCIAL_GENERIC_POSTS)
            items.append({"kind": "generic", "post": SOCIAL_GENERIC_POSTS[generic_index]})
    return items
