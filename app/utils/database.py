from supabase import create_client, Client
from app.utils.config import SUPABASE_URL, SUPABASE_KEY

# Initialize the Supabase client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def get_db():
    """Dependency to inject the database client into our routes"""
    return supabase