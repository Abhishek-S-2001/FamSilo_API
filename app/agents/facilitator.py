"""
Silo Facilitator Agent
Keeps silos alive by generating creative content when no posts appear for 24h.
Triggered by the frontend on silo open (once per silo per day).
"""
from datetime import date, datetime, timedelta, timezone
import uuid
from supabase import Client
from app.utils.ai_agent import generate_text

_FACILITATOR_SYSTEM = """You are the FamSilo community facilitator — a friendly AI that keeps
family conversations going. Generate ONE short, engaging piece of content for a family group chat.

Rotate between these styles (pick one that feels fresh and different):
1. A thought-provoking question the family can vote on as a proposal
2. A light-hearted joke or fun fact
3. A "This week's challenge" — a fun activity for the family
4. A nostalgic conversation starter ("What's your favourite family memory of...?")
5. A would-you-rather question

Keep it SHORT (max 2 sentences), warm, and family-friendly.
Do NOT include greetings or sign-offs — just the content itself.
Respond with JSON: {"type": "proposal|text", "content": "...your content..."}"""


def check_and_facilitate(silo_id: str, silo_name: str, db: Client) -> dict:
    """
    Check if the silo needs facilitator content.
    Returns whether content was posted and the post id if so.
    """
    today = date.today().isoformat()

    # ── 1. Already ran today? ─────────────────────────────────────────────────
    existing = (
        db.table("facilitator_runs")
        .select("triggered, post_id")
        .eq("silo_id", silo_id)
        .eq("run_date", today)
        .limit(1)
        .execute()
    )
    if existing.data:
        run = existing.data[0]
        return {"triggered": run["triggered"], "post_id": run.get("post_id"), "cached": True}

    # ── 2. Check for organic activity in last 24h ────────────────────────────
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    recent = (
        db.table("posts")
        .select("id")
        .eq("group_id", silo_id)
        .eq("is_ai_generated", False)
        .gte("created_at", since)
        .limit(1)
        .execute()
    )

    if recent.data:
        # Organic content exists — record non-trigger run and return
        _record_run(db, silo_id, today, triggered=False, post_id=None)
        return {"triggered": False, "post_id": None, "cached": False}

    # ── 3. Generate content ───────────────────────────────────────────────────
    prompt = (
        f"Generate engaging content for a family group called '{silo_name}'. "
        f"The group has been quiet for the past 24 hours."
    )
    raw = generate_text(prompt, system_instruction=_FACILITATOR_SYSTEM)

    # Parse JSON response
    import json
    post_type = "text"
    content = raw
    try:
        parsed = json.loads(raw)
        post_type = parsed.get("type", "text")
        content = parsed.get("content", raw)
    except Exception:
        # Fallback: treat entire response as text content
        post_type = "text"
        content = raw.strip()

    # ── 4. Get the facilitator bot user id ───────────────────────────────────
    bot_user_id = _get_or_create_bot_user(db)
    if not bot_user_id:
        return {"triggered": False, "post_id": None, "cached": False}

    # ── 5. Insert post ────────────────────────────────────────────────────────
    post_data = {
        "group_id": silo_id,
        "user_id": bot_user_id,
        "post_type": post_type,
        "caption": content,
        "image_path": f"__{post_type}__",
        "is_public": True,
        "moderation_status": "approved",  # AI-generated content is pre-approved
        "is_ai_generated": True,
        "ai_agent": "facilitator",
    }
    if post_type == "proposal":
        post_data["proposal_status"] = "pending"

    try:
        result = db.table("posts").insert(post_data).execute()
        post_id = result.data[0]["id"] if result.data else None
        _record_run(db, silo_id, today, triggered=True, post_id=post_id)
        return {"triggered": True, "post_id": post_id, "cached": False}
    except Exception as e:
        print(f"[Facilitator] Insert failed: {e}")
        return {"triggered": False, "post_id": None, "cached": False}


def _record_run(db: Client, silo_id: str, today: str, triggered: bool, post_id) -> None:
    try:
        db.table("facilitator_runs").upsert({
            "silo_id": silo_id,
            "run_date": today,
            "triggered": triggered,
            "post_id": post_id,
        }, on_conflict="silo_id,run_date").execute()
    except Exception as e:
        print(f"[Facilitator] _record_run failed (non-fatal): {e}")


def _get_or_create_bot_user(db: Client) -> str | None:
    """
    Get the facilitator bot user id from profiles.
    The bot profile must exist in the DB with username='silo_facilitator_bot'.
    Returns None if not found (graceful degradation).
    """
    try:
        res = (
            db.table("profiles")
            .select("id")
            .eq("username", "silo_facilitator_bot")
            .limit(1)
            .execute()
        )
        if res.data:
            return res.data[0]["id"]
        return None
    except Exception as e:
        print(f"[Facilitator] _get_or_create_bot_user error: {e}")
        return None
