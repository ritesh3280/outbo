"""Test the improved people finder and domain discovery for common companies."""

import asyncio
import logging

from backend.tools.browser import BrowserTool
from backend.tools.scraper import ScraperTool
from backend.agents.people_finder import PeopleFinder
from backend.agents.email_finder import discover_company_domain

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def test_company(company: str, role: str = "Software Engineer"):
    """Test people finding and domain discovery for a company."""
    print("\n" + "="*80)
    print(f"Testing: {company}")
    print("="*80)
    
    # Test 1: Domain discovery
    print(f"\n[1] Domain Discovery for {company}...")
    scraper = ScraperTool()
    try:
        domain = await discover_company_domain(company, scraper, explicit_website=None)
        print(f"✓ Discovered domain: {domain}")
    except Exception as e:
        print(f"✗ Domain discovery failed: {e}")
        domain = None
    
    # Test 2: People finding with validation
    print(f"\n[2] Finding people at {company} for role: {role}...")
    browser = BrowserTool()
    finder = PeopleFinder(browser=browser)
    
    try:
        people = await finder.find_people(
            company=company,
            role=role,
            target_count=5,  # Smaller for testing
        )
        
        print(f"✓ Found {len(people)} people after validation:")
        for i, person in enumerate(people[:3], 1):  # Show top 3
            print(f"  {i}. {person.name}")
            print(f"     Title: {person.title}")
            print(f"     Score: {person.priority_score:.2f}")
            print(f"     LinkedIn: {person.linkedin_url}")
            if person.recent_activity:
                snippet = person.recent_activity[:100].replace("\n", " ")
                print(f"     Snippet: {snippet}...")
        
        if len(people) > 3:
            print(f"  ... and {len(people) - 3} more")
        
        return {"company": company, "domain": domain, "people_count": len(people), "success": True}
        
    except Exception as e:
        print(f"✗ People finding failed: {e}")
        import traceback
        traceback.print_exc()
        return {"company": company, "domain": domain, "people_count": 0, "success": False}


async def main():
    """Test multiple companies."""
    companies = [
        ("Stripe", "Software Engineer"),
        ("Google", "Software Engineer"),
        ("Datadog", "Software Engineer"),
    ]
    
    results = []
    for company, role in companies:
        result = await test_company(company, role)
        results.append(result)
        await asyncio.sleep(2)  # Brief pause between tests
    
    # Summary
    print("\n" + "="*80)
    print("SUMMARY")
    print("="*80)
    for r in results:
        status = "✓" if r["success"] else "✗"
        print(f"{status} {r['company']:15s} Domain: {r['domain']:30s} People: {r['people_count']}")
    
    success_count = sum(1 for r in results if r["success"])
    print(f"\n{success_count}/{len(results)} tests passed")


if __name__ == "__main__":
    asyncio.run(main())
