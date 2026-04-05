from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, EmailStr
from supabase import Client
from app.utils.database import get_db
import re

router = APIRouter(
    prefix="/auth",
    tags=["Authentication"]
)

class UserSignUp(BaseModel):
    email: EmailStr
    password: str
    username: str


class UserLogin(BaseModel):
    identifier: str  # Can be email OR username
    password: str

@router.post("/signup")
def sign_up(user: UserSignUp, db: Client = Depends(get_db)):
    """Registers a new user, ensuring usernames are completely unique."""
    try:
        clean_username = user.username.lower() # Force lowercase!
        
        if not re.match(r"^[a-z0-9_]{3,20}$", clean_username):
            raise HTTPException(
                status_code=400, 
                detail="Username must be 3-20 characters and contain only letters, numbers, and underscores."
            )
        # 1. Unique Username Check
        existing_user = db.table("profiles").select("id").eq("username", user.username).execute()
        if existing_user.data:
            raise HTTPException(status_code=400, detail=f"The username '@{user.username}' is already taken.")

        # 2. Create the auth account
        auth_response = db.auth.sign_up({
            "email": user.email,
            "password": user.password,
        })
        user_id = auth_response.user.id
        
        # 3. Save profile WITH the email attached for future username logins!
        db.table("profiles").insert({
            "id": user_id,
            "username": user.username,
            "email": user.email,
            "terms_accepted": False
        }).execute()
        
        if auth_response.session is None:
            return {"message": "User registered! Verify email.", "user_id": user_id, "access_token": None}
            
        return {"message": "User registered!", "user_id": user_id, "access_token": auth_response.session.access_token}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/login")
def login(user: UserLogin, db: Client = Depends(get_db)):
    """Authenticates a user via Email OR Username."""
    try:
        login_email = user.identifier

        # TRANSLATION LOOKUP: If there is no '@' symbol, assume it's a username!
        if "@" not in user.identifier:
            profile_lookup = db.table("profiles").select("email").eq("username", user.identifier).execute()
            
            if not profile_lookup.data:
                raise HTTPException(status_code=401, detail="Username not found.")
            
            # Grab the hidden email linked to this username
            login_email = profile_lookup.data[0]["email"]

        # Now we log in normally using the resolved email
        auth_response = db.auth.sign_in_with_password({
            "email": login_email,
            "password": user.password
        })
        
        if auth_response.session is None:
            raise HTTPException(status_code=403, detail="Please verify your email.")
        
        return {
            "access_token": auth_response.session.access_token, 
            "token_type": "bearer",
            "user_id": auth_response.user.id
        }
        
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Authentication failed: Invalid credentials.")