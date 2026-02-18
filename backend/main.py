import asyncio
import uuid
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from backend.models.schemas import SearchRequest, SearchResult, SearchStatus


# ── In-memory job store ──────────────────────────────────────────────────

jobs: dict[str, SearchResult] = {}
job_websockets: dict[str, list[WebSocket]] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    jobs.clear()
    job_websockets.clear()


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
    jobs[job_id] = SearchResult(
        job_id=job_id,
        status=SearchStatus.PENDING,
        company=request.company,
        role=request.role,
    )
    background_tasks.add_task(_run_search_task, job_id, request)
    return {"job_id": job_id}


@app.get("/api/search/{job_id}")
async def get_search(job_id: str):
    """Get current status and results for a search job."""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    return jobs[job_id]


async def _run_search_task(job_id: str, request: SearchRequest) -> None:
    """Background task that runs the full orchestrator pipeline."""
    from backend.agents.orchestrator import run_search

    async def on_update(result: SearchResult) -> None:
        jobs[job_id] = result.model_copy()
        await _broadcast_to_websockets(job_id, result)

    try:
        result = await run_search(request, job_id, on_update=on_update)
        jobs[job_id] = result
        await _broadcast_to_websockets(job_id, result)
    except Exception as e:
        jobs[job_id].status = SearchStatus.FAILED
        jobs[job_id].error = str(e)


# ── WebSocket for live updates ───────────────────────────────────────────

@app.websocket("/ws/{job_id}")
async def websocket_endpoint(websocket: WebSocket, job_id: str):
    await websocket.accept()

    if job_id not in job_websockets:
        job_websockets[job_id] = []
    job_websockets[job_id].append(websocket)

    try:
        # Send current state immediately
        if job_id in jobs:
            await websocket.send_json(jobs[job_id].model_dump())

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


# ── Email editing ────────────────────────────────────────────────────────

@app.put("/api/email/edit")
async def edit_email(payload: dict):
    """Update a draft email before sending."""
    job_id = payload.get("job_id", "")
    contact_name = payload.get("name", "")
    new_subject = payload.get("subject")
    new_body = payload.get("body")

    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    result = jobs[job_id]
    for draft in result.email_drafts:
        if draft.name == contact_name:
            if new_subject is not None:
                draft.subject = new_subject
            if new_body is not None:
                draft.body = new_body
            return {"status": "updated"}

    raise HTTPException(status_code=404, detail="Contact not found in drafts")


# ── History (in-memory for now, SQLite in Phase 7) ───────────────────────

@app.get("/api/history")
async def get_history():
    """List past searches."""
    return [
        {
            "job_id": r.job_id,
            "company": r.company,
            "role": r.role,
            "status": r.status.value,
            "people_count": len(r.people),
            "drafts_count": len(r.email_drafts),
        }
        for r in jobs.values()
    ]
