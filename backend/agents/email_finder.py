"""Email Finder Agent.

Discovers email addresses for contacts using:
1. Email pattern guessing (free, pure logic)
2. GitHub org/commits to verify company email patterns (Firecrawl)
3. GitHub API to find public emails for engineers (free)
4. DNS MX record verification (free)

Zero Browser Use credits consumed.
"""

import asyncio
import json
import logging
import re

import httpx

from backend.config import settings
from backend.models.schemas import EmailConfidence, EmailResult, Person
from backend.tools.scraper import ScraperTool
from backend.tools.verifier import check_mx_record

logger = logging.getLogger(__name__)


_KNOWN_DOMAINS: dict[str, str] = {
    "stripe": "stripe.com",
    "google": "google.com",
    "meta": "meta.com",
    "facebook": "meta.com",
    "apple": "apple.com",
    "amazon": "amazon.com",
    "microsoft": "microsoft.com",
    "netflix": "netflix.com",
    "uber": "uber.com",
    "lyft": "lyft.com",
    "airbnb": "airbnb.com",
    "spotify": "spotify.com",
    "slack": "slack.com",
    "salesforce": "salesforce.com",
    "twitter": "x.com",
    "x": "x.com",
    "linkedin": "linkedin.com",
    "snap": "snap.com",
    "snapchat": "snap.com",
    "pinterest": "pinterest.com",
    "reddit": "reddit.com",
    "shopify": "shopify.com",
    "databricks": "databricks.com",
    "figma": "figma.com",
    "notion": "notion.so",
    "vercel": "vercel.com",
    "openai": "openai.com",
    "anthropic": "anthropic.com",
    "palantir": "palantir.com",
    "coinbase": "coinbase.com",
    "robinhood": "robinhood.com",
    "plaid": "plaid.com",
    "square": "squareup.com",
    "block": "block.xyz",
    "doordash": "doordash.com",
    "instacart": "instacart.com",
}

# In-memory cache so we only search once per company per process
_domain_cache: dict[str, str] = {}


def get_company_domain(company: str) -> str:
    """Return a quick guess for the company domain from the known list.

    For an accurate domain, call discover_company_domain() instead.
    """
    normalized = company.strip().lower().replace(" ", "")
    if normalized in _KNOWN_DOMAINS:
        return _KNOWN_DOMAINS[normalized]
    if company in _domain_cache:
        return _domain_cache[company]
    # Fallback guess — will be verified/replaced by discover_company_domain
    slug = re.sub(r"[^a-z0-9]", "", normalized)
    return f"{slug}.com"


async def discover_company_domain(
    company: str,
    scraper: ScraperTool,
    explicit_website: str | None = None,
) -> str:
    """Discover the real domain for any company via Firecrawl search.

    If explicit_website is provided (from user input), extract and return
    its domain directly — no search credits needed.

    Falls back to get_company_domain() if search fails.
    """
    # User-supplied website takes highest priority
    if explicit_website:
        match = re.search(r"https?://(?:www\.)?([^/]+)", explicit_website)
        if match:
            domain = match.group(1).lower()
            logger.info("Using user-supplied domain for %s: %s", company, domain)
            _domain_cache[company] = domain
            return domain

    normalized = company.strip().lower().replace(" ", "")

    # Fast path: known companies
    if normalized in _KNOWN_DOMAINS:
        return _KNOWN_DOMAINS[normalized]

    # Check cache
    if company in _domain_cache:
        return _domain_cache[company]

    try:
        from firecrawl import FirecrawlApp

        app = FirecrawlApp(api_key=settings.firecrawl_api_key)
        query = f"{company} official company website"

        result = await asyncio.to_thread(app.search, query, limit=3)

        urls: list[str] = []
        if hasattr(result, "web") and result.web:
            urls = [r.url for r in result.web if r.url]
        elif isinstance(result, list):
            urls = [r.url for r in result if hasattr(r, "url") and r.url]

        skip = {"linkedin.com", "wikipedia.org", "glassdoor.com",
                "crunchbase.com", "bloomberg.com", "techcrunch.com",
                "forbes.com", "businessinsider.com", "indeed.com",
                "yelp.com", "yellowpages.com", "bbb.org", "zoominfo.com",
                "pitchbook.com", "angel.co", "wellfound.com"}

        # Build keyword set from company name for relevance scoring
        company_keywords = set(re.sub(r"[^a-z0-9 ]", "", company.lower()).split())
        company_keywords.discard("inc")
        company_keywords.discard("llc")
        company_keywords.discard("corp")
        company_keywords.discard("ltd")
        # Remove common location words (NY, CA, etc.) that inflate false matches
        company_keywords = {w for w in company_keywords if len(w) > 2}

        candidates: list[str] = []
        for url in urls:
            match = re.search(r"https?://(?:www\.)?([^/]+)", url)
            if not match:
                continue
            domain = match.group(1).lower()
            if any(s in domain for s in skip):
                continue
            candidates.append(domain)

        if not candidates:
            raise ValueError("No usable domains found in search results")

        # Prefer domains that contain at least one company keyword
        for domain in candidates:
            if any(kw in domain for kw in company_keywords):
                logger.info("Discovered domain for %s: %s", company, domain)
                _domain_cache[company] = domain
                return domain

        # Fall back to first non-skip domain
        domain = candidates[0]
        logger.info("Discovered domain for %s (no keyword match): %s", company, domain)
        _domain_cache[company] = domain
        return domain

    except Exception as e:
        logger.warning("Domain discovery via Firecrawl failed for %s: %s", company, e)

    # Final fallback
    fallback = get_company_domain(company)
    _domain_cache[company] = fallback
    logger.warning("Using fallback domain for %s: %s", company, fallback)
    return fallback


def parse_name(full_name: str) -> tuple[str, str]:
    """Split a full name into first and last name.

    Handles common edge cases: suffixes, initials, single names.
    """
    # Clean up the name
    name = full_name.strip()
    name = re.sub(r"\s+", " ", name)

    # Remove common suffixes/prefixes
    for suffix in [" Jr.", " Sr.", " III", " II", " IV", " PhD", " MD"]:
        name = name.replace(suffix, "")

    parts = name.split()

    if len(parts) == 0:
        return ("", "")
    if len(parts) == 1:
        return (parts[0].lower(), "")

    first = parts[0].lower()
    last = parts[-1].lower()

    # Handle "LastName, FirstName" format
    if "," in parts[0]:
        first = parts[1].lower() if len(parts) > 1 else ""
        last = parts[0].replace(",", "").lower()

    # Strip non-alpha chars (e.g. "R." → "r")
    first = re.sub(r"[^a-z]", "", first)
    last = re.sub(r"[^a-z]", "", last)

    return (first, last)


# ── Step 3.1: Email Pattern Guessing ─────────────────────────────────────


def generate_email_patterns(first: str, last: str, domain: str) -> list[str]:
    """Generate candidate email addresses from name components + domain.

    Returns patterns ordered by likelihood (most common corporate patterns first).
    """
    if not first or not domain:
        return []

    patterns = []

    if first and last:
        patterns = [
            f"{first}.{last}@{domain}",       # john.smith@company.com (most common)
            f"{first}{last}@{domain}",         # johnsmith@company.com
            f"{first[0]}{last}@{domain}",      # jsmith@company.com
            f"{first}@{domain}",               # john@company.com
            f"{first}_{last}@{domain}",        # john_smith@company.com
            f"{first[0]}.{last}@{domain}",     # j.smith@company.com
            f"{last}.{first}@{domain}",        # smith.john@company.com
            f"{first}{last[0]}@{domain}",      # johns@company.com
        ]
    elif first:
        patterns = [
            f"{first}@{domain}",
        ]

    return patterns


# ── Step 3.2: Pattern Verification via GitHub ────────────────────────────


async def discover_company_email_pattern(
    company: str, domain: str, scraper: ScraperTool
) -> str | None:
    """Try to discover the company's email pattern from public GitHub data.

    Scrapes the company's GitHub org page to find real email addresses
    in commit history or contributor profiles.

    Returns:
        The detected pattern format (e.g. "first.last") or None.
    """
    github_org = company.strip().lower().replace(" ", "")

    # Try scraping the GitHub org members/people page
    url = f"https://github.com/orgs/{github_org}/people"
    result = await scraper.scrape_url(url)

    emails_found: list[str] = []

    if result.success and result.content:
        emails_found.extend(_extract_emails_from_text(result.content, domain))

    # Also try the org's main page for any visible emails
    if not emails_found:
        url = f"https://github.com/{github_org}"
        result = await scraper.scrape_url(url)
        if result.success and result.content:
            emails_found.extend(_extract_emails_from_text(result.content, domain))

    if not emails_found:
        logger.info("No company emails found on GitHub for %s", company)
        return None

    # Analyze the found emails to determine the pattern
    pattern = _infer_pattern_from_emails(emails_found)
    logger.info("Detected email pattern for %s: %s (from %d emails)", company, pattern, len(emails_found))
    return pattern


def _extract_emails_from_text(text: str, domain: str) -> list[str]:
    """Extract email addresses matching a specific domain from text."""
    pattern = rf"[\w.+-]+@{re.escape(domain)}"
    return list(set(re.findall(pattern, text, re.IGNORECASE)))


def _infer_pattern_from_emails(emails: list[str]) -> str | None:
    """Analyze a list of real emails to infer the naming pattern.

    Returns: "first.last", "firstlast", "flast", "first", etc.
    """
    if not emails:
        return None

    local_parts = [e.split("@")[0].lower() for e in emails]

    dot_count = sum(1 for lp in local_parts if "." in lp)
    underscore_count = sum(1 for lp in local_parts if "_" in lp)

    if dot_count > len(local_parts) / 2:
        return "first.last"
    if underscore_count > len(local_parts) / 2:
        return "first_last"

    # Check average length — short means likely "flast" or "first"
    avg_len = sum(len(lp) for lp in local_parts) / len(local_parts)
    if avg_len < 6:
        return "flast"

    return "firstlast"


def reorder_patterns_by_detected(
    patterns: list[str], detected_format: str | None
) -> list[str]:
    """Reorder email pattern candidates to put the detected format first."""
    if not detected_format:
        return patterns

    format_map = {
        "first.last": lambda p: "." in p.split("@")[0] and "_" not in p.split("@")[0],
        "first_last": lambda p: "_" in p.split("@")[0],
        "firstlast": lambda p: "." not in p.split("@")[0] and "_" not in p.split("@")[0] and len(p.split("@")[0]) > 5,
        "flast": lambda p: len(p.split("@")[0]) <= 6 and "." not in p.split("@")[0],
    }

    matcher = format_map.get(detected_format)
    if not matcher:
        return patterns

    matching = [p for p in patterns if matcher(p)]
    non_matching = [p for p in patterns if not matcher(p)]
    return matching + non_matching


# ── Step 3.3: Direct Email Discovery via GitHub API ──────────────────────


async def find_github_email(person_name: str, company: str) -> str | None:
    """Search GitHub for a person and return their public email if available.

    Uses the GitHub Search API (free, no key required for basic use).
    """
    query = f"{person_name} {company}"

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.github.com/search/users",
                params={"q": query, "per_page": 3},
                headers={"Accept": "application/vnd.github.v3+json"},
                timeout=10.0,
            )

            if response.status_code == 403:
                logger.debug("GitHub API rate limited")
                return None

            if response.status_code != 200:
                return None

            data = response.json()
            items = data.get("items", [])

            for user in items:
                login = user.get("login", "")
                # Fetch the full user profile to get the email
                user_resp = await client.get(
                    f"https://api.github.com/users/{login}",
                    headers={"Accept": "application/vnd.github.v3+json"},
                    timeout=10.0,
                )

                if user_resp.status_code != 200:
                    continue

                user_data = user_resp.json()
                email = user_data.get("email")
                bio = (user_data.get("bio") or "").lower()
                user_company = (user_data.get("company") or "").lower()

                # Verify this is the right person (company match)
                company_lower = company.lower()
                if email and (
                    company_lower in user_company
                    or company_lower in bio
                    or f"@{company_lower}" in user_company
                ):
                    logger.info("Found GitHub email for %s: %s", person_name, email)
                    return email

    except Exception as e:
        logger.debug("GitHub email search failed for %s: %s", person_name, e)

    return None


# ── Main Email Finder ────────────────────────────────────────────────────


class EmailFinder:
    """Finds email addresses for a list of people at a company.

    Pipeline:
    1. Determine company domain
    2. Verify domain has MX records
    3. Try to discover email pattern from GitHub
    4. For each person: try GitHub API, then pattern matching
    5. Assign confidence levels
    """

    def __init__(self, scraper: ScraperTool | None = None):
        self.scraper = scraper or ScraperTool()

    async def find_emails(
        self,
        people: list[Person],
        company: str,
        company_website: str | None = None,
    ) -> list[EmailResult]:
        """Find emails for all people at a company.

        Args:
            people: List of Person objects.
            company: Company name.
            company_website: Optional URL of the company website (from user input).

        Returns:
            List of EmailResult objects, one per person.
        """
        domain = await discover_company_domain(company, self.scraper, explicit_website=company_website)
        logger.info("Company domain: %s", domain)

        # Verify the domain accepts email
        has_mx = await check_mx_record(domain)
        if not has_mx:
            logger.warning("Domain %s has no MX records — emails may not work", domain)

        # Try to discover the email pattern from GitHub
        detected_pattern = await discover_company_email_pattern(
            company, domain, self.scraper
        )

        # Find emails for each person concurrently
        tasks = [
            self._find_email_for_person(person, domain, detected_pattern)
            for person in people
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        email_results: list[EmailResult] = []
        for i, result in enumerate(results):
            if isinstance(result, EmailResult):
                email_results.append(result)
            else:
                logger.warning("Email discovery failed for %s: %s", people[i].name, result)
                first, last = parse_name(people[i].name)
                patterns = generate_email_patterns(first, last, domain)
                email_results.append(EmailResult(
                    name=people[i].name,
                    email=patterns[0] if patterns else "",
                    confidence=EmailConfidence.LOW,
                    source="Pattern guess (fallback after error)",
                    alternative_emails=patterns[1:3],
                ))

        return email_results

    async def _find_email_for_person(
        self,
        person: Person,
        domain: str,
        detected_pattern: str | None,
    ) -> EmailResult:
        """Find the email for a single person."""
        first, last = parse_name(person.name)

        if not first:
            return EmailResult(
                name=person.name,
                email="",
                confidence=EmailConfidence.LOW,
                source="Could not parse name",
            )

        # Step 1: Try GitHub API (engineers often have public emails)
        is_engineer = any(
            kw in person.title.lower()
            for kw in ["engineer", "developer", "software", "sre", "devops", "staff", "principal"]
        )

        if is_engineer:
            github_email = await find_github_email(person.name, person.company)
            if github_email:
                patterns = generate_email_patterns(first, last, domain)
                return EmailResult(
                    name=person.name,
                    email=github_email,
                    confidence=EmailConfidence.HIGH,
                    source="GitHub public profile",
                    alternative_emails=patterns[:2],
                )

        # Step 2: Generate patterns and reorder by detected format
        patterns = generate_email_patterns(first, last, domain)
        patterns = reorder_patterns_by_detected(patterns, detected_pattern)

        if not patterns:
            return EmailResult(
                name=person.name,
                email="",
                confidence=EmailConfidence.LOW,
                source="Could not generate patterns",
            )

        # Assign confidence based on what we know
        if detected_pattern:
            confidence = EmailConfidence.MEDIUM
            source = f"Pattern match ({detected_pattern}@{domain} format verified via GitHub)"
        else:
            confidence = EmailConfidence.LOW
            source = f"Pattern guess (most common corporate format for {domain})"

        return EmailResult(
            name=person.name,
            email=patterns[0],
            confidence=confidence,
            source=source,
            alternative_emails=patterns[1:3],
        )
