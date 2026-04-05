from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from supabase import Client
from app.utils.database import get_db

# Tell Swagger UI we use standard Bearer token auth (adds the 'Authorize' button in /docs)
security = HTTPBearer()

def get_current_user_id(credentials: HTTPAuthorizationCredentials = Depends(security), db: Client = Depends(get_db)):
    """
    Dependency that extracts the JWT token from the header,
    verifies it with Supabase, and returns the secure user_id.
    """
    token = credentials.credentials
    try:
        # Ask Supabase to verify the token and get the user data
        user_response = db.auth.get_user(token)
        
        if not user_response or not user_response.user:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
            
        # Return the verified UUID!
        return user_response.user.id
        
    except Exception as e:
        # Guarantee a 401 even if Supabase throws a 400 AuthApiError under the hood
        raise HTTPException(status_code=401, detail=f"Authentication failed: {str(e)}")