"""Search-box helpers (pure Python, no database)."""

MAX_TERMS = 5
MAX_TERM_LENGTH = 50
MAX_QUERY_LENGTH = 100


def clean_query(raw) -> str:
    """The search text as we keep and echo it back: trimmed and length-capped."""
    return (raw or "").strip()[:MAX_QUERY_LENGTH]


def parse_terms(raw) -> list[str]:
    """'  jollof   rice ' -> ['jollof', 'rice'].  Every term must match (AND)."""
    return [t[:MAX_TERM_LENGTH] for t in clean_query(raw).split()][:MAX_TERMS]


def like_pattern(term: str) -> str:
    """Contains-match for SQL LIKE, with the user's % and _ treated literally.

    Use with `.ilike(pattern, escape="\\\\")` so typing '100%' searches for the
    text '100%' instead of matching every listing.
    """
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"
