from fastapi import APIRouter, Depends, HTTPException, Query
from supabase import Client
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime, timezone, timedelta, date
import base64
import uuid

from app.utils.database import get_db
from app.utils.dependencies import get_current_user_id

router = APIRouter(
    prefix="/users",
    tags=["Users"]
)

# --- PYDANTIC SCHEMAS ---
class ProfileUpdate(BaseModel):
    bio: Optional[str] = None
    pronouns: Optional[str] = None
    family_role: Optional[str] = None
    location: Optional[str] = None
    dob: Optional[date] = None
    hobbies: Optional[List[str]] = None
    avatar_url: Optional[str] = None
    cover_photo_url: Optional[str] = None

# --- ENDPOINTS ---

@router.get("/me")
def get_my_profile(db: Client = Depends(get_db), current_user_id: str = Depends(get_current_user_id)):
    """Fetches the current user's profile, plus detailed lists of their Silos and Network."""
    try:
        # 1. Get Base Profile Data
        profile_resp = db.table("profiles").select("*").eq("id", current_user_id).execute()
        if not profile_resp.data:
            raise HTTPException(status_code=404, detail="Profile not found")
        profile = profile_resp.data[0]

        # 2. Fetch Silos the user is in (Joining with the groups table to get names)
        silos_resp = db.table("group_members").select("group_id, groups(id, name)").eq("user_id", current_user_id).execute()
        
        silos_list = []
        silo_ids = []
        
        if silos_resp.data:
            for item in silos_resp.data:
                g = item.get("groups")
                if g:
                    silo_ids.append(g["id"])
                    silos_list.append({
                        "id": g["id"],
                        "name": g["name"],
                        "members": 0 # We will calculate this exact count in the next step
                    })

        # 3. Fetch the actual Members in those Silos
        members_list = []
        if silo_ids:
            # Join with profiles to get usernames and avatars!
            peers_resp = db.table("group_members").select("user_id, group_id, profiles(id, username, avatar_url)").in_("group_id", silo_ids).execute()
            
            if peers_resp.data:
                silo_counts = {}
                unique_members = {}
                
                for peer in peers_resp.data:
                    g_id = peer["group_id"]
                    u_id = peer["user_id"]
                    p = peer.get("profiles")
                    
                    # Count how many total people are in each Silo
                    silo_counts[g_id] = silo_counts.get(g_id, 0) + 1
                    
                    # Deduplicate members for the Network list (and skip yourself!)
                    if u_id != current_user_id and p:
                        if u_id not in unique_members:
                            unique_members[u_id] = {
                                "id": p["id"],
                                "name": p.get("username", "Family Member"),
                                "avatar": p.get("avatar_url"),
                                "shared_silos": 1
                            }
                        else:
                            unique_members[u_id]["shared_silos"] += 1
                
                # Apply the member counts back to the silos list
                for s in silos_list:
                    s["members"] = silo_counts.get(s["id"], 0)
                    
                members_list = list(unique_members.values())

        # 4. Return everything to Next.js!
        return {
            "profile": profile,
            "stats": {
                "silos_joined": len(silos_list),
                "known_members": len(members_list),
                "media_posts": 0 # We will link this when we build the Photo Vault
            },
            "silos_list": silos_list,
            "members_list": members_list
        }

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/me")
def update_profile(payload: dict, db: Client = Depends(get_db), current_user_id: str = Depends(get_current_user_id)):
    """Updates the user profile, handles base64 image uploads, and enforces rules."""
    try:
        current_profile = db.table("profiles").select("*").eq("id", current_user_id).execute().data[0]

        # 1. ENFORCE 7-DAY USERNAME RULE
        new_username = payload.get("username")
        if new_username and new_username != current_profile.get("username"):
            existing = db.table("profiles").select("id").eq("username", new_username).execute()
            if existing.data:
                raise HTTPException(status_code=400, detail="That username is already taken!")
            
            last_change = current_profile.get("last_username_change")
            if last_change:
                last_change_date = datetime.fromisoformat(last_change.replace("Z", "+00:00"))
                if datetime.now(timezone.utc) < last_change_date + timedelta(days=7):
                    raise HTTPException(status_code=400, detail="You can only change your username once every 7 days.")
            
            payload["last_username_change"] = datetime.now(timezone.utc).isoformat()

        # 2. EXTRACT AND REMOVE BASE64 DATA FROM PAYLOAD
        # .pop() removes the key from the dictionary so it never hits the database!
        avatar_b64 = payload.pop("avatar_base64", None)
        cover_b64 = payload.pop("cover_base64", None)

        # 3. HANDLE IMAGE UPLOADS
        def upload_base64_image(base64_string, folder):
            header, encoded = base64_string.split(",", 1)
            file_bytes = base64.b64decode(encoded)
            file_extension = header.split(";")[0].split("/")[1]
            file_name = f"{current_user_id}/{folder}_{uuid.uuid4().hex}.{file_extension}"
            
            db.storage.from_("profiles").upload(file_name, file_bytes, {"content-type": f"image/{file_extension}"})
            return db.storage.from_("profiles").get_public_url(file_name)

        # Only attempt upload if they actually selected a file (checks if it starts with 'data:image')
        if avatar_b64 and avatar_b64.startswith("data:image"):
            payload["avatar_url"] = upload_base64_image(avatar_b64, "avatar")
            
        if cover_b64 and cover_b64.startswith("data:image"):
            payload["cover_photo_url"] = upload_base64_image(cover_b64, "cover")

        # 4. SAVE CLEAN PAYLOAD TO DATABASE
        response = db.table("profiles").update(payload).eq("id", current_user_id).execute()
        return response.data[0]

    except Exception as e:
        print("Profile Update Error:", e)
        raise HTTPException(status_code=400, detail=str(e).replace("400: ", ""))
    

@router.post("/me/accept-terms")
def accept_terms(db: Client = Depends(get_db), current_user_id: str = Depends(get_current_user_id)):
    """Marks the user as having accepted the application's terms and conditions."""
    try:
        db.table("profiles").update({"terms_accepted": True}).eq("id", current_user_id).execute()
        return {"message": "Terms accepted successfully"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/me/image")
def upload_profile_image(payload: dict, db: Client = Depends(get_db), current_user_id: str = Depends(get_current_user_id)):
    try:
        image_b64 = payload.get("image_base64")
        image_type = payload.get("type") # 'avatar' or 'cover'
        column = "avatar_url" if image_type == "avatar" else "cover_photo_url"

        # 1. FIND AND DELETE THE OLD FILE (Cleanup)
        current_profile = db.table("profiles").select(column).eq("id", current_user_id).execute().data[0]
        old_url = current_profile.get(column)
        
        if old_url:
            try:
                # Extract the relative path from the full public URL
                path_to_delete = old_url.split("/public/profiles/")[1]
                
                # Execute the delete command
                remove_response = db.storage.from_("profiles").remove([path_to_delete])
                
                # Supabase returns a list of deleted files. If the list is empty, it failed!
                if len(remove_response) > 0:
                    print(f"✅ Successfully deleted old {image_type}: {path_to_delete}")
                else:
                    print(f"⚠️ Failed to delete old {image_type}. File not found or blocked by RLS.")
                    
            except Exception as delete_error:
                print(f"Cleanup error: {delete_error}")

        # 2. UPLOAD NEW IMAGE (Existing logic)
        header, encoded = image_b64.split(",", 1)
        file_bytes = base64.b64decode(encoded)
        file_extension = header.split(";")[0].split("/")[1]
        file_name = f"{current_user_id}/{image_type}_{uuid.uuid4().hex}.{file_extension}"
        
        db.storage.from_("profiles").upload(file_name, file_bytes, {"content-type": f"image/{file_extension}"})
        public_url = db.storage.from_("profiles").get_public_url(file_name)

        # 3. UPDATE DB
        db.table("profiles").update({column: public_url}).eq("id", current_user_id).execute()

        return {"url": public_url}

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    

@router.get("/search")
def search_users(
    q: str = Query("", description="The search query"),
    limit: int = 20, # Fetch a bit more so we can sort them properly
    db: Client = Depends(get_db),
    current_user_id: str = Depends(get_current_user_id)
):
    """Real-time search with smart relevance sorting."""
    if not q.strip():
        return {"users": []}
    
    try:
        # 1. Fetch everyone who has these letters ANYWHERE in their name (in-between or start)
        response = db.table("profiles")\
            .select("id, display_name, username, avatar_url")\
            .or_(f"username.ilike.%{q}%,display_name.ilike.%{q}%")\
            .neq("id", current_user_id)\
            .limit(limit)\
            .execute()
            
        users = response.data
        query_lower = q.lower()

        # 2. The Upgraded Magic
        def get_relevance_score(user):
            name = (user.get("display_name") or "").lower()
            uname = (user.get("username") or "").lower()

            # Score 0: Absolute exact match
            if query_lower == name or query_lower == uname:
                return 0
            
            # Score 1: The very first letter of their name/username matches
            if name.startswith(query_lower) or uname.startswith(query_lower):
                return 1
            
            # Score 2: ANY word in their name starts with it (e.g. "Sa" matches "Aunt Sarah")
            if any(word.startswith(query_lower) for word in name.split()):
                return 2
            
            # Score 3: Letters are just randomly in-between (e.g. "Sa" matches "Melissa")
            return 3

        # 3. Sort using the new scoring system
        users.sort(key=get_relevance_score)

        return {"users": users}

    except Exception as e:
        return {"users": [], "error": str(e)}


@router.get("/{target_user_id}")
def get_public_profile(target_user_id: str, db: Client = Depends(get_db)):
    """Fetches a user's public profile and enforces their privacy toggles."""
    try:
        resp = db.table("profiles").select("*").eq("id", target_user_id).execute()
        if not resp.data:
            raise HTTPException(status_code=404, detail="User not found")
        
        profile = resp.data[0]
        
        # Enforce Privacy Toggles (Scrub data if hidden)
        if not profile.get("show_location"):
            profile["location"] = None
        if not profile.get("show_dob"):
            profile["dob"] = None
        if not profile.get("show_hobbies"):
            profile["hobbies"] = []
            
        return profile
        
    except Exception as e:
        print("Error fetching public profile:", e)
        raise HTTPException(status_code=400, detail=str(e))