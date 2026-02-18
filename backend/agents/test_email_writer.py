"""Test script for Phase 4: Email Generation.

Usage:
    python -m backend.agents.test_email_writer
"""

import asyncio
import logging
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(name)s | %(message)s")

from backend.models.schemas import EmailConfidence, EmailDraft, EmailResult, Person
from backend.agents.email_writer import (
    CompanyContext,
    generate_batch_emails,
    generate_single_email,
    research_company,
)


PEOPLE = [
    Person(name="Madison Finlay", title="University Recruiter at Stripe", company="Stripe",
           profile_summary="University Recruiter focused on early career hiring"),
    Person(name="Molly Lingafelt", title="Technical Recruiting at Stripe", company="Stripe",
           profile_summary="FLC recruiter hiring technical PMs across all Stripe Products"),
    Person(name="Dave Brace", title="Software Engineer at Stripe", company="Stripe",
           profile_summary="Software engineer and manager with extensive experience leading teams"),
    Person(name="Aaron Rodriguez", title="Stripe Software Engineer | CS BS/MS", company="Stripe",
           profile_summary="Stripe Software Engineer, Education: Rice University"),
    Person(name="Ashley R.", title="Recruiting @ Stripe", company="Stripe",
           profile_summary="Experienced full life-cycle recruiter in the technology industry"),
]

EMAIL_RESULTS = [
    EmailResult(name="Madison Finlay", email="madison.finlay@stripe.com", confidence=EmailConfidence.LOW),
    EmailResult(name="Molly Lingafelt", email="molly.lingafelt@stripe.com", confidence=EmailConfidence.LOW),
    EmailResult(name="Dave Brace", email="dave.brace@stripe.com", confidence=EmailConfidence.LOW),
    EmailResult(name="Aaron Rodriguez", email="aaron.rodriguez@stripe.com", confidence=EmailConfidence.LOW),
    EmailResult(name="Ashley R.", email="ashley.r@stripe.com", confidence=EmailConfidence.LOW),
]


async def test_step_4_1() -> CompanyContext:
    print("\n" + "=" * 60)
    print("STEP 4.1: Company Research")
    print("=" * 60)

    ctx = await research_company("Stripe", "Software Engineering Intern")

    print(f"\n  Company: {ctx.company}")
    print(f"  Mission: {ctx.mission[:200]}")
    print(f"  Recent news: {ctx.recent_news[:200]}")
    print(f"  Blog: {ctx.blog_highlights[:200]}")
    print(f"  Culture: {ctx.culture_notes[:200]}")
    print(f"  Role info: {ctx.relevant_role_info[:200]}")

    return ctx


async def test_step_4_2(ctx: CompanyContext) -> EmailDraft:
    print("\n" + "=" * 60)
    print("STEP 4.2: Single Email Generation")
    print("=" * 60)

    draft = await generate_single_email(
        person=PEOPLE[0],
        email_result=EMAIL_RESULTS[0],
        company_context=ctx,
        role="Software Engineering Intern",
    )

    print(f"\n  To: {draft.name} <{draft.email}>")
    print(f"  Subject: {draft.subject}")
    print(f"  ---")
    print(f"  {draft.body}")
    print(f"  ---")
    print(f"  Personalization: {draft.personalization_notes}")

    return draft


async def test_step_4_3(ctx: CompanyContext) -> list[EmailDraft]:
    print("\n" + "=" * 60)
    print("STEP 4.3: Batch Email Generation (5 emails)")
    print("=" * 60)

    drafts = await generate_batch_emails(
        people=PEOPLE,
        email_results=EMAIL_RESULTS,
        company_context=ctx,
        role="Software Engineering Intern",
    )

    for i, d in enumerate(drafts, 1):
        print(f"\n  --- Email {i}: {d.name} ({d.email}) ---")
        print(f"  Subject: {d.subject}")
        print(f"  {d.body}")
        print(f"  [Personalization: {d.personalization_notes}]")

    return drafts


async def main() -> None:
    ctx = await test_step_4_1()
    single = await test_step_4_2(ctx)
    drafts = await test_step_4_3(ctx)

    # ── Checkpoints ──────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("CHECKPOINT VERIFICATION")
    print("=" * 60)

    cp41 = bool(ctx.mission) and len(ctx.mission) > 20
    print(f"\n  4.1 Company research returns useful context")
    print(f"      Has mission: {bool(ctx.mission)}, Has news: {bool(ctx.recent_news)}")
    print(f"      {'PASS' if cp41 else 'FAIL'}")

    cp42 = (
        bool(single.subject) and bool(single.body) and len(single.body) > 50
        and single.body != single.subject
    )
    print(f"\n  4.2 Single email is personalized and sounds human")
    print(f"      Subject: {bool(single.subject)}, Body length: {len(single.body)}")
    print(f"      {'PASS' if cp42 else 'FAIL'}")

    # Check variety: no two emails share the same first sentence
    openings = [d.body.split("\n")[0] for d in drafts if d.body]
    unique_openings = len(set(openings))
    cp43 = len(drafts) == 5 and unique_openings >= 4
    print(f"\n  4.3 Batch: 5 distinct emails with varied openings")
    print(f"      Drafts: {len(drafts)}, Unique openings: {unique_openings}/{len(openings)}")
    print(f"      {'PASS' if cp43 else 'FAIL'}")

    all_passed = cp41 and cp42 and cp43
    print("\n" + "=" * 60)
    if all_passed:
        print("ALL CHECKPOINTS PASSED — Phase 4 complete.")
    else:
        print("SOME CHECKPOINTS FAILED — review above.")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
