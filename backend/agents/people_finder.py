"""People Finder Agent.

Wide-net pipeline (when Serper API key is set):
  Dynamic tiered queries → 30–40 candidates → hard filter → warm signal cross-reference →
  validation (with recency) → scoring (dual-axis) → dynamic diversity selection → 6–8 contacts.

Fallback (no Serper): 2 Browser Use tasks + validation + scoring.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import unicodedata
from collections import defaultdict
from typing import TYPE_CHECKING

from openai import AsyncOpenAI
from pydantic import BaseModel

from backend.config import settings
from backend.models.schemas import Person
from backend.tools.browser import BrowserTool
from backend.tools.serper import search as serper_search
from backend.agents.priority_scorer import score_people
from backend.agents.job_analyzer import build_search_queries, QueryGroup

if TYPE_CHECKING:
    from backend.agents.user_profile_extractor import UserProfile

logger = logging.getLogger(__name__)


def _extract_name_from_linkedin_url(url: str) -> str:
    """Extract a full name from a LinkedIn URL slug like /in/claire-robert."""
    match = re.search(r"linkedin\.com/in/([a-zA-Z0-9-]+)", url)
    if not match:
        return ""
    slug = match.group(1).lower()
    # Remove trailing ID suffixes (e.g., claire-robert-1234ab, claire-robert-123)
    slug = re.sub(r"-[\da-f]{4,}$", "", slug)
    slug = re.sub(r"-\d+$", "", slug)
    parts = [p for p in slug.split("-") if p.isalpha()]
    # Require at least 2 parts, each with 2+ chars (reject single-letter parts like "t")
    if len(parts) >= 2 and all(len(p) >= 2 for p in parts):
        return " ".join(p.capitalize() for p in parts)
    return ""


def _extract_slug_from_url(url: str) -> str:
    """Extract the normalized profile slug from a LinkedIn URL for dedup."""
    match = re.search(r"linkedin\.com/in/([a-zA-Z0-9-]+)", url)
    if not match:
        return ""
    slug = match.group(1).lower()
    # Normalize: strip trailing IDs so /in/claire-robert and /in/claire-robert-123 match
    slug = re.sub(r"-[\da-f]{4,}$", "", slug)
    slug = re.sub(r"-\d+$", "", slug)
    return slug


# ── Hard filter: exclude people who will rarely reply to intern cold emails ───

EXCLUDE_KEYWORDS = {
    "ceo", "cfo", "cto", "coo", "founder", "co-founder",
    "president", "vp", "vice president", "director", "head of",
    "chief", "partner", "general counsel", "controller",
    "cpa", "cfa", "board member",
}

EXCLUDE_DEPARTMENTS = {
    "finance", "accounting", "legal", "compliance",
    "sales", "marketing", "operations", "supply chain",
}


class LinkedInPerson(BaseModel):
    name: str = ""
    title: str = ""
    linkedin_url: str = ""
    recent_activity: str = ""
    discovery_source: str = ""
    warm_signals: list[str] = []
    profile_recency: str = "unknown"  # "active", "stale", "unknown"


def hard_filter(person: LinkedInPerson, role: str) -> bool:
    """Remove people who are unlikely to reply to intern cold emails. Deterministic, no LLM."""
    title = person.title.lower()
    if any(kw in title for kw in EXCLUDE_KEYWORDS):
        return False
    if any(dept in title for dept in EXCLUDE_DEPARTMENTS):
        if "recruit" not in title:
            return False
    return True


QUALITY_THRESHOLD = 0.60


def _normalize_title(title: str) -> str:
    """Strip emojis and special characters from a title for level inference."""
    cleaned = []
    for c in title:
        cp = ord(c)
        # Keep ASCII and standard Latin chars; drop emoji/symbol ranges
        if cp < 0x2000:
            cleaned.append(c)
        elif unicodedata.category(c) in ("Ll", "Lu", "Lt", "Lm", "Lo"):
            cleaned.append(c)
        else:
            cleaned.append(" ")
    return " ".join("".join(cleaned).split()).lower()


def _infer_level(title: str) -> int:
    """Infer organizational level from a title string.

    Returns:
        0 = IC (individual contributor / engineer)
        1 = manager / lead
        2 = senior manager
        3 = director or above
    """
    t = _normalize_title(title)
    if any(w in t for w in ["director", "vp ", "vice president", "head of", "chief"]):
        return 3
    if any(w in t for w in ["senior engineering manager", "senior manager", "staff em", "principal manager"]):
        return 2
    # "engineering manager" or "team lead" / "tech lead" → level 1
    has_manager = any(w in t for w in ["engineering manager", "tech lead", "team lead", "engineering lead"])
    # Standalone "lead" only if not next to "developer" or "engineer" (which implies IC lead)
    has_lead_only = " lead" in t and "developer" not in t and "engineer" not in t
    if has_manager or has_lead_only:
        return 1
    return 0


def select_final_contacts(
    scored_people: list[Person],
    target: int = 8,
) -> list[Person]:
    """Dynamic diversity selection based on contact_category and warm signals.

    Instead of fixed quotas (2 recruiters, 3 engineers, 1 manager), uses:
    0. Quality threshold: drop contacts below 0.60 with no warm signals.
    1. Always include anyone who shared the job posting.
    2. Ensure at least 1 person from each available contact_category.
    3. If warm connections exist, ensure at least 2 are included.
    4. Fill remaining slots with highest-scoring candidates.
    """
    scored_people = sorted(scored_people, key=lambda p: p.priority_score, reverse=True)

    # Step 0: Quality threshold — drop low-scoring contacts with no warm signals.
    # Warm signals can exempt from the full threshold, but contacts must still
    # meet a minimum floor (influence > 0 or score >= 0.40) to avoid wasting
    # slots on zero-influence unknowns who merely share a university.
    qualified = []
    for p in scored_people:
        if p.priority_score >= QUALITY_THRESHOLD:
            qualified.append(p)
        elif p.warm_signals and (p.influence_score > 0 or p.priority_score >= 0.40):
            qualified.append(p)
    if not qualified:
        qualified = scored_people[:3]  # edge case: keep top 3 regardless

    dropped = [p for p in scored_people if p not in qualified]
    logger.info(
        "Quality threshold: %d/%d passed (threshold=%.2f)",
        len(qualified), len(scored_people), QUALITY_THRESHOLD,
    )
    for p in dropped:
        logger.info("  Dropped: %s (score=%.2f, no warm signals)", p.name, p.priority_score)
    scored_people = qualified

    selected: list[Person] = []
    selected_set: set[str] = set()  # track by name to avoid dupes

    def _add(person: Person) -> bool:
        if person.name in selected_set:
            return False
        selected.append(person)
        selected_set.add(person.name)
        return True

    # Step 1: Always include job posting sharers
    for p in scored_people:
        if len(selected) >= target:
            break
        if p.discovery_source == "job_posting_sharer" or "shared_job_posting" in p.warm_signals:
            _add(p)

    # Step 2: Ensure at least 1 from each available category
    available_categories = {p.contact_category for p in scored_people if p.contact_category}
    for cat in available_categories:
        if len(selected) >= target:
            break
        cat_people = [p for p in scored_people if p.contact_category == cat and p.name not in selected_set]
        if cat_people:
            _add(cat_people[0])

    # Step 2.5: Org-level diversity within the same contact_category
    # If ≥2 selected contacts share the same category AND the same inferred level,
    # try to swap the weakest same-level duplicate for a different-level candidate
    # (only if the replacement scores within 15% of the one being replaced).
    selected_by_cat: dict[str, list[Person]] = defaultdict(list)
    for p in selected:
        if p.contact_category:
            selected_by_cat[p.contact_category].append(p)

    for cat, cat_selected in selected_by_cat.items():
        if len(cat_selected) < 2:
            continue
        levels = [_infer_level(p.title) for p in cat_selected]
        if len(set(levels)) > 1:
            continue  # already diverse
        # All same level — try to find a different-level candidate from unselected pool
        same_cat_unselected = [
            p for p in scored_people
            if p.contact_category == cat and p.name not in selected_set
        ]
        for candidate in same_cat_unselected:
            if _infer_level(candidate.title) == levels[0]:
                continue  # still same level, skip
            # Swap out the lowest-scoring duplicate (keep the best one)
            to_remove = min(cat_selected[1:], key=lambda p: p.priority_score)
            if candidate.priority_score >= to_remove.priority_score * 0.85:
                selected.remove(to_remove)
                selected_set.discard(to_remove.name)
                _add(candidate)
                logger.info(
                    "Org diversity: swapped %s (level %d, %.2f) for %s (level %d, %.2f) in category '%s'",
                    to_remove.name, _infer_level(to_remove.title), to_remove.priority_score,
                    candidate.name, _infer_level(candidate.title), candidate.priority_score,
                    cat,
                )
                break

    # Step 3: Ensure at least 2 warm connections if available
    warm_in_selected = sum(1 for p in selected if p.warm_signals)
    if warm_in_selected < 2:
        warm_candidates = [p for p in scored_people if p.warm_signals and p.name not in selected_set]
        for p in warm_candidates:
            if warm_in_selected >= 2 or len(selected) >= target:
                break
            if _add(p):
                warm_in_selected += 1

    # Step 4: Fill remaining with highest-scoring
    for p in scored_people:
        if len(selected) >= target:
            break
        _add(p)

    return selected


class PeopleFinder:
    """Finds relevant people at a company. Uses Serper (wide net) when key is set, else Browser Use."""

    def __init__(self, browser: BrowserTool | None = None):
        self.browser = browser or BrowserTool()

    # ── Serper: dynamic tiered queries ────────────────────────────────

    @staticmethod
    def _parse_linkedin_from_serper(result, category: str = "general") -> LinkedInPerson | None:
        """Parse one Serper organic result into LinkedInPerson if it's a LinkedIn profile."""
        link = (result.link or "").strip()
        if "linkedin.com/in/" not in link:
            return None
        title_raw = (result.title or "").strip()
        snippet = (result.snippet or "").strip()
        if " | " in title_raw:
            title_raw = title_raw.split(" | ")[0].strip()
        parts = [p.strip() for p in title_raw.split(" - ") if p.strip()]
        name = parts[0] if parts else ""
        job_title = " - ".join(parts[1:]) if len(parts) > 1 else ""
        if not name:
            return None

        # Detect truncated names (e.g. "Claire J." → single-char last name)
        name_parts = name.split()
        if len(name_parts) >= 2:
            last_part = name_parts[-1].rstrip(".")
            if len(last_part) == 1:
                full_name = _extract_name_from_linkedin_url(link)
                if full_name:
                    logger.info("  Name recovery: '%s' → '%s' (from URL slug)", name, full_name)
                    name = full_name

        return LinkedInPerson(
            name=name,
            title=job_title,
            linkedin_url=link,
            recent_activity=snippet,
            discovery_source=category,
        )

    async def search_serper_wide(
        self,
        company: str,
        role: str,
        job_context: dict | None = None,
        user_profile: "UserProfile | None" = None,
        job_url: str | None = None,
        seed_candidates: list[dict] | None = None,
    ) -> list[LinkedInPerson]:
        """Run dynamic tiered Serper queries concurrently, return aggregated LinkedIn profiles."""
        team_keyword = self._extract_team_keyword(role)
        query_groups = build_search_queries(
            company=company,
            job_context=job_context,
            user_profile=user_profile,
            job_url=job_url,
            role_keyword=team_keyword,
        )

        # Inject seed candidates from team page scraping as tier-1 queries (cap at 3)
        if seed_candidates:
            from backend.agents.job_analyzer import QueryGroup
            seed_queries: list[QueryGroup] = []
            for candidate in seed_candidates[:3]:
                name = (candidate.get("name") or "").strip()
                if name:
                    seed_queries.append(QueryGroup(
                        query=f'site:linkedin.com/in "{name}" "{company}"',
                        category="hiring_manager",
                        priority=1,
                    ))
            if seed_queries:
                logger.info("Injecting %d seed candidate queries from team page", len(seed_queries))
                # Insert before existing queries, preserve sort order
                query_groups = seed_queries + query_groups
                query_groups.sort(key=lambda q: q.priority)
                # Re-apply cap (12 for intern, otherwise 10 — use existing cap logic)
                jc = job_context or {}
                is_intern = (jc.get("seniority") or "").lower() == "intern"
                cap = 12 if is_intern else 10
                cap += len(seed_queries)  # Expand cap to accommodate seeds without pushing out existing queries
                query_groups = query_groups[:cap]

        logger.info("Running %d Serper queries for %s...", len(query_groups), company)

        for i, qg in enumerate(query_groups, 1):
            logger.info("  Query %d [tier %d, %s]: %s", i, qg.priority, qg.category, qg.query)

        tasks = [serper_search(qg.query, num=10) for qg in query_groups]
        results_per_query = await asyncio.gather(*tasks)

        raw: list[LinkedInPerson] = []
        for qg, results in zip(query_groups, results_per_query):
            count = 0
            for r in results:
                p = self._parse_linkedin_from_serper(r, category=qg.category)
                if p:
                    raw.append(p)
                    count += 1
            logger.info("  [%s] → %d LinkedIn profiles", qg.category, count)

        deduped = self._deduplicate(raw)
        logger.info("Serper: %d raw → %d unique after dedup", len(raw), len(deduped))
        return deduped

    # ── Browser Use search (fallback) ─────────────────────────────────

    async def search_google_for_linkedin(
        self, company: str, query: str, max_results: int = 10
    ) -> list[LinkedInPerson]:
        """Search Google to find LinkedIn profiles at a company."""
        search_query = f'site:linkedin.com/in "at {company}" OR "{company} ·" "{query}"'

        task_prompt = (
            f'Go to google.com and search for: {search_query}. '
            f'For each search result that is a LinkedIn profile, get the name, '
            f'title, URL, and the Google snippet text (the description shown below each result). '
            f'Return JSON: {{"people": [{{"name": "...", "title": "...", '
            f'"linkedin_url": "...", "recent_activity": "the Google snippet text for this result"}}]}}. '
            f'Return up to {max_results} people.'
        )

        result = await self.browser.run_task(
            task=task_prompt,
            start_url="https://www.google.com",
            max_steps=20,
        )

        if not result.success:
            logger.warning("Google search failed for '%s %s': %s", company, query, result.error)
            return []

        return self._parse_people_from_output(result.output)

    async def search_linkedin(
        self, company: str, query: str, max_results: int = 10
    ) -> list[LinkedInPerson]:
        """Direct LinkedIn search — fallback if Google returns too few."""
        task_prompt = (
            f'Go to linkedin.com and search for people who work at "{company}" with title or role "{query}". '
            f'Extract the name, title, and profile URL for each person in the results (up to {max_results}). '
            f'Only include people who currently work or recently worked at {company} (not people with that name). '
            f'If LinkedIn requires login, try the search anyway or return whatever is visible. '
            f'Return JSON: {{"people": [{{"name": "...", "title": "...", '
            f'"linkedin_url": "...", "recent_activity": ""}}]}}'
        )

        result = await self.browser.run_task(
            task=task_prompt,
            start_url="https://www.linkedin.com/search/results/people/",
            max_steps=30,
        )

        if not result.success:
            logger.warning("LinkedIn search failed for '%s %s': %s", company, query, result.error)
            return []

        return self._parse_people_from_output(result.output)

    # ── Warm signal cross-referencing ─────────────────────────────────

    @staticmethod
    def _cross_reference_warm_signals(
        people: list[LinkedInPerson],
        user_profile: "UserProfile | None",
    ) -> list[LinkedInPerson]:
        """Tag people with warm connection signals based on user profile and activity."""
        for person in people:
            text = f"{person.title} {person.recent_activity}".lower()

            # Cross-reference with user's background
            if user_profile:
                for uni in user_profile.universities:
                    if uni.lower() in text:
                        person.warm_signals.append(f"same_university:{uni}")
                for co in user_profile.previous_companies:
                    if co.lower() in text:
                        person.warm_signals.append(f"shared_company:{co}")

            # Activity-based signals (work for all people regardless of user profile)
            if any(kw in text for kw in ["hiring", "we're growing", "join my team", "open role", "we're hiring"]):
                person.warm_signals.append("posted_about_hiring")
            if any(kw in text for kw in ["just joined", "excited to announce", "new role", "thrilled to join", "started a new position"]):
                person.warm_signals.append("recently_joined")

        return people

    # ── Validation ───────────────────────────────────────────────────

    async def _validate_person_works_at_company(
        self, person: LinkedInPerson, company: str
    ) -> tuple[bool, str]:
        """Use OpenAI to validate if a person works at the company and check profile recency.

        Returns (works_here: bool, recency: str).
        """
        if not settings.openai_api_key:
            return True, "unknown"

        client = AsyncOpenAI(api_key=settings.openai_api_key)

        prompt = f"""Given this LinkedIn profile information, answer two questions about "{company}":

1. Does this person CURRENTLY work at {company}? (yes/no)
2. How recently does their profile seem to be updated? (active/stale/unknown)
   - "active" = recent posts, 2024-2026 dates visible, recent activity
   - "stale" = no recent updates, dates are 2+ years old
   - "unknown" = can't tell from available info

Name: {person.name}
Title: {person.title}
Profile snippet: {person.recent_activity[:300]}

Return JSON only: {{"works_here": "yes" or "no", "recency": "active" or "stale" or "unknown"}}"""

        try:
            response = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                response_format={"type": "json_object"},
                max_tokens=50,
            )
            content = response.choices[0].message.content or "{}"
            data = json.loads(content)
            works_here = "yes" in str(data.get("works_here", "yes")).lower()
            recency = data.get("recency", "unknown")
            if recency not in ("active", "stale", "unknown"):
                recency = "unknown"

            if not works_here:
                logger.info("Filtered out %s (title: %s) - doesn't work at %s",
                           person.name, person.title, company)

            return works_here, recency
        except Exception as e:
            logger.warning("Validation failed for %s: %s", person.name, e)
            return True, "unknown"

    async def _filter_valid_people(
        self, people: list[LinkedInPerson], company: str
    ) -> list[LinkedInPerson]:
        """Filter out false positives and tag recency using OpenAI validation."""
        if not people:
            return []

        logger.info("Validating %d profiles...", len(people))
        validation_tasks = [
            self._validate_person_works_at_company(p, company) for p in people
        ]
        validations = await asyncio.gather(*validation_tasks)

        valid_people = []
        for p, (works_here, recency) in zip(people, validations):
            if works_here:
                p.profile_recency = recency
                valid_people.append(p)

        filtered_count = len(people) - len(valid_people)
        if filtered_count > 0:
            logger.info("Filtered out %d/%d false positives", filtered_count, len(people))

        return valid_people

    # ── GitHub presence check ─────────────────────────────────────────

    async def _check_github_presence_batch(self, people: list["Person"], company: str) -> None:
        """Set has_public_github=True on engineers/managers who have a matching GitHub profile.

        Runs checks sequentially with a small delay to stay within unauthenticated rate limit
        (10 req/min). With GITHUB_TOKEN set, runs concurrently (60 req/min).
        """
        from backend.agents.email_finder import check_github_presence

        non_recruiters = [
            p for p in people
            if "recruiter" not in p.title.lower() and "talent" not in p.title.lower()
        ]
        if not non_recruiters:
            return

        logger.info("Checking GitHub presence for %d engineer/manager candidates...", len(non_recruiters))

        if settings.github_token:
            # Authenticated: run concurrently
            results = await asyncio.gather(
                *[check_github_presence(p.name, company) for p in non_recruiters],
                return_exceptions=True,
            )
            for person, has_gh in zip(non_recruiters, results):
                if has_gh is True:
                    person.has_public_github = True
        else:
            # Unauthenticated: run with small delay to stay within 10 req/min
            for person in non_recruiters:
                try:
                    has_gh = await check_github_presence(person.name, company)
                    if has_gh:
                        person.has_public_github = True
                    await asyncio.sleep(0.15)  # ~6-7 checks/min safety margin
                except Exception:
                    pass

        gh_count = sum(1 for p in non_recruiters if p.has_public_github)
        if gh_count:
            logger.info("GitHub: %d/%d candidates have public GitHub profiles", gh_count, len(non_recruiters))

    # ── Main pipeline ────────────────────────────────────────────────

    @staticmethod
    def _normalize_linkedin_url(url: str) -> str:
        """Normalize LinkedIn URL for deduplication (lowercase, no trailing slash, no query)."""
        if not url or not url.strip():
            return ""
        u = url.strip().lower().rstrip("/")
        return u.split("?")[0] if "?" in u else u

    async def find_people(
        self,
        company: str,
        role: str,
        target_count: int = 8,
        job_context: dict | None = None,
        exclude_linkedin_urls: set[str] | None = None,
        user_profile: "UserProfile | None" = None,
        job_url: str | None = None,
        seed_candidates: list[dict] | None = None,
    ) -> list[Person]:
        """Find relevant people at a company for a given role.

        With Serper API: dynamic tiered queries → hard filter → warm signal cross-reference →
        validation (with recency) → scoring (dual-axis) → dynamic diversity selection.

        Args:
            company: Target company name.
            role: Role being applied for.
            target_count: Number of final contacts to return.
            job_context: Optional dict from job_analyzer.
            exclude_linkedin_urls: Optional set of LinkedIn URLs to skip.
            user_profile: Optional UserProfile for warm-path matching.
            job_url: Optional job posting URL for tier-1 queries.
            seed_candidates: Optional list of {name, title} dicts from team page scraping.
        """
        exclude = set()
        if exclude_linkedin_urls:
            for u in exclude_linkedin_urls:
                n = PeopleFinder._normalize_linkedin_url(u)
                if n:
                    exclude.add(n)
        if settings.serper_api_key:
            return await self._find_people_serper(company, role, target_count, job_context, exclude, user_profile, job_url, seed_candidates)
        return await self._find_people_browser(company, role, target_count, job_context, exclude, user_profile)

    async def _find_people_serper(
        self,
        company: str,
        role: str,
        target_count: int,
        job_context: dict | None = None,
        exclude_urls: set[str] | None = None,
        user_profile: "UserProfile | None" = None,
        job_url: str | None = None,
        seed_candidates: list[dict] | None = None,
    ) -> list[Person]:
        """Wide-net pipeline: dynamic queries → hard filter → warm signals → validation → scoring → diversity selection."""
        all_people = await self.search_serper_wide(company, role, job_context, user_profile, job_url, seed_candidates)
        if not all_people:
            logger.warning("Serper returned no candidates for %s", company)
            return []

        if exclude_urls:
            all_people = [
                p for p in all_people
                if self._normalize_linkedin_url(p.linkedin_url) not in exclude_urls
            ]
            logger.info("After excluding existing: %d candidates", len(all_people))

        all_people = [p for p in all_people if hard_filter(p, role)]
        logger.info("After hard filter: %d candidates", len(all_people))
        if not all_people:
            return []

        # Cross-reference warm signals before validation
        all_people = self._cross_reference_warm_signals(all_people, user_profile)

        all_people = await self._filter_valid_people(all_people, company)
        logger.info("After validation: %d confirmed employees", len(all_people))
        if not all_people:
            return []

        people = [
            Person(
                name=lp.name,
                title=lp.title,
                company=company,
                linkedin_url=lp.linkedin_url,
                recent_activity=lp.recent_activity,
                profile_summary=lp.recent_activity,
                discovery_source=lp.discovery_source,
                warm_signals=lp.warm_signals,
            )
            for lp in all_people
        ]

        # ── GitHub presence check (before scoring so scorer can factor it in) ──
        # Only for engineers/managers (not recruiters). Respects rate limits.
        await self._check_github_presence_batch(people, company)

        logger.info("Scoring %d people (dual-axis: influence + reachability)...", len(people))
        scored = await score_people(people, role, company, job_context=job_context)
        final = select_final_contacts(scored, target=target_count)
        logger.info("People finder complete: %d final contacts for %s", len(final), company)
        return final

    async def _find_people_browser(
        self,
        company: str,
        role: str,
        target_count: int,
        job_context: dict | None = None,
        exclude_urls: set[str] | None = None,
        user_profile: "UserProfile | None" = None,
    ) -> list[Person]:
        """Fallback: Browser Use (2 tasks) → warm signals → validation → scoring → diversity selection."""
        team_keyword = self._extract_team_keyword(role)
        logger.info("Searching for %s recruiters and %s %s (concurrent)...", company, company, team_keyword)
        recruiter_task = self.search_google_for_linkedin(company, "recruiter")
        engineer_task = self.search_google_for_linkedin(company, team_keyword)
        recruiter_results, engineer_results = await asyncio.gather(recruiter_task, engineer_task)
        logger.info("Found %d recruiters + %d engineers/managers", len(recruiter_results), len(engineer_results))

        if len(recruiter_results) < 2 and len(engineer_results) < 2:
            logger.info("Too few results, trying LinkedIn direct search...")
            recruiter_results.extend(await self.search_linkedin(company, "recruiter"))

        interleaved: list[LinkedInPerson] = []
        max_len = max(len(recruiter_results), len(engineer_results))
        for i in range(max_len):
            if i < len(recruiter_results):
                interleaved.append(recruiter_results[i])
            if i < len(engineer_results):
                interleaved.append(engineer_results[i])

        all_people = self._deduplicate(interleaved)
        if exclude_urls:
            all_people = [
                p for p in all_people
                if self._normalize_linkedin_url(p.linkedin_url) not in exclude_urls
            ]
            logger.info("After excluding existing: %d candidates", len(all_people))
        all_people = [p for p in all_people if hard_filter(p, role)]

        # Cross-reference warm signals
        all_people = self._cross_reference_warm_signals(all_people, user_profile)

        all_people = await self._filter_valid_people(all_people, company)

        people = [
            Person(
                name=lp.name,
                title=lp.title,
                company=company,
                linkedin_url=lp.linkedin_url,
                recent_activity=lp.recent_activity,
                profile_summary=lp.recent_activity,
                discovery_source=lp.discovery_source,
                warm_signals=lp.warm_signals,
            )
            for lp in all_people
        ]

        logger.info("Scoring %d people (dual-axis: influence + reachability)...", len(people))
        scored = await score_people(people, role, company, job_context=job_context)
        final = select_final_contacts(scored, target=target_count)
        logger.info("People finder complete: %d final contacts for %s", len(final), company)
        return final

    # ── Helpers ──────────────────────────────────────────────────────

    def _parse_people_from_output(self, output: str) -> list[LinkedInPerson]:
        """Parse Browser Use output into LinkedInPerson objects."""
        if not output:
            return []

        # Unescape string-encoded JSON from Browser Use
        if '\\"' in output[:50]:
            try:
                output = json.loads(f'"{output}"')
            except (json.JSONDecodeError, ValueError):
                output = output.replace('\\"', '"')

        # Fix invalid escape sequences
        cleaned = re.sub(r'\\(?!["\\/bfnrtu])', lambda m: m.group(0)[1:], output)

        # Try JSON object with "people" key
        people = self._try_parse_json(cleaned)
        if people:
            return people

        # Retry with raw output
        if cleaned != output:
            people = self._try_parse_json(output)
            if people:
                return people

        logger.warning("Could not parse people from output (length=%d)", len(output))
        return []

    def _try_parse_json(self, text: str) -> list[LinkedInPerson] | None:
        """Try to extract people from JSON in text."""
        # Try object
        obj_start = text.find("{")
        obj_end = text.rfind("}") + 1
        if obj_start >= 0 and obj_end > obj_start:
            try:
                data = json.loads(text[obj_start:obj_end])
                if "people" in data and isinstance(data["people"], list):
                    return [LinkedInPerson(**p) for p in data["people"] if isinstance(p, dict) and p.get("name")]
                if "name" in data:
                    return [LinkedInPerson(**data)]
            except (json.JSONDecodeError, TypeError, ValueError):
                pass

        # Try array
        arr_start = text.find("[")
        arr_end = text.rfind("]") + 1
        if arr_start >= 0 and arr_end > arr_start:
            try:
                data = json.loads(text[arr_start:arr_end])
                if isinstance(data, list):
                    return [LinkedInPerson(**p) for p in data if isinstance(p, dict) and p.get("name")]
            except (json.JSONDecodeError, TypeError, ValueError):
                pass

        return None

    def _deduplicate(self, people: list[LinkedInPerson]) -> list[LinkedInPerson]:
        """Remove duplicate people based on LinkedIn URL, URL slug, or name."""
        seen_urls: set[str] = set()
        seen_names: set[str] = set()
        seen_slugs: set[str] = set()
        unique: list[LinkedInPerson] = []

        for p in people:
            url_key = p.linkedin_url.rstrip("/").lower() if p.linkedin_url else ""
            name_key = p.name.strip().lower()
            slug = _extract_slug_from_url(url_key)

            if url_key and url_key in seen_urls:
                continue
            if name_key and name_key in seen_names:
                continue
            # Slug dedup catches "Claire J." vs "Claire Robert" with same /in/claire-robert
            if slug and slug in seen_slugs:
                continue

            if url_key:
                seen_urls.add(url_key)
            if name_key:
                seen_names.add(name_key)
            if slug:
                seen_slugs.add(slug)
            unique.append(p)

        return unique

    def _extract_team_keyword(self, role: str) -> str:
        """Extract a team-relevant keyword from the role."""
        role_lower = role.lower()

        keyword_map = {
            "software eng": "software engineer",
            "swe": "software engineer",
            "frontend": "frontend engineer",
            "backend": "backend engineer",
            "full stack": "fullstack engineer",
            "fullstack": "fullstack engineer",
            "data sci": "data scientist",
            "machine learning": "machine learning engineer",
            "ml ": "machine learning engineer",
            "product manage": "product manager",
            "product design": "product designer",
            "ux ": "UX designer",
            "devops": "devops engineer",
            "infrastructure": "infrastructure engineer",
            "security": "security engineer",
        }

        for key, value in keyword_map.items():
            if key in role_lower:
                return value

        cleaned = role_lower.replace("intern", "").replace("internship", "").strip()
        return cleaned or "engineer"
