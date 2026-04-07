from supabase import create_client, Client
from app.utils.config import SUPABASE_URL, SUPABASE_KEY

# We no longer export a single global client to prevent thread-safety issues
# in FastAPI's synchronous route handlers

def get_db() -> Client:
    """
    Dependency to inject a distinct database client into our routes.
    Creating a new client per request prevents [Errno 11] socket exhaustion 
    caused by multiple threads sharing the same httpx connection pool.
    """
    return create_client(SUPABASE_URL, SUPABASE_KEY)