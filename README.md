# OutreachBot

AI-powered cold outreach agent for internship applicants. Provide a company name and role — the agent finds contacts, discovers emails, and generates personalized cold emails.

## Quick Start

### Backend

```bash
cd outbo
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
cp .env.example .env  # fill in your API keys
uvicorn backend.main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173 — the app will proxy API calls to the backend at :8000.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | React + TypeScript + Tailwind CSS v4 |
| Backend | FastAPI (Python) |
| Database | MongoDB (optional; in-memory fallback if `MONGODB_URI` not set) |
| Browser Automation | Browser Use Cloud API |
| Web Scraping | Firecrawl |
| Email Sending | AgentMail |
| Observability | Langfuse |
