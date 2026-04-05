from fastapi import APIRouter, Depends, HTTPException
from supabase import Client
from app.utils.database import get_db
from app.utils.dependencies import get_current_user_id
# Import your schema from Step 1 here

router = APIRouter(prefix="/notifications", tags=["Notifications"])

@router.get("/", response_model=dict)
def get_notifications(
    limit: int = 20, 
    db: Client = Depends(get_db), 
    current_user_id: str = Depends(get_current_user_id)
):
    """Fetches notifications with rich data for Web and Mobile."""
    
    try:
        # 1. Fetch the raw notifications
        # In a production app, you'd do a SQL JOIN here, but using the Supabase client
        # we can fetch them and enrich them quickly.
        notifs_response = db.table("notifications")\
            .select("*")\
            .eq("user_id", current_user_id)\
            .order("created_at", desc=True)\
            .limit(limit)\
            .execute()
            
        notifications = notifs_response.data
        
        # 2. Calculate the unread count for the red badge on Mobile/Web!
        unread_count = sum(1 for n in notifications if not n.get("is_read"))

        # 3. (Optional but recommended) Map the actor_id to their Profile Name/Avatar 
        # so the frontend can just render "Mom liked your post" directly.
        # ... logic to fetch profiles and map them to notifications ...

        return {
            "unread_count": unread_count,
            "notifications": notifications
        }

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.patch("/read-all")
def mark_all_as_read(
    db: Client = Depends(get_db), 
    current_user_id: str = Depends(get_current_user_id)
):
    """Mobile/Web calls this when a user hits 'Mark all as read'."""
    try:
        db.table("notifications")\
            .update({"is_read": True})\
            .eq("user_id", current_user_id)\
            .eq("is_read", False)\
            .execute()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.patch("/{notification_id}/read")
def mark_as_read(
    notification_id: str, 
    db: Client = Depends(get_db), 
    current_user_id: str = Depends(get_current_user_id)
):
    """Mobile/Web calls this when a user taps a specific notification."""
    try:
        db.table("notifications")\
            .update({"is_read": True})\
            .eq("id", notification_id)\
            .eq("user_id", current_user_id)\
            .execute()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))