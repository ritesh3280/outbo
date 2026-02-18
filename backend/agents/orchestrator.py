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
    Person,
    SearchRequest,
    SearchResult,
    SearchStatus,
)
from backend.tools.browser import BrowserTool
from backend.tools.scraper import ScraperTool
from backend.agents.people_finder import PeopleFinder
from backend.agents.email_finder import EmailFinder
from backend.agents.email_writer import research_company, generate_batch_emails

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

    # ── Step 1: Find people ──────────────────────────────────────────
    result.status = SearchStatus.FINDING_PEOPLE
    await update(f"Searching for contacts at {request.company}...")

    try:
        finder = PeopleFinder(browser=browser)
        people = await finder.find_people(
            company=request.company,
            role=request.role,
            target_count=8,
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

    # ── Step 4: Generate emails ──────────────────────────────────────
    if result.email_results and company_context:
        result.status = SearchStatus.GENERATING_EMAILS
        await update("Generating personalized emails...")

        try:
            user_info = ""
            if request.resume_url:
                user_info += f"Resume: {request.resume_url}\n"
            if request.linkedin_url:
                user_info += f"LinkedIn: {request.linkedin_url}\n"

            drafts = await generate_batch_emails(
                people=result.people,
                email_results=result.email_results,
                company_context=company_context,
                role=request.role,
                user_info=user_info,
            )
            result.email_drafts = drafts
            await update(f"Generated {len(drafts)} personalized emails", "email_drafted")
        except Exception as e:
            logger.error("Email generation failed: %s", e)
            await update(f"Error generating emails: {e}", "error")

    # ── Done ─────────────────────────────────────────────────────────
    result.status = SearchStatus.COMPLETED
    await update("Search complete!", "complete")

    logger.info(
        "[%s] Pipeline complete: %d people, %d emails, %d drafts",
        job_id, len(result.people), len(result.email_results), len(result.email_drafts),
    )
    return result
