from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Import our new router
from app.routers import groups, posts, auth

app = FastAPI(
    title="Family Group API",
    description="Backend service for a private, group-based social platform",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Connect the router to the app
app.include_router(groups.router)
app.include_router(posts.router)
app.include_router(auth.router)


@app.get("/")
def health_check():
    return {"status": "ok", "message": "Family Group API is running!"}