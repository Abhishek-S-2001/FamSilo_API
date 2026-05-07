"""
Daily Briefing Agent
Generates a personalized AI summary of unseen posts for a user.
Results are cached per-user per-day in the daily_briefings table.
"""
from datetime import date
from supabase import Client
from app.utils.ai_agent import generate_text

_BRIEFING_SYSTEM = """You are FamSilo's friendly daily briefing assistant.
Your job is to write a warm, personalized 2-3 sentence summary of what's been happening
in a user's private family network. Be conversational, upbeat, and specific — mention
names and topics where available. Never make up information not in the post data.
End with one gentle call-to-action (e.g. "Jump in and share your thoughts!").
Keep the total response under 120 words."""


def get_or_create_briefing(user_id: str, db: Client) -> dict:
    """
    Returns today's briefing for the user.
    If it doesn't exist yet, generates one from unseen posts and caches it.
    """
    today = date.today().isoformat()

    # ── 1. Check cache ───────────────────────────────────────────────────────
    existing = (
        db.table("daily_briefings")
        .select("summary, post_count, created_at")
        .eq("user_id", user_id)
        .eq("briefing_date", today)
        .limit(1)
        .execute()
    )
    if existing.data:
        row = existing.data[0]
        return {
            "summary": row["summary"],
            "post_count": row["post_count"],
            "cached": True,
            "date": today,
        }

    # ── 2. Fetch unseen posts (last 24h from user's silos) ───────────────────
    posts = _fetch_recent_posts(user_id, db)

    # ── 3. Generate briefing ─────────────────────────────────────────────────
    if not posts:
        summary = (
            "All quiet in your silos today 🌿 — it's a perfect time to share a memory, "
            "start a conversation, or post a proposal. Your family is waiting to hear from you!"
        )
    else:
        post_snippets = "\n".join(
            f"- [{p.get('silo_name', 'Silo')}] {p['author']}: {p['snippet']}"
            for p in posts[:10]
        )
        prompt = (
            f"Here are the recent posts from this user's family network:\n\n"
            f"{post_snippets}\n\n"
            f"Write a warm daily briefing summary for the user."
        )
        summary = generate_text(prompt, system_instruction=_BRIEFING_SYSTEM)

    # ── 4. Cache result ──────────────────────────────────────────────────────
    try:
        db.table("daily_briefings").upsert({
            "user_id": user_id,
            "briefing_date": today,
            "summary": summary,
            "post_count": len(posts),
        }, on_conflict="user_id,briefing_date").execute()
    except Exception as e:
        print(f"[Briefing] Cache write failed (non-fatal): {e}")

    return {
        "summary": summary,
        "post_count": len(posts),
        "cached": False,
        "date": today,
    }


def _fetch_recent_posts(user_id: str, db: Client) -> list[dict]:
    """Fetch posts from the last 24h across all silos the user belongs to."""
    try:
        # Get user's silos
        memberships = (
            db.table("group_members")
            .select("group_id, groups(name)")
            .eq("user_id", user_id)
            .execute()
        )
        silo_ids = [m["group_id"] for m in (memberships.data or [])]
        silo_names = {
            m["group_id"]: (m.get("groups") or {}).get("name", "Family")
            for m in (memberships.data or [])
        }

        if not silo_ids:
            return []

        # Fetch recent posts (last 24h) from those silos
        from datetime import datetime, timedelta, timezone
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()

        posts_res = (
            db.table("posts")
            .select("id, caption, group_id, profiles(username)")
            .in_("group_id", silo_ids)
            .eq("moderation_status", "approved")
            .eq("is_ai_generated", False)
            .gte("created_at", since)
            .order("created_at", desc=True)
            .limit(15)
            .execute()
        )

        result = []
        for p in (posts_res.data or []):
            caption = p.get("caption") or ""
            if not caption:
                continue
            author = (p.get("profiles") or {}).get("username", "Someone")
            result.append({
                "silo_name": silo_names.get(p["group_id"], "Family"),
                "author": author,
                "snippet": caption[:120],
            })
        return result

    except Exception as e:
        print(f"[Briefing] _fetch_recent_posts error: {e}")
        return []
