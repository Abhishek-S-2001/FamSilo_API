from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel
from supabase import Client
from typing import Optional
from app.utils.database import get_db

from app.utils.dependencies import get_current_user_id

# Initialize the router
router = APIRouter(
    prefix="/groups",
    tags=["Groups"]
)

# --- Pydantic Schemas for Request Validation ---
class GroupCreate(BaseModel):
    name: str
    description: Optional[str] = None

class GroupMemberAdd(BaseModel):
    user_id: str  # The UUID of the user being added
    role: str = "member"


# --- Endpoints ---

@router.post("/")
def create_group(
    group: GroupCreate, 
    db: Client = Depends(get_db), 
    current_user_id: str = Depends(get_current_user_id)
):
    """Creates a new group and automatically adds the creator as an admin."""
    
    # 1. Insert the new group into the database
    try:
        group_response = db.table("groups").insert({
            "name": group.name,
            "description": group.description,
            "created_by": current_user_id
        }).execute()
        
        new_group = group_response.data[0]
        group_id = new_group["id"]

        # 2. Add the creator to the group_members junction table as an admin
        db.table("group_members").insert({
            "group_id": group_id,
            "user_id": current_user_id,
            "role": "admin"
        }).execute()

        return {"message": "Group created successfully", "group": new_group}

    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error creating group: {str(e)}")

@router.get("/")
def get_user_groups(
    db: Client = Depends(get_db), 
    current_user_id: str = Depends(get_current_user_id)
):
    """Fetches all groups the current user is a member of."""
    try:
        response = db.table("group_members") \
            .select("group_id, groups(name, description)") \
            .eq("user_id", current_user_id) \
            .execute()
            
        result = []
        for item in response.data:
            # 1. Safely grab the joined group data (defaults to an empty dict if null)
            group_data = item.get("groups") or {}
            
            # 2. Supabase sometimes returns foreign table joins as a list. Let's flatten it safely.
            if isinstance(group_data, list):
                group_data = group_data[0] if len(group_data) > 0 else {}

            # 3. Safely append using .get() to prevent KeyErrors
            result.append({
                "id": item.get("group_id"), 
                "name": group_data.get("name", "Unknown Group"), 
                "description": group_data.get("description", "")
            })
            
        return result

    except Exception as e:
        # This will print the full traceback to your Python terminal so you can see the exact crash
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=400, detail=f"Database parsing error: {str(e)}")

@router.post("/{group_id}/members")
def add_member_to_group(
    group_id: str, 
    member: GroupMemberAdd, 
    db: Client = Depends(get_db), 
    current_user_id: str = Depends(get_current_user_id)
):
    """Adds a new user to an existing group."""
    
    # Optional Security Check: Ensure the person making the request is an admin of this group
    admin_check = db.table("group_members").select("role").eq("group_id", group_id).eq("user_id", current_user_id).execute()
    
    if not admin_check.data or admin_check.data[0].get("role") != "admin":
        raise HTTPException(status_code=403, detail="Only group admins can add new members")

    # Insert the new member
    try:
        db.table("group_members").insert({
            "group_id": group_id,
            "user_id": member.user_id,
            "role": member.role
        }).execute()

        return {"message": f"User added to group successfully"}
        
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error adding member: {str(e)}")