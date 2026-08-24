"""Print page counts from Google Books — opt-in, cached, and never citable.

Used for one thing only: annotating an EPUB chapter locator with a rough
"about page N" so a passage can be found again in a print copy. Google Books
page counts are frequently the ebook's own pagination rather than a print
edition's, so anything derived from this is flagged estimated and warned about
before export.
"""

from __future__ import annotations

import re
import sqlite3

import httpx

from .config import settings
from .store import now_iso

ENDPOINT = "https://www.googleapis.com/books/v1/volumes"


def _cache_key(isbn: str | None, title: str | None, author: str | None) -> str:
    return "|".join([(isbn or ""), (title or "")[:120], (author or "")[:60]])


def page_count(
    conn: sqlite3.Connection,
    *,
    isbn: str | None = None,
    title: str | None = None,
    author: str | None = None,
    client: httpx.Client | None = None,
) -> int | None:
    """Best-effort lookup. A failure is a missing estimate, never an error."""
    key = _cache_key(isbn, title, author)
    if not key.strip("|"):
        return None

    row = conn.execute(
        "SELECT page_count FROM gbooks_cache WHERE cache_key = ?", (key,)
    ).fetchone()
    if row is not None:
        return row["page_count"]

    if isbn:
        query = f"isbn:{re.sub(r'[^0-9Xx]', '', isbn)}"
    else:
        query = " ".join(
            filter(
                None,
                [f'intitle:"{title}"' if title else None,
                 f'inauthor:"{author}"' if author else None],
            )
        )

    found: int | None = None
    owned = client is None
    client = client or httpx.Client(timeout=15, headers={"User-Agent": settings.user_agent})
    try:
        resp = client.get(ENDPOINT, params={"q": query, "maxResults": 5})
        resp.raise_for_status()
        for item in resp.json().get("items", []):
            count = item.get("volumeInfo", {}).get("pageCount")
            if isinstance(count, int) and count > 0:
                found = count
                break
    except Exception:
        found = None
    finally:
        if owned:
            client.close()

    conn.execute(
        "INSERT OR REPLACE INTO gbooks_cache (cache_key, page_count, fetched_at) "
        "VALUES (?, ?, ?)",
        (key, found, now_iso()),
    )
    return found
