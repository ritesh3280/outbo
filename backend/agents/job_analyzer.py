"""Job Posting Analyzer.

User provides a job posting URL → Firecrawl scrapes it → OpenAI extracts
structured context (team, tech stack, requirements, etc.) for use in
search queries, scoring, and email personalization.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from openai import AsyncOpenAI

from backend.config import settings
from backend.tools.scraper import ScraperTool

if TYPE_CHECKING:
    from backend.agents.user_profile_extractor import UserProfile

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
        keywords, seniority, location, job_title_exact, hiring_signals, posting_date.
        Empty strings/lists when not found.
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
    "location": "office location or remote",
    "job_title_exact": "the exact job title as listed in the posting",
    "hiring_signals": ["any names mentioned as contacts, recruiters, or people to reach out to"],
    "posting_date": "date the job was posted if visible, else empty string",
    "email_domain": "if the posting mentions an official email domain like @gc.com or @stripe.com, extract just the domain (no @). Empty string if not mentioned.",
    "reporting_to": "if the posting says who this role reports to (e.g. 'report to a Senior Engineer on the Payments team'), extract the full phrase. Empty string if not mentioned."
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
            "job_title_exact": data.get("job_title_exact") or "",
            "hiring_signals": data.get("hiring_signals") if isinstance(data.get("hiring_signals"), list) else [],
            "posting_date": data.get("posting_date") or "",
            "email_domain": data.get("email_domain") or _extract_email_domain_from_text(content),
            "reporting_to": data.get("reporting_to") or "",
        }
        logger.info("Job context extracted: team=%s, department=%s, hiring_manager=%s, email_domain=%s", out["team"], out["department"], out["hiring_manager"], out["email_domain"])
        return out
    except Exception as e:
        logger.warning("Job posting analysis failed: %s", e)
        return _empty_job_context()


_SKIP_EMAIL_DOMAINS = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "example.com", "email.com"}


def _extract_email_domain_from_text(content: str) -> str:
    """Regex fallback: find @domain.com patterns in job posting text."""
    matches = re.findall(r'@([a-zA-Z0-9][-a-zA-Z0-9]*\.[a-zA-Z]{2,})', content)
    for m in matches:
        if m.lower() not in _SKIP_EMAIL_DOMAINS:
            return m.lower()
    return ""


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
        "job_title_exact": "",
        "hiring_signals": [],
        "posting_date": "",
        "email_domain": "",
        "reporting_to": "",
    }


# ── Dynamic, tiered query builder ─────────────────────────────────────────


@dataclass
class QueryGroup:
    """A search query tagged with its category and priority tier."""
    query: str
    category: str  # "job_posting_sharer" | "hiring_manager" | "team_search" | "warm_path" | "recruiter" | "general"
    priority: int  # 1=highest, lower runs first


def build_search_queries(
    company: str,
    job_context: JobContext | None = None,
    user_profile: "UserProfile | None" = None,
    job_url: str | None = None,
    role_keyword: str = "engineer",
) -> list[QueryGroup]:
    """Build prioritized, tiered Serper queries based on all available context.

    Tiers:
      1. Job-posting-driven (who shared/promoted the listing, hiring manager)
      2. Team-specific (engineers + managers on the exact team)
      3. Warm path (same university, same previous company as the applicant)
      4. Recency signals (recent hiring activity)
      5. Standard fallback (recruiters, general engineers)

    Returns up to 10 QueryGroups sorted by priority (ascending = highest first).
    """
    queries: list[QueryGroup] = []
    jc = job_context or {}

    team = jc.get("team", "") or ""
    department = jc.get("department", "") or ""
    keywords = jc.get("keywords", [])
    first_keyword = (keywords[0] if keywords else role_keyword).strip()
    job_title_exact = jc.get("job_title_exact", "") or ""
    hiring_manager = jc.get("hiring_manager", "") or ""
    hiring_signals = jc.get("hiring_signals", [])

    # ── TIER 1: Job-posting-driven ────────────────────────────────────
    if job_url:
        queries.append(QueryGroup(
            query=f'"{job_url}" site:linkedin.com',
            category="job_posting_sharer",
            priority=1,
        ))
        title_for_search = job_title_exact or first_keyword
        queries.append(QueryGroup(
            query=f'site:linkedin.com/in "{company}" "{title_for_search}" "hiring" OR "join my team" OR "we\'re hiring"',
            category="job_posting_sharer",
            priority=1,
        ))

    if hiring_manager:
        queries.append(QueryGroup(
            query=f'site:linkedin.com/in "{hiring_manager}" "{company}"',
            category="hiring_manager",
            priority=1,
        ))

    for signal_name in hiring_signals[:2]:
        if signal_name and signal_name != hiring_manager:
            queries.append(QueryGroup(
                query=f'site:linkedin.com/in "{signal_name}" "{company}"',
                category="hiring_manager",
                priority=1,
            ))

    # ── TIER 1b: Reporting structure (find the person this role reports to)
    reporting_to = jc.get("reporting_to", "") or ""
    if reporting_to:
        queries.append(QueryGroup(
            query=f'site:linkedin.com/in "{company}" "{reporting_to}"',
            category="reporting_manager",
            priority=1,
        ))
        # If a team name is embedded, search for it specifically
        for sep in [" on the ", " on ", " in the ", " in "]:
            if sep in reporting_to:
                team_part = reporting_to.split(sep)[-1].rstrip(" team").strip()
                if team_part and team_part.lower() != (team or "").lower():
                    queries.append(QueryGroup(
                        query=f'site:linkedin.com/in "{company}" "{team_part}"',
                        category="reporting_manager",
                        priority=1,
                    ))
                break

    # ── TIER 2: Team-specific ─────────────────────────────────────────
    if team:
        queries.append(QueryGroup(
            query=f'site:linkedin.com/in "at {company}" "{team}" engineer OR developer',
            category="team_search",
            priority=2,
        ))
        queries.append(QueryGroup(
            query=f'site:linkedin.com/in "at {company}" "{team}" manager OR lead',
            category="team_search",
            priority=2,
        ))

    if department and department.lower() != (team or "").lower():
        queries.append(QueryGroup(
            query=f'site:linkedin.com/in "at {company}" "{department}" engineer OR developer',
            category="team_search",
            priority=2,
        ))

    if first_keyword and first_keyword != team:
        queries.append(QueryGroup(
            query=f'site:linkedin.com/in "at {company}" "{first_keyword}"',
            category="team_search",
            priority=2,
        ))

    # ── TIER 3: Warm path (only if user profile available) ────────────
    if user_profile:
        for uni in (user_profile.universities or [])[:2]:
            queries.append(QueryGroup(
                query=f'site:linkedin.com/in "at {company}" "{uni}"',
                category="warm_path",
                priority=3,
            ))
        for prev_co in (user_profile.previous_companies or [])[:2]:
            queries.append(QueryGroup(
                query=f'site:linkedin.com/in "at {company}" previously "{prev_co}" OR "formerly" "{prev_co}"',
                category="warm_path",
                priority=3,
            ))

    # ── TIER 4: Recency signals ───────────────────────────────────────
    queries.append(QueryGroup(
        query=f'site:linkedin.com/in "at {company}" "{role_keyword}" hiring 2026',
        category="general",
        priority=4,
    ))

    # ── TIER 5: Standard fallback (always included) ───────────────────
    queries.append(QueryGroup(
        query=f'site:linkedin.com/in "at {company}" "university recruiter" OR "campus recruiter" OR "early career recruiter"',
        category="recruiter",
        priority=5,
    ))
    queries.append(QueryGroup(
        query=f'site:linkedin.com/in "at {company}" "recruiter" OR "talent acquisition"',
        category="recruiter",
        priority=5,
    ))
    queries.append(QueryGroup(
        query=f'site:linkedin.com/in "at {company}" "engineering manager" OR "tech lead"',
        category="general",
        priority=5,
    ))
    queries.append(QueryGroup(
        query=f'site:linkedin.com/in "at {company}" "hiring" OR "intern" OR "internship"',
        category="general",
        priority=5,
    ))

    # ── Budget cap: sort by priority, keep top 10 ─────────────────────
    queries.sort(key=lambda q: q.priority)
    queries = queries[:10]

    logger.info(
        "Built %d search queries (tiers: %s)",
        len(queries),
        ", ".join(sorted({str(q.priority) for q in queries})),
    )
    return queries
