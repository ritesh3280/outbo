"""Priority Scorer Agent.

Uses OpenAI to score each person 0-1 on how likely they are to help
with an internship/job application at the target company.
"""

import json
import logging

from openai import AsyncOpenAI

from backend.config import settings
from backend.models.schemas import Person

logger = logging.getLogger(__name__)

SCORING_SYSTEM_PROMPT = """You are an expert at evaluating cold outreach targets for job applicants.

Given a list of people at a company and the role being applied for, score each person 0.0 to 1.0 on how useful they would be to contact for a cold outreach email.

Scoring criteria:
- University/campus recruiter → 0.90-1.00 (highest priority for internships)
- Hiring manager on the relevant team → 0.80-0.95
- Technical recruiter on the relevant team → 0.75-0.90
- Engineer on the relevant team who posts about hiring → 0.70-0.85
- General recruiter → 0.60-0.75
- Engineer on the relevant team → 0.50-0.70
- Engineering manager on a different team → 0.40-0.55
- Random employee → 0.10-0.30

Bonus (add up to +0.10):
- Recently posted about hiring, open roles, or interns
- Has "university", "campus", "new grad", "early career" in their title
- Is a manager or lead on the team relevant to the role

For each person, return a JSON array with their score and a short reason.
"""


async def score_people(
    people: list[Person],
    role: str,
    company: str,
) -> list[Person]:
    """Score each person on relevance for the given role.

    Args:
        people: List of Person objects to score.
        role: The role being applied for.
        company: The target company.

    Returns:
        List of Person objects with priority_score and priority_reason populated,
        sorted by priority_score descending.
    """
    if not people:
        return []

    if not settings.openai_api_key:
        logger.warning("No OpenAI API key — using heuristic scoring")
        return _heuristic_score(people, role)

    client = AsyncOpenAI(api_key=settings.openai_api_key)

    people_data = [
        {
            "name": p.name,
            "title": p.title,
            "recent_activity": p.recent_activity[:200] if p.recent_activity else "",
            "profile_summary": p.profile_summary[:200] if p.profile_summary else "",
        }
        for p in people
    ]

    user_prompt = (
        f"Company: {company}\n"
        f"Role being applied for: {role}\n\n"
        f"People to score:\n{json.dumps(people_data, indent=2)}\n\n"
        f"Return a JSON array of objects, one per person, in the same order:\n"
        f'[{{"name": "...", "score": 0.85, "reason": "..."}}]'
    )

    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SCORING_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        content = response.choices[0].message.content or ""
        logger.info("OpenAI scoring response: %s", content[:300])
        data = json.loads(content)

        # Handle {"scores": [...]}, {"results": [...]}, {"people": [...]}, or direct [...]
        if isinstance(data, list):
            scores = data
        elif isinstance(data, dict):
            # Find the first list value in the response
            scores = []
            for key in ("scores", "results", "people", "data"):
                if key in data and isinstance(data[key], list):
                    scores = data[key]
                    break
            if not scores:
                # Try any list value
                for v in data.values():
                    if isinstance(v, list):
                        scores = v
                        break
        else:
            scores = []

        if not isinstance(scores, list):
            logger.warning("Unexpected scoring response format: %s", type(data))
            return _heuristic_score(people, role)

        logger.info("Parsed %d score entries", len(scores))

        # Match scores to people by name for robustness (order may vary)
        score_map: dict[str, dict] = {}
        for entry in scores:
            name = entry.get("name", "").strip().lower()
            if name:
                score_map[name] = entry

        for person in people:
            entry = score_map.get(person.name.strip().lower())
            if entry:
                person.priority_score = max(0.0, min(1.0, float(entry.get("score", 0.5))))
                person.priority_reason = entry.get("reason", "")
            else:
                # Fall back to index-based matching
                idx = people.index(person)
                if idx < len(scores):
                    person.priority_score = max(0.0, min(1.0, float(scores[idx].get("score", 0.5))))
                    person.priority_reason = scores[idx].get("reason", "")

        # Sort by priority score descending
        people.sort(key=lambda p: p.priority_score, reverse=True)

        logger.info(
            "Scored %d people — top: %s (%.2f), bottom: %s (%.2f)",
            len(people),
            people[0].name,
            people[0].priority_score,
            people[-1].name,
            people[-1].priority_score,
        )

        return people

    except Exception as e:
        logger.error("OpenAI scoring failed: %s", e)
        return _heuristic_score(people, role)


def _heuristic_score(people: list[Person], role: str) -> list[Person]:
    """Simple keyword-based scoring when OpenAI is unavailable."""
    role_lower = role.lower()

    for person in people:
        title_lower = person.title.lower()
        score = 0.3  # baseline

        if any(kw in title_lower for kw in ["university", "campus", "new grad", "early career"]):
            score = 0.95
        elif "recruiter" in title_lower or "talent acquisition" in title_lower:
            score = 0.70
        elif "hiring manager" in title_lower:
            score = 0.80
        elif "manager" in title_lower or "lead" in title_lower:
            score = 0.60
        elif "engineer" in title_lower or "developer" in title_lower:
            score = 0.50

        # Bonus for role relevance
        for kw in role_lower.split():
            if kw in title_lower and kw not in ("intern", "internship", "at", "the"):
                score = min(1.0, score + 0.05)

        person.priority_score = round(score, 2)
        person.priority_reason = f"Heuristic score based on title: {person.title}"

    people.sort(key=lambda p: p.priority_score, reverse=True)
    return people
