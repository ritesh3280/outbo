"""Job Posting Analyzer.

User provides a job posting URL → Firecrawl scrapes it → OpenAI extracts
structured context (team, tech stack, requirements, etc.) for use in
search queries, scoring, and email personalization.
"""

import json
import logging

from openai import AsyncOpenAI

from backend.config import settings
from backend.tools.scraper import ScraperTool

logger = logging.getLogger(__name__)

# Type alias for the extracted context (used across people_finder, priority_scorer, email_writer)
JobContext = dict


async def analyze_job_posting(
    url: str | None = None,
    raw_text: str | None = None,
    scraper: ScraperTool | None = None,
) -> JobContext:
    """Extract targeting context from a job posting.

    Args:
        url: Job posting URL (scraped via Firecrawl).
        raw_text: If provided instead of url, use this as the posting content.
        scraper: Optional ScraperTool (uses default if not provided).

    Returns:
        Dict with team, department, hiring_manager, tech_stack, key_requirements,
        keywords, seniority, location. Empty strings/lists when not found.
    """
    if url and not raw_text:
        scraper = scraper or ScraperTool()
        result = await scraper.scrape_url(url)
        content = result.content if result.success else ""
        if not content:
            logger.warning("No content scraped from job URL: %s", url)
            return _empty_job_context()
    elif raw_text:
        content = raw_text
    else:
        return _empty_job_context()

    if not settings.openai_api_key:
        logger.warning("No OpenAI API key — job analysis skipped")
        return _empty_job_context()

    prompt = f"""Extract the following from this job posting. Return JSON only, no markdown.

{{
    "team": "exact team name (e.g. 'Platform Infrastructure') or empty string if not stated",
    "department": "engineering, product, data, etc.",
    "hiring_manager": "name if mentioned, else empty string",
    "tech_stack": ["list", "of", "technologies", "mentioned"],
    "key_requirements": ["top 3-4 requirements or responsibilities"],
    "keywords": ["terms that someone on this team would have in their LinkedIn title"],
    "seniority": "intern/junior/mid/senior or empty",
    "location": "office location or remote"
}}

Job posting:
{content[:8000]}"""

    try:
        client = AsyncOpenAI(api_key=settings.openai_api_key)
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            response_format={"type": "json_object"},
        )
        text = response.choices[0].message.content or "{}"
        data = json.loads(text)
        out = {
            "team": data.get("team") or "",
            "department": data.get("department") or "",
            "hiring_manager": data.get("hiring_manager") or "",
            "tech_stack": data.get("tech_stack") if isinstance(data.get("tech_stack"), list) else [],
            "key_requirements": data.get("key_requirements") if isinstance(data.get("key_requirements"), list) else [],
            "keywords": data.get("keywords") if isinstance(data.get("keywords"), list) else [],
            "seniority": data.get("seniority") or "",
            "location": data.get("location") or "",
        }
        logger.info("Job context extracted: team=%s, department=%s", out["team"], out["department"])
        return out
    except Exception as e:
        logger.warning("Job posting analysis failed: %s", e)
        return _empty_job_context()


def _empty_job_context() -> JobContext:
    return {
        "team": "",
        "department": "",
        "hiring_manager": "",
        "tech_stack": [],
        "key_requirements": [],
        "keywords": [],
        "seniority": "",
        "location": "",
    }


def build_search_queries(company: str, job_context: JobContext) -> list[str]:
    """Build laser-targeted Serper queries from job context."""
    team = job_context.get("team", "") or ""
    department = job_context.get("department", "") or ""
    keywords = job_context.get("keywords", [])
    first_keyword = (keywords[0] if keywords else "engineer").strip()

    queries = [
        f'site:linkedin.com/in "at {company}" "university recruiter" OR "campus recruiter" OR "early career"',
        f'site:linkedin.com/in "at {company}" "recruiter" OR "talent acquisition"',
        f'site:linkedin.com/in "at {company}" "{first_keyword}"',
        f'site:linkedin.com/in "at {company}" "engineering manager" OR "tech lead"',
        f'site:linkedin.com/in "at {company}" "hiring" OR "intern" OR "internship"',
    ]

    if department:
        queries[1] = f'site:linkedin.com/in "at {company}" "recruiter" "{department}"'
    if team:
        queries[2] = f'site:linkedin.com/in "at {company}" "{team}"'
        queries[4] = f'site:linkedin.com/in "at {company}" "engineering manager" OR "tech lead" "{team}"'

    return queries
