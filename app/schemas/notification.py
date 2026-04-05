from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class NotificationBase(BaseModel):
    id: str
    type: str # 'like', 'comment', 'join_request', 'new_post'
    is_read: bool
    created_at: datetime
    entity_id: Optional[str] = None # The ID of the post/comment
    silo_id: Optional[str] = None
    
    # We include rich data so the mobile app doesn't have to fetch it separately!
    actor_name: Optional[str] = "Someone"
    actor_avatar: Optional[str] = None
    silo_name: Optional[str] = None

class NotificationListResponse(BaseModel):
    unread_count: int
    notifications: list[NotificationBase]