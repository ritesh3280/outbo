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
}
