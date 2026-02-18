"""Firecrawl SDK wrapper.

When FIRECRAWL_API_KEY is set, delegates to the Firecrawl API.
Otherwise, returns mock data for local development and testing.
"""

import asyncio
import logging
from dataclasses import dataclass

from backend.config import settings

logger = logging.getLogger(__name__)

MOCK_SCRAPE_RESULT = {
    "url": "https://example.com/about",
    "title": "About Us — Example Corp (Mock)",
    "content": (
        "Example Corp is a technology company founded in 2020. "
        "We build tools that help developers ship faster. "
        "Our team of 150 engineers is distributed across San Francisco, "
        "New York, and London. We are hiring for multiple roles including "
        "software engineering, product management, and design.\n\n"
        "Our mission is to make developer tools more accessible and powerful. "
        "Recent achievements include launching our v2.0 platform and "
        "growing to 50,000 active users."
    ),
}


@dataclass
class ScrapeResult:
    """Result from a scrape operation."""
    url: str = ""
    title: str = ""
    content: str = ""
    success: bool = True
    error: str = ""


@dataclass
class ScraperTool:
    """Wrapper around Firecrawl API with stub fallback."""

    _is_stub: bool = False

    def __post_init__(self) -> None:
        self._is_stub = not settings.firecrawl_api_key

    async def scrape_url(self, url: str) -> ScrapeResult:
        """Scrape a URL and return structured text content."""
        if self._is_stub:
            logger.info("Stub scraping URL: %s", url)
            return ScrapeResult(
                url=url,
                title=MOCK_SCRAPE_RESULT["title"],
                content=MOCK_SCRAPE_RESULT["content"],
                success=True,
            )

        try:
            from firecrawl import FirecrawlApp

            app = FirecrawlApp(api_key=settings.firecrawl_api_key)

            # Firecrawl v4+ returns a Document object, not a dict
            result = await asyncio.to_thread(
                app.scrape, url, formats=["markdown"]
            )

            title = ""
            content = ""

            if hasattr(result, "markdown"):
                content = result.markdown or ""
            if hasattr(result, "metadata") and result.metadata:
                meta = result.metadata
                if hasattr(meta, "title"):
                    title = meta.title or ""
                elif isinstance(meta, dict):
                    title = meta.get("title", "")

            logger.info("Scraped %s — title: %s, length: %d", url, title[:50], len(content))
            return ScrapeResult(url=url, title=title, content=content, success=True)

        except Exception as e:
            logger.error("Failed to scrape %s: %s", url, e)
            return ScrapeResult(url=url, success=False, error=str(e))

    async def scrape_multiple(self, urls: list[str]) -> list[ScrapeResult]:
        """Scrape multiple URLs concurrently."""
        tasks = [self.scrape_url(url) for url in urls]
        return await asyncio.gather(*tasks)
