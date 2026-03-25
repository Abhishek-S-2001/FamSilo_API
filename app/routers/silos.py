from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel, EmailStr
from supabase import Client
from typing import Optional
import uuid
import resend
import secrets

from app.database import get_db
from app.dependencies import get_current_user_id
import smtplib
from email.message import EmailMessage
import os

# Initialize the router using the proper "Silo" branding
router = APIRouter(
    prefix="/silos",
    tags=["Silos"]
)

# --- Pydantic Schemas ---
class SiloCreate(BaseModel):
    name: str
    description: Optional[str] = None

class SiloInvite(BaseModel):
    email: EmailStr
    role: str = "member"

# --- Endpoints ---
@router.post("/")
def create_silo(
    silo_data: SiloCreate, # (Use whatever your Pydantic model is named here)
    db: Client = Depends(get_db), 
    current_user_id: str = Depends(get_current_user_id)
):
    """Creates a new Silo and structurally links the creator as an Admin member."""
    try:
        # 1. Insert the new Silo into the 'groups' table
        new_silo = db.table("groups").insert({
            "name": silo_data.name,
            "description": silo_data.description,
            "created_by": current_user_id
        }).execute()
        
        silo_id = new_silo.data[0]["id"]

        # 2. THE MISSING DATABASE LINK: Insert the creator into 'group_members'!
        db.table("group_members").insert({
            "group_id": silo_id,
            "user_id": current_user_id,
            "role": "admin"
        }).execute()

        return new_silo.data[0]

    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to create Silo: {str(e)}")
    
    
@router.get("/")
def get_my_silos(db: Client = Depends(get_db), current_user_id: str = Depends(get_current_user_id)):
    """Fetches all silos (groups) the current user belongs to."""
    try:
        # We query 'group_members' and join the 'groups' table to get the details!
        response = db.table("group_members").select("group_id, groups(*)").eq("user_id", current_user_id).execute()
        
        # The frontend expects a flat list of silo objects, so we extract them here:
        silos = []
        if response.data:
            for item in response.data:
                group_data = item.get("groups")
                if group_data:
                    silos.append(group_data)
                    
        return silos
        
    except Exception as e:
        print("Error fetching silos for sidebar:", e)
        raise HTTPException(status_code=400, detail=str(e))

# --- INVITATION SYSTEM ---

def send_invitation_email(email_to: str, invite_link: str, silo_name: str):
    """Sends a beautifully formatted HTML email using the Resend HTTP API."""
    
    # 1. Authenticate with Resend
    resend.api_key = os.getenv("RESEND_API_KEY") 

    # Clean the silo name just in case it has hidden HTML spaces
    clean_silo_name = silo_name.replace('\xa0', ' ')

    # Premium HTML Email matching your DESIGN.md vibe
    html_content = f"""
    <div style="font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 40px 20px; background-color: #f7f9fb; border-radius: 20px;">
        <h2 style="color: #0434c6; font-size: 24px; margin-bottom: 8px;">FamSilo</h2>
        <p style="color: #464555; font-size: 16px; line-height: 1.6;">
            You have been invited to join <strong>{clean_silo_name}</strong>—a secure, private digital heirloom for your family's most precious memories.
        </p>
        <div style="margin: 32px 0;">
            <a href="{invite_link}" style="background-color: #0434c6; color: #ffffff; padding: 14px 28px; text-decoration: none; border-radius: 30px; font-weight: bold; display: inline-block;">
                Accept Invitation
            </a>
        </div>
        <p style="color: #777587; font-size: 12px; margin-top: 40px;">
            If you don't have an account yet, you'll be prompted to quickly create one.
        </p>
    </div>
    """

    try:
        # 2. Send the email via HTTP (Bypasses the [Errno 101] port block!)
        r = resend.Emails.send({
            "from": "FamSilo <onboarding@resend.dev>", # See note below about this email
            "to": email_to,
            "subject": f"You're invited to the {clean_silo_name} Vault",
            "html": html_content
        })
        print(f"✅ Email sent successfully via HTTP! ID: {r.get('id')}")
    except Exception as e:
        print(f"❌ Failed to send email via Resend: {e}")


@router.post("/{silo_id}/invites")
def invite_user_to_silo(
    silo_id: str, 
    invite: SiloInvite,
    background_tasks: BackgroundTasks,
    db: Client = Depends(get_db), 
    current_user_id: str = Depends(get_current_user_id)
):
    """Generates a secure invite token and emails it to a family member."""
    
    admin_check = db.table("group_members").select("role").eq("group_id", silo_id).eq("user_id", current_user_id).execute()
    if not admin_check.data or admin_check.data[0].get("role") != "admin":
        raise HTTPException(status_code=403, detail="Only Silo admins can invite new members.")

    silo_data = db.table("groups").select("name").eq("id", silo_id).execute()
    silo_name = silo_data.data[0].get("name") if silo_data.data else "a FamSilo"

    invite_token = secrets.token_urlsafe(32)

    try:
        db.table("silo_invites").insert({
            "silo_id": silo_id,
            "email": invite.email,
            "token": invite_token,
            "role": invite.role,
            "invited_by": current_user_id,
            "status": "pending"
        }).execute()

        # 🚀 CRITICAL FIX FOR VERCEL: 
        # Since you are live, we cannot use localhost for the email link!
        frontend_url = os.getenv("NEXT_PUBLIC_FRONTEND_URL", "https://your-famsilo-app.vercel.app") 
        invite_link = f"{frontend_url}/join?token={invite_token}"

        background_tasks.add_task(send_invitation_email, invite.email, invite_link, silo_name)

        return {"message": f"Invitation successfully sent to {invite.email}"}
        
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error sending invite: {str(e)}")
    


class JoinSiloRequest(BaseModel):
    token: str

@router.post("/join")
def join_silo(
    request: JoinSiloRequest, 
    db: Client = Depends(get_db), 
    current_user_id: str = Depends(get_current_user_id)
):
    """Redeems an invite token and adds the user to the Silo."""
    try:
        # 1. Find the pending invite
        invite_resp = db.table("silo_invites").select("*").eq("token", request.token).eq("status", "pending").execute()
        
        if not invite_resp.data:
            raise HTTPException(status_code=404, detail="Invalid, expired, or already used invitation link.")
        
        invite = invite_resp.data[0]
        silo_id = invite["silo_id"]

        # 2. Check if user is ALREADY in the group (prevents duplicates)
        existing = db.table("group_members").select("*").eq("group_id", silo_id).eq("user_id", current_user_id).execute()
        
        if not existing.data:
            # 3. Add them to the Silo
            db.table("group_members").insert({
                "group_id": silo_id,
                "user_id": current_user_id,
                "role": invite["role"]
            }).execute()

        # 4. Mark invite as used
        db.table("silo_invites").update({"status": "accepted"}).eq("id", invite["id"]).execute()

        return {"message": "Successfully joined the Silo!", "silo_id": silo_id}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to join Silo: {str(e)}")
    

@router.get("/{silo_id}")
def get_silo_details(
    silo_id: str, 
    db: Client = Depends(get_db), 
    current_user_id: str = Depends(get_current_user_id)
):
    try:
        # 1. Verify membership
        membership = db.table("group_members").select("*").eq("group_id", silo_id).eq("user_id", current_user_id).execute()
        if not membership.data:
            raise HTTPException(status_code=403, detail="You are not a member of this Silo.")

        # 2. Get Silo info
        silo_data = db.table("groups").select("*").eq("id", silo_id).execute()
        
        # 3. Get all Members
        members_data = db.table("group_members").select("user_id, role").eq("group_id", silo_id).execute()

        # 4. Fetch Usernames strictly from the 'profiles' table
        user_ids = [m["user_id"] for m in members_data.data]
        user_dictionary = {}
        
        if user_ids:
            profiles_response = db.table("profiles").select("id, username, avatar_url").in_("id", user_ids).execute()
            for p in profiles_response.data:
                user_dictionary[p["id"]] = {
                    "username": p.get("username", "Family Member"),
                    "avatar": p.get("avatar_url")
                }

        # 5. Map the final response
        formatted_members = []
        for m in members_data.data:
            uid = m["user_id"]
            profile = user_dictionary.get(uid, {"username": "Family Member", "avatar": None})
            
            formatted_members.append({
                "id": uid,
                "role": m["role"],
                "username": profile["username"], 
                "avatar": profile["avatar"]
            })

        return {
            "silo": silo_data.data[0] if silo_data.data else {},
            "members": formatted_members
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))