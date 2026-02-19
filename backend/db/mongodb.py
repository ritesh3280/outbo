"""MongoDB job store. Persists SearchResult documents for campaigns."""

import logging
from typing import Optional

import certifi
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from backend.config import settings
from backend.models.schemas import SearchResult

logger = logging.getLogger(__name__)

COLLECTION_JOBS = "jobs"

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
