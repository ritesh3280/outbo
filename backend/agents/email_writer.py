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


async def _enrich_recipient(person: Person) -> str:
    """Serper lookup to get recipient's education/background from LinkedIn snippet.

    If we already have a decent profile_summary, skip. Otherwise do a quick search
    so we have real facts (school, previous companies, etc.) to personalize with.
    """
    if person.profile_summary and len(person.profile_summary) > 150:
        return person.profile_summary

    try:
        from backend.tools.serper import search as serper_search
        query = f'"{person.name}" "{person.company}" site:linkedin.com'
        results = await serper_search(query, num=3)
        snippets = []
        for r in results:
            if r.snippet:
                snippets.append(r.snippet)
        if snippets:
            enriched = " | ".join(snippets[:2])
            logger.info("Enriched recipient %s: %s", person.name, enriched[:120])
            return enriched
    except Exception as e:
        logger.debug("Recipient enrichment failed for %s: %s", person.name, e)

    return person.profile_summary or ""


SINGLE_EMAIL_SYSTEM_PROMPT = """You write cold outreach emails from a college student to people at companies they applied to.

FORMAT (follow exactly):

Hi [First Name],

[LINE 1: who you are (first name, school, year) + you applied for the role. Example: "I'm Ritesh, a CS junior at UMD — I applied for the SWE Intern role at Brex."]
[LINE 2 (OPTIONAL): A fact about the recipient from RECIPIENT DATA only. Their school, previous company, or career path. Example: "Saw you came from Georgia Tech before joining Brex." If RECIPIENT DATA is just a name and title with no real background info, SKIP THIS LINE ENTIRELY. Do NOT guess.]
[LINE 3-4: ONE project or experience from the sender's resume/background below. Use the EXACT name as it appears. Describe it in 1-2 short sentences — what it does, key tech. Then connect it to the company. Example: "At HackPrinceton, I built Orbital Finance — a voice AI platform for real-time crypto trading using Solana and RAG. The real-time data pipeline work feels close to what Brex handles with financial infrastructure."]
[LINE 5: Clear ask. Example: "Would love to chat for 15 min about the team — and if you think I'd be a fit."]

Thanks,
[EXACT sender name from SENDER_NAME field]

ABSOLUTE RULES:
- ONLY use projects/experiences that appear in SENDER BACKGROUND (resume text). If the project name is not in the text, you are hallucinating. Stop.
- ONLY mention facts about the recipient that appear in RECIPIENT DATA. If you cannot point to where you read it, you are hallucinating. Stop.
- Sign off with the EXACT full name from SENDER_NAME. Not initials, not shortened, not a different name.
- Do NOT include any URLs in the body.
- 4-6 sentences max. No fluff.
- BANNED: "I was impressed", "I hope this finds you well", "I came across your profile", "innovative solutions", "cutting-edge", "scalable solutions", "impactful"

Return JSON only:
{"subject": "...", "body": "...", "personalization_notes": "recipient fact used (or 'none'), project picked and why"}"""


def _fix_signoff(body: str, name: str, linkedin: str, resume: str, portfolio: str) -> str:
    """Post-process: force correct name in sign-off and always append links.

    Finds the sign-off pattern (Thanks/Best/Cheers + name) and replaces whatever
    name the LLM put with the correct one. Then appends links.
    """
    if not body.strip():
        return body

    lines = body.rstrip().split("\n")

    # Find "Thanks," / "Best," / "Cheers," line and replace the name line after it
    if name:
        for i, line in enumerate(lines):
            stripped = line.strip().rstrip(",").lower()
            if stripped in ("thanks", "best", "best regards", "cheers", "thank you"):
                # The next non-empty line should be the name — replace it
                for j in range(i + 1, min(i + 3, len(lines))):
                    if lines[j].strip():
                        lines[j] = name
                        break
                else:
                    # No name line found after sign-off — add it
                    lines.insert(i + 1, name)
                break

    result = "\n".join(lines).rstrip()

    # Append links
    link_lines = []
    if linkedin:
        link_lines.append(linkedin)
    if resume:
        link_lines.append(resume)
    if portfolio:
        link_lines.append(portfolio)
    if link_lines:
        result += "\n\n" + "\n".join(link_lines)

    return result


async def generate_single_email(
    person: Person,
    email_result: EmailResult,
    company_context: CompanyContext,
    role: str,
    user_info: str = "",
    previous_openings: list[str] | None = None,
    job_context: dict | None = None,
) -> EmailDraft:
    """Generate a personalized cold email for one contact."""
    if not settings.openai_api_key:
        return EmailDraft(
            name=person.name,
            email=email_result.email,
            subject=f"Interested in {role} at {person.company}",
            body=f"Hi {person.name.split()[0]},\n\nI'm reaching out about the {role} position at {person.company}.\n\nBest regards",
            tone="warm-professional",
            personalization_notes="Stub — no OpenAI key",
        )

    # ── Enrich recipient background ──────────────────────────────────
    enriched_summary = await _enrich_recipient(person)

    # ── Parse sender details from user_info ──────────────────────────
    sender_name = ""
    sender_linkedin = ""
    sender_resume = ""
    sender_portfolio = ""
    for line in (user_info or "").split("\n"):
        stripped = line.strip()
        if stripped.startswith("Sender name:"):
            sender_name = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("LinkedIn URL:") or stripped.startswith("LinkedIn:"):
            val = stripped.split(":", 1)[1].strip()
            if val.startswith("http"):
                sender_linkedin = val
        elif stripped.startswith("Resume URL:") or stripped.startswith("Resume:"):
            val = stripped.split(":", 1)[1].strip()
            if val.startswith("http"):
                sender_resume = val
        elif stripped.startswith("Portfolio URL:") or stripped.startswith("Portfolio:"):
            val = stripped.split(":", 1)[1].strip()
            if val.startswith("http"):
                sender_portfolio = val

    # ── Recipient type ───────────────────────────────────────────────
    title_lower = person.title.lower()
    if any(kw in title_lower for kw in ["recruit", "talent", "hiring"]):
        recipient_type = "Recruiter"
    elif any(kw in title_lower for kw in ["manager", "lead", "head", "director", "vp"]):
        recipient_type = "Manager"
    else:
        recipient_type = "Engineer"

    # ── Variety instruction ──────────────────────────────────────────
    variety_instruction = ""
    if previous_openings:
        variety_instruction = (
            f"\n\nDo NOT start with any of these (already used):\n"
            + "\n".join(f"- \"{o}\"" for o in previous_openings)
        )

    # ── Job context ──────────────────────────────────────────────────
    job_block = ""
    if job_context and any(job_context.get(k) for k in ("team", "tech_stack", "key_requirements")):
        job_block = (
            f"Role details:\n"
            f"- Team: {job_context.get('team', '')}\n"
            f"- Tech stack: {job_context.get('tech_stack', [])}\n"
            f"- Requirements: {job_context.get('key_requirements', [])}\n"
        )

    # ── Build prompt ─────────────────────────────────────────────────
    user_prompt = (
        f"SENDER_NAME: {sender_name}\n\n"
        f"RECIPIENT DATA:\n"
        f"  Name: {person.name}\n"
        f"  Title: {person.title} at {person.company}\n"
        f"  LinkedIn/background: {enriched_summary or '(no data — do NOT invent their background)'}\n"
        f"  Outreach angle: {person.outreach_angle or '(none)'}\n"
        f"  Warm signals: {', '.join(person.warm_signals) if person.warm_signals else '(none)'}\n"
        f"  Type: {recipient_type}\n\n"
        f"SENDER BACKGROUND:\n{user_info or '(not provided)'}\n\n"
        f"ROLE: {role} at {company_context.company} (sender has already applied)\n"
        f"{job_block}\n"
        f"COMPANY: {company_context.mission}\n"
        f"News: {company_context.recent_news}\n"
        f"Culture: {company_context.culture_notes}\n"
        f"{variety_instruction}"
    )

    logger.info("Generating email for %s — enriched=%d chars, type=%s",
                person.name, len(enriched_summary), recipient_type)

    client = AsyncOpenAI(api_key=settings.openai_api_key)

    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SINGLE_EMAIL_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.7,
            response_format={"type": "json_object"},
        )

        content = response.choices[0].message.content or ""
        data = json.loads(content)
        body = data.get("body", "")

        # ── Post-process: fix name and append links ──────────────────
        body = _fix_signoff(body, sender_name, sender_linkedin, sender_resume, sender_portfolio)

        return EmailDraft(
            name=person.name,
            email=email_result.email,
            subject=data.get("subject", f"{role} at {person.company}"),
            body=body,
            tone="warm-professional",
            personalization_notes=data.get("personalization_notes", ""),
        )

    except Exception as e:
        logger.error("Email generation failed for %s: %s", person.name, e)
        first_name = person.name.split()[0] if person.name else "there"
        fallback_body = (
            f"Hi {first_name},\n\n"
            f"I'm {sender_name or 'a student'} — I just applied for the {role} "
            f"position at {person.company} and wanted to reach out.\n\n"
            f"Would you have 15 minutes for a quick chat about the team?\n\n"
            f"Thanks,\n{sender_name or 'Best regards'}"
        )
        fallback_body = _fix_signoff(fallback_body, sender_name, sender_linkedin, sender_resume, sender_portfolio)
        return EmailDraft(
            name=person.name,
            email=email_result.email,
            subject=f"{role} at {person.company}",
            body=fallback_body,
            tone="warm-professional",
            personalization_notes=f"Fallback (generation failed: {e})",
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
