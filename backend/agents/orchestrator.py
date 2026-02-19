"""Orchestrator Agent.

Chains the full pipeline: People Finder → Email Finder → Company Research → Email Writer.
Handles errors at each step and continues with partial results.
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from backend.models.schemas import (
    ActivityLogEntry,
    EmailConfidence,
    EmailResult,
    SearchRequest,
    SearchResult,
    SearchStatus,
)
from backend.tools.browser import BrowserTool
from backend.tools.scraper import ScraperTool
from backend.agents.people_finder import PeopleFinder
from backend.agents.email_finder import EmailFinder
from backend.tools.scraper import ScraperTool
from backend.agents.email_writer import research_company
from backend.agents.job_analyzer import analyze_job_posting

logger = logging.getLogger(__name__)


def _log_entry(message: str, entry_type: str = "status") -> ActivityLogEntry:
    return ActivityLogEntry(
        timestamp=datetime.now(timezone.utc).isoformat(),
        message=message,
        type=entry_type,
    )


async def run_search(
    request: SearchRequest,
    job_id: str,
    on_update: Callable[..., Any] | None = None,
) -> SearchResult:
    """Run the full outreach pipeline.

    Args:
        request: The search request (company + role + optional user info).
        job_id: Unique job ID for tracking.
        on_update: Optional callback called with the SearchResult after each step.

    Returns:
        Complete SearchResult with people, emails, and draft emails.
    """
    result = SearchResult(
        job_id=job_id,
        status=SearchStatus.PENDING,
        company=request.company,
        role=request.role,
    )

    async def update(msg: str, msg_type: str = "status") -> None:
        result.activity_log.append(_log_entry(msg, msg_type))
        logger.info("[%s] %s", job_id, msg)
        if on_update:
            try:
                await on_update(result)
            except Exception:
                pass

    browser = BrowserTool()
    scraper = ScraperTool()
    job_context = None

    if request.job_url:
        result.status = SearchStatus.FINDING_PEOPLE
        await update("Analyzing job posting...")
        try:
            job_context = await analyze_job_posting(url=request.job_url, scraper=scraper)
            await update("Job posting analyzed — targeting specific team and role")
        except Exception as e:
            logger.warning("Job posting analysis failed: %s", e)
            await update(f"Could not analyze job posting: {e}", "error")

    # ── Step 1: Find people ──────────────────────────────────────────
    result.status = SearchStatus.FINDING_PEOPLE
    await update(f"Searching for contacts at {request.company}...")

    try:
        finder = PeopleFinder(browser=browser)
        people = await finder.find_people(
            company=request.company,
            role=request.role,
            target_count=8,
            job_context=job_context,
        )
        result.people = people
        await update(f"Found {len(people)} contacts", "person_found")
    except Exception as e:
        logger.error("People finder failed: %s", e)
        await update(f"Error finding people: {e}", "error")
        if not result.people:
            result.status = SearchStatus.FAILED
            result.error = f"People finder failed: {e}"
            return result

    # ── Step 2: Find emails ──────────────────────────────────────────
    result.status = SearchStatus.FINDING_EMAILS
    await update(f"Discovering emails for {len(result.people)} contacts...")

    try:
        email_finder = EmailFinder(scraper=scraper)
        email_results = await email_finder.find_emails(
            result.people,
            request.company,
            company_website=request.company_website,
        )
        result.email_results = email_results
        found_count = sum(1 for er in email_results if er.email)
        await update(f"Found emails for {found_count}/{len(result.people)} contacts", "email_found")
    except Exception as e:
        logger.error("Email finder failed: %s", e)
        await update(f"Error finding emails: {e}", "error")

    # ── Step 3: Research company (concurrent with nothing — just run it) ─
    result.status = SearchStatus.RESEARCHING
    await update(f"Researching {request.company}...")

    company_context = None
    try:
        company_context = await research_company(
            company=request.company,
            role=request.role,
            scraper=scraper,
        )
        await update("Company research complete", "status")
    except Exception as e:
        logger.error("Company research failed: %s", e)
        await update(f"Error researching company: {e}", "error")

    # Build user_info for on-demand email generation
    user_info = ""
    if request.resume_url:
        user_info += f"Resume: {request.resume_url}\n"
    if request.linkedin_url:
        user_info += f"LinkedIn: {request.linkedin_url}\n"

    result.company_context = company_context.model_dump() if company_context else None
    result.job_context = job_context
    result.user_info = user_info

    # ── Done (emails generated on demand when user clicks "Generate email") ─
    result.status = SearchStatus.COMPLETED
    await update("Search complete! Generate an email for any contact when ready.", "complete")

    logger.info(
        "[%s] Pipeline complete: %d people, %d emails (drafts on demand)",
        job_id, len(result.people), len(result.email_results),
    )
    return result


async def run_more_leads(
    result: SearchResult,
    on_update: Callable[..., Any] | None = None,
) -> None:
    """Find more people for an existing campaign and merge them (no duplicates).

    Modifies result in place: appends new people and email_results, updates status and activity_log.
    Skips anyone we already have (by normalized LinkedIn URL).
    """
    async def update(msg: str, msg_type: str = "status") -> None:
        result.activity_log.append(_log_entry(msg, msg_type))
        logger.info("[%s] %s", result.job_id, msg)
        if on_update:
            try:
                await on_update(result)
            except Exception:
                pass

    exclude_urls = {
        PeopleFinder._normalize_linkedin_url(p.linkedin_url)
        for p in result.people
        if p.linkedin_url
    }

    result.status = SearchStatus.FINDING_PEOPLE
    await update("Finding more contacts (excluding existing)...", "status")

    try:
        finder = PeopleFinder(browser=BrowserTool())
        new_people = await finder.find_people(
            company=result.company,
            role=result.role,
            target_count=8,
            job_context=result.job_context,
            exclude_linkedin_urls=exclude_urls,
        )
    except Exception as e:
        logger.error("More leads people finder failed: %s", e)
        await update(f"Error finding more people: {e}", "error")
        result.status = SearchStatus.COMPLETED
        return

    if not new_people:
        await update("No new contacts found for this campaign.", "status")
        result.status = SearchStatus.COMPLETED
        return

    result.status = SearchStatus.FINDING_EMAILS
    await update(f"Discovering emails for {len(new_people)} new contacts...")

    try:
        email_finder = EmailFinder(scraper=ScraperTool())
        new_email_results = await email_finder.find_emails(
            new_people,
            result.company,
            company_website=None,
        )
        result.people.extend(new_people)
        result.email_results.extend(new_email_results)
        found_count = sum(1 for er in new_email_results if er.email)
        await update(f"Added {len(new_people)} contacts ({found_count} with emails)", "email_found")
    except Exception as e:
        logger.error("More leads email finder failed: %s", e)
        result.people.extend(new_people)
        result.email_results.extend(
            EmailResult(name=p.name, email="", confidence=EmailConfidence.LOW) for p in new_people
        )
        await update(f"Added {len(new_people)} contacts (email discovery had errors)", "email_found")

    result.status = SearchStatus.COMPLETED
    await update("More leads added. Generate an email for any new contact when ready.", "complete")
    logger.info("[%s] More leads: added %d people, total %d", result.job_id, len(new_people), len(result.people))
