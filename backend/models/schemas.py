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
    created_at: str = ""
    updated_at: str = ""
