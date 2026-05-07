"""
Moderation Management Router
Provides:
  - Status polling endpoint (for frontend async UX)
  - Admin-only quarantine inspection, release, and confirm endpoints
"""

from fastapi import APIRouter, Depends, HTTPException
from supabase import Client

from app.utils.database import get_db
from app.utils.dependencies import get_current_user_id
from app.utils.moderation import ModerationResult, log_moderation_event

router = APIRouter(
    prefix="/moderation",
    tags=["Moderation"]
)

QUARANTINE_BUCKET = "media-quarantine"
PUBLIC_BUCKET = "group-media"


# ── Helper: Verify the caller is an admin of the post's silo ─────────────────

def _require_silo_admin(post_id: str, current_user_id: str, db: Client) -> dict:
    """
    Looks up the post and confirms the caller is an admin/creator of its silo.
    Returns the post row on success, raises 403/404 on failure.
    """
    post_resp = db.table("posts") \
        .select("id, group_id, post_type, image_path, moderation_status, author_id") \
        .eq("id", post_id) \
        .execute()

    if not post_resp.data:
        raise HTTPException(status_code=404, detail="Post not found.")

    post = post_resp.data[0]
    group_id = post["group_id"]

    membership = db.table("group_members") \
        .select("role") \
        .eq("group_id", group_id) \
        .eq("user_id", current_user_id) \
        .execute()

    if not membership.data or membership.data[0]["role"] not in ("admin", "creator"):
        raise HTTPException(
            status_code=403,
            detail="Only Silo admins can perform quarantine actions."
        )

    return post


# ── 1. STATUS POLL ────────────────────────────────────────────────────────────

@router.get("/status/{post_id}")
def get_moderation_status(
    post_id: str,
    db: Client = Depends(get_db),
    current_user_id: str = Depends(get_current_user_id),
):
    """
    Returns the current moderation_status of a post.
    Used by the frontend to poll until async media analysis completes.
    Only the post author or a Silo admin can query this.
    """
    post_resp = db.table("posts") \
        .select("id, moderation_status, author_id, group_id") \
        .eq("id", post_id) \
        .execute()

    if not post_resp.data:
        raise HTTPException(status_code=404, detail="Post not found.")

    post = post_resp.data[0]

    # Only the author or a silo admin may see detailed moderation state
    is_author = str(post["author_id"]) == str(current_user_id)
    if not is_author:
        membership = db.table("group_members") \
            .select("role") \
            .eq("group_id", post["group_id"]) \
            .eq("user_id", current_user_id) \
            .execute()
        is_admin = membership.data and membership.data[0]["role"] in ("admin", "creator")
        if not is_admin:
            raise HTTPException(
                status_code=403,
                detail="Only the post author or a Silo admin can poll moderation status."
            )

    # Fetch latest audit log entry for this post (most recent verdict)
    log_resp = db.table("moderation_logs") \
        .select("content_type, verdict, flags, reason, reviewed_by, created_at") \
        .eq("post_id", post_id) \
        .order("created_at", desc=True) \
        .limit(1) \
        .execute()

    latest_log = log_resp.data[0] if log_resp.data else None

    return {
        "post_id": post_id,
        "moderation_status": post["moderation_status"],
        "latest_audit": latest_log,
    }


# ── 2. LIST QUARANTINED POSTS (Admin) ─────────────────────────────────────────

@router.get("/quarantine")
def list_quarantined_posts(
    db: Client = Depends(get_db),
    current_user_id: str = Depends(get_current_user_id),
):
    """
    Returns all quarantined posts across silos where the caller is an admin.
    Includes the latest audit log entry (flags + reason) for each post.
    """
    # Find all silos where this user is admin/creator
    memberships = db.table("group_members") \
        .select("group_id, role") \
        .eq("user_id", current_user_id) \
        .in_("role", ["admin", "creator"]) \
        .execute()

    if not memberships.data:
        return {"quarantined_posts": [], "total": 0}

    admin_group_ids = [m["group_id"] for m in memberships.data]

    # Fetch quarantined posts in those silos
    posts_resp = db.table("posts") \
        .select("id, group_id, post_type, image_path, caption, author_id, created_at, moderation_status, profiles(username, avatar_url), groups(name)") \
        .in_("group_id", admin_group_ids) \
        .eq("moderation_status", "quarantined") \
        .order("created_at", desc=True) \
        .execute()

    posts = posts_resp.data or []

    if not posts:
        return {"quarantined_posts": [], "total": 0}

    # Batch-fetch latest audit logs for each quarantined post
    post_ids = [p["id"] for p in posts]
    logs_resp = db.table("moderation_logs") \
        .select("post_id, flags, reason, content_type, created_at") \
        .in_("post_id", post_ids) \
        .order("created_at", desc=True) \
        .execute()

    # Build a map: post_id → most recent log (already desc order)
    log_map: dict = {}
    for log in (logs_resp.data or []):
        pid = log["post_id"]
        if pid not in log_map:
            log_map[pid] = log  # first entry per post_id is the most recent

    # Enrich each quarantined post with its audit log
    enriched = []
    for p in posts:
        pid = p["id"]
        enriched.append({
            **p,
            "silo_name": (p.get("groups") or {}).get("name", "Unknown Silo"),
            "audit": log_map.get(pid),
        })

    return {"quarantined_posts": enriched, "total": len(enriched)}


# ── 3. RELEASE FROM QUARANTINE (Admin Override) ───────────────────────────────

@router.post("/quarantine/{post_id}/release")
def release_from_quarantine(
    post_id: str,
    db: Client = Depends(get_db),
    current_user_id: str = Depends(get_current_user_id),
):
    """
    Admin overrides the AI quarantine decision.
    Moves the media file back to the public bucket and marks the post approved.
    An audit log entry is written with reviewed_by = admin user_id.
    """
    post = _require_silo_admin(post_id, current_user_id, db)

    if post["moderation_status"] != "quarantined":
        raise HTTPException(
            status_code=400,
            detail=f"Post is not quarantined (current status: {post['moderation_status']})."
        )

    media_path = post.get("image_path", "")
    is_real_file = media_path and not media_path.startswith("__")

    # Move file from quarantine → public bucket
    if is_real_file:
        try:
            file_bytes = db.storage.from_(QUARANTINE_BUCKET).download(media_path)
            db.storage.from_(PUBLIC_BUCKET).upload(media_path, file_bytes)
            db.storage.from_(QUARANTINE_BUCKET).remove([media_path])
        except Exception as e:
            # Non-fatal: continue even if file move fails
            print(f"[MODERATION RELEASE] Storage move failed for {media_path}: {e}")

    # Update post status
    db.table("posts").update({"moderation_status": "approved"}).eq("id", post_id).execute()

    # Write admin override audit log
    override_result = ModerationResult(
        safe=True,
        reason=f"Manually approved by admin {current_user_id}",
        flags=[]
    )
    log_moderation_event(db, post_id, post["post_type"], override_result, reviewed_by=current_user_id)

    return {
        "message": "Post released from quarantine and approved.",
        "post_id": post_id,
        "moderation_status": "approved",
    }


# ── 4. CONFIRM QUARANTINE / PERMANENT DELETE (Admin) ─────────────────────────

@router.post("/quarantine/{post_id}/confirm")
def confirm_quarantine(
    post_id: str,
    db: Client = Depends(get_db),
    current_user_id: str = Depends(get_current_user_id),
):
    """
    Admin permanently confirms the quarantine decision.
    Deletes the media file from the quarantine bucket and removes the post record.
    """
    post = _require_silo_admin(post_id, current_user_id, db)

    if post["moderation_status"] != "quarantined":
        raise HTTPException(
            status_code=400,
            detail=f"Post is not quarantined (current status: {post['moderation_status']})."
        )

    media_path = post.get("image_path", "")
    is_real_file = media_path and not media_path.startswith("__")

    # Permanently delete file from quarantine bucket
    if is_real_file:
        try:
            db.storage.from_(QUARANTINE_BUCKET).remove([media_path])
        except Exception as e:
            print(f"[MODERATION CONFIRM] Failed to delete quarantine file {media_path}: {e}")

    # Delete post record (cascade will clean up likes, comments, etc.)
    db.table("posts").delete().eq("id", post_id).execute()

    return {
        "message": "Quarantined post permanently deleted.",
        "post_id": post_id,
    }
