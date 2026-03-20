from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    company: str
    role: str
    resume_url: Optional[str] = None
    linkedin_url: Optional[str] = None
    company_website: Optional[str] = None
    job_url: Optional[str] = None
    portfolio_url: Optional[str] = None


class Person(BaseModel):
    name: str
    title: str
    company: str
    linkedin_url: str = ""
    priority_score: float = Field(default=0.0, ge=0.0, le=1.0)
    priority_reason: str = ""
    recent_activity: str = ""
    profile_summary: str = ""
    influence_score: float = Field(default=0.0, ge=0.0, le=1.0)
    reachability_score: float = Field(default=0.0, ge=0.0, le=1.0)
    contact_category: str = ""
    outreach_angle: str = ""
    warm_signals: list[str] = []
    discovery_source: str = ""
    angle_confidence: str = ""  # "verified" | "suggested"
    has_public_github: bool = False
    apollo_id: str = ""            # Apollo.io person ID for enrichment
    has_apollo_email: bool = False  # Apollo confirmed email exists for this person


class EmailConfidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class EmailResult(BaseModel):
    name: str
    email: str
    confidence: EmailConfidence = EmailConfidence.LOW
    source: str = ""
    alternative_emails: list[str] = []


class EmailDraft(BaseModel):
    name: str
    email: str
    subject: str
    body: str
    tone: str = "warm-professional"
    personalization_notes: str = ""
    sent_at: Optional[str] = None       # ISO datetime string when marked sent
    replied: Optional[bool] = None      # True/False/None
    outcome: Optional[str] = None       # "no_response" | "replied" | "referral" | "interview"


class SearchStatus(str, Enum):
    PENDING = "pending"
    FINDING_PEOPLE = "finding_people"
    FINDING_EMAILS = "finding_emails"
    RESEARCHING = "researching"
    GENERATING_EMAILS = "generating_emails"
    COMPLETED = "completed"
    FAILED = "failed"


class ActivityLogEntry(BaseModel):
    timestamp: str
    message: str
    type: str = "status"


class SearchResult(BaseModel):
    job_id: str
    status: SearchStatus = SearchStatus.PENDING
    company: str = ""
    role: str = ""
    people: list[Person] = []
    email_results: list[EmailResult] = []
    email_drafts: list[EmailDraft] = []
    activity_log: list[ActivityLogEntry] = []
    error: Optional[str] = None
    # Stored for on-demand email generation (not sent to client in some flows if desired)
    company_context: Optional[dict] = None
    job_context: Optional[dict] = None
    user_info: Optional[str] = None
    user_profile_data: Optional[dict] = None


class Project(BaseModel):
    name: str
    description: str = ""
    location: str = ""  # e.g. "HackMIT 2024", "MIT CSAIL", "Personal project"


class WorkExperience(BaseModel):
    company: str
    title: str
    description: str = ""
    start_date: str = ""  # e.g. "2022" or "Jun 2022"
    end_date: str = ""    # empty = present/current


class UserProfileDoc(BaseModel):
    """Persistent user profile — saved once, reused across all campaigns."""
    profile_id: str = "default"
    name: str = ""
    linkedin_url: str = ""
    resume_url: str = ""
    universities: list[str] = []
    previous_companies: list[str] = []
    skills: list[str] = []
    linkedin_headline: str = ""
    linkedin_summary: str = ""
    portfolio_url: str = ""
    bio: str = ""  # Rich summary scraped from portfolio/resume — used in email generation
    active_resume_id: str = ""  # ID of the resume to use in email generation
    projects: list[Project] = []
    work_experience: list[WorkExperience] = []
    created_at: str = ""
    updated_at: str = ""
