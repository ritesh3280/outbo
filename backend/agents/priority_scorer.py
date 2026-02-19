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

SCORING_SYSTEM_PROMPT = """You are ranking people by how likely they are to respond to a cold email from a college student applying for a role at a company.

Score each person 0-100 based on these criteria:

REPLY LIKELIHOOD (most important):
- Campus/university recruiters → 90-100 (this is literally their job)
- Early career / new grad recruiters → 85-95
- General technical recruiters → 70-85
- Junior engineers (1-3 years exp, recent grads) → 60-80 (they remember applying recently, most empathetic)
- Mid-level engineers on the relevant team → 40-60
- Engineering managers → 30-50 (busy but can refer directly)
- Senior/staff engineers → 20-40 (busy, less likely to reply)

NEGATIVE SIGNALS (reduce score):
- Title suggests 10+ years experience → -20
- No connection to recruiting or the target team → -30
- Title is vague or unclear → -15

BONUS SIGNALS (increase score):
- Recent activity mentions hiring, interns, or open roles → +15
- Title includes "university", "campus", "early career" → +20
- Title matches the exact team for the role → +10
- Profile snippet mentions mentoring or helping students → +10

Return a JSON array of objects, one per person, in the same order: [{"name": "...", "score": N, "reason": "..."}]. Use score 0-100.
"""


def _scoring_system_prompt(job_context: dict | None) -> str:
    """Base prompt; when job_context provided, add role-specific context."""
    base = SCORING_SYSTEM_PROMPT
    if not job_context or not any(job_context.get(k) for k in ("team", "department", "tech_stack")):
        return base
    return base + """

When job context is provided below, use it to rank by relevance to THIS specific role:
- Recruiter who handles this department → 90-100
- Engineer ON this exact team → 80-95
- Engineer using the same tech stack → 70-85
- Engineering manager of this team → 75-90
- General recruiter → 60-75
- Engineer on a different team → 30-50
- Unrelated department → 0-10
"""


async def score_people(
    people: list[Person],
    role: str,
    company: str,
    job_context: dict | None = None,
) -> list[Person]:
    """Score each person on relevance for the given role.

    Args:
        people: List of Person objects to score.
        role: The role being applied for.
        company: The target company.
        job_context: Optional dict from job_analyzer (team, department, tech_stack, etc.).

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

    role_block = (
        f"Company: {company}\n"
        f"Role being applied for: {role}\n\n"
    )
    if job_context and any(job_context.get(k) for k in ("team", "department", "tech_stack", "key_requirements")):
        role_block += (
            "ROLE CONTEXT (from job posting):\n"
            f"- Team: {job_context.get('team', '')}\n"
            f"- Department: {job_context.get('department', '')}\n"
            f"- Tech stack: {job_context.get('tech_stack', [])}\n"
            f"- Key requirements: {job_context.get('key_requirements', [])}\n\n"
        )

    user_prompt = (
        f"{role_block}"
        f"People to score:\n{json.dumps(people_data, indent=2)}\n\n"
        f"Return a JSON array of objects, one per person, in the same order. Use score 0-100:\n"
        f'[{{"name": "...", "score": 85, "reason": "..."}}]'
    )

    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _scoring_system_prompt(job_context)},
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
                raw_score = float(entry.get("score", 50))
                person.priority_score = max(0.0, min(1.0, raw_score / 100.0))
                person.priority_reason = entry.get("reason", "")
            else:
                idx = people.index(person)
                if idx < len(scores):
                    raw_score = float(scores[idx].get("score", 50))
                    person.priority_score = max(0.0, min(1.0, raw_score / 100.0))
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
