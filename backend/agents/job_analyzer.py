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
    "hiring_manager": "a specific person's NAME (first + last) mentioned as the hiring manager, recruiter, or contact. Must be an actual name like 'Jane Smith', NOT a job title like 'Senior Engineer'. Empty string if no specific person is named.",
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

        # Extract #LI- recruiter tags from raw posting text (ATS-syndicated LinkedIn tags)
        li_tags = re.findall(r'#LI-([A-Z]{2}\d*)', content)
        out["recruiter_tags"] = list(set(li_tags))
        if out["recruiter_tags"]:
            logger.info("Found #LI recruiter tags: %s", out["recruiter_tags"])

        # Validate hiring_manager is a person's name, not a job title
        hm = out["hiring_manager"]
        if hm:
            _ROLE_WORDS = {"engineer", "manager", "recruiter", "developer", "lead", "director",
                           "designer", "analyst", "coordinator", "specialist", "architect",
                           "intern", "associate", "senior", "junior", "staff", "principal",
                           "full", "stack", "frontend", "backend", "front-end", "back-end",
                           "head", "vp", "chief", "officer", "scientist", "researcher",
                           "product", "software", "hardware", "platform", "data", "ml",
                           "ai", "devops", "sre", "security", "mobile", "web", "cloud"}
            hm_words = [w.lower().rstrip(".,") for w in hm.split()]
            # Must have at least 2 words (first + last name)
            too_short = len(hm_words) < 2
            # If most words (>50%) are role words, it's a title not a name
            role_word_count = sum(1 for w in hm_words if w in _ROLE_WORDS)
            is_title = role_word_count > 0 and role_word_count >= len(hm_words) * 0.5
            if too_short or is_title:
                logger.info("Clearing hiring_manager '%s' — looks like a title, not a name (too_short=%s, is_title=%s)", hm, too_short, is_title)
                # Salvage: move to reporting_to if it's not already set and looks like a role phrase
                if is_title and not out["reporting_to"]:
                    out["reporting_to"] = hm
                    logger.info("Moved hiring_manager value to reporting_to: '%s'", hm)
                out["hiring_manager"] = ""

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


def _extract_city(location: str) -> str | None:
    """Extract a specific city name from a location string, or None if remote/hybrid/unspecific.

    Handles formats like:
    - "New York" → "New York"
    - "San Francisco, CA" → "San Francisco"
    - "Austin, TX (Hybrid)" → "Austin"
    - "GameChanger HQ - New York" → "New York"
    - "Remote" / "Hybrid" / "" → None
    """
    _SKIP = {"remote", "anywhere", "hybrid", "flexible", "worldwide", "global", "us", "usa"}
    if not location:
        return None
    # Handle "HQ - City" or "Office - City" pattern — take last segment
    if " - " in location:
        location = location.split(" - ")[-1]
    # Strip parentheticals like "(Hybrid)" or "(Remote)"
    location = re.sub(r'\s*\(.*?\)', '', location).strip()
    # Strip state abbreviation like ", CA" or ", TX"
    location = re.sub(r',\s*[A-Z]{2}$', '', location).strip()
    if not location or location.lower() in _SKIP:
        return None
    # If remaining text contains a skip word, bail
    if any(skip in location.lower().split() for skip in _SKIP):
        return None
    return location


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
        "recruiter_tags": [],
    }


# ── Helpers ───────────────────────────────────────────────────────────────


def _parse_reporting_to(reporting_to: str) -> tuple[str, str]:
    """Parse 'report to a Senior Engineer on the Subscriptions Enablement team' into (role, team).

    Returns (role_title, team_name). Either may be empty string.
    """
    text = reporting_to.strip()
    # Strip common prefixes
    for prefix in [
        "report to a ", "report to an ", "report to the ",
        "reports to a ", "reports to an ", "reports to the ",
        "reporting to a ", "reporting to an ", "reporting to the ",
        "report to ", "reports to ", "reporting to ",
    ]:
        if text.lower().startswith(prefix):
            text = text[len(prefix):]
            break

    # Split on team separator
    role_title = ""
    team_name = ""
    for sep in [" on the ", " on ", " in the ", " in "]:
        idx = text.lower().find(sep)
        if idx >= 0:
            role_title = text[:idx].strip()
            team_name = text[idx + len(sep):].strip()
            for suffix in [" team", " group", " org"]:
                if team_name.lower().endswith(suffix):
                    team_name = team_name[: len(team_name) - len(suffix)].strip()
            break

    if not role_title:
        role_title = text  # No separator found — whole thing is the role

    # Truncate fluff: "Full Stack Engineer who will support your growth" → "Full Stack Engineer"
    if len(role_title.split()) > 5:
        for fluff_sep in [" who ", " that ", " and will ", " and "]:
            idx = role_title.lower().find(fluff_sep)
            if idx >= 0:
                role_title = role_title[:idx].strip()
                break

    return (role_title, team_name)


def _normalize_department(department: str) -> str:
    """Normalize department names to produce valid manager titles."""
    _DEPT_MAP = {
        "engineering": "Engineering",
        "product": "Product Engineering",  # "Product Manager" is an IC role
        "data": "Data Engineering",
        "design": "Design",
        "security": "Security Engineering",
        "devops": "DevOps",
        "platform": "Platform Engineering",
        "infrastructure": "Infrastructure",
        "internship": "Engineering",       # not a real department
        "": "Engineering",
    }
    return _DEPT_MAP.get(department.lower().strip(), department.capitalize())


def _infer_manager_titles(seniority: str, department: str) -> list[str]:
    """Infer what the hiring manager's title likely is based on role seniority."""
    dept = _normalize_department(department)
    seniority = seniority.lower()

    if seniority in ("intern", "junior"):
        return [f"{dept} Manager", "Tech Lead"]
    elif seniority == "mid":
        return [f"Senior {dept} Manager", f"Director of {dept}"]
    elif seniority == "senior":
        return [f"Director of {dept}", f"VP of {dept}"]
    else:
        return [f"{dept} Manager", "Tech Lead"]


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
    careers_job_count: int | None = None,
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
    tech_stack = jc.get("tech_stack", [])

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
        # Search LinkedIn posts (people share jobs as posts, not on profiles)
        queries.append(QueryGroup(
            query=f'site:linkedin.com/posts "{company}" "{title_for_search}" "hiring" OR "we\'re hiring" OR "join"',
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
        role_title, team_name = _parse_reporting_to(reporting_to)
        if role_title:
            queries.append(QueryGroup(
                query=f'site:linkedin.com/in "{company}" "{role_title}"',
                category="reporting_manager",
                priority=1,
            ))
        if team_name:
            last_word = role_title.split()[-1] if role_title else "engineer"
            queries.append(QueryGroup(
                query=f'site:linkedin.com/in "{company}" "{team_name}" {last_word}',
                category="reporting_manager",
                priority=1,
            ))

    # ── TIER 1c: Inferred hiring manager (when no explicit manager or reporting_to) ──
    if not hiring_manager and not reporting_to:
        seniority_val = jc.get("seniority", "") or ""
        dept = jc.get("department", "") or "engineering"
        inferred_titles = _infer_manager_titles(seniority_val, dept)
        if inferred_titles:
            for title in inferred_titles[:2]:
                queries.append(QueryGroup(
                    query=f'site:linkedin.com/in "at {company}" "{title}"',
                    category="likely_manager",
                    priority=2,
                ))

    # ── TIER 1d: #LI- recruiter tag queries (ATS-assigned recruiter) ──
    recruiter_tags = jc.get("recruiter_tags", [])
    if recruiter_tags:
        # Search for the tag itself — sometimes visible on recruiter profiles/activity
        for tag in recruiter_tags[:1]:  # cap at 1 to save query budget
            queries.append(QueryGroup(
                query=f'"#LI-{tag}" site:linkedin.com "{company}"',
                category="job_posting_recruiter",
                priority=1,
            ))
        # Promoted recruiter search — the tag confirms a recruiter is assigned to this job
        queries.append(QueryGroup(
            query=f'site:linkedin.com/in "at {company}" "recruiter" OR "talent acquisition"',
            category="job_posting_recruiter",
            priority=1,
        ))

    # ── TIER 2: Team-specific ─────────────────────────────────────────
    # Prefer keywords and tech stack (which appear on LinkedIn) over internal team names

    # 2a: Job context keywords (e.g., "Software Engineer Intern", "Intern")
    for kw in keywords[:2]:
        kw = kw.strip()
        if kw and kw.lower() != (team or "").lower() and not kw.startswith("http"):
            queries.append(QueryGroup(
                query=f'site:linkedin.com/in "at {company}" "{kw}"',
                category="team_search",
                priority=2,
            ))

    # 2b: Tech stack terms (people list these on LinkedIn)
    if tech_stack:
        tech_terms = " OR ".join(f'"{t}"' for t in tech_stack[:3])
        queries.append(QueryGroup(
            query=f'site:linkedin.com/in "at {company}" {tech_terms}',
            category="team_search",
            priority=2,
        ))

    # 2c: Team name as backup (may work for well-known teams)
    if team:
        queries.append(QueryGroup(
            query=f'site:linkedin.com/in "at {company}" "{team}" engineer OR manager',
            category="team_search",
            priority=2,
        ))

    # 2d: Department-based (if different from team)
    if department and department.lower() != (team or "").lower():
        queries.append(QueryGroup(
            query=f'site:linkedin.com/in "at {company}" "{department}" engineer OR developer',
            category="team_search",
            priority=2,
        ))

    # 2e: Location-filtered (alongside, not replacing, existing queries)
    city = _extract_city(jc.get("location", "") or "")
    if city and keywords:
        queries.append(QueryGroup(
            query=f'site:linkedin.com/in "at {company}" "{keywords[0]}" "{city}"',
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

    # ── TIER 3b: Recently joined employees (high reachability) ────────
    queries.append(QueryGroup(
        query=f'site:linkedin.com/in "at {company}" "joined" OR "started" 2025 OR 2026 engineer OR developer',
        category="team_search",
        priority=3,
    ))

    # ── TIER 4: Broad role match ──────────────────────────────────────
    tier4_kw = first_keyword if first_keyword and not first_keyword.startswith("http") else "engineer"
    queries.append(QueryGroup(
        query=f'site:linkedin.com/in "at {company}" {tier4_kw} engineer OR developer',
        category="general",
        priority=4,
    ))

    # ── TIER 2b: Intern-specific recruiter queries (promoted for intern roles) ──
    seniority = jc.get("seniority", "") or ""
    is_intern = seniority.lower() == "intern"
    is_small_company = careers_job_count is not None and careers_job_count < 10
    if is_intern and not is_small_company:
        queries.append(QueryGroup(
            query=f'site:linkedin.com/in "at {company}" "university recruiter" OR "campus recruiter"',
            category="recruiter",
            priority=2,
        ))
        queries.append(QueryGroup(
            query=f'site:linkedin.com/in "at {company}" "technical recruiter" "intern" OR "internship"',
            category="recruiter",
            priority=2,
        ))
    elif is_intern and is_small_company:
        logger.info(
            "Skipping campus recruiter tier-2 queries — small company (~%d open role lines)", careers_job_count
        )

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

    # ── Budget cap: sort by priority, keep top N (12 for intern, 10 otherwise) ──
    cap = 14 if is_intern else 12
    queries.sort(key=lambda q: q.priority)
    queries = queries[:cap]

    logger.info(
        "Built %d search queries (tiers: %s)",
        len(queries),
        ", ".join(sorted({str(q.priority) for q in queries})),
    )
    return queries


# ── ATS public API for structured job counting ────────────────────────────


async def count_jobs_via_ats(job_url: str) -> int | None:
    """Use ATS public API to count open jobs. Returns None if ATS not detected or API fails."""
    if not job_url:
        return None

    import httpx

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            # Lever: jobs.lever.co/{company}/...
            lever_match = re.match(r'https?://jobs\.lever\.co/([^/]+)', job_url)
            if lever_match:
                company_slug = lever_match.group(1)
                resp = await client.get(f"https://api.lever.co/v0/postings/{company_slug}?mode=json")
                if resp.status_code == 200:
                    return len(resp.json())

            # Greenhouse: boards.greenhouse.io/{board_token}/...
            gh_match = re.match(r'https?://(?:boards|job-boards)\.greenhouse\.io/([^/]+)', job_url)
            if gh_match:
                board_token = gh_match.group(1)
                resp = await client.get(f"https://api.greenhouse.io/v1/boards/{board_token}/jobs")
                if resp.status_code == 200:
                    data = resp.json()
                    return len(data.get("jobs", []))

            # Ashby: jobs.ashbyhq.com/{company}/...
            ashby_match = re.match(r'https?://jobs\.ashbyhq\.com/([^/]+)', job_url)
            if ashby_match:
                company_slug = ashby_match.group(1)
                resp = await client.get(
                    f"https://api.ashbyhq.com/posting-api/job-board/{company_slug}",
                )
                if resp.status_code == 200:
                    data = resp.json()
                    jobs = data.get("jobs", data.get("jobPostings", []))
                    return len(jobs) if isinstance(jobs, list) else None

        except Exception as e:
            logger.warning("ATS job count failed for %s: %s", job_url, e)
    return None
