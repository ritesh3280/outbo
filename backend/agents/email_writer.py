"""Email Writer Agent.

Handles:
- Company research via Firecrawl (Step 4.1)
- Single personalized email generation via OpenAI (Step 4.2)
- Batch generation with variety enforcement (Step 4.3)
"""

import asyncio
import json
import logging

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


async def research_company(
    company: str,
    role: str,
    scraper: ScraperTool | None = None,
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

    urls = [
        f"https://{domain}/about",
        f"https://{domain}/blog",
        f"https://{domain}/careers",
    ]

    logger.info("Researching %s — scraping %d URLs...", company, len(urls))
    results = await scraper.scrape_multiple(urls)

    # Collect whatever we got
    scraped_text = ""
    for r in results:
        if r.success and r.content:
            # Truncate each page to keep total context manageable
            scraped_text += f"\n\n--- {r.title or r.url} ---\n{r.content[:3000]}"

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
- 4-6 sentences max. Short and genuine.
- Open with something specific to THE RECIPIENT (not the company generically).
- Briefly mention 1-2 relevant things about the sender (if provided).
- End with a clear, low-friction ask (15-min chat, referral, or advice).
- Sound like a real human, not a template.
- Warm but professional tone.

Tone adjustments:
- Recruiter → be more direct, mention the specific role you're applying for.
- Engineer → lead with technical interest, mention shared interests or their work.
- Manager → show you understand what their team does.

Return JSON:
{"subject": "...", "body": "...", "personalization_notes": "what you referenced to personalize this email"}"""


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
            "For engineers, mention shared tech stack interest. For recruiters, reference the exact posting. "
            "For managers, show you understand what their team builds.\n\n"
        )

    user_prompt = (
        f"Write a cold outreach email.\n\n"
        f"Sender: A student applying for {role} at {company_context.company}\n"
        f"Sender's background: {user_info or 'Not provided'}\n\n"
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
