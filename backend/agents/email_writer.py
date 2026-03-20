"""Email Writer Agent.

Handles:
- Company research via Firecrawl (Step 4.1)
- Single personalized email generation via OpenAI (Step 4.2)
- Batch generation with variety enforcement (Step 4.3)
"""

import asyncio
import json
import logging
import re

from openai import AsyncOpenAI
from pydantic import BaseModel

from backend.config import settings
from backend.models.schemas import EmailDraft, EmailResult, Person
from backend.tools.scraper import ScraperTool

logger = logging.getLogger(__name__)


# ── Step 4.1: Company Research ───────────────────────────────────────────


class CompanyContext(BaseModel):
    """Structured company research context for email personalization."""
    company: str = ""
    mission: str = ""
    recent_news: str = ""
    blog_highlights: str = ""
    culture_notes: str = ""
    relevant_role_info: str = ""


_ERROR_TITLES = {"not found", "404", "page not found", "error", "403 forbidden", "access denied"}


def _is_error_page(result) -> bool:
    """Detect soft 404/error pages that Firecrawl reports as success."""
    if result.title and result.title.strip().lower() in _ERROR_TITLES:
        return True
    if result.content and len(result.content) < 500:
        start = result.content[:200].lower()
        if "page not found" in start or "404" in start:
            return True
    return False


_MIN_NAME_PATTERNS = 5  # Minimum person-name patterns before calling OpenAI


_JOB_ROLE_RE = re.compile(
    r'\b(?:Engineer|Developer|Designer|Analyst|Manager|Recruiter|Scientist|Architect|Intern)\b'
)


def _count_job_listings(page_text: str) -> int:
    """Count unique lines containing role keywords as a proxy for open job count."""
    job_lines = {line.strip() for line in page_text.split('\n') if _JOB_ROLE_RE.search(line)}
    return len(job_lines)


async def scrape_team_pages(
    company: str,
    base_url: str,
    department: str,
    scraper: ScraperTool | None = None,
) -> tuple[list[dict], dict[str, str], int | None]:
    """Scrape company /about, /team, and /careers pages to extract names + titles.

    Returns:
        (candidates, page_cache, careers_job_count)
        candidates: [{"name": "...", "title": "..."}] filtered to department-relevant roles
        page_cache: {url: content} for reuse in research_company() to avoid double-scraping
        careers_job_count: rough count of open jobs (None if careers page not found)
    """
    scraper = scraper or ScraperTool()
    # Normalize base_url
    if not base_url.startswith("http"):
        base_url = f"https://{base_url}"
    base_url = base_url.rstrip("/")

    # Extract bare domain for subdomain variant (e.g. "stripe.com" from "https://stripe.com")
    import urllib.parse as _urlparse
    _parsed = _urlparse.urlparse(base_url)
    _bare_domain = _parsed.netloc or _parsed.path  # fallback: base_url itself

    # Expanded team page patterns — scrape first 5 that succeed
    # Cap at 5 to keep Firecrawl usage reasonable; results are merged
    team_url_candidates = [
        f"{base_url}/about",
        f"{base_url}/team",
        f"{base_url}/our-team",
        f"{base_url}/people",
        f"{base_url}/leadership",
        f"{base_url}/about/team",
        f"{base_url}/company/team",
        f"{base_url}/about-us",
        f"https://team.{_bare_domain}",  # subdomain variant
    ]
    # Deduplicate while preserving order
    seen_t: set[str] = set()
    team_urls: list[str] = []
    for _u in team_url_candidates:
        if _u not in seen_t:
            seen_t.add(_u)
            team_urls.append(_u)

    careers_urls = [f"{base_url}/careers", f"{base_url}/jobs"]
    logger.info("[Step 0.7] Scraping team pages for %s (%d candidates)", company, len(team_urls))

    all_results = await scraper.scrape_multiple(team_urls + careers_urls)
    page_cache: dict[str, str] = {}
    combined_text = ""
    careers_job_count: int | None = None

    for r in all_results:
        if not r.success or not r.content:
            continue
        if _is_error_page(r):
            logger.info("[Step 0.7] Skipping error page: %s", r.url)
            continue

        # Careers/jobs pages — count job listings, add to cache, don't extract names
        if r.url in careers_urls:
            careers_job_count = _count_job_listings(r.content)
            page_cache[r.url] = r.content  # reuse in Step 3 to avoid double-scraping
            logger.info(
                "[Step 0.7] Careers page %s — ~%d job listing lines", r.url, careers_job_count
            )
            continue

        # Team/about pages — add to cache and check for person-name patterns
        page_cache[r.url] = r.content
        name_patterns = re.findall(r'\b[A-Z][a-z]+ [A-Z][a-z]+\b', r.content)
        if len(name_patterns) < _MIN_NAME_PATTERNS:
            logger.info("[Step 0.7] Skipping %s — only %d name patterns found", r.url, len(name_patterns))
            continue
        combined_text += f"\n\n--- {r.url} ---\n{r.content[:4000]}"

    if not combined_text.strip() or not settings.openai_api_key:
        return [], page_cache, careers_job_count

    dept_hint = department or "engineering"
    prompt = (
        f"Extract people's names and titles from this company website content.\n"
        f"Return a JSON array: [{{\"name\": \"...\", \"title\": \"...\"}}]\n"
        f"Only include people whose titles are relevant to {dept_hint} (engineers, managers, tech leads, recruiters).\n"
        f"Exclude founders, CEOs, CFOs, CMOs, and other pure executive/C-suite roles.\n"
        f"If no relevant people are listed, return [].\n\n"
        f"Content:\n{combined_text[:6000]}"
    )

    try:
        client = AsyncOpenAI(api_key=settings.openai_api_key)
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content or "{}"
        data = json.loads(raw)
        # Accept both {"people": [...]} and [...] top-level arrays
        if isinstance(data, list):
            candidates = data
        else:
            candidates = data.get("people", data.get("candidates", data.get("results", [])))
        if not isinstance(candidates, list):
            candidates = []
        # Validate each entry has name + title
        valid = [
            c for c in candidates
            if isinstance(c, dict) and c.get("name") and c.get("title")
        ]
        logger.info("[Step 0.7] Extracted %d team page candidates for %s", len(valid), company)
        return valid, page_cache, careers_job_count
    except Exception as e:
        logger.warning("[Step 0.7] Team page extraction failed: %s", e)
        return [], page_cache, careers_job_count


async def research_company(
    company: str,
    role: str,
    scraper: ScraperTool | None = None,
    page_cache: dict[str, str] | None = None,
) -> CompanyContext:
    """Gather company context for email personalization.

    Scrapes the company's about page, blog, and careers page via Firecrawl,
    then summarizes the findings via OpenAI.

    Args:
        company: Company name.
        role: The role being applied for.
        scraper: Optional ScraperTool instance.

    Returns:
        CompanyContext with structured research data.
    """
    scraper = scraper or ScraperTool()
    domain = _guess_domain(company)
    page_cache = page_cache or {}

    urls = [
        f"https://{domain}/about",
        f"https://{domain}/blog",
        f"https://{domain}/careers",
    ]

    # Separate into already-cached and URLs we need to fetch
    urls_to_fetch = [u for u in urls if u not in page_cache]
    logger.info("Researching %s — scraping %d URLs (%d cached)...", company, len(urls_to_fetch), len(urls) - len(urls_to_fetch))

    if urls_to_fetch:
        results = await scraper.scrape_multiple(urls_to_fetch)
    else:
        results = []

    # Collect whatever we got (skip error/404 pages)
    scraped_text = ""
    # First add cached content
    for url in urls:
        if url in page_cache and page_cache[url]:
            scraped_text += f"\n\n--- {url} ---\n{page_cache[url][:3000]}"
    # Then add newly fetched content
    for r in results:
        if r.success and r.content and not _is_error_page(r):
            # Truncate each page to keep total context manageable
            scraped_text += f"\n\n--- {r.title or r.url} ---\n{r.content[:3000]}"
        elif r.success and _is_error_page(r):
            logger.info("Skipping error page: %s (title: %s)", r.url, r.title)

    if not scraped_text.strip():
        logger.warning("No content scraped for %s", company)
        return CompanyContext(company=company)

    # Summarize via OpenAI
    if not settings.openai_api_key:
        return CompanyContext(
            company=company,
            mission=scraped_text[:500],
        )

    client = AsyncOpenAI(api_key=settings.openai_api_key)

    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You summarize company research for cold outreach emails. "
                        "Be concise. Focus on things useful for personalizing an email "
                        "from a student applying for an internship/job."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Company: {company}\n"
                        f"Role applying for: {role}\n\n"
                        f"Scraped content:\n{scraped_text[:6000]}\n\n"
                        f"Summarize into JSON:\n"
                        f'{{"mission": "1-2 sentences about what the company does",'
                        f'"recent_news": "any recent announcements, launches, or news",'
                        f'"blog_highlights": "interesting recent blog posts or topics",'
                        f'"culture_notes": "team culture, values, or interesting facts",'
                        f'"relevant_role_info": "anything relevant to {role} specifically"}}'
                    ),
                },
            ],
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        content = response.choices[0].message.content or ""
        data = json.loads(content)

        ctx = CompanyContext(
            company=company,
            mission=data.get("mission", ""),
            recent_news=data.get("recent_news", ""),
            blog_highlights=data.get("blog_highlights", ""),
            culture_notes=data.get("culture_notes", ""),
            relevant_role_info=data.get("relevant_role_info", ""),
        )
        logger.info("Company research complete for %s", company)
        return ctx

    except Exception as e:
        logger.error("Company research summarization failed: %s", e)
        return CompanyContext(company=company, mission=scraped_text[:500])


def _guess_domain(company: str) -> str:
    """Quick domain guess for URL construction."""
    from backend.agents.email_finder import get_company_domain
    return get_company_domain(company)


# ── Step 4.2: Single Email Generation ────────────────────────────────────


SINGLE_EMAIL_SYSTEM_PROMPT = """You write cold outreach emails from a student applying for jobs/internships.

Rules:
- 5-7 sentences. No more, no less.
- Sign off with the sender's actual name from "Sender name:" in the prompt. Never invent or assume a name.
- First sentence: ONE specific thing about the RECIPIENT — their role, a project their team ships, or something from their LinkedIn. Not "I came across your profile."
- Middle: 1-2 sentences about the sender — school, relevant skills, and ONE concrete thing they've built or done. Be specific, not generic.
- Closing: a clear, low-friction ask. Either a 15-min chat, advice on the role, or (for engineers) subtly mention a referral if they think you'd be a fit.
- No hollow phrases: "I was impressed by", "I hope this finds you well", "I'd love to learn more about your journey", "I came across your profile".
- Write like a real person, not a cover letter. Conversational but professional.

Tone by recipient type:
- Recruiter: name the exact role title, keep it direct.
- Engineer: lead with shared tech or something specific about what they build. Casual referral mention is fine.
- Manager: show you understand what their team is responsible for.

Return JSON only:
{"subject": "...", "body": "...", "personalization_notes": "what specific detail you used to personalize"}"""


async def generate_single_email(
    person: Person,
    email_result: EmailResult,
    company_context: CompanyContext,
    role: str,
    user_info: str = "",
    previous_openings: list[str] | None = None,
    job_context: dict | None = None,
) -> EmailDraft:
    """Generate a personalized cold email for one contact.

    Args:
        person: The contact to email.
        email_result: Their discovered email.
        company_context: Research about the company.
        role: The role being applied for.
        user_info: Optional info about the sender (resume highlights, etc.).
        previous_openings: Opening lines used in previous emails (for variety).

    Returns:
        EmailDraft with subject, body, and personalization notes.
    """
    if not settings.openai_api_key:
        return EmailDraft(
            name=person.name,
            email=email_result.email,
            subject=f"Interested in {role} at {person.company}",
            body=f"Hi {person.name.split()[0]},\n\nI'm reaching out about the {role} position at {person.company}. I'd love to learn more about the team and the role.\n\nWould you have 15 minutes for a quick chat?\n\nBest regards",
            tone="warm-professional",
            personalization_notes="Stub mode — no OpenAI key",
        )

    variety_instruction = ""
    if previous_openings:
        variety_instruction = (
            f"\n\nIMPORTANT: Do NOT start the email with any of these openings "
            f"(already used for other contacts at the same company):\n"
            + "\n".join(f"- \"{o}\"" for o in previous_openings)
            + "\nUse a completely different opening angle."
        )

    # Determine recipient type for tone
    title_lower = person.title.lower()
    if any(kw in title_lower for kw in ["recruit", "talent", "hiring"]):
        recipient_type = "Recruiter"
    elif any(kw in title_lower for kw in ["manager", "lead", "head", "director", "vp"]):
        recipient_type = "Manager"
    else:
        recipient_type = "Engineer"

    job_block = ""
    if job_context and any(job_context.get(k) for k in ("team", "tech_stack", "key_requirements")):
        team = job_context.get("team", "")
        tech = job_context.get("tech_stack", [])
        reqs = job_context.get("key_requirements", [])
        job_block = (
            f"\nThe sender is applying for this specific role (use to make the email specific):\n"
            f"- Team: {team}\n"
            f"- Key tech: {tech}\n"
            f"- What the role involves: {reqs}\n"
            "For engineers, mention shared tech stack interest; you may mention the referral possibility as a reason to connect — but keep it casual, not transactional. "
            "For recruiters, reference the exact posting. "
            "For managers, show you understand what their team builds.\n\n"
        )

    user_prompt = (
        f"Write a cold outreach email.\n\n"
        f"Sender is a student applying for {role} at {company_context.company}.\n"
        f"Sender info (use 'Sender name' as the sign-off name — do not invent a different name):\n{user_info or 'Not provided'}\n\n"
        f"Recipient: {person.name}, {person.title}\n"
        f"Recipient type: {recipient_type}\n"
        f"Their LinkedIn snippet: {person.profile_summary[:300] if person.profile_summary else 'Not available'}\n\n"
        f"{job_block}"
        f"Company context:\n"
        f"- Mission: {company_context.mission}\n"
        f"- Recent news: {company_context.recent_news}\n"
        f"- Blog highlights: {company_context.blog_highlights}\n"
        f"- Culture: {company_context.culture_notes}\n"
        f"- Role info: {company_context.relevant_role_info}\n"
        f"{variety_instruction}"
    )

    client = AsyncOpenAI(api_key=settings.openai_api_key)

    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SINGLE_EMAIL_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.8,
            response_format={"type": "json_object"},
        )

        content = response.choices[0].message.content or ""
        data = json.loads(content)

        return EmailDraft(
            name=person.name,
            email=email_result.email,
            subject=data.get("subject", f"Re: {role} at {person.company}"),
            body=data.get("body", ""),
            tone="warm-professional",
            personalization_notes=data.get("personalization_notes", ""),
        )

    except Exception as e:
        logger.error("Email generation failed for %s: %s", person.name, e)
        first_name = person.name.split()[0] if person.name else "there"
        return EmailDraft(
            name=person.name,
            email=email_result.email,
            subject=f"Interested in {role} at {person.company}",
            body=f"Hi {first_name},\n\nI'm reaching out about the {role} position at {person.company}. I'd love to learn more.\n\nWould you have 15 minutes for a quick chat?\n\nBest regards",
            tone="warm-professional",
            personalization_notes=f"Fallback template (generation failed: {e})",
        )


# ── Step 4.3: Batch Email Generation ────────────────────────────────────


async def generate_batch_emails(
    people: list[Person],
    email_results: list[EmailResult],
    company_context: CompanyContext,
    role: str,
    user_info: str = "",
    job_context: dict | None = None,
) -> list[EmailDraft]:
    """Generate personalized emails for all contacts with variety enforcement.

    Generates emails sequentially to track previous openings and enforce
    variety — no two emails to the same company should start the same way.

    Args:
        people: List of contacts.
        email_results: Corresponding email results.
        company_context: Company research context.
        role: Role being applied for.
        user_info: Optional sender info.

    Returns:
        List of EmailDraft objects.
    """
    if len(people) != len(email_results):
        logger.error("Mismatch: %d people but %d email results", len(people), len(email_results))
        email_results = email_results + [
            EmailResult(name=p.name, email="") for p in people[len(email_results):]
        ]

    drafts: list[EmailDraft] = []
    previous_openings: list[str] = []

    for person, email_result in zip(people, email_results):
        if not email_result.email:
            logger.info("Skipping %s — no email found", person.name)
            continue

        logger.info("Generating email for %s (%s)...", person.name, email_result.email)

        draft = await generate_single_email(
            person=person,
            email_result=email_result,
            company_context=company_context,
            role=role,
            user_info=user_info,
            previous_openings=previous_openings if previous_openings else None,
            job_context=job_context,
        )

        # Track the opening line for variety enforcement
        first_line = draft.body.split("\n")[0] if draft.body else ""
        if first_line:
            previous_openings.append(first_line)

        drafts.append(draft)

    logger.info("Generated %d email drafts", len(drafts))
    return drafts
