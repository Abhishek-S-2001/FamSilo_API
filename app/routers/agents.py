"""
Agents Router — REST + SSE endpoints for all three AI agents.
All endpoints require authentication.
"""
import json
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from supabase import Client

from app.utils.database import get_db
from app.utils.dependencies import get_current_user_id
from app.agents.briefing import get_or_create_briefing
from app.agents.facilitator import check_and_facilitate
from app.agents.concierge import stream_concierge_answer, index_silo_posts

router = APIRouter(prefix="/agents", tags=["AI Agents"])


# ── Daily Briefing ────────────────────────────────────────────────────────────

@router.get("/briefing")
def get_daily_briefing(
    db: Client = Depends(get_db),
    current_user_id: str = Depends(get_current_user_id),
):
    """
    Returns today's personalized AI briefing for the authenticated user.
    Generates one if it doesn't exist yet (cached per user per day).
    """
    result = get_or_create_briefing(current_user_id, db)
    return result


# ── Silo Facilitator ──────────────────────────────────────────────────────────

@router.post("/facilitator/check/{silo_id}")
def trigger_facilitator(
    silo_id: str,
    db: Client = Depends(get_db),
    current_user_id: str = Depends(get_current_user_id),
):
    """
    Frontend pings this when a user opens a silo.
    Checks if the silo is dormant (no posts in 24h) and generates content if so.
    Safe to call on every page load — idempotent via run_date uniqueness.
    """
    # Verify user is a member of the silo
    membership = (
        db.table("group_members")
        .select("id")
        .eq("group_id", silo_id)
        .eq("user_id", current_user_id)
        .limit(1)
        .execute()
    )
    if not membership.data:
        raise HTTPException(status_code=403, detail="Not a member of this silo")

    # Get silo name for the prompt
    silo_res = db.table("groups").select("name").eq("id", silo_id).limit(1).execute()
    silo_name = silo_res.data[0]["name"] if silo_res.data else "Family Silo"

    result = check_and_facilitate(silo_id, silo_name, db)
    return result


# ── Interactive Concierge (SSE) ───────────────────────────────────────────────

@router.get("/concierge/stream")
async def concierge_stream(
    q: str = Query(..., description="The user's question"),
    silo_id: str | None = Query(None, description="Optional silo scope for RAG"),
    db: Client = Depends(get_db),
    current_user_id: str = Depends(get_current_user_id),
):
    """
    Server-Sent Events endpoint. Streams a Gemini answer grounded in silo posts.
    Frontend calls this with fetch() + ReadableStream (not EventSource, to send auth header).

    Response format: plain text chunks (each chunk is raw text, not data: ... SSE format)
    so the frontend can simply concatenate them into a message bubble.
    """
    # Verify silo membership if silo_id provided
    if silo_id:
        membership = (
            db.table("group_members")
            .select("id")
            .eq("group_id", silo_id)
            .eq("user_id", current_user_id)
            .limit(1)
            .execute()
        )
        if not membership.data:
            raise HTTPException(status_code=403, detail="Not a member of this silo")

    async def event_stream():
        async for chunk in stream_concierge_answer(q, current_user_id, silo_id, db):
            # SSE format: each chunk as a data event
            yield f"data: {json.dumps({'chunk': chunk})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # Disable nginx buffering for SSE
        },
    )


# ── Indexing (Admin / on silo creation) ──────────────────────────────────────

@router.post("/index/{silo_id}")
def index_silo(
    silo_id: str,
    db: Client = Depends(get_db),
    current_user_id: str = Depends(get_current_user_id),
):
    """
    Index all posts in a silo into pgvector for RAG.
    Can be called on silo creation or as an admin refresh.
    """
    membership = (
        db.table("group_members")
        .select("role")
        .eq("group_id", silo_id)
        .eq("user_id", current_user_id)
        .limit(1)
        .execute()
    )
    if not membership.data:
        raise HTTPException(status_code=403, detail="Not a member of this silo")

    result = index_silo_posts(silo_id, db)
    return result
