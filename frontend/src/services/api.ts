const API_BASE = '';

export async function checkHealth(): Promise<{ status: string }> {
  const response = await fetch(`${API_BASE}/health`);
  if (!response.ok) throw new Error(`Health check failed: ${response.status}`);
  return response.json();
}

export async function startSearch(params: {
  company: string;
  role: string;
  resume_url?: string;
  linkedin_url?: string;
  company_website?: string;
  job_url?: string;
}): Promise<{ job_id: string }> {
  const response = await fetch(`${API_BASE}/api/search`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  });
  if (!response.ok) throw new Error(`Search failed: ${response.status}`);
  return response.json();
}

export async function getSearchResult(jobId: string): Promise<SearchResult> {
  const response = await fetch(`${API_BASE}/api/search/${jobId}`);
  if (!response.ok) throw new Error(`Fetch failed: ${response.status}`);
  return response.json();
}

/** Start finding more contacts for this campaign (excludes existing). Returns when started (202). */
export async function postMoreLeads(jobId: string): Promise<void> {
  const response = await fetch(`${API_BASE}/api/search/${jobId}/more-leads`, {
    method: 'POST',
  });
  if (response.status === 409) throw new Error('Campaign must be completed first');
  if (!response.ok) throw new Error(`More leads failed: ${response.status}`);
}

export async function generateEmail(params: {
  job_id: string;
  name: string;
}): Promise<EmailDraft> {
  const response = await fetch(`${API_BASE}/api/email/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  });
  if (!response.ok) throw new Error(`Generate failed: ${response.status}`);
  return response.json();
}

export async function editEmail(params: {
  job_id: string;
  name: string;
  subject?: string;
  body?: string;
}): Promise<{ status: string }> {
  const response = await fetch(`${API_BASE}/api/email/edit`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  });
  if (!response.ok) throw new Error(`Edit failed: ${response.status}`);
  return response.json();
}

export async function markOutcome(params: {
  job_id: string;
  name: string;
  email: string;
  sent_at?: string;
  replied?: boolean;
  outcome?: string;
}): Promise<{ status: string }> {
  const response = await fetch(`${API_BASE}/api/email/outcome`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  });
  if (!response.ok) throw new Error(`Outcome update failed: ${response.status}`);
  return response.json();
}

export async function getHistory(): Promise<HistoryEntry[]> {
  const response = await fetch(`${API_BASE}/api/history`);
  if (!response.ok) throw new Error(`History failed: ${response.status}`);
  return response.json();
}

// ── Types ───────────────────────────────────────────────────────────────

export interface Person {
  name: string;
  title: string;
  company: string;
  linkedin_url: string;
  priority_score: number;
  priority_reason: string;
  recent_activity: string;
  profile_summary: string;
  influence_score: number;
  reachability_score: number;
  contact_category: string;
  outreach_angle: string;
  warm_signals: string[];
  discovery_source: string;
  angle_confidence: string;
  has_public_github?: boolean;
}

export interface EmailResult {
  name: string;
  email: string;
  confidence: 'high' | 'medium' | 'low';
  source: string;
  alternative_emails: string[];
}

export interface EmailDraft {
  name: string;
  email: string;
  subject: string;
  body: string;
  tone: string;
  personalization_notes: string;
  sent_at?: string | null;
  replied?: boolean | null;
  outcome?: string | null;
}

export interface ActivityLogEntry {
  timestamp: string;
  message: string;
  type: string;
}

export interface SearchResult {
  job_id: string;
  status: string;
  company: string;
  role: string;
  people: Person[];
  email_results: EmailResult[];
  email_drafts: EmailDraft[];
  activity_log: ActivityLogEntry[];
  error: string | null;
}

export interface HistoryEntry {
  job_id: string;
  company: string;
  role: string;
  status: string;
  people_count: number;
  drafts_count: number;
  sent_count: number;
  replied_count: number;
}

export interface Project {
  name: string;
  description: string;
  location: string;
}

export interface WorkExperience {
  company: string;
  title: string;
  description: string;
  start_date: string;
  end_date: string;
}

export interface ResumeDoc {
  resume_id: string;
  filename: string;
  size: number;
  uploaded_at: string;
}

export interface UserProfileDoc {
  profile_id: string;
  name: string;
  linkedin_url: string;
  resume_url: string;
  portfolio_url: string;
  universities: string[];
  previous_companies: string[];
  skills: string[];
  linkedin_headline: string;
  linkedin_summary: string;
  bio: string;
  active_resume_id: string;
  projects: Project[];
  work_experience: WorkExperience[];
  created_at: string;
  updated_at: string;
}

// ── Profile API ────────────────────────────────────────────────────────

export async function getProfile(): Promise<UserProfileDoc | null> {
  const response = await fetch(`${API_BASE}/api/profile`);
  if (!response.ok) return null;
  const data = await response.json();
  return data || null;
}

export async function saveProfile(
  profile: Partial<UserProfileDoc>,
): Promise<UserProfileDoc> {
  const response = await fetch(`${API_BASE}/api/profile`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(profile),
  });
  if (!response.ok) throw new Error(`Save profile failed: ${response.status}`);
  return response.json();
}

export async function deleteProfile(): Promise<void> {
  const response = await fetch(`${API_BASE}/api/profile`, {
    method: 'DELETE',
  });
  if (!response.ok) throw new Error(`Delete profile failed: ${response.status}`);
}

export async function uploadResume(file: File): Promise<ResumeDoc> {
  const formData = new FormData();
  formData.append('file', file);
  const response = await fetch(`${API_BASE}/api/profile/resumes`, {
    method: 'POST',
    body: formData,
  });
  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(err.detail || `Resume upload failed: ${response.status}`);
  }
  return response.json();
}

export async function listResumes(): Promise<ResumeDoc[]> {
  const response = await fetch(`${API_BASE}/api/profile/resumes`);
  if (!response.ok) return [];
  return response.json();
}

export async function deleteResume(resumeId: string): Promise<void> {
  const response = await fetch(`${API_BASE}/api/profile/resumes/${resumeId}`, {
    method: 'DELETE',
  });
  if (!response.ok) throw new Error(`Delete resume failed: ${response.status}`);
}

export function getResumeDownloadUrl(resumeId: string): string {
  return `${API_BASE}/api/profile/resumes/${resumeId}`;
}
