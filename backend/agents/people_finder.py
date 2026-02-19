"""People Finder Agent.

Wide-net pipeline (when Serper API key is set):
  5 Serper queries (~$0.005) → 30–40 candidates → hard filter → validation → scoring → diversity selection → 6–8 contacts.

Fallback (no Serper): 2 Browser Use tasks + validation + scoring.
"""

import asyncio
import json
import logging
import re

from openai import AsyncOpenAI
from pydantic import BaseModel

from backend.config import settings
from backend.models.schemas import Person
from backend.tools.browser import BrowserTool
from backend.tools.serper import search as serper_search
from backend.agents.priority_scorer import score_people
from backend.agents.job_analyzer import build_search_queries

logger = logging.getLogger(__name__)

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


def hard_filter(person: LinkedInPerson, role: str) -> bool:
    """Remove people who are unlikely to reply to intern cold emails. Deterministic, no LLM."""
    title = person.title.lower()
    if any(kw in title for kw in EXCLUDE_KEYWORDS):
        return False
    if any(dept in title for dept in EXCLUDE_DEPARTMENTS):
        if "recruit" not in title:
            return False
    return True


def select_final_contacts(scored_people: list[Person], target: int = 8) -> list[Person]:
    """Pick final contacts with diversity: at least 2 recruiters, 3 engineers, 1 manager."""
    scored_people = sorted(scored_people, key=lambda p: p.priority_score, reverse=True)

    selected: list[Person] = []
    categories: dict[str, int] = {"recruiter": 0, "engineer": 0, "manager": 0}
    TARGETS = {"recruiter": 2, "engineer": 3, "manager": 1}

    def categorize(title: str) -> str:
        t = title.lower()
        if "recruit" in t or "talent" in t:
            return "recruiter"
        if "manager" in t or "lead" in t:
            return "manager"
        return "engineer"

    for person in scored_people:
        cat = categorize(person.title)
        if categories[cat] < TARGETS.get(cat, 0):
            selected.append(person)
            categories[cat] += 1
        if len(selected) >= target:
            break

    for person in scored_people:
        if person not in selected and len(selected) < target:
            selected.append(person)

    return selected


class PeopleFinder:
    """Finds relevant people at a company. Uses Serper (wide net) when key is set, else Browser Use."""

    def __init__(self, browser: BrowserTool | None = None):
        self.browser = browser or BrowserTool()

    # ── Serper: wide net (5 queries, ~$0.005) ──────────────────────────────

    def _serper_queries(self, company: str, team_keyword: str, job_context: dict | None = None) -> list[str]:
        """Five targeted queries. If job_context provided, use build_search_queries for laser targeting."""
        if job_context:
            return build_search_queries(company, job_context)
        return [
            f'site:linkedin.com/in "at {company}" "university recruiter" OR "campus recruiter" OR "early career recruiter"',
            f'site:linkedin.com/in "at {company}" "recruiter" OR "talent acquisition"',
            f'site:linkedin.com/in "at {company}" "{team_keyword}"',
            f'site:linkedin.com/in "at {company}" "engineering manager" OR "tech lead"',
            f'site:linkedin.com/in "at {company}" "hiring" OR "intern" OR "internship"',
        ]

    @staticmethod
    def _parse_linkedin_from_serper(result) -> LinkedInPerson | None:
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
        return LinkedInPerson(
            name=name,
            title=job_title,
            linkedin_url=link,
            recent_activity=snippet,
        )

    async def search_serper_wide(
        self, company: str, role: str, job_context: dict | None = None
    ) -> list[LinkedInPerson]:
        """Run 5 Serper queries concurrently, return aggregated LinkedIn profiles."""
        team_keyword = self._extract_team_keyword(role)
        queries = self._serper_queries(company, team_keyword, job_context)
        logger.info("Running %d Serper queries for %s...", len(queries), company)
        tasks = [serper_search(q, num=10) for q in queries]
        results_per_query = await asyncio.gather(*tasks)
        raw: list[LinkedInPerson] = []
        for results in results_per_query:
            for r in results:
                p = self._parse_linkedin_from_serper(r)
                if p:
                    raw.append(p)
        deduped = self._deduplicate(raw)
        logger.info("Serper: %d raw → %d unique after dedup", len(raw), len(deduped))
        return deduped

    # ── Browser Use search (fallback) ─────────────────────────────────────

    async def search_google_for_linkedin(
        self, company: str, query: str, max_results: int = 10
    ) -> list[LinkedInPerson]:
        """Search Google to find LinkedIn profiles at a company."""
        # Use "at {company}" to match LinkedIn's employment format and avoid name matches
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

    # ── Validation ───────────────────────────────────────────────────────

    async def _validate_person_works_at_company(
        self, person: LinkedInPerson, company: str
    ) -> bool:
        """Use OpenAI to validate if a person actually works at the company.
        
        Catches false positives from search (e.g., people with company name in their name).
        Returns True if the person works at the company, False otherwise.
        """
        if not settings.openai_api_key:
            return True  # Skip validation in stub mode

        client = AsyncOpenAI(api_key=settings.openai_api_key)
        
        prompt = f"""Given this LinkedIn profile information, does this person currently work at "{company}"?

Name: {person.name}
Title: {person.title}
Profile snippet: {person.recent_activity[:300]}

Answer with ONLY "yes" or "no". 
- Answer "yes" if the title or snippet indicates they work/worked at {company}
- Answer "no" if they just have a similar name or no clear connection to {company}"""

        try:
            response = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=5,
            )
            answer = response.choices[0].message.content.strip().lower()
            is_valid = "yes" in answer
            
            if not is_valid:
                logger.info("Filtered out %s (title: %s) - doesn't work at %s", 
                           person.name, person.title, company)
            
            return is_valid
        except Exception as e:
            logger.warning("Validation failed for %s: %s", person.name, e)
            return True  # Default to keeping on error

    async def _filter_valid_people(
        self, people: list[LinkedInPerson], company: str
    ) -> list[LinkedInPerson]:
        """Filter out false positives using OpenAI validation."""
        if not people:
            return []
        
        logger.info("Validating %d profiles...", len(people))
        validation_tasks = [
            self._validate_person_works_at_company(p, company) for p in people
        ]
        validations = await asyncio.gather(*validation_tasks)
        
        valid_people = [p for p, is_valid in zip(people, validations) if is_valid]
        filtered_count = len(people) - len(valid_people)
        
        if filtered_count > 0:
            logger.info("Filtered out %d/%d false positives", filtered_count, len(people))
        
        return valid_people

    # ── Main pipeline ────────────────────────────────────────────────────

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
    ) -> list[Person]:
        """Find relevant people at a company for a given role.

        With Serper API: 5 queries → 30–40 raw → hard filter → validation → scoring → diversity selection.
        job_context from a job posting URL makes queries and scoring team-specific.
        exclude_linkedin_urls: optional set of LinkedIn URLs (or normalized) to skip (e.g. already have).
        """
        exclude = set()
        if exclude_linkedin_urls:
            for u in exclude_linkedin_urls:
                n = PeopleFinder._normalize_linkedin_url(u)
                if n:
                    exclude.add(n)
        if settings.serper_api_key:
            return await self._find_people_serper(company, role, target_count, job_context, exclude)
        return await self._find_people_browser(company, role, target_count, job_context, exclude)

    async def _find_people_serper(
        self,
        company: str,
        role: str,
        target_count: int,
        job_context: dict | None = None,
        exclude_urls: set[str] | None = None,
    ) -> list[Person]:
        """Wide-net pipeline: Serper → hard filter → validation → scoring → diversity selection."""
        all_people = await self.search_serper_wide(company, role, job_context)
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
            )
            for lp in all_people
        ]

        logger.info("Scoring %d people for reply likelihood...", len(people))
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
    ) -> list[Person]:
        """Fallback: Browser Use (2 tasks) → validation → scoring → diversity selection."""
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
        all_people = await self._filter_valid_people(all_people, company)

        people = [
            Person(
                name=lp.name,
                title=lp.title,
                company=company,
                linkedin_url=lp.linkedin_url,
                recent_activity=lp.recent_activity,
                profile_summary=lp.recent_activity,
            )
            for lp in all_people
        ]

        logger.info("Scoring %d people for relevance to '%s'...", len(people), role)
        scored = await score_people(people, role, company, job_context=job_context)
        final = select_final_contacts(scored, target=target_count)
        logger.info("People finder complete: %d final contacts for %s", len(final), company)
        return final

    # ── Helpers ──────────────────────────────────────────────────────────

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
        """Remove duplicate people based on LinkedIn URL or name."""
        seen_urls: set[str] = set()
        seen_names: set[str] = set()
        unique: list[LinkedInPerson] = []

        for p in people:
            url_key = p.linkedin_url.rstrip("/").lower() if p.linkedin_url else ""
            name_key = p.name.strip().lower()

            if url_key and url_key in seen_urls:
                continue
            if name_key and name_key in seen_names:
                continue

            if url_key:
                seen_urls.add(url_key)
            if name_key:
                seen_names.add(name_key)
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
