"""GitHub Organization member fetcher.

Fetches public members of a GitHub organization and returns lightweight
contact dicts for merging into the people-finder pipeline.

No Firecrawl needed — uses the public GitHub REST API.
Rate limits:
  - Unauthenticated: 60 req/hr  → caps member profile fetches at 15, 1 s delay
  - Authenticated (GITHUB_TOKEN): 5000 req/hr → concurrent, up to 30 profiles
"""

import asyncio
import logging
import re

import httpx

from backend.config import settings

logger = logging.getLogger(__name__)


def _infer_org_slugs(company: str) -> list[str]:
    """Generate likely GitHub org slug candidates from a company name.

    Examples:
      "Stripe"        → ["stripe"]
      "Scale AI"      → ["scale-ai", "scaleai", "scale-ai", "scale ai"]
      "Y Combinator"  → ["y-combinator", "ycombinator", ...]
    """
    base = company.lower().strip()
    # Remove trailing Inc/Corp/LLC artifacts
    base = re.sub(r"\s+(inc\.?|corp\.?|llc\.?|ltd\.?)$", "", base).strip()
    variants = [
        re.sub(r"[^a-z0-9-]", "", base.replace(" ", "-")).strip("-"),  # "scale-ai"
        base.replace(" ", ""),                                           # "scaleai"
        base.replace(" ", "-"),                                          # "scale-ai" (same, already covered)
        base,                                                            # "scale ai" (raw)
    ]
    seen, result = set(), []
    for v in variants:
        if v and v not in seen:
            seen.add(v)
            result.append(v)
    return result


async def _github_get(client: httpx.AsyncClient, url: str) -> dict | list | None:
    """Make an authenticated (if token present) GitHub API GET request."""
    headers = {"Accept": "application/vnd.github+json"}
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"
    try:
        resp = await client.get(url, headers=headers, timeout=10.0)
        if resp.status_code == 404:
            return None
        if resp.status_code == 403:
            logger.warning("GitHub API rate-limit or access denied: %s", url)
            return None
        resp.raise_for_status()
        return resp.json()
    except httpx.TimeoutException:
        logger.debug("GitHub API timeout: %s", url)
        return None
    except Exception as e:
        logger.debug("GitHub API error %s: %s", url, e)
        return None


async def fetch_github_org_members(
    company: str,
    max_members: int = 30,
) -> list[dict]:
    """Fetch public GitHub org members for a company.

    Tries multiple slug variants of the company name.  Returns up to
    ``max_members`` members as dicts with keys:
      name, github_login, github_url, bio, company_field

    Returns [] if the org is not found or the API is unavailable.
    """
    slugs = _infer_org_slugs(company)

    async with httpx.AsyncClient() as client:
        # ── 1. Find the right org slug ──────────────────────────────────
        found_slug: str | None = None
        for slug in slugs:
            data = await _github_get(client, f"https://api.github.com/orgs/{slug}")
            if data and isinstance(data, dict) and data.get("login"):
                found_slug = slug
                logger.info("GitHub org found: %s → slug '%s'", company, found_slug)
                break

        if not found_slug:
            logger.info(
                "No GitHub org found for '%s' (tried slugs: %s)",
                company, slugs,
            )
            return []

        # ── 2. Fetch org member list ────────────────────────────────────
        per_page = min(max_members, 100)
        members_data = await _github_get(
            client,
            f"https://api.github.com/orgs/{found_slug}/members?per_page={per_page}",
        )
        if not members_data or not isinstance(members_data, list):
            logger.info("GitHub org %s: empty or missing member list", found_slug)
            return []

        logger.info(
            "GitHub org '%s': %d public member(s) returned (page 1, cap=%d)",
            found_slug, len(members_data), max_members,
        )

        # ── 3. Fetch individual profiles to get display names + bios ───
        members_to_fetch = members_data[:max_members]

        if settings.github_token:
            # Authenticated → concurrent (5000 req/hr is plenty)
            profile_tasks = [
                _github_get(client, f"https://api.github.com/users/{m['login']}")
                for m in members_to_fetch
            ]
            profiles = await asyncio.gather(*profile_tasks, return_exceptions=True)
        else:
            # Unauthenticated → sequential with 1 s gap (max ~60 req/hr)
            # Cap at 15 to avoid exhausting the hourly budget on a single org
            cap = min(len(members_to_fetch), 15)
            profiles: list = []
            for m in members_to_fetch[:cap]:
                profile = await _github_get(
                    client, f"https://api.github.com/users/{m['login']}"
                )
                profiles.append(profile)
                await asyncio.sleep(1.0)
            # Pad remainder so zip stays aligned
            profiles.extend([None] * (len(members_to_fetch) - cap))

        # ── 4. Build result list ────────────────────────────────────────
        result: list[dict] = []
        for m, profile in zip(members_to_fetch, profiles):
            if not profile or not isinstance(profile, dict):
                continue
            name = (profile.get("name") or "").strip()
            if not name:
                # Profile has no display name — skip (login-only accounts)
                continue
            result.append({
                "name": name,
                "github_login": m["login"],
                "github_url": f"https://github.com/{m['login']}",
                "bio": (profile.get("bio") or "").strip(),
                "company_field": (profile.get("company") or "").strip(),
            })

        logger.info(
            "GitHub org '%s': %d/%d members have display names → %d usable contacts",
            found_slug, len(result), len(members_to_fetch), len(result),
        )
        return result
