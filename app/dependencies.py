from fastapi import Depends, HTTPException, Header
from supabase import Client
from app.database import get_db

def get_current_user_id(authorization: str = Header(...), db: Client = Depends(get_db)):
    """
    Dependency that extracts the JWT token from the header,
    verifies it with Supabase, and returns the secure user_id.
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid Authorization header format. Must be 'Bearer <token>'")
    
    # Extract the token string
    token = authorization.split(" ")[1]
    
    try:
        # Ask Supabase to verify the token and get the user data
        user_response = db.auth.get_user(token)
        
        if not user_response or not user_response.user:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
            
        # Return the verified UUID!
        return user_response.user.id
        
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Authentication failed: {str(e)}")