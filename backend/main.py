import uuid
from contextlib import asynccontextmanager

from datetime import datetime, timezone

from fastapi import BackgroundTasks, FastAPI, HTTPException, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from backend.models.schemas import ActivityLogEntry, SearchRequest, SearchResult, SearchStatus
from backend.db.mongodb import (
    connect_mongodb,
    close_mongodb,
    get_db,
    get_job,
    save_job,
    list_jobs as db_list_jobs,
)

# In-memory fallback when MongoDB is not configured
jobs: dict[str, SearchResult] = {}
job_websockets: dict[str, list[WebSocket]] = {}


async def _get_job(job_id: str) -> SearchResult | None:
    if get_db() is not None:
        return await get_job(job_id)
    return jobs.get(job_id)


async def _save_job(result: SearchResult) -> None:
    if get_db() is not None:
        await save_job(result)
    else:
        jobs[result.job_id] = result


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_mongodb()
    yield
    jobs.clear()
    job_websockets.clear()
    await close_mongodb()


app = FastAPI(title="OutreachBot", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Health ───────────────────────────────────────────────────────────────

@app.get("/health")
async def health_check():
    return {"status": "ok"}


# ── Search endpoints ─────────────────────────────────────────────────────

@app.post("/api/search")
async def start_search(request: SearchRequest, background_tasks: BackgroundTasks):
    """Start a new outreach search. Returns a job_id immediately."""
    job_id = str(uuid.uuid4())
    result = SearchResult(
        job_id=job_id,
        status=SearchStatus.PENDING,
        company=request.company,
        role=request.role,
    )
    await _save_job(result)
    background_tasks.add_task(_run_search_task, job_id, request)
    return {"job_id": job_id}


@app.get("/api/search/{job_id}")
async def get_search(job_id: str):
    """Get current status and results for a search job."""
    result = await _get_job(job_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return result


@app.post("/api/search/{job_id}/more-leads")
async def start_more_leads(job_id: str, background_tasks: BackgroundTasks):
    """Find more contacts for an existing campaign (deduplicated by LinkedIn URL). Returns 202."""
    result = await _get_job(job_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if result.status != SearchStatus.COMPLETED:
        raise HTTPException(
            status_code=409,
            detail="Campaign must be completed before generating more leads",
        )
    background_tasks.add_task(_run_more_leads_task, job_id)
    return Response(status_code=202)


async def _run_more_leads_task(job_id: str) -> None:
    """Background task: find more people (excluding existing), find emails, merge into job."""
    from backend.agents.orchestrator import run_more_leads

    result = await _get_job(job_id)
    if result is None:
        return

    async def on_update(updated: SearchResult) -> None:
        await _save_job(updated.model_copy())
        await _broadcast_to_websockets(job_id, updated)

    try:
        await run_more_leads(result, on_update=on_update)
        await _save_job(result)
        await _broadcast_to_websockets(job_id, result)
    except Exception as e:
        current = await _get_job(job_id)
        if current:
            current.status = SearchStatus.COMPLETED  # keep usable
            current.activity_log.append(
                ActivityLogEntry(
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    message=f"Error finding more leads: {e}",
                    type="error",
                )
            )
            await _save_job(current)
            await _broadcast_to_websockets(job_id, current)
        raise


async def _run_search_task(job_id: str, request: SearchRequest) -> None:
    """Background task that runs the full orchestrator pipeline."""
    from backend.agents.orchestrator import run_search

    async def on_update(result: SearchResult) -> None:
        await _save_job(result.model_copy())
        await _broadcast_to_websockets(job_id, result)

    try:
        result = await run_search(request, job_id, on_update=on_update)
        await _save_job(result)
        await _broadcast_to_websockets(job_id, result)
    except Exception as e:
        current = await _get_job(job_id)
        if current:
            current.status = SearchStatus.FAILED
            current.error = str(e)
            await _save_job(current)
        await _broadcast_to_websockets(job_id, current or SearchResult(job_id=job_id, status=SearchStatus.FAILED, error=str(e)))


# ── WebSocket for live updates ───────────────────────────────────────────

@app.websocket("/ws/{job_id}")
async def websocket_endpoint(websocket: WebSocket, job_id: str):
    await websocket.accept()

    if job_id not in job_websockets:
        job_websockets[job_id] = []
    job_websockets[job_id].append(websocket)

    try:
        result = await _get_job(job_id)
        if result:
            await websocket.send_json(result.model_dump())

        # Keep connection open until client disconnects
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        if job_id in job_websockets:
            job_websockets[job_id] = [
                ws for ws in job_websockets[job_id] if ws != websocket
            ]


async def _broadcast_to_websockets(job_id: str, result: SearchResult) -> None:
    """Send updated result to all connected WebSocket clients."""
    if job_id not in job_websockets:
        return
    data = result.model_dump()
    dead: list[WebSocket] = []
    for ws in job_websockets[job_id]:
        try:
            await ws.send_json(data)
        except Exception:
            dead.append(ws)
    for ws in dead:
        job_websockets[job_id].remove(ws)


# ── On-demand email generation ───────────────────────────────────────────

@app.post("/api/email/generate")
async def generate_email_for_contact(payload: dict):
    """Generate a single email draft for one contact. Call when user clicks 'Generate email'."""
    job_id = payload.get("job_id", "")
    contact_name = payload.get("name", "")

    result = await _get_job(job_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if not result.company_context:
        raise HTTPException(
            status_code=400,
            detail="Job context not available (company research may have failed)",
        )

    person = next((p for p in result.people if p.name == contact_name), None)
    if not person:
        raise HTTPException(status_code=404, detail="Contact not found")

    email_result = next((e for e in result.email_results if e.name == contact_name), None)
    if not email_result or not email_result.email:
        raise HTTPException(
            status_code=400,
            detail="No email address found for this contact",
        )

    # Already have a draft for this contact
    existing = next((d for d in result.email_drafts if d.name == contact_name), None)
    if existing:
        return existing.model_dump()

    from backend.agents.email_writer import generate_single_email, CompanyContext

    company_ctx = CompanyContext(**result.company_context)
    previous_openings = [
        d.body.split("\n")[0] for d in result.email_drafts
        if d.body and d.body.strip()
    ]

    draft = await generate_single_email(
        person=person,
        email_result=email_result,
        company_context=company_ctx,
        role=result.role,
        user_info=result.user_info or "",
        previous_openings=previous_openings or None,
        job_context=result.job_context,
    )

    result.email_drafts.append(draft)
    await _save_job(result)
    await _broadcast_to_websockets(job_id, result)
    return draft.model_dump()


# ── Email editing ────────────────────────────────────────────────────────

@app.put("/api/email/edit")
async def edit_email(payload: dict):
    """Update a draft email before sending."""
    job_id = payload.get("job_id", "")
    contact_name = payload.get("name", "")
    new_subject = payload.get("subject")
    new_body = payload.get("body")

    result = await _get_job(job_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Job not found")

    for draft in result.email_drafts:
        if draft.name == contact_name:
            if new_subject is not None:
                draft.subject = new_subject
            if new_body is not None:
                draft.body = new_body
            await _save_job(result)
            return {"status": "updated"}

    raise HTTPException(status_code=404, detail="Contact not found in drafts")


# ── History ───────────────────────────────────────────────────────────────

@app.get("/api/history")
async def get_history():
    """List past searches (from MongoDB or in-memory)."""
    if get_db() is not None:
        results = await db_list_jobs()
    else:
        results = list(jobs.values())
    return [
        {
            "job_id": r.job_id,
            "company": r.company,
            "role": r.role,
            "status": r.status.value,
            "people_count": len(r.people),
            "drafts_count": len(r.email_drafts),
        }
        for r in results
    ]
