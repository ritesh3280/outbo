"""Orchestrator Agent.

Chains the full pipeline: User Profile → Job Analysis → People Finder → Email Finder → Company Research.
Handles errors at each step and continues with partial results.
"""

import asyncio
import logging
import time
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
from backend.agents.email_writer import research_company
from backend.agents.job_analyzer import analyze_job_posting
from backend.agents.user_profile_extractor import extract_user_profile, user_profile_from_doc, UserProfile

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
    pipeline_start = time.time()
    logger.info("=" * 60)
    logger.info("  PIPELINE START: %s — %s", request.company, request.role)
    logger.info("  Job ID: %s", job_id)
    logger.info("  Job URL: %s", request.job_url or "(none)")
    logger.info("  LinkedIn: %s", request.linkedin_url or "(none)")
    logger.info("  Resume: %s", request.resume_url or "(none)")
    logger.info("  Company website: %s", request.company_website or "(none)")
    logger.info("=" * 60)

    result = SearchResult(
        job_id=job_id,
        status=SearchStatus.PENDING,
        company=request.company,
        role=request.role,
    )

    async def update(msg: str, msg_type: str = "status") -> None:
        result.activity_log.append(_log_entry(msg, msg_type))
        if on_update:
            try:
                await on_update(result)
            except Exception:
                pass

    browser = BrowserTool()
    scraper = ScraperTool()
    job_context = None
    user_profile = None
    user_info_parts: list[str] = []

    # ── Step 0: Analyze job posting (if provided) ─────────────────────
    if request.job_url:
        result.status = SearchStatus.FINDING_PEOPLE
        await update("Analyzing job posting...")
        step_start = time.time()
        logger.info("[Step 0] Analyzing job posting: %s", request.job_url)
        try:
            job_context = await analyze_job_posting(url=request.job_url, scraper=scraper)
            logger.info("[Step 0] Done in %.1fs", time.time() - step_start)
            logger.info("  Team: %s", job_context.get("team") or "(not found)")
            logger.info("  Department: %s", job_context.get("department") or "(not found)")
            logger.info("  Seniority: %s", job_context.get("seniority") or "(not found)")
            logger.info("  Hiring manager: %s", job_context.get("hiring_manager") or "(not found)")
            logger.info("  Email domain: %s", job_context.get("email_domain") or "(not found)")
            logger.info("  Reporting to: %s", job_context.get("reporting_to") or "(not found)")
            logger.info("  Tech stack: %s", ", ".join(job_context.get("tech_stack", [])) or "(none)")
            logger.info("  Hiring signals: %s", job_context.get("hiring_signals", []) or "(none)")
            logger.info("  Keywords: %s", ", ".join(job_context.get("keywords", [])) or "(none)")
            await update("Job posting analyzed — targeting specific team and role")
        except Exception as e:
            logger.warning("[Step 0] FAILED: %s", e)
            await update(f"Could not analyze job posting: {e}", "error")

    # ── Step 0.5: Load or extract user profile (for warm path) ────────
    logger.info("[Step 0.5] Loading user profile...")
    # Priority 1: Load persistent saved profile from DB
    try:
        from backend.db.mongodb import get_profile as _db_get_profile, get_db as _get_db
        if _get_db() is not None:
            saved_profile = await _db_get_profile("default")
        else:
            # In-memory fallback — import from main module
            from backend.main import profiles as _mem_profiles
            saved_profile = _mem_profiles.get("default")
    except Exception:
        saved_profile = None

    if saved_profile and any(saved_profile.get(k) for k in ("universities", "previous_companies")):
        await update("Using your saved profile for warm connections...")
        user_profile = user_profile_from_doc(saved_profile)
        logger.info("[Step 0.5] Using saved profile: %s", saved_profile.get("name", "(unnamed)"))
        logger.info("  Universities: %s", user_profile.universities or "(none)")
        logger.info("  Previous companies: %s", user_profile.previous_companies or "(none)")
        logger.info("  Skills: %s", user_profile.skills or "(none)")
        signals = []
        if user_profile.universities:
            signals.append(f"{len(user_profile.universities)} universities")
        if user_profile.previous_companies:
            signals.append(f"{len(user_profile.previous_companies)} previous companies")
        if signals:
            await update(f"Loaded warm path signals: {', '.join(signals)}")

        # Build richer user_info from saved profile for email generation
        if saved_profile.get("linkedin_headline"):
            user_info_parts.append(f"LinkedIn headline: {saved_profile['linkedin_headline']}")
        if saved_profile.get("linkedin_summary"):
            user_info_parts.append(f"LinkedIn summary: {saved_profile['linkedin_summary']}")
        if saved_profile.get("linkedin_url"):
            user_info_parts.append(f"LinkedIn: {saved_profile['linkedin_url']}")
        if saved_profile.get("resume_url"):
            user_info_parts.append(f"Resume: {saved_profile['resume_url']}")

    # Priority 2: Fall back to extraction from provided URLs
    elif request.linkedin_url or request.resume_url:
        logger.info("[Step 0.5] No saved profile — extracting from URLs...")
        await update("Analyzing your profile for warm connections...")
        try:
            user_profile = await extract_user_profile(
                linkedin_url=request.linkedin_url,
                resume_url=request.resume_url,
                scraper=scraper,
            )
            logger.info("[Step 0.5] Extracted profile:")
            logger.info("  Universities: %s", user_profile.universities or "(none)")
            logger.info("  Previous companies: %s", user_profile.previous_companies or "(none)")
            logger.info("  Skills: %s", user_profile.skills or "(none)")
            signals = []
            if user_profile.universities:
                signals.append(f"{len(user_profile.universities)} universities")
            if user_profile.previous_companies:
                signals.append(f"{len(user_profile.previous_companies)} previous companies")
            if signals:
                await update(f"Found warm path signals: {', '.join(signals)}")
            else:
                await update("Profile analyzed (no warm path signals extracted)")
        except Exception as e:
            logger.warning("[Step 0.5] FAILED: %s", e)
            await update("Could not analyze your profile (continuing without warm path)", "error")
    else:
        logger.info("[Step 0.5] No saved profile and no URLs — skipping warm path")

    # ── Step 1: Find people ──────────────────────────────────────────
    result.status = SearchStatus.FINDING_PEOPLE
    await update(f"Searching for contacts at {request.company}...")
    step_start = time.time()
    logger.info("[Step 1] Finding people at %s...", request.company)

    try:
        finder = PeopleFinder(browser=browser)
        people = await finder.find_people(
            company=request.company,
            role=request.role,
            target_count=8,
            job_context=job_context,
            user_profile=user_profile,
            job_url=request.job_url,
        )
        result.people = people
        logger.info("[Step 1] Done in %.1fs — %d contacts selected:", time.time() - step_start, len(people))
        for i, p in enumerate(people, 1):
            warm = f" | warm: {', '.join(p.warm_signals)}" if p.warm_signals else ""
            conf = f" [{p.angle_confidence}]" if p.angle_confidence else ""
            logger.info("  %d. %s — %s", i, p.name, p.title or "(no title)")
            logger.info("     score=%.2f (influence=%.2f, reach=%.2f) | %s | src=%s%s",
                        p.priority_score, p.influence_score, p.reachability_score,
                        p.contact_category, p.discovery_source, warm)
            if p.outreach_angle:
                angle_text = p.outreach_angle[:100] + ("..." if len(p.outreach_angle) > 100 else "")
                logger.info("     angle: %s%s", angle_text, conf)
        await update(f"Found {len(people)} contacts", "person_found")
    except Exception as e:
        logger.error("[Step 1] FAILED: %s", e)
        await update(f"Error finding people: {e}", "error")
        if not result.people:
            result.status = SearchStatus.FAILED
            result.error = f"People finder failed: {e}"
            return result

    # ── Step 2: Find emails ──────────────────────────────────────────
    result.status = SearchStatus.FINDING_EMAILS
    await update(f"Discovering emails for {len(result.people)} contacts...")
    step_start = time.time()
    logger.info("[Step 2] Finding emails for %d contacts...", len(result.people))

    try:
        email_finder = EmailFinder(scraper=scraper)
        email_results = await email_finder.find_emails(
            result.people,
            request.company,
            company_website=request.company_website,
            job_context=job_context,
        )
        result.email_results = email_results
        found_count = sum(1 for er in email_results if er.email)
        logger.info("[Step 2] Done in %.1fs — %d/%d emails found:", time.time() - step_start, found_count, len(result.people))
        for er in email_results:
            if er.email:
                logger.info("  %s → %s [%s] (%s)", er.name, er.email, er.confidence, er.source)
            else:
                logger.info("  %s → (no email found)", er.name)
        await update(f"Found emails for {found_count}/{len(result.people)} contacts", "email_found")
    except Exception as e:
        logger.error("[Step 2] FAILED: %s", e)
        await update(f"Error finding emails: {e}", "error")

    # ── Step 3: Research company ─────────────────────────────────────
    result.status = SearchStatus.RESEARCHING
    await update(f"Researching {request.company}...")
    step_start = time.time()
    logger.info("[Step 3] Researching %s...", request.company)

    company_context = None
    try:
        company_context = await research_company(
            company=request.company,
            role=request.role,
            scraper=scraper,
        )
        logger.info("[Step 3] Done in %.1fs", time.time() - step_start)
        if company_context:
            logger.info("  Mission: %s", (company_context.mission or "(none)")[:100])
            logger.info("  Recent news: %s", (company_context.recent_news or "(none)")[:100])
        await update("Company research complete", "status")
    except Exception as e:
        logger.error("[Step 3] FAILED: %s", e)
        await update(f"Error researching company: {e}", "error")

    # Build user_info for on-demand email generation
    if request.resume_url and f"Resume: {request.resume_url}" not in user_info_parts:
        user_info_parts.append(f"Resume: {request.resume_url}")
    if request.linkedin_url and f"LinkedIn: {request.linkedin_url}" not in user_info_parts:
        user_info_parts.append(f"LinkedIn: {request.linkedin_url}")
    user_info = "\n".join(user_info_parts)

    result.company_context = company_context.model_dump() if company_context else None
    result.job_context = job_context
    result.user_info = user_info
    result.user_profile_data = user_profile.model_dump() if user_profile else None

    # ── Done (emails generated on demand when user clicks "Generate email") ─
    result.status = SearchStatus.COMPLETED
    await update("Search complete! Generate an email for any contact when ready.", "complete")

    total_time = time.time() - pipeline_start
    logger.info("=" * 60)
    logger.info("  PIPELINE COMPLETE in %.1fs", total_time)
    logger.info("  %d contacts, %d emails found (drafts on demand)",
                len(result.people), sum(1 for er in result.email_results if er.email))
    logger.info("=" * 60)
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

    # Reconstruct UserProfile from stored data (if available)
    user_profile = None
    if result.user_profile_data:
        try:
            user_profile = UserProfile.model_validate(result.user_profile_data)
        except Exception:
            logger.warning("Could not reconstruct UserProfile from stored data")

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
            user_profile=user_profile,
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
            job_context=result.job_context,
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
