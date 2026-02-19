"""Serper Google Search API — cheap search for LinkedIn profiles.

Used by PeopleFinder to cast a wide net (5 queries, ~$0.005 total).
"""

import logging
from dataclasses import dataclass

import httpx

from backend.config import settings

logger = logging.getLogger(__name__)

SERPER_SEARCH_URL = "https://google.serper.dev/search"


@dataclass
class SerperResult:
    """Single organic search result."""
    title: str
    link: str
    snippet: str


async def search(query: str, num: int = 10) -> list[SerperResult]:
    """Run a single Serper Google search.

    Args:
        query: Google search query string.
        num: Max number of results (default 10).

    Returns:
        List of SerperResult (title, link, snippet). Empty if no key or on error.
    """
    if not settings.serper_api_key:
        logger.warning("No SERPER_API_KEY — Serper search skipped")
        return []

    payload = {"q": query, "num": num}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                SERPER_SEARCH_URL,
                json=payload,
                headers={"X-API-KEY": settings.serper_api_key},
            )
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        logger.warning("Serper search failed: %s", e)
        return []

    organic = data.get("organic") or data.get("searchResults") or []
    results = []
    for item in organic:
        if isinstance(item, dict) and item.get("link"):
            results.append(SerperResult(
                title=item.get("title", ""),
                link=item.get("link", ""),
                snippet=item.get("snippet", ""),
            ))
    return results
