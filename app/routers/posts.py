from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from supabase import Client
from typing import Optional
from app.utils.database import get_db
from app.utils.dependencies import get_current_user_id

router = APIRouter(
    prefix="/posts",
    tags=["Posts"]
)

# ─── Pydantic Schemas ───

class PostCreate(BaseModel):
    group_id: str
    post_type: str = "photo"           # "photo" | "text" | "proposal"
    image_path: Optional[str] = None   # Supabase Storage path for photos
    caption: Optional[str] = None      # Caption (photo), content (text), or proposal description
    gradient: Optional[str] = None     # CSS gradient class for text posts
    is_public: bool = True             # Sharing scope

class CommentCreate(BaseModel):
    content: str

class VoteCreate(BaseModel):
    vote: str  # "up" or "down"


# ─── 1. CREATE POST ───

@router.post("/")
def create_post(
    post: PostCreate,
    db: Client = Depends(get_db),
    current_user_id: str = Depends(get_current_user_id)
):
    """Creates a Photo, Text, or Proposal post."""
    
    # Membership check
    membership = db.table("group_members").select("role").eq("group_id", post.group_id).eq("user_id", current_user_id).execute()
    if not membership.data:
        raise HTTPException(status_code=403, detail="You are not a member of this Silo.")

    try:
        insert_data = {
            "group_id": post.group_id,
            "author_id": current_user_id,
            "post_type": post.post_type,
            "image_path": post.image_path or f"__{post.post_type}__",  # NOT NULL column — use marker for non-photo posts
            "caption": post.caption,
            "gradient": post.gradient,
            "is_public": post.is_public,
        }

        # Proposals start as "pending"
        if post.post_type == "proposal":
            insert_data["proposal_status"] = "pending"

        result = db.table("posts").insert(insert_data).execute()
        return {"message": "Post created successfully", "post": result.data[0]}

    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error creating post: {str(e)}")


# ─── 2. GET SILO FEED ───

@router.get("/group/{group_id}")
def get_group_feed(
    group_id: str,
    db: Client = Depends(get_db),
    current_user_id: str = Depends(get_current_user_id)
):
    """Fetches the full feed with like counts, comment counts, vote data, and user's own interactions."""

    membership = db.table("group_members").select("role").eq("group_id", group_id).eq("user_id", current_user_id).execute()
    if not membership.data:
        raise HTTPException(status_code=403, detail="You cannot view posts for a Silo you are not in.")

    try:
        user_role = membership.data[0]["role"]
        
        # Get total members for proposal threshold calculation
        members_resp = db.table("group_members").select("user_id").eq("group_id", group_id).execute()
        total_members = len(members_resp.data) if members_resp.data else 0

        # Fetch posts with author profile
        feed_resp = db.table("posts") \
            .select("id, post_type, image_path, caption, gradient, is_public, proposal_status, created_at, author_id, profiles(username, avatar_url)") \
            .eq("group_id", group_id) \
            .order("created_at", desc=True) \
            .execute()

        posts = feed_resp.data or []
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


# ─── 2.5 GET GLOBAL HOME FEED ───

@router.get("/feed/home")
def get_home_feed(
    db: Client = Depends(get_db),
    current_user_id: str = Depends(get_current_user_id)
):
    """Fetches the aggregated public feed from all Silos the user belongs to."""
    try:
        # Get all silos the user is a member of
        memberships = db.table("group_members").select("group_id, role").eq("user_id", current_user_id).execute()
        if not memberships.data:
            return {"posts": []}
            
        group_ids = [m["group_id"] for m in memberships.data]
        role_map = {m["group_id"]: m["role"] for m in memberships.data}
        
        # Fetch public posts from these silos
        feed_resp = db.table("posts") \
            .select("id, group_id, post_type, image_path, caption, gradient, is_public, proposal_status, created_at, author_id, profiles(username, avatar_url), groups(name)") \
            .in_("group_id", group_ids) \
            .eq("is_public", True) \
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


# ─── 3. TOGGLE LIKE ───

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


# ─── 4. ADD COMMENT ───

@router.post("/{post_id}/comment")
def add_comment(
    post_id: str,
    comment: CommentCreate,
    db: Client = Depends(get_db),
    current_user_id: str = Depends(get_current_user_id)
):
    """Add a comment to a post."""
    try:
        result = db.table("post_comments").insert({
            "post_id": post_id,
            "user_id": current_user_id,
            "content": comment.content
        }).execute()
        return {"message": "Comment added", "comment": result.data[0]}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ─── 5. GET COMMENTS ───

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


# ─── 6. CAST PROPOSAL VOTE ───

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


# ─── 7. DELETE POST ───

@router.delete("/{post_id}")
def delete_post(
    post_id: str,
    db: Client = Depends(get_db),
    current_user_id: str = Depends(get_current_user_id)
):
    """Deletes a post if the user is the author or a Silo admin."""
    try:
        # Fetch the post to get author and group_id
        post_resp = db.table("posts").select("author_id, group_id").eq("id", post_id).execute()
        if not post_resp.data:
            raise HTTPException(status_code=404, detail="Post not found.")
            
        post = post_resp.data[0]
        
        # Check permissions
        is_author = str(post["author_id"]) == str(current_user_id)
        
        membership = db.table("group_members").select("role").eq("group_id", post["group_id"]).eq("user_id", current_user_id).execute()
        user_role = membership.data[0]["role"] if membership.data else None
        
        if not (is_author or user_role in ["admin", "creator"]):
            raise HTTPException(status_code=403, detail="Not authorized to delete this post.")
            
        # Delete from Supabase
        db.table("posts").delete().eq("id", post_id).execute()
        return {"message": "Post deleted successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))