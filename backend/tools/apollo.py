"""Apollo.io API client.

Active endpoints (free plan):
  Contact Create:  POST /api/v1/contacts   — FREE CRM operation.
                   Creates a contact and verifies the supplied email.
                   Used as a free email verifier in the pipeline.

  People Enrich:   POST /api/v1/people/match — COSTS CREDITS.
                   Returns full name, verified email, LinkedIn URL.
                   Kept for future use (paid plan).

Deprecated (requires paid plan):
  People Search:   POST /api/v1/mixed_people/api_search — 403 on free plan.
"""

import logging

import httpx

from backend.config import settings

logger = logging.getLogger(__name__)


# ── API endpoints ─────────────────────────────────────────────────────────

_CONTACT_CREATE_URL = "https://api.apollo.io/api/v1/contacts"
_ENRICH_URL = "https://api.apollo.io/api/v1/people/match"


# ── Internal HTTP helper ──────────────────────────────────────────────────

async def _apollo_post(client: httpx.AsyncClient, url: str, body: dict) -> dict | None:
    """Make an authenticated POST to Apollo."""
    headers = {
        "X-Api-Key": settings.apollo_api_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Cache-Control": "no-cache",
    }
    try:
        resp = await client.post(url, json=body, headers=headers, timeout=15.0)
        if resp.status_code == 401:
            logger.warning("Apollo API: unauthorized (check API key)")
            return None
        if resp.status_code == 403:
            logger.warning("Apollo API: forbidden — %s", resp.text[:200])
            return None
        if resp.status_code == 429:
            logger.warning("Apollo API: rate limited")
            return None
        if resp.status_code == 422:
            logger.warning("Apollo API: invalid params — %s", resp.text[:200])
            return None
        resp.raise_for_status()
        return resp.json()
    except httpx.TimeoutException:
        logger.warning("Apollo API timeout: %s", url)
        return None
    except Exception as e:
        logger.warning("Apollo API error %s: %s", url, e)
        return None


# ── Email verification via Contact Create (FREE) ─────────────────────────

async def verify_email_via_contact(
    first_name: str,
    last_name: str,
    organization_name: str,
    email: str,
) -> dict:
    """Create an Apollo CRM contact to verify an email address.

    FREE operation — no credits consumed.  Apollo verifies the supplied
    email and returns email_status ("verified", "unavailable", etc.).

    Returns dict with: email_status, verified (bool), email.
    """
    if not settings.apollo_api_key or not email:
        return {"email_status": "", "verified": False, "email": ""}

    body = {
        "first_name": first_name,
        "last_name": last_name,
        "organization_name": organization_name,
        "email": email,
    }

    async with httpx.AsyncClient() as client:
        data = await _apollo_post(client, _CONTACT_CREATE_URL, body)

    if not data:
        return {"email_status": "", "verified": False, "email": ""}

    contact = data.get("contact", {})
    if not isinstance(contact, dict):
        return {"email_status": "", "verified": False, "email": ""}

    status = (contact.get("email_status") or "").strip().lower()

    logger.info(
        "Apollo CRM verify: %s %s <%s> → %s",
        first_name, last_name, email, status or "no status",
    )

    return {
        "email_status": status,
        "verified": status == "verified",
        "email": email,
    }


async def verify_email_patterns(
    first_name: str,
    last_name: str,
    organization_name: str,
    domain: str,
    patterns: list[str],
    max_attempts: int = 3,
) -> tuple[str, str]:
    """Try to verify email patterns via Apollo CRM contact creation.

    Iterates through the most likely patterns (up to max_attempts) and
    returns the first verified email.  Each attempt creates a CRM contact
    (FREE — no credits consumed).

    Returns (verified_email, email_status) or ("", "").
    """
    if not settings.apollo_api_key or not patterns:
        return ("", "")

    for i, email in enumerate(patterns[:max_attempts]):
        result = await verify_email_via_contact(
            first_name=first_name,
            last_name=last_name,
            organization_name=organization_name,
            email=email,
        )

        if result["verified"]:
            logger.info(
                "Apollo verified %s (attempt %d/%d for %s@%s)",
                email, i + 1, max_attempts, first_name, domain,
            )
            return (email, result["email_status"])

        logger.debug(
            "Apollo: %s not verified (status=%s), attempt %d/%d",
            email, result["email_status"], i + 1, max_attempts,
        )

    return ("", "")


# ── People Enrichment (COSTS CREDITS) ────────────────────────────────────

async def enrich_person(
    apollo_id: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    domain: str | None = None,
    linkedin_url: str | None = None,
) -> dict | None:
    """Enrich a person via Apollo — COSTS CREDITS.

    Pass at least one identifier (apollo_id preferred, or name+domain, or linkedin_url).
    Returns dict with: first_name, last_name, email, email_status, linkedin_url,
    title, city, state, country, organization.
    Returns None on failure.
    """
    if not settings.apollo_api_key:
        return None

    body: dict = {}
    if apollo_id:
        body["id"] = apollo_id
    if first_name:
        body["first_name"] = first_name
    if last_name:
        body["last_name"] = last_name
    if domain:
        body["domain"] = domain
    if linkedin_url:
        body["linkedin_url"] = linkedin_url

    if not body:
        return None

    async with httpx.AsyncClient() as client:
        data = await _apollo_post(client, _ENRICH_URL, body)

    if not data:
        return None

    person = data.get("person")
    if not person or not isinstance(person, dict):
        return None

    result = {
        "first_name": (person.get("first_name") or "").strip(),
        "last_name": (person.get("last_name") or "").strip(),
        "name": (person.get("name") or "").strip(),
        "email": (person.get("email") or "").strip(),
        "email_status": (person.get("email_status") or "").strip(),
        "linkedin_url": (person.get("linkedin_url") or "").strip(),
        "title": (person.get("title") or "").strip(),
        "city": (person.get("city") or "").strip(),
        "state": (person.get("state") or "").strip(),
        "country": (person.get("country") or "").strip(),
        "organization_name": "",
    }
    org = person.get("organization")
    if org and isinstance(org, dict):
        result["organization_name"] = (org.get("name") or "").strip()

    logger.info(
        "Apollo enrich: %s → email=%s, linkedin=%s",
        result["name"] or apollo_id,
        "found" if result["email"] else "none",
        "found" if result["linkedin_url"] else "none",
    )
    return result
