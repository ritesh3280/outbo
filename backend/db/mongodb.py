"""MongoDB job store. Persists SearchResult documents for campaigns."""

import logging
from typing import Optional

import certifi
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from backend.config import settings
from backend.models.schemas import SearchResult

logger = logging.getLogger(__name__)

COLLECTION_JOBS = "jobs"
COLLECTION_PROFILES = "profiles"
COLLECTION_RESUMES = "resumes"

_client: Optional[AsyncIOMotorClient] = None
_db: Optional[AsyncIOMotorDatabase] = None


def get_db() -> Optional[AsyncIOMotorDatabase]:
    """Return the database instance if MongoDB is configured."""
    return _db


async def connect_mongodb() -> bool:
    """Connect to MongoDB. Returns True if connected, False if URI not set."""
    global _client, _db
    if not settings.mongodb_uri:
        logger.info("MONGODB_URI not set — using in-memory job store")
        return False
    try:
        _client = AsyncIOMotorClient(
            settings.mongodb_uri,
            serverSelectionTimeoutMS=5000,
            tlsCAFile=certifi.where(),
        )
        await _client.admin.command("ping")
        _db = _client[settings.mongodb_database]
        logger.info("MongoDB connected: database=%s", settings.mongodb_database)
        return True
    except Exception as e:
        logger.warning("MongoDB connection failed: %s", e)
        _client = None
        _db = None
        return False


async def close_mongodb() -> None:
    """Close the MongoDB connection."""
    global _client, _db
    if _client:
        _client.close()
        _client = None
        _db = None
        logger.info("MongoDB connection closed")


def _serialize(result: SearchResult) -> dict:
    doc = result.model_dump()
    doc["_id"] = result.job_id
    return doc


def _deserialize(doc: dict) -> SearchResult:
    out = dict(doc)
    out.pop("_id", None)
    return SearchResult.model_validate(out)


async def get_job(job_id: str) -> Optional[SearchResult]:
    """Load a job by id. Returns None if not found or DB not connected."""
    db = get_db()
    if db is None:
        return None
    try:
        doc = await db[COLLECTION_JOBS].find_one({"_id": job_id})
        if not doc:
            return None
        return _deserialize(doc)
    except Exception as e:
        logger.warning("get_job failed for %s: %s", job_id, e)
        return None


async def save_job(result: SearchResult) -> None:
    """Upsert a job. No-op if MongoDB not connected."""
    db = get_db()
    if db is None:
        return
    try:
        doc = _serialize(result)
        await db[COLLECTION_JOBS].replace_one(
            {"_id": result.job_id},
            doc,
            upsert=True,
        )
    except Exception as e:
        logger.warning("save_job failed for %s: %s", result.job_id, e)


async def list_jobs() -> list[SearchResult]:
    """List all jobs (newest first). Returns [] if DB not connected."""
    db = get_db()
    if db is None:
        return []
    try:
        cursor = db[COLLECTION_JOBS].find().sort("_id", -1)
        results = []
        async for doc in cursor:
            results.append(_deserialize(doc))
        return results
    except Exception as e:
        logger.warning("list_jobs failed: %s", e)
        return []


# ── Profile CRUD ─────────────────────────────────────────────────────────


async def get_profile(profile_id: str) -> Optional[dict]:
    """Load a user profile by id. Returns None if not found or DB not connected."""
    db = get_db()
    if db is None:
        return None
    try:
        doc = await db[COLLECTION_PROFILES].find_one({"_id": profile_id})
        if not doc:
            return None
        out = dict(doc)
        out.pop("_id", None)
        return out
    except Exception as e:
        logger.warning("get_profile failed for %s: %s", profile_id, e)
        return None


async def save_profile(doc: dict) -> None:
    """Upsert a user profile. No-op if MongoDB not connected."""
    db = get_db()
    if db is None:
        return
    try:
        profile_id = doc.get("profile_id", "default")
        to_save = dict(doc)
        to_save["_id"] = profile_id
        await db[COLLECTION_PROFILES].replace_one(
            {"_id": profile_id},
            to_save,
            upsert=True,
        )
    except Exception as e:
        logger.warning("save_profile failed: %s", e)


async def delete_profile(profile_id: str) -> None:
    """Delete a user profile. No-op if MongoDB not connected."""
    db = get_db()
    if db is None:
        return
    try:
        await db[COLLECTION_PROFILES].delete_one({"_id": profile_id})
    except Exception as e:
        logger.warning("delete_profile failed for %s: %s", profile_id, e)


# ── Resume file storage (multiple resumes per profile) ───────────────────


async def save_resume(resume_id: str, profile_id: str, pdf_bytes: bytes, filename: str, text: str) -> None:
    """Store a resume with its extracted text. No-op if MongoDB not connected."""
    from datetime import datetime, timezone
    db = get_db()
    if db is None:
        return
    try:
        doc = {
            "_id": resume_id,
            "profile_id": profile_id,
            "pdf": pdf_bytes,
            "filename": filename,
            "size": len(pdf_bytes),
            "text": text,
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
        }
        await db[COLLECTION_RESUMES].replace_one({"_id": resume_id}, doc, upsert=True)
        logger.info("Resume saved: %s (%d bytes, %d chars)", filename, len(pdf_bytes), len(text))
    except Exception as e:
        logger.warning("save_resume failed: %s", e)


async def list_resumes(profile_id: str) -> list[dict]:
    """List resume metadata for a profile (no PDF bytes). Returns [] if DB not connected."""
    db = get_db()
    if db is None:
        return []
    try:
        cursor = db[COLLECTION_RESUMES].find(
            {"profile_id": profile_id},
            {"pdf": 0},  # exclude binary field
        ).sort("uploaded_at", -1)
        results = []
        async for doc in cursor:
            results.append({
                "resume_id": doc["_id"],
                "filename": doc.get("filename", "resume.pdf"),
                "size": doc.get("size", 0),
                "uploaded_at": doc.get("uploaded_at", ""),
            })
        return results
    except Exception as e:
        logger.warning("list_resumes failed: %s", e)
        return []


async def get_resume_file(resume_id: str) -> dict | None:
    """Load resume PDF bytes. Returns {pdf, filename} or None."""
    db = get_db()
    if db is None:
        return None
    try:
        doc = await db[COLLECTION_RESUMES].find_one({"_id": resume_id})
        if not doc:
            return None
        return {"pdf": doc["pdf"], "filename": doc.get("filename", "resume.pdf")}
    except Exception as e:
        logger.warning("get_resume_file failed: %s", e)
        return None


async def get_resume_text(resume_id: str) -> str:
    """Load only the extracted text for a resume. Returns '' if not found."""
    db = get_db()
    if db is None:
        return ""
    try:
        doc = await db[COLLECTION_RESUMES].find_one({"_id": resume_id}, {"text": 1})
        if not doc:
            return ""
        return doc.get("text", "")
    except Exception as e:
        logger.warning("get_resume_text failed: %s", e)
        return ""


async def delete_resume(resume_id: str) -> None:
    """Delete a resume. No-op if MongoDB not connected."""
    db = get_db()
    if db is None:
        return
    try:
        await db[COLLECTION_RESUMES].delete_one({"_id": resume_id})
        logger.info("Resume deleted: %s", resume_id)
    except Exception as e:
        logger.warning("delete_resume failed: %s", e)
