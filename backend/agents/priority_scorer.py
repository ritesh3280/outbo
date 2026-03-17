"""Priority Scorer Agent.

Uses OpenAI to score each person on two axes:
  - Influence (0-100): how much hiring power they have for this specific role
  - Reachability (0-100): how likely they are to respond to a cold email

Also generates a specific outreach_angle for each contact.
Composite: priority_score = 0.4 * influence + 0.6 * reachability (favors reachability).
"""

import json
import logging

from openai import AsyncOpenAI

from backend.config import settings
from backend.models.schemas import Person

logger = logging.getLogger(__name__)

SCORING_SYSTEM_PROMPT = """You are scoring people for a cold outreach campaign from a student applying for a job/internship.

Score each person on TWO axes (0-100 each):

INFLUENCE (how much hiring power they have for THIS specific role):
- Hiring manager for the role → 90-100
- Team member who shared or promoted the job listing → 80-95
- Engineer on the same team → 70-85
- Recruiter tagged on or associated with the job posting → 65-80
- Engineering manager of a related team → 55-70
- General recruiter at the company → 45-65
- Engineer on a different team → 25-45
- Unrelated department → 0-20

REACHABILITY (how likely they are to respond to a cold email from a student):
- Recently posted about hiring or growing their team → 85-100
- Campus/university recruiter (responding is their job) → 80-95
- Active on social media (LinkedIn posts, blogs) → 70-90
- Recently joined the company (< 1 year, empathetic to job seekers) → 65-85
- Junior/mid level (more empathetic to students) → 60-80
- Has public email or active GitHub → 70-85
- Senior/staff with no recent activity → 20-40
- Executive level → 10-25

WARM SIGNAL BONUSES (apply to reachability):
- Same university as the applicant → +15
- Previously worked at same company as the applicant → +12
- Shared the job posting → +20
- Posted about hiring recently → +10

CATEGORY — classify each person as one of:
- "hiring_manager" — the hiring manager or their direct report for this role
- "team_member" — engineer/IC on the same team as the role
- "recruiter" — any recruiter or talent acquisition person
- "warm_connection" — shares a university, previous company, or other warm signal with the applicant
- "general" — none of the above

OUTREACH ANGLE — for each person, write a specific, actionable reason to contact them and a suggested approach angle. Be concrete: reference their title, team, shared background, or activity.
Examples:
- "Sarah is on Stripe's Payments team and went to UVA like you — mention your shared background and interest in payments infrastructure."
- "Raj shared the exact job posting on LinkedIn. Reference his post and express genuine interest in the role."
- "Campus recruiter who posts about intern hiring monthly. Be direct about the specific role."

ANGLE CONFIDENCE — classify each outreach angle as:
- "verified" — the angle ONLY references details directly present in the provided data (their title, recent_activity, profile_summary, warm_signals, or discovery_source). You can point to specific evidence.
- "suggested" — the angle involves inference, assumption, or details not directly present in the provided data. For example, assuming someone was a former intern when no evidence says so.
Be honest: if you reference something not in the data, mark it "suggested".

Return a JSON array: [{"name": "...", "influence": N, "reachability": N, "category": "...", "reason": "...", "outreach_angle": "...", "angle_confidence": "verified" or "suggested"}]"""


def _scoring_system_prompt(job_context: dict | None) -> str:
    """Base prompt; when job_context provided, add role-specific context."""
    base = SCORING_SYSTEM_PROMPT
    if not job_context or not any(job_context.get(k) for k in ("team", "department", "tech_stack")):
        return base
    return base + """

When job context is provided below, use it to more precisely assess influence:
- Recruiter who handles this department → influence 75-95
- Engineer ON this exact team → influence 80-95
- Engineer using the same tech stack → influence 65-85
- Engineering manager of this team → influence 85-100
- General recruiter → influence 45-65
- Engineer on a different team → influence 25-45
- Unrelated department → influence 0-10
"""


async def score_people(
    people: list[Person],
    role: str,
    company: str,
    job_context: dict | None = None,
) -> list[Person]:
    """Score each person on influence, reachability, and generate outreach angles.

    Args:
        people: List of Person objects to score (may have warm_signals and discovery_source set).
        role: The role being applied for.
        company: The target company.
        job_context: Optional dict from job_analyzer (team, department, tech_stack, etc.).

    Returns:
        List of Person objects with priority_score, influence_score, reachability_score,
        contact_category, outreach_angle, and priority_reason populated,
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
            "warm_signals": p.warm_signals if p.warm_signals else [],
            "discovery_source": p.discovery_source or "general",
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
            f"- Key requirements: {job_context.get('key_requirements', [])}\n"
            f"- Hiring manager: {job_context.get('hiring_manager', '')}\n\n"
        )

    user_prompt = (
        f"{role_block}"
        f"People to score:\n{json.dumps(people_data, indent=2)}\n\n"
        f"Return a JSON array of objects, one per person, in the same order:\n"
        f'[{{"name": "...", "influence": 85, "reachability": 70, "category": "team_member", '
        f'"reason": "...", "outreach_angle": "...", "angle_confidence": "verified"}}]'
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
            scores = []
            for key in ("scores", "results", "people", "data"):
                if key in data and isinstance(data[key], list):
                    scores = data[key]
                    break
            if not scores:
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

        # Match scores to people by name
        score_map: dict[str, dict] = {}
        for entry in scores:
            name = entry.get("name", "").strip().lower()
            if name:
                score_map[name] = entry

        for person in people:
            entry = score_map.get(person.name.strip().lower())
            if not entry:
                idx = people.index(person)
                if idx < len(scores):
                    entry = scores[idx]

            if entry:
                influence = max(0.0, min(100.0, float(entry.get("influence", 50))))
                reachability = max(0.0, min(100.0, float(entry.get("reachability", 50))))

                # Apply warm signal bonuses to reachability
                for signal in person.warm_signals:
                    if signal.startswith("same_university"):
                        reachability = min(100, reachability + 15)
                    elif signal.startswith("shared_company"):
                        reachability = min(100, reachability + 12)
                    elif signal == "shared_job_posting":
                        reachability = min(100, reachability + 20)
                    elif signal == "posted_about_hiring":
                        reachability = min(100, reachability + 10)
                    elif signal == "recently_joined":
                        reachability = min(100, reachability + 8)

                composite = 0.4 * influence + 0.6 * reachability
                person.priority_score = max(0.0, min(1.0, composite / 100.0))
                person.influence_score = influence / 100.0
                person.reachability_score = reachability / 100.0
                person.priority_reason = entry.get("reason", "")
                person.contact_category = entry.get("category", "general")
                person.outreach_angle = entry.get("outreach_angle", "")
                person.angle_confidence = entry.get("angle_confidence", "suggested")

        # Sort by composite score descending
        people.sort(key=lambda p: p.priority_score, reverse=True)

        logger.info(
            "Scored %d people — top: %s (%.2f, inf=%.2f, reach=%.2f), bottom: %s (%.2f)",
            len(people),
            people[0].name, people[0].priority_score,
            people[0].influence_score, people[0].reachability_score,
            people[-1].name, people[-1].priority_score,
        )

        return people

    except Exception as e:
        logger.error("OpenAI scoring failed: %s", e)
        return _heuristic_score(people, role)


def _heuristic_score(people: list[Person], role: str) -> list[Person]:
    """Keyword-based dual-axis scoring when OpenAI is unavailable."""
    role_lower = role.lower()

    for person in people:
        title_lower = person.title.lower()
        influence = 30.0
        reachability = 40.0

        # Influence heuristics
        if "hiring manager" in title_lower:
            influence = 90.0
        elif any(kw in title_lower for kw in ["university", "campus", "new grad", "early career"]):
            influence = 55.0
            reachability = 90.0
        elif "recruiter" in title_lower or "talent acquisition" in title_lower:
            influence = 55.0
            reachability = 65.0
        elif "manager" in title_lower or "lead" in title_lower:
            influence = 65.0
            reachability = 45.0
        elif "engineer" in title_lower or "developer" in title_lower:
            influence = 45.0
            reachability = 55.0

        # Reachability boost for role keyword match
        for kw in role_lower.split():
            if kw in title_lower and kw not in ("intern", "internship", "at", "the"):
                influence = min(100, influence + 10)

        # Warm signal bonuses
        for signal in person.warm_signals:
            if signal.startswith("same_university"):
                reachability = min(100, reachability + 15)
            elif signal.startswith("shared_company"):
                reachability = min(100, reachability + 12)
            elif signal == "posted_about_hiring":
                reachability = min(100, reachability + 10)
            elif signal == "recently_joined":
                reachability = min(100, reachability + 8)

        composite = 0.4 * influence + 0.6 * reachability
        person.priority_score = round(min(1.0, composite / 100.0), 2)
        person.influence_score = round(influence / 100.0, 2)
        person.reachability_score = round(reachability / 100.0, 2)
        person.priority_reason = f"Heuristic score based on title: {person.title}"

        # Category heuristic
        if "recruiter" in title_lower or "talent" in title_lower:
            person.contact_category = "recruiter"
        elif "manager" in title_lower or "lead" in title_lower:
            person.contact_category = "hiring_manager" if "hiring" in title_lower else "general"
        elif person.warm_signals:
            person.contact_category = "warm_connection"
        else:
            person.contact_category = "general"

        person.outreach_angle = f"Reach out to {person.name} ({person.title}) about the {role} role."
        person.angle_confidence = "suggested"

    people.sort(key=lambda p: p.priority_score, reverse=True)
    return people
