"""People Finder Agent.

Searches LinkedIn (via Google dorking) to find relevant contacts
at a target company for a given role.

Credit budget per search:
  - 2 Browser Use tasks (recruiter search + engineer search, concurrent)
  - N OpenAI validation calls (one per profile, concurrent, fast model)
  - 1 OpenAI call for priority scoring
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
from backend.agents.priority_scorer import score_people

logger = logging.getLogger(__name__)


class LinkedInPerson(BaseModel):
    name: str = ""
    title: str = ""
    linkedin_url: str = ""
    recent_activity: str = ""


class PeopleFinder:
    """Finds relevant people at a company using Google → LinkedIn + Firecrawl enrichment."""

    def __init__(self, browser: BrowserTool | None = None):
        self.browser = browser or BrowserTool()

    # ── Search methods ───────────────────────────────────────────────────

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

    async def find_people(
        self,
        company: str,
        role: str,
        target_count: int = 8,
    ) -> list[Person]:
        """Find relevant people at a company for a given role.

        Pipeline (optimized for minimal Browser Use credits):
        1. Two concurrent Google searches (recruiter + engineer) — 2 Browser Use tasks
        2. Interleave + deduplicate
        3. OpenAI validation to filter false positives (concurrent)
        4. OpenAI priority scoring
        """
        team_keyword = self._extract_team_keyword(role)

        # Step 1: Run both searches concurrently (2 Browser Use tasks)
        logger.info("Searching for %s recruiters and %s %s (concurrent)...", company, company, team_keyword)
        recruiter_task = self.search_google_for_linkedin(company, "recruiter")
        engineer_task = self.search_google_for_linkedin(company, team_keyword)

        recruiter_results, engineer_results = await asyncio.gather(
            recruiter_task, engineer_task
        )
        logger.info("Found %d recruiters + %d engineers/managers", len(recruiter_results), len(engineer_results))

        # Step 2: Fallback if either search returned too few results
        if len(recruiter_results) < 2 and len(engineer_results) < 2:
            logger.info("Too few results, trying LinkedIn direct search...")
            fallback_results = await self.search_linkedin(company, "recruiter")
            recruiter_results.extend(fallback_results)

        # Step 3: Interleave for balanced mix, then dedup
        interleaved: list[LinkedInPerson] = []
        max_len = max(len(recruiter_results), len(engineer_results))
        for i in range(max_len):
            if i < len(recruiter_results):
                interleaved.append(recruiter_results[i])
            if i < len(engineer_results):
                interleaved.append(engineer_results[i])

        all_people = self._deduplicate(interleaved)
        logger.info("After interleave + dedup: %d unique people", len(all_people))

        # Step 3.5: Validate people actually work at the company (OpenAI post-filter)
        all_people = await self._filter_valid_people(all_people, company)
        logger.info("After validation: %d confirmed employees", len(all_people))

        # Trim to target count
        all_people = all_people[:target_count]

        # Step 4: Build Person objects.
        # Enrichment comes from Google search snippets (captured in recent_activity).
        # LinkedIn profile scraping is skipped — Firecrawl blocks LinkedIn and
        # Browser Use per-profile is too expensive. Deeper personalization context
        # comes from company research in Phase 4 instead.
        people: list[Person] = []
        for lp in all_people:
            people.append(Person(
                name=lp.name,
                title=lp.title,
                company=company,
                linkedin_url=lp.linkedin_url,
                recent_activity=lp.recent_activity,
                profile_summary=lp.recent_activity,
            ))

        # Step 5: Priority scoring via OpenAI
        logger.info("Scoring %d people for relevance to '%s'...", len(people), role)
        scored = await score_people(people, role, company)
        logger.info("People finder complete: %d scored people for %s", len(scored), company)
        return scored

    # ── Helpers ───────────────────────────────────────────────────────────

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
