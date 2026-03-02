from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel
from supabase import Client
from typing import Optional
from app.database import get_db

from app.dependencies import get_current_user_id

router = APIRouter(
    prefix="/posts",
    tags=["Posts"]
)

# --- Pydantic Schemas ---
class PostCreate(BaseModel):
    group_id: str
    image_path: str  # The reference path from Supabase Storage
    caption: Optional[str] = None


# --- Endpoints ---

@router.post("/")
def create_post(
    post: PostCreate, 
    db: Client = Depends(get_db), 
    current_user_id: str = Depends(get_current_user_id)
):
    """Creates a new post tied to a specific group."""
    
    # 1. Verify the user is actually a member of the group they are posting to
    membership_check = db.table("group_members").select("role").eq("group_id", post.group_id).eq("user_id", current_user_id).execute()
    
    if not membership_check.data:
        raise HTTPException(status_code=403, detail="Forbidden: You are not a member of this group.")

    # 2. Insert the post into the database
    try:
        post_response = db.table("posts").insert({
            "group_id": post.group_id,
            "author_id": current_user_id,
            "image_path": post.image_path,
            "caption": post.caption
        }).execute()

        return {"message": "Post created successfully", "post": post_response.data[0]}

    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error creating post: {str(e)}")


@router.get("/group/{group_id}")
def get_group_feed(
    group_id: str, 
    db: Client = Depends(get_db), 
    current_user_id: str = Depends(get_current_user_id)
):
    """Fetches the feed for a specific group."""
    
    # Notice we don't strictly need a Python logic check here if RLS is fully handling it, 
    # but doing a quick membership check provides a cleaner error message to the frontend.
    membership_check = db.table("group_members").select("role").eq("group_id", group_id).eq("user_id", current_user_id).execute()
    
    if not membership_check.data:
        raise HTTPException(status_code=403, detail="Forbidden: You cannot view posts for a group you are not in.")

    # Fetch the posts, ordered by newest first.
    # We also use Supabase's foreign key joining to grab the author's username!
    try:
        feed_response = db.table("posts") \
            .select("id, image_path, caption, created_at, profiles(username, avatar_url)") \
            .eq("group_id", group_id) \
            .order("created_at", desc=True) \
            .execute()

        return {"group_id": group_id, "posts": feed_response.data}

    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error fetching feed: {str(e)}")