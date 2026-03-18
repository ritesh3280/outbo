# OutreachBot (outbo) - Architecture & Documentation

## What Is This?

**OutreachBot** is an AI-powered cold outreach automation tool for job/internship seekers. Given a company, role, and optionally a job posting URL and your LinkedIn/resume, it:

1. Analyzes the job posting to identify the exact team, tech stack, hiring contacts, email domain, and reporting structure
2. Loads your persistent profile (or extracts it from LinkedIn/resume) for warm-path matching
3. Scrapes company team/about pages to extract named contacts as seed candidates (Step 0.7)
4. Finds 6-8 high-value contacts using tiered, context-aware search queries (with quality threshold filtering)
5. Scores each contact on two axes: **influence** (hiring power) and **reachability** (likelihood to respond), with verifiable GitHub presence as a concrete bonus signal
6. Discovers email addresses with confidence levels (verified, pattern-matched, or guessed) — boosted by domain extracted from job posting
7. Researches the company for personalization context (reusing pages already scraped in Step 0.7)
8. Generates personalized cold emails with specific outreach angles per contact
9. Tracks outreach outcomes — sent date, replies, and results per contact for future learning

The philosophy: **precision over volume**. A reachable person with moderate influence beats an unreachable person with high influence. Six strong contacts beat eight with filler. Outreach angles are labeled as "verified" or "suggested" so users know what's backed by data vs. inference.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| **Frontend** | React 19 + TypeScript + Tailwind CSS 4 + Vite |
| **Backend** | Python + FastAPI + Uvicorn |
| **Database** | MongoDB (async via Motor), with in-memory fallback. Collections: `jobs`, `profiles` |
| **LLM** | OpenAI (`gpt-4o-mini`) |
| **Browser Automation** | Browser Use Cloud SDK |
| **Web Scraping** | Firecrawl API |
| **Search** | Serper (Google Search API) |
| **Email Verification** | dnspython (MX record checks) |
| **Observability** | Langfuse (optional) |

---

## Directory Structure

```
outbo/
├── backend/
│   ├── agents/                         # AI agent pipeline
│   │   ├── orchestrator.py             # Main pipeline controller
│   │   ├── people_finder.py            # Contact discovery (tiered queries + dynamic selection)
│   │   ├── email_finder.py             # Email address discovery
│   │   ├── email_writer.py             # Email generation + company research
│   │   ├── priority_scorer.py          # Dual-axis scoring (influence × reachability)
│   │   ├── job_analyzer.py             # Job posting analysis + dynamic query builder
│   │   ├── user_profile_extractor.py   # Applicant profile extraction (warm-path matching)
│   │   └── test_*.py                   # Agent tests
│   ├── tools/                          # External API wrappers
│   │   ├── browser.py                  # Browser Use Cloud SDK
│   │   ├── scraper.py                  # Firecrawl SDK
│   │   ├── serper.py                   # Google Search via Serper
│   │   └── verifier.py                 # DNS MX record validation
│   ├── db/
│   │   └── mongodb.py                  # MongoDB async driver
│   ├── models/
│   │   └── schemas.py                  # Pydantic data models
│   ├── config.py                       # Settings / env vars
│   ├── main.py                         # FastAPI application & routes
│   └── requirements.txt                # Python dependencies
│
├── frontend/
│   ├── src/
│   │   ├── pages/
│   │   │   ├── SearchPage.tsx          # New campaign form (auto-uses saved profile)
│   │   │   ├── ResultsPage.tsx         # Live results + contacts
│   │   │   ├── HistoryPage.tsx         # Past campaigns list
│   │   │   └── ProfilePage.tsx         # Persistent user profile management
│   │   ├── components/
│   │   │   ├── DashboardLayout.tsx     # Sidebar + layout shell
│   │   │   ├── ActivityFeed.tsx        # Live agent activity log
│   │   │   └── ContactCard.tsx         # Per-contact card (scores, warm signals, outreach angle, email editor)
│   │   ├── hooks/
│   │   │   └── useWebSocket.ts         # WebSocket hook for live updates
│   │   ├── services/
│   │   │   └── api.ts                  # API client + TypeScript types
│   │   ├── App.tsx                     # Root component + routing
│   │   ├── main.tsx                    # Entry point
│   │   └── index.css                   # Tailwind imports
│   ├── vite.config.ts                  # Vite config + API proxy
│   ├── tsconfig.json
│   ├── package.json
│   └── index.html
│
├── .env                                # Environment variables
├── .env.example                        # Env vars template
└── .gitignore
```

---

## Environment Variables

```
OPENAI_API_KEY=           # Required - powers all LLM features
BROWSER_USE_API_KEY=      # Optional - Browser Use Cloud for web automation
FIRECRAWL_API_KEY=        # Optional - Firecrawl for web scraping
SERPER_API_KEY=           # Optional - Serper for Google search (~$0.001/query)
GITHUB_TOKEN=             # Optional - increases GitHub API rate limit (10→60 req/min) for presence checks
AGENTMAIL_API_KEY=        # Reserved for future email sending
LANGFUSE_PUBLIC_KEY=      # Optional - observability
LANGFUSE_SECRET_KEY=      # Optional - observability
LANGFUSE_HOST=            # Defaults to https://cloud.langfuse.com
MONGODB_URI=              # Optional - falls back to in-memory storage
MONGODB_DATABASE=outbo    # Database name
```

All external APIs degrade gracefully — if a key is missing, the system uses fallbacks (heuristics, mock data, or skips the step).

---

## Backend: API Endpoints

### REST Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/search` | Start a new outreach campaign |
| `GET` | `/api/search/{job_id}` | Get current status of a campaign |
| `POST` | `/api/search/{job_id}/more-leads` | Find additional contacts for an existing campaign |
| `POST` | `/api/email/generate` | Generate a personalized email for one contact |
| `PUT` | `/api/email/edit` | Save edits to an email draft |
| `POST` | `/api/email/outcome` | Track sent/replied/outcome status for a draft (matched by name + email) |
| `GET` | `/api/profile` | Get the saved user profile (returns `null` if none) |
| `PUT` | `/api/profile` | Create/update the persistent user profile |
| `DELETE` | `/api/profile` | Delete the saved user profile |
| `GET` | `/api/history` | List all past campaigns (includes `sent_count` and `replied_count` per campaign) |
| `GET` | `/health` | Health check |

### WebSocket

| Path | Description |
|------|-------------|
| `ws://localhost:8000/ws/{job_id}` | Real-time updates — pushes `SearchResult` JSON on every state change |

---

## Backend: Data Models

### SearchRequest (input)
```
company: string           # Required: "Stripe"
role: string              # Required: "Backend Engineer Intern"
resume_url?: string       # Optional - enables warm-path matching via profile extraction
linkedin_url?: string     # Optional - enables warm-path matching via profile extraction
company_website?: string  # Optional - overrides domain discovery for emails
job_url?: string          # Optional - enables job analysis for targeted search queries
```

### SearchResult (output / persisted state)
```
job_id: string
status: "pending" | "finding_people" | "finding_emails" | "researching" | "generating_emails" | "completed" | "failed"
company: string
role: string
people: Person[]
email_results: EmailResult[]
email_drafts: EmailDraft[]
activity_log: ActivityLogEntry[]
error?: string
company_context?: dict         # Researched company info (mission, blog, culture)
job_context?: dict             # Extracted job posting info (team, tech stack, hiring manager)
user_info?: string             # Resume/LinkedIn URLs for email generation
user_profile_data?: dict       # Serialized UserProfile for reuse in "more leads"
```

### Person
```
name: string
title: string
company: string
linkedin_url: string
priority_score: float (0-1)       # Composite: 0.4 * influence + 0.6 * reachability
priority_reason: string
recent_activity: string
profile_summary: string
influence_score: float (0-1)      # How much hiring power for this specific role
reachability_score: float (0-1)   # How likely to respond to a cold email
contact_category: string          # "hiring_manager" | "team_member" | "recruiter" | "warm_connection" | "general"
outreach_angle: string            # Specific, actionable reason to contact this person
angle_confidence: string          # "verified" (backed by data) | "suggested" (LLM inference)
warm_signals: string[]            # e.g. ["same_university:UVA", "shared_job_posting", "recently_joined"]
discovery_source: string          # "job_posting_sharer" | "hiring_manager" | "team_search" | "warm_path" | "recruiter" | "general"
has_public_github: bool           # True if a matching GitHub profile was found (engineers/ICs only)
```

### EmailResult
```
name: string
email: string
confidence: "high" | "medium" | "low"
source: string                    # e.g. "GitHub public profile", "Pattern match (first.last@stripe.com verified via GitHub)"
alternative_emails: string[]
```

### EmailDraft
```
name: string
email: string
subject: string
body: string
tone: string
personalization_notes: string
sent_at: string | null            # ISO datetime when user marked the email as sent
replied: bool | null              # True = got a reply, False = no response, null = not yet tracked
outcome: string | null            # "no_response" | "replied" | "referral" | "interview"
```

### UserProfileDoc (persistent — saved once, reused across campaigns)
```
profile_id: string = "default"
name: string
linkedin_url: string
resume_url: string
universities: string[]            # ["University of Maryland", "Stanford"]
previous_companies: string[]      # ["Google", "Meta"]
skills: string[]                  # ["Python", "React", "Machine Learning"]
linkedin_headline: string         # Pasted from LinkedIn profile
linkedin_summary: string          # Pasted from LinkedIn About section
created_at: string
updated_at: string
```

### UserProfile (runtime — converted from UserProfileDoc or extracted from LinkedIn/resume)
```
universities: string[]            # Full names: ["University of Maryland", "Stanford"]
previous_companies: string[]      # Past employers (not current)
skills: string[]                  # Top 5-8 technical skills
linkedin_url: string
```

---

## The Agent Pipeline

When a user starts a search, the **Orchestrator** (`orchestrator.py`) runs a multi-step async pipeline in the background.

### Overview

```
User Input (company, role, job_url?, linkedin_url?, resume_url?)
    │
    ├──── Step 0: Job Analysis (if job_url provided)
    │     Scrape job posting → Extract team, tech stack, hiring manager,
    │     keywords, seniority, hiring signals, email domain, reporting structure
    │
    ├──── Step 0.5: Load User Profile
    │     Saved profile (DB) → or extract from LinkedIn/resume →
    │     Universities, companies, skills → warm-path matching
    │
    ├──── Step 0.7: Team Page Scraping (if company_website or email_domain known)
    │     Scrape /about + /team → Extract names+titles via OpenAI →
    │     Seed candidates injected as tier-1 queries → page_cache passed to Step 3
    │
    ├──── Step 1: Find People (PeopleFinder)
    │     Build tiered queries (incl. seed names, reporting manager) → Serper search →
    │     Hard filter → Cross-reference warm signals → Validate (with recency) →
    │     GitHub presence check (engineers only, before scoring) →
    │     Dual-axis scoring + angle confidence → Quality threshold (≥0.60) →
    │     Dynamic diversity selection (with org-level deduplication) → 6-8 contacts
    │
    ├──── Step 2: Find Emails (EmailFinder)
    │     Domain discovery → GitHub org pattern detection →
    │     GitHub API lookup → Pattern generation →
    │     Confidence assignment (boosted by job posting domain)
    │
    ├──── Step 3: Research Company (EmailWriter)
    │     Reuse page_cache from Step 0.7 → Scrape /blog, /careers →
    │     Summarize mission, news, culture
    │
    └──── COMPLETED
              │
              ▼  (User clicks "Generate Email" per contact)
         Step 4: Generate Email (on-demand)
              Detect recipient type → Inject context →
              Enforce variety → Return draft

              ▼  (User clicks "Copy" then "Did you send this?")
         Outcome Tracking (user-driven)
              Mark sent → Mark replied/no-response → Aggregate stats on History page
```

### Step 0: Job Analysis (`job_analyzer.py`)

**Trigger**: Only if the user provides a `job_url`.

Scrapes the job posting via Firecrawl, then extracts structured context via OpenAI:

| Field | Description |
|-------|-------------|
| `team` | Exact team name (e.g. "Subscriptions Enablement") |
| `department` | Engineering, product, data, etc. |
| `hiring_manager` | Name if mentioned in the posting |
| `tech_stack` | Technologies listed |
| `key_requirements` | Top 3-4 responsibilities |
| `keywords` | Terms someone on this team would have in their LinkedIn title |
| `seniority` | intern / junior / mid / senior |
| `location` | Office location or remote |
| `job_title_exact` | Exact title as listed |
| `hiring_signals` | Names mentioned as contacts, recruiters, or hiring managers |
| `posting_date` | Date posted if visible |
| `email_domain` | Email domain extracted from posting (e.g. "gc.com" from `@gc.com`). LLM extraction + regex fallback |
| `reporting_to` | Who this role reports to (e.g. "Senior Engineer on the Subscriptions Enablement team") |

**Email domain extraction** uses a dual approach:
1. LLM extraction from the posting text (e.g. "apply to jobs@gc.com" → `gc.com`)
2. Regex fallback scanning for `@domain.com` patterns (skips gmail/yahoo/hotmail/outlook/example)

**Reporting structure** is extracted when the posting mentions who the role reports to (e.g. "report to a Senior Engineer on the Subscriptions Enablement team"). The `_parse_reporting_to()` helper strips prefixes ("report to a/an/the") and splits on separators ("on the", "in the") to extract a role title ("Senior Engineer") and team name ("Subscriptions Enablement"). These are used to generate tier-1 targeted search queries — searching for the role and team separately rather than the verbatim phrase.

This context feeds into tiered query building (Step 1), scoring (Step 1), email confidence boosting (Step 2), and email personalization (Step 4).

#### Dynamic Query Builder (`build_search_queries`)

Replaces the old static 5-query approach with a **tiered, context-aware query builder** that constructs up to 10 targeted Serper queries based on all available context:

```python
@dataclass
class QueryGroup:
    query: str
    category: str   # "job_posting_sharer" | "hiring_manager" | "team_search" | "warm_path" | "recruiter" | "general"
    priority: int   # 1=highest
```

| Tier | Priority | Condition | Example Queries | Category |
|------|----------|-----------|-----------------|----------|
| 1 | 1 | `job_url` provided | `"{job_url}" site:linkedin.com`; `"Company" "Job Title" "hiring" OR "join my team"` | `job_posting_sharer` |
| 1 | 1 | `hiring_manager` extracted | `site:linkedin.com/in "John Smith" "Stripe"` | `hiring_manager` |
| 1 | 1 | `reporting_to` extracted | `"Company" "Senior Engineer"` (parsed role); `"Company" "Subscriptions Enablement" Engineer` (parsed team + role keyword) | `reporting_manager` |
| 2 | 2 | `keywords` from job | `"at Stripe" "Software Engineer Intern"`; `"at Stripe" "Intern"` | `team_search` |
| 2 | 2 | `tech_stack` from job | `"at Stripe" "TypeScript" OR "React" OR "Node"` | `team_search` |
| 2 | 2 | `team` known (backup) | `"at Stripe" "Payments" engineer OR manager` | `team_search` |
| 3 | 3 | `user_profile` has data | `"at Stripe" "University of Maryland"`; `"at Stripe" previously "Google"` | `warm_path` |
| 4 | 4 | Always | `"at Stripe" Software Engineer Intern engineer OR developer` | `general` |
| 5 | 5 | Always (fallback) | University recruiter, general recruiter, engineering manager, hiring/intern | `recruiter` / `general` |

Queries are sorted by priority and capped at 10 to control Serper API costs (~$0.01 per campaign).

### Step 0.5: User Profile Loading (`user_profile_extractor.py`)

**Trigger**: Always runs (checks for saved profile first).

Loads the user's background for warm-path matching, with two strategies:

**Strategy A — Persistent profile (preferred)**:
1. Check the `profiles` collection in MongoDB for a saved `UserProfileDoc`
2. If found, convert to runtime `UserProfile` via `user_profile_from_doc()` — skips all scraping entirely
3. Also loads `linkedin_headline` and `linkedin_summary` into `user_info` for richer email generation

**Strategy B — Extraction fallback** (only if no saved profile and `linkedin_url` or `resume_url` provided):
1. Scrape LinkedIn directly via Firecrawl (often fails due to login wall)
2. Serper search for LinkedIn profile to get Google snippet
3. Scrape resume URL via Firecrawl
4. Send gathered text to OpenAI for structured extraction

Returns a `UserProfile` with universities, previous companies, and skills. Gracefully degrades to empty profile if all strategies fail.

The loaded/extracted profile is:
- Passed to `build_search_queries()` for tier-3 warm-path queries
- Passed to `PeopleFinder._cross_reference_warm_signals()` to tag contacts
- Serialized to `result.user_profile_data` so "more leads" can reuse it without re-extraction

### Step 1: Find People (`people_finder.py`)

**Goal**: Find 6-8 high-value, reachable contacts at the target company.

#### Search Strategy

**With Serper API key** (preferred — fast & cheap):
- Calls `build_search_queries()` to get up to 10 tiered queries
- Each result is tagged with its `QueryGroup.category` as `discovery_source`
- Collects 30-40 raw LinkedIn profile results

**Without Serper** (fallback — uses Browser Use):
- Runs 2 AI-powered browser searches on Google/LinkedIn
- Slower and more expensive

#### Name Recovery & Deduplication

Serper sometimes returns truncated LinkedIn names (e.g. "Claire J." instead of "Claire Robert"). The pipeline handles this:
- **Name recovery**: If the parsed last name is a single character (e.g. "J."), `_extract_name_from_linkedin_url()` extracts the full name from the LinkedIn URL slug (`/in/claire-robert` → "Claire Robert")
- **Slug-based dedup**: In addition to URL and name matching, `_deduplicate()` extracts URL slugs (stripping trailing IDs) so that "Claire J." and "Claire Robert" from the same `/in/claire-robert` profile are correctly merged

#### Hard Filtering (deterministic, no LLM)

Immediately excludes people with titles containing:
- C-suite: CEO, CFO, CTO, founder, president
- Senior leadership: VP, director, "head of", partner
- Irrelevant departments: finance, accounting, legal, compliance, sales, marketing, operations, supply chain

Exception: Recruiters are never filtered by department keywords.

#### Warm Signal Cross-Reference

After gathering candidates, `_cross_reference_warm_signals(people, user_profile)` scans each person's title, snippet, and profile for:

| Signal | How Detected | Tag |
|--------|-------------|-----|
| Same university | Title/snippet contains a university from `user_profile.universities` | `same_university:{name}` |
| Shared previous company | Title/snippet contains a company from `user_profile.previous_companies` | `shared_company:{name}` |
| Recently joined | "Recently joined", "new to", or start date within ~1 year | `recently_joined` |
| Posted about hiring | "hiring", "we're hiring", "join my team" in snippet | `posted_about_hiring` |
| Shared job posting | Discovered via a job-posting-sharer query | `shared_job_posting` |

#### Validation with Recency (via OpenAI)

Each candidate is validated: "Does this person work at {company}?" plus a recency check:
- Returns `(works_here: bool, recency: str)` where recency is `"active"`, `"stale"`, or `"unknown"`
- Recency feeds into reachability scoring (active profiles score higher)

#### Dual-Axis Scoring (`priority_scorer.py`)

Each contact is scored on two independent axes via OpenAI:

**Influence (0-100)** — how much hiring power for *this specific role*:

| Role | Score Range |
|------|-------------|
| Hiring manager for the role | 90-100 |
| Team member who shared/promoted the listing | 80-95 |
| Engineer on the same team | 70-85 |
| Recruiter tagged on the posting | 65-80 |
| Engineering manager of related team | 55-70 |
| General recruiter | 45-65 |
| Engineer on a different team | 25-45 |
| Unrelated department | 0-20 |

**Reachability (0-100)** — how likely to respond to a cold email from a student:

| Signal | Score Range |
|--------|-------------|
| Recently posted about hiring | 85-100 |
| Campus/university recruiter | 80-95 |
| Active on social media | 70-90 |
| Recently joined (<1 year) | 65-85 |
| Junior/mid level | 60-80 |
| Public email or active GitHub | 70-85 |
| Senior/staff with no activity | 20-40 |
| Executive level | 10-25 |

**Warm signal bonuses** (applied to reachability post-scoring):
- Same university: +15
- Shared previous company: +12
- Shared job posting: +20
- Posted about hiring: +10
- Recently joined: +8

**Composite score**: `priority_score = (0.4 × influence + 0.6 × reachability) / 100`
Intentionally favors reachability — a reachable person with moderate influence beats an unreachable person with high influence.

The scorer also returns:
- `contact_category`: classification of each contact
- `outreach_angle`: specific, actionable reason to contact them with a suggested approach
- `angle_confidence`: "verified" (angle only references data present in the profile) or "suggested" (involves inference)

Has a heuristic fallback if OpenAI is unavailable that produces the same dual-axis scores using keyword matching. Heuristic fallback always marks angles as "suggested".

#### Quality Threshold

Before diversity selection, contacts are filtered by quality:
- **Threshold**: `QUALITY_THRESHOLD = 0.60`
- Contacts at or above 0.60 are always kept
- Contacts below 0.60 with warm signals are kept **only if** they have `influence_score > 0` or `priority_score >= 0.40` — this prevents zero-influence unknowns (e.g. someone who merely shares your university but has no discernible role) from wasting a slot
- Contacts below 0.60 with no warm signals are dropped
- Edge case: if no contacts pass the threshold, the top 3 are kept regardless

This prevents low-quality filler contacts from diluting the list.

#### Dynamic Diversity Selection

Replaces the old fixed quotas (2 recruiters, 3 engineers, 1 manager) with context-aware selection:

1. **Always include** anyone who shared the job posting (highest signal)
2. **Ensure at least 1** from each available `contact_category`
3. **Ensure at least 2 warm connections** if they exist
4. **Fill remaining** with highest-scoring candidates regardless of category

Returns 6-8 contacts.

### Step 2: Find Emails (`email_finder.py`)

**Goal**: Discover email addresses with confidence levels for each contact.

#### Domain Discovery

1. Check hardcoded known domains (Stripe → stripe.com, Google → google.com, etc.)
2. User-provided `company_website` always takes priority
3. Firecrawl search for `"{company} official company website"`
4. OpenAI picks the best domain from results (filters LinkedIn, Wikipedia, Glassdoor)
5. Validates domain has MX records (can receive email)

#### Pattern Detection (via GitHub)

1. Scrapes `github.com/orgs/{company}/people` via Firecrawl
2. Extracts real emails from GitHub commit data
3. Infers the company's email pattern: `first.last@`, `firstlast@`, `flast@`, `first_last@`
4. Reorders generated candidates to match detected pattern

#### Email Generation (per person)

1. **GitHub API lookup** (free, for engineers): `https://api.github.com/search/users?q={name}+{company}`
   - If found with public email → **HIGH** confidence
2. **Pattern generation**: Creates candidates in priority order:
   - `first.last@domain`, `firstlast@domain`, `flast@domain`, `first@domain`, `first_last@domain`, `f.last@domain`, `last.first@domain`
3. **Reorder** by detected pattern if GitHub org data found
4. **Confidence assignment**:
   - **HIGH**: GitHub-verified public email
   - **MEDIUM**: Pattern match with detected format (verified via GitHub org), OR pattern match at a domain confirmed in the job posting (`email_domain` from `job_context`)
   - **LOW**: Pattern guess (no verification)

The email finder now accepts `job_context` from the orchestrator. If a pattern-matched email would be LOW confidence but the domain matches the `email_domain` extracted from the job posting (e.g. `@gc.com` from "apply to careers@gc.com"), it's boosted to MEDIUM.

### Step 3: Research Company (`email_writer.py`)

**Goal**: Build company context for personalized emails.

1. Scrapes 3 pages via Firecrawl: `/about`, `/blog`, `/careers`
2. Filters out soft 404/error pages via `_is_error_page()` — checks title against known error strings ("Not Found", "404", "Page not found", etc.) and short content. Skipped pages are logged rather than silently passed to OpenAI
3. OpenAI summarizes into structured context: mission, recent news, blog highlights, engineering culture signals, role-specific info

### Step 4: Generate Emails (`email_writer.py`)

**Trigger**: On-demand when the user clicks "Generate Email" for a specific contact.

1. **Detect recipient type**: Recruiter → more direct about the role; Engineer → lead with technical interest; Manager → show understanding of the team
2. **Inject context**: Company research, job posting details, user's background
3. **Enforce variety**: Tracks previous email openings for the same company; forbids reuse
4. **Generate via OpenAI**: 4-6 sentences, genuine tone, specific mention of recipient's role/work, clear low-friction ask
5. **Returns**: Subject line, body, and personalization notes

---

## Frontend: Page-by-Page

### SearchPage (`pages/SearchPage.tsx`)

Campaign creation form with:
- **Company** (required)
- **Company website** (optional) — overrides domain discovery
- **Role** (required)
- **Job posting URL** (optional) — enables job analysis for targeted queries

**Profile-aware behavior**: On mount, checks for a saved profile (`GET /api/profile`). If a profile exists with universities or previous companies:
- Hides LinkedIn URL and Resume URL fields
- Shows green banner: "Using your saved profile for warm connections"
- Sends the profile's `linkedin_url` and `resume_url` in the search request (backend loads full profile from DB)

If no saved profile: shows **LinkedIn URL** and **Resume URL** fields as before.

On submit: calls `POST /api/search`, gets back a `job_id`, navigates to the results page.

### ResultsPage (`pages/ResultsPage.tsx`)

Main workspace showing live progress of the agent pipeline:

- **Progress bar**: Visual indicator of which step is running
- **Activity feed** (`ActivityFeed.tsx`): Real-time log of agent actions
- **Contacts list**: Each contact rendered as a `ContactCard`
- **"Generate more leads" button**: Appears after completion, finds additional contacts (deduplicating by LinkedIn URL)

**Real-time updates** via two mechanisms:
1. **WebSocket** (`useWebSocket.ts`): Primary — receives full `SearchResult` on every state change
2. **Polling fallback**: `GET /api/search/{jobId}` every 3 seconds if WebSocket disconnects

### ContactCard (`components/ContactCard.tsx`)

Per-contact expandable card showing:
- **Name + title + company**
- **Contact category badge** (hiring_manager, team_member, recruiter, warm_connection, general)
- **Priority score** with hover tooltip showing influence and reachability breakdown
- **Outreach angle** (italic blue text) — specific reason to contact this person, with **confidence label**: green "verified" (backed by data) or gray "suggested" (LLM inference)
- **Warm signal badges** (purple) — e.g. "Same University: UVA", "Recently Joined", "Shared Job Posting"
- **Email + confidence badge** (high/medium/low)
- **LinkedIn link**
- **"Generate Email" button** → `POST /api/email/generate`
- **Email editor**: Editable subject + body with Save and Copy buttons

### ProfilePage (`pages/ProfilePage.tsx`)

Persistent user profile form — saved once, reused across all campaigns for warm connection matching. Fields:
- **Name**, **LinkedIn URL**, **Resume URL** (text/URL inputs)
- **Universities**, **Previous Companies**, **Skills** (comma-separated text inputs — split on save, joined on load)
- **LinkedIn Headline** (text input — paste from LinkedIn)
- **LinkedIn Summary / About** (textarea — paste from LinkedIn)
- **Save Profile** button → `PUT /api/profile`
- **Clear Profile** button (with confirm dialog) → `DELETE /api/profile`

On mount: loads existing profile via `GET /api/profile` and populates form fields.

### HistoryPage (`pages/HistoryPage.tsx`)

Lists all past campaigns: company, role, contact count, draft count, status. Click to re-open.

### DashboardLayout (`components/DashboardLayout.tsx`)

Shell layout with sidebar ("New Campaign" + "Campaigns" + "Profile" nav), header (back button, title, status), and content area.

---

## Real-Time Architecture

```
Frontend (React)                          Backend (FastAPI)
     │                                         │
     │  POST /api/search ──────────────────►   │  Creates job_id
     │  ◄────────────────── {job_id} ──────    │  Starts background task
     │                                         │
     │  WebSocket /ws/{job_id} ◄───────────►   │
     │                                         │
     │  ◄── SearchResult JSON ─────────────    │  On every state change:
     │  ◄── SearchResult JSON ─────────────    │  - Person found
     │  ◄── SearchResult JSON ─────────────    │  - Email found
     │  ◄── SearchResult JSON ─────────────    │  - Status change
     │                                         │
     │  (fallback: GET /api/search/{id})       │
     │  every 3s if WS disconnects             │
```

The backend maintains a list of active WebSocket connections per `job_id`. Every time the `SearchResult` is updated, it broadcasts the full result to all connected clients.

---

## Database

### MongoDB (primary)

Two collections:

| Collection | Key | Document |
|------------|-----|----------|
| `jobs` | `job_id` | Full serialized `SearchResult` (including `user_profile_data`, `job_context`, `company_context`) |
| `profiles` | `profile_id` (default: `"default"`) | `UserProfileDoc` — persistent user profile for warm-path matching |

- **Driver**: Motor (async)
- **Connection**: SSL/TLS via certifi

All new Person/SearchResult fields have defaults, so existing MongoDB documents deserialize cleanly (backward compatible).

### In-Memory Fallback

If `MONGODB_URI` is not set:
```python
jobs: dict[str, SearchResult] = {}
profiles: dict[str, dict] = {}
```
Data is lost on restart. Useful for local development.

---

## How to Run

### Backend
```bash
cd backend
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

### Frontend
```bash
cd frontend
npm install
npm run dev    # Starts on http://localhost:5173
```

The Vite dev server proxies `/api/*`, `/health`, and `/ws/*` to `http://localhost:8000`.

---

## External API Cost Breakdown

| Service | Cost | When Used |
|---------|------|-----------|
| **OpenAI (gpt-4o-mini)** | ~$0.01-0.05 per campaign | Job analysis, profile extraction, validation, scoring, research, emails |
| **Serper** | ~$0.01 per campaign (up to 10 queries) | Tiered people search |
| **Firecrawl** | ~$0.01-0.03 per campaign | Job scraping, domain discovery, company research, profile extraction |
| **Browser Use** | ~$0.05-0.10 per search (fallback only) | People search when no Serper key |
| **GitHub API** | Free | Email lookup (rate limited ~10 req/min) |
| **dnspython** | Free (local) | MX record verification |

A typical campaign costs roughly **$0.02-0.10** depending on which APIs are configured.

---

## Key Design Decisions

1. **Job-posting-centric pipeline**: When a `job_url` is provided, the pipeline mines it aggressively — extracting the team, hiring manager, contact names, and building targeted search queries. This beats generic company-wide searches.
2. **Dual-axis scoring**: Contacts are scored on influence (hiring power) and reachability (likelihood to respond) independently, then combined with a 40/60 weighting that favors reachability. A campus recruiter who'll definitely reply is more useful than a VP who won't.
3. **Warm-path detection**: User's universities and previous companies are cross-referenced against contacts. Warm signals (same school, former colleague, shared job posting) get bonus reachability points and ensure at least 2 warm connections are included.
4. **Tiered query construction**: Search queries are built dynamically based on all available context (job posting, team, user profile) in 5 priority tiers. Budget-capped at 10 queries.
5. **Dynamic diversity selection**: Replaces fixed quotas with context-aware selection — always includes job posting sharers, ensures category representation, prioritizes warm connections, fills remaining by score.
6. **Async everything**: FastAPI + Motor + AsyncOpenAI — the entire pipeline is non-blocking.
7. **Background tasks**: Search runs as a background task so the API responds immediately.
8. **WebSocket + polling**: Real-time updates with a reliable fallback.
9. **Graceful degradation**: Every external API is optional; the system works (with reduced quality) even with just an OpenAI key. User profile extraction tries 3 strategies before giving up. Scoring falls back to heuristics.
10. **On-demand email generation**: Emails are generated one at a time, not in batch, so the user stays in control.
11. **Hard filtering before LLM**: Deterministic title/department filters run before expensive LLM validation — saves API costs.
12. **Variety enforcement**: Tracks previous email openings to prevent repetitive outreach to the same company.
13. **Backward-compatible data model**: All new fields on Person and SearchResult have defaults, so existing MongoDB documents deserialize without migration.
14. **No authentication**: Currently a local-only, single-user tool with no auth layer.
15. **In-memory fallback**: Works without MongoDB for quick local testing.
16. **Persistent profile over scraping**: LinkedIn scraping via Firecrawl is unreliable (login walls). Instead, users paste their info once into a saved profile that's reused across all campaigns, completely bypassing scraping.
17. **Quality threshold**: Contacts below 0.60 priority score with no warm signals are dropped. Warm contacts must still have influence > 0 or score >= 0.40 to pass. Fewer strong contacts beat more weak ones.
18. **Job posting domain boost**: Email confidence is boosted from LOW to MEDIUM when the email domain matches one extracted from the job posting — a strong signal that pattern-matched emails are correct.
19. **Angle confidence labels**: Outreach angles are classified as "verified" (only references data in the profile) or "suggested" (involves LLM inference). This helps users trust the angles and know which ones to double-check.
20. **Reporting structure search**: When a job posting mentions who the role reports to, the `_parse_reporting_to()` helper extracts role + team and generates tier-1 queries — searching for "Senior Engineer" and "Subscriptions Enablement Engineer" rather than the verbatim phrase.
21. **Terminal logging**: `logging.basicConfig(level=INFO)` in `main.py` enables all `logger.info()` calls across the pipeline to print to the terminal. The orchestrator logs each step with timing, the query builder logs all queries with tier/category, the people finder logs per-query result counts and quality threshold drops, and the email finder logs domain confidence boosts. Third-party loggers (httpx, openai, firecrawl) are silenced to WARNING.
22. **Smart query generation**: Tier 2 queries use job context keywords and tech stack terms (which appear on LinkedIn profiles) rather than internal team names (which don't). Tier 4 guards against URL values in role keywords. Reporting manager queries parse natural language into searchable role + team components.
23. **Name recovery from URL slugs**: When Serper returns truncated names (e.g. "Claire J."), the full name is recovered from the LinkedIn URL slug (`/in/claire-robert` → "Claire Robert"). Dedup also uses URL slugs to catch duplicate profiles with different display names.
24. **Soft 404 detection**: Company research filters out error pages before passing content to OpenAI — checks page titles against known error strings and short content length.
