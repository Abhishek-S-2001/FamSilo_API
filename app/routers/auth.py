from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, EmailStr
from supabase import Client
from app.database import get_db

router = APIRouter(
    prefix="/auth",
    tags=["Authentication"]
)

class UserSignUp(BaseModel):
    email: EmailStr
    password: str
    username: str

class UserLogin(BaseModel):
    email: EmailStr
    password: str

@router.post("/signup")
def sign_up(user: UserSignUp, db: Client = Depends(get_db)):
    """Registers a new user and creates their public profile."""
    try:
        auth_response = db.auth.sign_up({
            "email": user.email,
            "password": user.password,
        })
        
        user_id = auth_response.user.id
        
        db.table("profiles").insert({
            "id": user_id,
            "username": user.username
        }).execute()
        
        # --- DEFENSIVE ERROR HANDLING ---
        # If email confirmation is ON, Supabase returns user data but NO session.
        if auth_response.session is None:
            return {
                "message": "User registered successfully! Please check your email to verify your account before logging in.", 
                "user_id": user_id,
                "access_token": None
            }
            
        # If email confirmation is OFF, they get logged in immediately.
        return {
            "message": "User registered successfully!", 
            "user_id": user_id,
            "access_token": auth_response.session.access_token
        }

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/login")
def login(user: UserLogin, db: Client = Depends(get_db)):
    """Authenticates a user and returns a JWT access token."""
    try:
        auth_response = db.auth.sign_in_with_password({
            "email": user.email,
            "password": user.password
        })
        
        # --- DEFENSIVE ERROR HANDLING ---
        if auth_response.session is None:
            raise HTTPException(
                status_code=403, 
                detail="Login successful, but no active session was returned. Please ensure your email is verified."
            )
        
        return {
            "access_token": auth_response.session.access_token, 
            "token_type": "bearer",
            "user_id": auth_response.user.id
        }
        
    except Exception as e:
        # Supabase will usually throw its own error if the email isn't verified,
        # but catching it here ensures our API always returns a clean 401.
        raise HTTPException(status_code=401, detail=f"Authentication failed: {str(e)}")