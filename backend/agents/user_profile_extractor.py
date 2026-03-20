"""User Profile Extractor.

Extracts the applicant's universities, previous companies, and skills from their
LinkedIn profile and/or resume. Used for warm-path matching in the people-finding
pipeline (same university, same previous employer, etc.).
"""

import json
import logging

from openai import AsyncOpenAI
from pydantic import BaseModel

from backend.config import settings
from backend.tools.scraper import ScraperTool
from backend.tools.serper import search as serper_search

logger = logging.getLogger(__name__)


class UserProfile(BaseModel):
    """Extracted from the applicant's LinkedIn/resume/portfolio for warm-path matching."""
    universities: list[str] = []
    previous_companies: list[str] = []
    skills: list[str] = []
    bio: str = ""
    linkedin_url: str = ""


def user_profile_from_doc(doc: dict) -> UserProfile:
    """Convert a stored UserProfileDoc dict to a runtime UserProfile."""
    return UserProfile(
        universities=doc.get("universities") or [],
        previous_companies=doc.get("previous_companies") or [],
        skills=doc.get("skills") or [],
        linkedin_url=doc.get("linkedin_url") or "",
    )


async def extract_user_profile(
    linkedin_url: str | None = None,
    resume_url: str | None = None,
    portfolio_url: str | None = None,
    scraper: ScraperTool | None = None,
) -> UserProfile:
    """Extract user background from LinkedIn and/or resume.

    Tries multiple strategies in order:
      1. Scrape LinkedIn URL via Firecrawl (may fail if login-walled).
      2. Serper search for the LinkedIn profile to get the Google snippet.
      3. Scrape resume URL via Firecrawl.
      4. Send whatever text we gathered to OpenAI for structured extraction.

    Gracefully degrades: returns empty UserProfile if everything fails.

    Args:
        linkedin_url: User's LinkedIn profile URL.
        resume_url: URL to the user's resume (PDF, Google Doc, etc.).
        portfolio_url: User's portfolio/personal website URL.
        scraper: Optional ScraperTool instance.

    Returns:
        UserProfile with universities, previous_companies, and skills.
    """
    if not linkedin_url and not resume_url and not portfolio_url:
        return UserProfile()

    scraper = scraper or ScraperTool()
    gathered_text = ""

    # Strategy 1: Scrape LinkedIn directly
    if linkedin_url:
        try:
            result = await scraper.scrape_url(linkedin_url)
            if result.success and result.content:
                gathered_text += f"\n--- LinkedIn Profile ---\n{result.content[:4000]}"
                logger.info("Scraped LinkedIn profile directly")
        except Exception as e:
            logger.info("LinkedIn direct scrape failed (expected if login-walled): %s", e)

    # Strategy 2: Serper search for LinkedIn snippet
    if linkedin_url and not gathered_text.strip():
        try:
            slug = linkedin_url.rstrip("/").split("/")[-1]
            results = await serper_search(f'site:linkedin.com/in/{slug}', num=3)
            for r in results:
                if r.snippet:
                    gathered_text += f"\n--- LinkedIn Snippet ---\n{r.snippet}"
            if gathered_text.strip():
                logger.info("Got LinkedIn info from Serper snippet")
        except Exception as e:
            logger.info("Serper LinkedIn lookup failed: %s", e)

    # Strategy 3: Scrape resume
    if resume_url:
        try:
            result = await scraper.scrape_url(resume_url)
            if result.success and result.content:
                gathered_text += f"\n--- Resume ---\n{result.content[:5000]}"
                logger.info("Scraped resume content")
        except Exception as e:
            logger.info("Resume scrape failed: %s", e)

    # Strategy 4: Scrape portfolio/personal website
    if portfolio_url:
        try:
            result = await scraper.scrape_url(portfolio_url)
            if result.success and result.content:
                gathered_text += f"\n--- Portfolio / Personal Website ---\n{result.content[:5000]}"
                logger.info("Scraped portfolio: %s", portfolio_url)
        except Exception as e:
            logger.info("Portfolio scrape failed: %s", e)

    if not gathered_text.strip():
        logger.warning("No content gathered for user profile extraction")
        return UserProfile(linkedin_url=linkedin_url or "")

    # Extract structured data via OpenAI
    if not settings.openai_api_key:
        logger.warning("No OpenAI API key — user profile extraction skipped")
        return UserProfile(linkedin_url=linkedin_url or "")

    return await _extract_with_llm(gathered_text, linkedin_url or "")


async def _extract_with_llm(text: str, linkedin_url: str) -> UserProfile:
    """Use OpenAI to extract structured profile data from gathered text."""
    client = AsyncOpenAI(api_key=settings.openai_api_key)

    prompt = f"""Extract the following from this person's profile/resume/portfolio. Return JSON only.

{{
    "universities": ["list of universities/colleges attended (full names)"],
    "previous_companies": ["list of companies they previously worked at (not the current one)"],
    "skills": ["top 5-8 technical skills or areas of expertise"],
    "bio": "2-3 sentence summary of their background for cold email writing. Include: school, year, most impressive project or experience, and a key technical skill. Be specific — mention project names, technologies, or research topics. Write in third person."
}}

If something is unclear or not mentioned, use an empty list / empty string.

Profile content:
{text[:6000]}"""

    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content or "{}"
        data = json.loads(content)

        profile = UserProfile(
            universities=data.get("universities") if isinstance(data.get("universities"), list) else [],
            previous_companies=data.get("previous_companies") if isinstance(data.get("previous_companies"), list) else [],
            skills=data.get("skills") if isinstance(data.get("skills"), list) else [],
            bio=data.get("bio") or "",
            linkedin_url=linkedin_url,
        )
        logger.info(
            "User profile extracted: %d universities, %d previous companies, %d skills, bio=%s",
            len(profile.universities), len(profile.previous_companies), len(profile.skills),
            "yes" if profile.bio else "no",
        )
        return profile
    except Exception as e:
        logger.warning("User profile LLM extraction failed: %s", e)
        return UserProfile(linkedin_url=linkedin_url)
