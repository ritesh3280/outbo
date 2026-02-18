"""Manual test script for ScraperTool.

Usage:
    python -m backend.tools.test_scraper
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.tools.scraper import ScraperTool


async def main() -> None:
    scraper = ScraperTool()
    print(f"Stub mode: {scraper._is_stub}")
    print()

    print("=== Scraping https://stripe.com/about ===")
    result = await scraper.scrape_url("https://stripe.com/about")
    print(f"  Success: {result.success}")
    print(f"  URL: {result.url}")
    print(f"  Title: {result.title}")
    print(f"  Content (first 300 chars): {result.content[:300]}")
    print()

    print("=== Scraping multiple URLs ===")
    results = await scraper.scrape_multiple([
        "https://stripe.com/about",
        "https://stripe.com/blog",
    ])
    for r in results:
        print(f"  {r.url} — title: {r.title}, success: {r.success}")
    print()

    print("All scraper tool tests passed!")


if __name__ == "__main__":
    asyncio.run(main())
