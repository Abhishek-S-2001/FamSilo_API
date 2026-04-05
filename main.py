from fastapi import FastAPI
import os
from fastapi.middleware.cors import CORSMiddleware

# Import our new router
from app.routers import groups, posts, auth, silos, chat, users,notifications

app = FastAPI(
    title="FamSilo API",
    description="Backend service for a private, group-based social platform",
    version="1.0.0"
)

# 1. Define who is allowed to talk to your API
origins = [
    "*",
    "http://localhost:3000",          # Your local Next.js frontend
    "http://192.168.1.40:3000",       # Your phone on the local network
    "http://127.0.0.1:3000",
    "https://famsilo-webapp.vercel.app",     # Your production frontend URL
]

# 2. Add the shield
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"], # Lock down allowed actions
    allow_headers=["Authorization", "Content-Type"], # Only allow specific headers
)

# Connect the router to the app
app.include_router(groups.router)
app.include_router(posts.router)
app.include_router(auth.router)
app.include_router(silos.router)
app.include_router(chat.router)
app.include_router(users.router)
app.include_router(notifications.router)


@app.get("/")
def health_check():
    return {"status": "ok", "message": "Family Group API is running!"}