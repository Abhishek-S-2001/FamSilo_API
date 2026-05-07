from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel
from supabase import Client
from typing import Optional
from app.utils.database import get_db
from app.utils.dependencies import get_current_user_id
from app.utils.moderation import moderate_text, moderate_image, moderate_video, log_moderation_event

router = APIRouter(
    prefix="/posts",
    tags=["Posts"]
)

# ─── Pydantic Schemas ───────────────────────────────────────────────────────

class PostCreate(BaseModel):
    group_id: str
    post_type: str = "photo"           # "photo" | "text" | "video" | "proposal"
    image_path: Optional[str] = None   # Supabase Storage path for photos
    video_path: Optional[str] = None   # Supabase Storage path for videos (stored in image_path column)
    caption: Optional[str] = None      # Caption / text content / proposal description
    gradient: Optional[str] = None     # CSS gradient class for text posts
    is_public: bool = True             # Sharing scope

class CommentCreate(BaseModel):
    content: str

class VoteCreate(BaseModel):
    vote: str  # "up" or "down"


# ─── Moderation Helpers ──────────────────────────────────────────────────────

QUARANTINE_BUCKET = "media-quarantine"
PUBLIC_BUCKET = "group-media"


def _move_to_quarantine(db: Client, file_path: str) -> None:
    """Download a file from the public bucket and re-upload it to quarantine."""
    try:
        data = db.storage.from_(PUBLIC_BUCKET).download(file_path)
        db.storage.from_(QUARANTINE_BUCKET).upload(file_path, data)
        db.storage.from_(PUBLIC_BUCKET).remove([file_path])
    except Exception:
        pass  # Non-fatal — the DB row will still be marked quarantined


def _run_media_moderation(post_id: str, media_path: str, post_type: str, db: Client) -> None:
    """
    Background task: download media from storage, run AI moderation,
    then update the post's moderation_status in the DB.
    """
    try:
        # Download file bytes from Supabase Storage
        file_bytes = db.storage.from_(PUBLIC_BUCKET).download(media_path)

        if post_type == "photo":
            # Detect MIME from extension
            ext = media_path.rsplit(".", 1)[-1].lower()
            mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                    "webp": "image/webp", "gif": "image/gif"}.get(ext, "image/jpeg")
            result = moderate_image(file_bytes, mime_type=mime)
        elif post_type == "video":
            ext = media_path.rsplit(".", 1)[-1].lower()
            mime = {"mp4": "video/mp4", "webm": "video/webm"}.get(ext, "video/mp4")
            result = moderate_video(file_bytes, mime_type=mime)
        else:
            # Shouldn't happen, but default to safe
            return

        if result.safe:
            db.table("posts").update({"moderation_status": "approved"}).eq("id", post_id).execute()
            log_moderation_event(db, post_id, post_type, result)
        else:
            db.table("posts").update({"moderation_status": "quarantined"}).eq("id", post_id).execute()
            _move_to_quarantine(db, media_path)
            log_moderation_event(db, post_id, post_type, result)
            print(f"[MODERATION] Post {post_id} quarantined. Flags: {result.flags}. Reason: {result.reason}")

    except Exception as e:
        # On any unexpected error, approve so content isn't silently blocked
        db.table("posts").update({"moderation_status": "approved"}).eq("id", post_id).execute()
        print(f"[MODERATION] Background task error for post {post_id}: {e}")


# ─── 1. CREATE POST ──────────────────────────────────────────────────────────

@router.post("/")
def create_post(
    post: PostCreate,
    background_tasks: BackgroundTasks,
    db: Client = Depends(get_db),
    current_user_id: str = Depends(get_current_user_id)
):
    """Creates a Photo, Text, Video, or Proposal post with AI moderation gate."""

    # Membership check
    membership = db.table("group_members").select("role").eq("group_id", post.group_id).eq("user_id", current_user_id).execute()
    if not membership.data:
        raise HTTPException(status_code=403, detail="You are not a member of this Silo.")

    # ── Step 1: Synchronous text/caption moderation ──────────────────────────
    # Run BEFORE any DB write. A flagged caption is rejected immediately.
    if post.caption and post.caption.strip():
        text_result = moderate_text(post.caption)
        if not text_result.safe:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "content_flagged",
                    "message": "Your post was flagged by our content moderation system and could not be published.",
                    "flags": text_result.flags,
                }
            )

    try:
        # ── Step 2: Resolve media path ──────────────────────────────────────
        if post.post_type == "video":
            media_path = post.video_path or "__video__"
        elif post.post_type == "photo":
            media_path = post.image_path or "__photo__"
        else:
            media_path = f"__{post.post_type}__"  # text / proposal markers

        # ── Step 3: Determine initial moderation status ─────────────────────
        # Text & proposal: caption already cleared above → approved immediately.
        # Photo & video: async moderation → starts as "pending".
        has_real_media = media_path and not media_path.startswith("__")
        if post.post_type in ("photo", "video") and has_real_media:
            initial_status = "pending"
        else:
            initial_status = "approved"

        insert_data = {
            "group_id": post.group_id,
            "author_id": current_user_id,
            "post_type": post.post_type,
            "image_path": media_path,
            "caption": post.caption,
            "gradient": post.gradient,
            "is_public": post.is_public,
            "moderation_status": initial_status,
        }

        # Proposals start as "pending" proposal_status (separate from moderation)
        if post.post_type == "proposal":
            insert_data["proposal_status"] = "pending"

        result = db.table("posts").insert(insert_data).execute()
        post_record = result.data[0]
        post_id = post_record["id"]

        # ── Step 4: Dispatch async media moderation ─────────────────────────
        if initial_status == "pending":
            background_tasks.add_task(
                _run_media_moderation,
                post_id=post_id,
                media_path=media_path,
                post_type=post.post_type,
                db=db,
            )

        return {
            "message": "Post created successfully",
            "post": post_record,
            "moderation_status": initial_status,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error creating post: {str(e)}")


# ─── 2. GET SILO FEED ────────────────────────────────────────────────────────

@router.get("/group/{group_id}")
def get_group_feed(
    group_id: str,
    db: Client = Depends(get_db),
    current_user_id: str = Depends(get_current_user_id)
):
    """Fetches the full feed — shows approved posts + the author's own pending posts."""

    membership = db.table("group_members").select("role").eq("group_id", group_id).eq("user_id", current_user_id).execute()
    if not membership.data:
        raise HTTPException(status_code=403, detail="You cannot view posts for a Silo you are not in.")

    try:
        user_role = membership.data[0]["role"]

        # Get total members for proposal threshold calculation
        members_resp = db.table("group_members").select("user_id").eq("group_id", group_id).execute()
        total_members = len(members_resp.data) if members_resp.data else 0

        # Fetch posts: approved for everyone + pending/quarantined for the author only
        feed_resp = db.table("posts") \
            .select("id, post_type, image_path, caption, gradient, is_public, proposal_status, moderation_status, created_at, author_id, profiles(username, avatar_url)") \
            .eq("group_id", group_id) \
            .order("created_at", desc=True) \
            .execute()

        # Filter: show approved posts to all, show pending/quarantined only to author
        posts = [
            p for p in (feed_resp.data or [])
            if p.get("moderation_status") == "approved"
            or str(p.get("author_id")) == str(current_user_id)
        ]
        post_ids = [p["id"] for p in posts]

        # Batch fetch likes, comments, and votes for all posts in one go
        likes_map = {}
        comments_map = {}
        votes_map = {}
        user_likes = set()
        user_votes = {}

        if post_ids:
            # All likes
            all_likes = db.table("post_likes").select("post_id, user_id").in_("post_id", post_ids).execute()
            for l in (all_likes.data or []):
                likes_map[l["post_id"]] = likes_map.get(l["post_id"], 0) + 1
                if l["user_id"] == current_user_id:
                    user_likes.add(l["post_id"])

            # All comment counts
            all_comments = db.table("post_comments").select("post_id").in_("post_id", post_ids).execute()
            for c in (all_comments.data or []):
                comments_map[c["post_id"]] = comments_map.get(c["post_id"], 0) + 1

            # All proposal votes
            proposal_ids = [p["id"] for p in posts if p.get("post_type") == "proposal"]
            if proposal_ids:
                all_votes = db.table("proposal_votes").select("post_id, user_id, vote").in_("post_id", proposal_ids).execute()
                for v in (all_votes.data or []):
                    pid = v["post_id"]
                    if pid not in votes_map:
                        votes_map[pid] = {"up": 0, "down": 0}
                    if v["vote"] == "up":
                        votes_map[pid]["up"] += 1
                    else:
                        votes_map[pid]["down"] += 1
                    if v["user_id"] == current_user_id:
                        user_votes[pid] = v["vote"]

        # Enrich each post
        enriched = []
        for p in posts:
            pid = p["id"]
            is_author = str(p["author_id"]) == str(current_user_id)
            enriched.append({
                **p,
                "like_count": likes_map.get(pid, 0),
                "comment_count": comments_map.get(pid, 0),
                "liked_by_me": pid in user_likes,
                "upvotes": votes_map.get(pid, {}).get("up", 0),
                "downvotes": votes_map.get(pid, {}).get("down", 0),
                "my_vote": user_votes.get(pid),
                "total_members": total_members,
                "is_author": is_author,
                "can_delete": is_author or user_role in ["admin", "creator"]
            })

        return {"group_id": group_id, "posts": enriched, "total_members": total_members}

    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error fetching feed: {str(e)}")


# ─── 2.5 GET GLOBAL HOME FEED ────────────────────────────────────────────────

@router.get("/feed/home")
def get_home_feed(
    db: Client = Depends(get_db),
    current_user_id: str = Depends(get_current_user_id)
):
    """Fetches the aggregated public feed — only approved posts are surfaced."""
    try:
        # Get all silos the user is a member of
        memberships = db.table("group_members").select("group_id, role").eq("user_id", current_user_id).execute()
        if not memberships.data:
            return {"posts": []}

        group_ids = [m["group_id"] for m in memberships.data]
        role_map = {m["group_id"]: m["role"] for m in memberships.data}

        # Fetch approved public posts from these silos
        feed_resp = db.table("posts") \
            .select("id, group_id, post_type, image_path, caption, gradient, is_public, proposal_status, moderation_status, created_at, author_id, profiles(username, avatar_url), groups(name)") \
            .in_("group_id", group_ids) \
            .eq("is_public", True) \
            .eq("moderation_status", "approved") \
            .order("created_at", desc=True) \
            .limit(50) \
            .execute()

        posts = feed_resp.data or []
        post_ids = [p["id"] for p in posts]

        if not post_ids:
            return {"posts": []}

        # Batch fetch likes, comments, and votes
        likes_map = {}
        comments_map = {}
        votes_map = {}
        user_likes = set()
        user_votes = {}

        # All likes
        all_likes = db.table("post_likes").select("post_id, user_id").in_("post_id", post_ids).execute()
        for l in (all_likes.data or []):
            likes_map[l["post_id"]] = likes_map.get(l["post_id"], 0) + 1
            if l["user_id"] == current_user_id:
                user_likes.add(l["post_id"])

        # All comment counts
        all_comments = db.table("post_comments").select("post_id").in_("post_id", post_ids).execute()
        for c in (all_comments.data or []):
            comments_map[c["post_id"]] = comments_map.get(c["post_id"], 0) + 1

        # All proposal votes
        proposal_ids = [p["id"] for p in posts if p.get("post_type") == "proposal"]
        if proposal_ids:
            all_votes = db.table("proposal_votes").select("post_id, user_id, vote").in_("post_id", proposal_ids).execute()
            for v in (all_votes.data or []):
                pid = v["post_id"]
                if pid not in votes_map:
                    votes_map[pid] = {"up": 0, "down": 0}
                if v["vote"] == "up":
                    votes_map[pid]["up"] += 1
                else:
                    votes_map[pid]["down"] += 1
                if v["user_id"] == current_user_id:
                    user_votes[pid] = v["vote"]

        # Total members per silo (for proposal threshold)
        members_resp = db.table("group_members").select("group_id").in_("group_id", group_ids).execute()
        total_members_map = {}
        for m in (members_resp.data or []):
            gid = m["group_id"]
            total_members_map[gid] = total_members_map.get(gid, 0) + 1

        # Enrich each post
        enriched = []
        for p in posts:
            pid = p["id"]
            gid = p["group_id"]
            is_author = str(p["author_id"]) == str(current_user_id)
            user_role = role_map.get(gid)

            enriched.append({
                **p,
                "silo_name": p.get("groups", {}).get("name") if p.get("groups") else "Unknown Silo",
                "like_count": likes_map.get(pid, 0),
                "comment_count": comments_map.get(pid, 0),
                "liked_by_me": pid in user_likes,
                "upvotes": votes_map.get(pid, {}).get("up", 0),
                "downvotes": votes_map.get(pid, {}).get("down", 0),
                "my_vote": user_votes.get(pid),
                "total_members": total_members_map.get(gid, 0),
                "is_author": is_author,
                "can_delete": is_author or user_role in ["admin", "creator"]
            })

        return {"posts": enriched}

    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error fetching home feed: {str(e)}")


# ─── 2.6 GET USER PROFILE FEED ───────────────────────────────────────────────

@router.get("/user/{target_user_id}")
def get_user_feed(
    target_user_id: str,
    db: Client = Depends(get_db),
    current_user_id: str = Depends(get_current_user_id)
):
    """Fetches the complete timeline of a user for their profile."""
    try:
        # Get all silos the current user is a member of
        memberships = db.table("group_members").select("group_id, role").eq("user_id", current_user_id).execute()
        current_user_group_ids = [m["group_id"] for m in (memberships.data or [])]
        role_map = {m["group_id"]: m["role"] for m in (memberships.data or [])}

        # Build query
        query = db.table("posts") \
            .select("id, group_id, post_type, image_path, caption, gradient, is_public, proposal_status, moderation_status, created_at, author_id, profiles(username, avatar_url), groups(name)") \
            .eq("author_id", target_user_id) \
            .order("created_at", desc=True) \
            .limit(50)

        feed_resp = query.execute()
        posts = feed_resp.data or []

        # Filter privacy & moderation in Python
        visible_posts = []
        for p in posts:
            is_self = target_user_id == current_user_id
            
            # Moderation check: hide quarantined for everyone. 
            # Hide pending unless it's your own profile.
            mod_status = p.get("moderation_status")
            if mod_status == "quarantined":
                continue
            if mod_status == "pending" and not is_self:
                continue
                
            # Privacy check
            if not is_self:
                # You can ONLY see posts from silos you are a member of
                if p.get("group_id") not in current_user_group_ids:
                    continue
                        
            visible_posts.append(p)

        post_ids = [p["id"] for p in visible_posts]

        if not post_ids:
            return {"posts": []}

        # Batch fetch likes, comments, and votes
        likes_map = {}
        comments_map = {}
        votes_map = {}
        user_likes = set()
        user_votes = {}

        all_likes = db.table("post_likes").select("post_id, user_id").in_("post_id", post_ids).execute()
        for l in (all_likes.data or []):
            likes_map[l["post_id"]] = likes_map.get(l["post_id"], 0) + 1
            if l["user_id"] == current_user_id:
                user_likes.add(l["post_id"])

        all_comments = db.table("post_comments").select("post_id").in_("post_id", post_ids).execute()
        for c in (all_comments.data or []):
            comments_map[c["post_id"]] = comments_map.get(c["post_id"], 0) + 1

        proposal_ids = [p["id"] for p in visible_posts if p.get("post_type") == "proposal"]
        if proposal_ids:
            all_votes = db.table("proposal_votes").select("post_id, user_id, vote").in_("post_id", proposal_ids).execute()
            for v in (all_votes.data or []):
                pid = v["post_id"]
                if pid not in votes_map:
                    votes_map[pid] = {"up": 0, "down": 0}
                if v["vote"] == "up":
                    votes_map[pid]["up"] += 1
                else:
                    votes_map[pid]["down"] += 1
                if v["user_id"] == current_user_id:
                    user_votes[pid] = v["vote"]

        # Total members per silo (for proposal threshold)
        all_silo_ids = list(set([p["group_id"] for p in visible_posts]))
        members_resp = db.table("group_members").select("group_id").in_("group_id", all_silo_ids).execute()
        total_members_map = {}
        for m in (members_resp.data or []):
            gid = m["group_id"]
            total_members_map[gid] = total_members_map.get(gid, 0) + 1

        # Enrich
        enriched = []
        for p in visible_posts:
            pid = p["id"]
            gid = p["group_id"]
            is_author = str(p["author_id"]) == str(current_user_id)
            user_role = role_map.get(gid)

            enriched.append({
                **p,
                "silo_name": p.get("groups", {}).get("name") if p.get("groups") else "Unknown Silo",
                "like_count": likes_map.get(pid, 0),
                "comment_count": comments_map.get(pid, 0),
                "liked_by_me": pid in user_likes,
                "upvotes": votes_map.get(pid, {}).get("up", 0),
                "downvotes": votes_map.get(pid, {}).get("down", 0),
                "my_vote": user_votes.get(pid),
                "total_members": total_members_map.get(gid, 0),
                "is_author": is_author,
                "can_delete": is_author or user_role in ["admin", "creator"]
            })

        return {"posts": enriched}

    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error fetching user feed: {str(e)}")


# ─── 3. TOGGLE LIKE ──────────────────────────────────────────────────────────

@router.post("/{post_id}/like")
def toggle_like(
    post_id: str,
    db: Client = Depends(get_db),
    current_user_id: str = Depends(get_current_user_id)
):
    """Toggle like on a post. If already liked → unlike. If not → like."""
    try:
        existing = db.table("post_likes").select("id").eq("post_id", post_id).eq("user_id", current_user_id).execute()

        if existing.data:
            db.table("post_likes").delete().eq("id", existing.data[0]["id"]).execute()
            return {"liked": False, "message": "Like removed"}
        else:
            db.table("post_likes").insert({"post_id": post_id, "user_id": current_user_id}).execute()
            return {"liked": True, "message": "Post liked"}

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ─── 4. ADD COMMENT ──────────────────────────────────────────────────────────

@router.post("/{post_id}/comment")
def add_comment(
    post_id: str,
    comment: CommentCreate,
    db: Client = Depends(get_db),
    current_user_id: str = Depends(get_current_user_id)
):
    """Add a comment to a post — comment text is moderated synchronously."""
    # Moderate comment text before saving
    if comment.content and comment.content.strip():
        text_result = moderate_text(comment.content)
        if not text_result.safe:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "content_flagged",
                    "message": "Your comment was flagged by our content moderation system.",
                    "flags": text_result.flags,
                }
            )

    try:
        result = db.table("post_comments").insert({
            "post_id": post_id,
            "user_id": current_user_id,
            "content": comment.content
        }).execute()
        return {"message": "Comment added", "comment": result.data[0]}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ─── 5. GET COMMENTS ─────────────────────────────────────────────────────────

@router.get("/{post_id}/comments")
def get_comments(
    post_id: str,
    db: Client = Depends(get_db),
    current_user_id: str = Depends(get_current_user_id)
):
    """Fetches all comments for a post with author profiles."""
    try:
        resp = db.table("post_comments") \
            .select("id, content, created_at, user_id, profiles(username, avatar_url)") \
            .eq("post_id", post_id) \
            .order("created_at", desc=False) \
            .execute()
        return {"comments": resp.data or []}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ─── 6. CAST PROPOSAL VOTE ───────────────────────────────────────────────────

@router.post("/{post_id}/vote")
def cast_vote(
    post_id: str,
    vote_data: VoteCreate,
    db: Client = Depends(get_db),
    current_user_id: str = Depends(get_current_user_id)
):
    """Cast or change a vote on a proposal. Auto-checks 40% threshold after each vote."""
    if vote_data.vote not in ("up", "down"):
        raise HTTPException(status_code=400, detail="Vote must be 'up' or 'down'.")

    try:
        # Check if post is a proposal
        post = db.table("posts").select("id, post_type, group_id, proposal_status").eq("id", post_id).execute()
        if not post.data or post.data[0]["post_type"] != "proposal":
            raise HTTPException(status_code=400, detail="This post is not a proposal.")

        post_row = post.data[0]
        if post_row.get("proposal_status") == "passed":
            raise HTTPException(status_code=400, detail="This proposal has already passed.")

        # Upsert vote (delete old, insert new)
        existing = db.table("proposal_votes").select("id").eq("post_id", post_id).eq("user_id", current_user_id).execute()
        if existing.data:
            db.table("proposal_votes").delete().eq("id", existing.data[0]["id"]).execute()

        db.table("proposal_votes").insert({
            "post_id": post_id,
            "user_id": current_user_id,
            "vote": vote_data.vote
        }).execute()

        # ── 40% Threshold Check ──
        group_id = post_row["group_id"]
        members_resp = db.table("group_members").select("user_id").eq("group_id", group_id).execute()
        total_members = len(members_resp.data) if members_resp.data else 0

        upvotes_resp = db.table("proposal_votes").select("id").eq("post_id", post_id).eq("vote", "up").execute()
        upvote_count = len(upvotes_resp.data) if upvotes_resp.data else 0

        new_status = post_row.get("proposal_status", "pending")
        if total_members > 0 and (upvote_count / total_members) >= 0.4:
            new_status = "passed"
            db.table("posts").update({"proposal_status": "passed"}).eq("id", post_id).execute()

        return {
            "message": "Vote recorded",
            "vote": vote_data.vote,
            "upvotes": upvote_count,
            "total_members": total_members,
            "proposal_status": new_status
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ─── 7. DELETE POST ──────────────────────────────────────────────────────────

@router.delete("/{post_id}")
def delete_post(
    post_id: str,
    db: Client = Depends(get_db),
    current_user_id: str = Depends(get_current_user_id)
):
    """Deletes a post (and its storage file) if the user is the author or a Silo admin."""
    try:
        # Fetch the post — include image_path and post_type for storage cleanup
        post_resp = db.table("posts").select("author_id, group_id, post_type, image_path, moderation_status").eq("id", post_id).execute()
        if not post_resp.data:
            raise HTTPException(status_code=404, detail="Post not found.")

        post = post_resp.data[0]

        # Check permissions
        is_author = str(post["author_id"]) == str(current_user_id)

        membership = db.table("group_members").select("role").eq("group_id", post["group_id"]).eq("user_id", current_user_id).execute()
        user_role = membership.data[0]["role"] if membership.data else None

        if not (is_author or user_role in ["admin", "creator"]):
            raise HTTPException(status_code=403, detail="Not authorized to delete this post.")

        # ── Storage Cleanup ──
        post_type = post.get("post_type", "photo")
        media_path = post.get("image_path", "")
        mod_status = post.get("moderation_status", "approved")
        is_real_file = media_path and not media_path.startswith("__")

        if post_type in ("photo", "video") and is_real_file:
            try:
                # File may be in public bucket or quarantine bucket
                bucket = QUARANTINE_BUCKET if mod_status == "quarantined" else PUBLIC_BUCKET
                db.storage.from_(bucket).remove([media_path])
            except Exception:
                pass  # Non-fatal — DB delete must still proceed

        # Delete the post record from DB
        db.table("posts").delete().eq("id", post_id).execute()
        return {"message": "Post deleted successfully"}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))