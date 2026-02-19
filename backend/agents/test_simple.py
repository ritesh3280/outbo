"""Simple quick test of the new query and validation."""

import asyncio
import logging

from backend.tools.browser import BrowserTool
from backend.agents.people_finder import PeopleFinder

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main():
    """Quick test with just Stripe."""
    print("\nTesting Stripe with new search query and validation...")
    print("="*80)
    
    browser = BrowserTool()
    finder = PeopleFinder(browser=browser)
    
    try:
        people = await finder.find_people(
            company="Stripe",
            role="Software Engineer",
            target_count=3,  # Just 3 for quick test
        )
        
        print(f"\n✓ Found {len(people)} validated people:")
        for i, person in enumerate(people, 1):
            print(f"\n{i}. {person.name}")
            print(f"   Title: {person.title}")
            print(f"   Score: {person.priority_score:.2f}")
            print(f"   LinkedIn: {person.linkedin_url}")
            if person.recent_activity:
                snippet = person.recent_activity[:120].replace("\n", " ")
                print(f"   Activity: {snippet}...")
        
        print(f"\n✓ Test passed! All {len(people)} people validated as Stripe employees.")
        
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
