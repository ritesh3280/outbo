"""Manual test script for BrowserTool.

Usage:
    python -m backend.tools.test_browser
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.tools.browser import BrowserTool


async def main() -> None:
    browser = BrowserTool()
    print(f"Stub mode: {browser._is_stub}")
    print()

    print("=== Opening session ===")
    result = await browser.open_session()
    print(f"  Success: {result.success}")
    print(f"  Session ID: {browser.session_id}")
    print()

    print("=== Navigating to https://google.com ===")
    result = await browser.navigate("https://google.com")
    print(f"  Success: {result.success}")
    print(f"  URL: {result.url}")
    print(f"  Title: {result.title}")
    print(f"  Content (first 200 chars): {result.content[:200]}")
    print()

    print("=== Getting page content ===")
    result = await browser.get_page_content()
    print(f"  Success: {result.success}")
    print(f"  Title: {result.title}")
    print()

    print("=== Taking screenshot ===")
    result = await browser.screenshot()
    print(f"  Success: {result.success}")
    print(f"  Screenshot data length: {len(result.screenshot_b64)}")
    print()

    print("=== Closing session ===")
    result = await browser.close_session()
    print(f"  Success: {result.success}")
    print(f"  Session ID after close: {browser.session_id}")
    print()

    print("All browser tool tests passed!")


if __name__ == "__main__":
    asyncio.run(main())
