from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, Query
from supabase import Client
from typing import Dict, List
from datetime import datetime
import uuid
import json

from app.utils.dependencies import get_current_user_id
from app.utils.database import get_db

router = APIRouter(
    prefix="/chat",
    tags=["Chat"]
)

# --- UNIFIED CONNECTION MANAGER ---
class ConnectionManager:
    def __init__(self):
        # Maps a room_id to a list of active WebSocket connections
        self.active_connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, room_id: str):
        await websocket.accept()
        if room_id not in self.active_connections:
            self.active_connections[room_id] = []
        self.active_connections[room_id].append(websocket)

    def disconnect(self, websocket: WebSocket, room_id: str):
        if room_id in self.active_connections:
            self.active_connections[room_id].remove(websocket)
            if not self.active_connections[room_id]:
                del self.active_connections[room_id]

    async def broadcast(self, message_data: dict, room_id: str):
        if room_id in self.active_connections:
            for connection in self.active_connections[room_id]:
                await connection.send_json(message_data)

manager = ConnectionManager()


@router.websocket("/ws/{room_id}")
async def chat_endpoint(websocket: WebSocket, room_id: str, token: str, db: Client = Depends(get_db)):
    """Handles real-time chat for both Silos and Direct Messages."""
    
    # 1. AUTHENTICATE THE USER
    try:
        user_resp = db.auth.get_user(token)
        if not user_resp or not user_resp.user:
            await websocket.close(code=1008, reason="Invalid Token")
            return
            
        user_id = user_resp.user.id
        
        # Get sender's profile for the UI
        profile = db.table("profiles").select("username, avatar_url").eq("id", user_id).execute()
        username = profile.data[0].get("username", "Family Member") if profile.data else "Unknown"
        avatar = profile.data[0].get("avatar_url") if profile.data else None

        # (Optional but recommended: Add logic here to verify user_id is allowed in room_id)

    except Exception as e:
        print(f"WebSocket Auth Error: {e}")
        await websocket.close(code=1008, reason="Authentication Failed")
        return

    # 2. CONNECT TO THE ROOM
    await manager.connect(websocket, room_id)
    
    try:
        while True:
            # Wait for incoming messages
            data = await websocket.receive_text()
            
            # 3. ROUTE & SAVE TO DATABASE
            is_dm = room_id.startswith("dm_")
            
            if is_dm:
                # Extract the other person's ID from the room string (e.g., "dm_user1_user2")
                ids = room_id.replace("dm_", "").split("_")
                receiver_id = ids[1] if ids[0] == user_id else ids[0]
                
                saved_msg = db.table("messages").insert({
                    "receiver_id": receiver_id,
                    "user_id": user_id,
                    "content": data
                }).execute()
            else:
                # It's a Silo Chat
                saved_msg = db.table("messages").insert({
                    "silo_id": room_id,
                    "user_id": user_id,
                    "content": data
                }).execute()
            
            # 4. BROADCAST TO EVERYONE IN THE ROOM
            message_payload = {
                "id": saved_msg.data[0]["id"] if saved_msg.data else str(uuid.uuid4()),
                "room_id": room_id,
                "user_id": user_id,
                "username": username,
                "avatar": avatar,
                "content": data,
                "created_at": saved_msg.data[0]["created_at"] if saved_msg.data else "Just now"
            }
            
            await manager.broadcast(message_payload, room_id)
            
    except WebSocketDisconnect:
        manager.disconnect(websocket, room_id)


@router.get("/search")
def search_inbox(
    q: str = Query("", description="The search query"),
    limit: int = 10,
    db: Client = Depends(get_db),
    current_user_id: str = Depends(get_current_user_id)
):
    """Unified search for both Users and Silos."""
    if not q.strip():
        return {"results": []}
    
    try:
        query_lower = q.lower()
        results = []

        # 1. Search Users (Profiles)
        users = db.table("profiles")\
            .select("id, display_name, username, avatar_url")\
            .or_(f"username.ilike.%{q}%,display_name.ilike.%{q}%")\
            .neq("id", current_user_id)\
            .limit(limit)\
            .execute()

        for u in users.data:
            name = u.get("display_name") or u.get("username")
            
            # Generate the exact DM room ID so the frontend doesn't have to guess!
            ids = sorted([current_user_id, u["id"]])
            dm_room_id = f"dm_{ids[0]}_{ids[1]}"
            
            results.append({
                "id": dm_room_id,             # The standard Chat ID
                "name": name,
                "type": "dm",                 # Tells ChatItem to make it a circle avatar
                "avatar": u.get("avatar_url"),
                "last_message_preview": f"@{u.get('username')}" # Use this space to show their handle!
            })

        # 2. Search Silos (Groups)
        # Assuming your table is named 'groups'. We only search groups they are a part of!
        # Note: If you don't have a user_groups join table yet, you can just search the groups table directly for now.
        silos = db.table("groups")\
            .select("id, name")\
            .ilike("name", f"%{q}%")\
            .limit(limit)\
            .execute()

        for s in silos.data:
            results.append({
                "id": s["id"],                # For Silos, the ID is just the Silo ID
                "name": s["name"],
                "type": "group",              # Tells ChatItem to make it a square avatar
                "avatar": None,
                "last_message_preview": "Silo Chat"
            })

        # 3. The Relevance Sort (Works for both now!)
        def get_relevance_score(item):
            name = (item.get("name") or "").lower()
            if query_lower == name: return 0
            if any(word.startswith(query_lower) for word in name.split()): return 1
            return 2

        results.sort(key=get_relevance_score)

        return {"results": results}

    except Exception as e:
        return {"results": [], "error": str(e)}


@router.get("/{room_id}/messages")
def get_chat_history(room_id: str, db: Client = Depends(get_db)):
    """Fetches history and automatically attaches the sender's profile info."""
    try:
        # 1. Fetch Direct Messages History
        if room_id.startswith("dm_"):
            users = room_id.replace("dm_", "").split("_")
            if len(users) != 2:
                return []
            u1, u2 = users[0], users[1]
            
            resp = db.table("messages") \
                .select("*, profiles!messages_user_id_fkey(username, avatar_url)") \
                .or_(f"and(user_id.eq.{u1},receiver_id.eq.{u2}),and(user_id.eq.{u2},receiver_id.eq.{u1})") \
                .order("created_at") \
                .execute()
                
            return resp.data

        # 2. Fetch Silo Group Chat History
        else:
            resp = db.table("messages") \
                .select("*, profiles!messages_user_id_fkey(username, avatar_url)") \
                .eq("silo_id", room_id) \
                .order("created_at") \
                .execute()
                
            return resp.data

    except Exception as e:
        print(f"Error fetching history for {room_id}:", str(e))
        return []
    

@router.get("/dms")
def get_direct_messages(db: Client = Depends(get_db), current_user_id: str = Depends(get_current_user_id)):
    """Finds all unique users you have an active DM history with."""
    try:
        # 1. Fetch your messages without the complex Supabase null filters
        sent_msgs = db.table("messages").select("receiver_id").eq("user_id", current_user_id).execute()
        received_dms = db.table("messages").select("user_id").eq("receiver_id", current_user_id).execute()

        # 2. Extract unique User IDs safely in Python
        peer_ids = set()
        
        # Add people you SENT messages to (ignoring Silo messages where receiver_id is None)
        if sent_msgs.data:
            for msg in sent_msgs.data:
                if msg.get("receiver_id"): 
                    peer_ids.add(msg["receiver_id"])
                    
        # Add people who SENT messages to you
        if received_dms.data:
            for msg in received_dms.data:
                if msg.get("user_id"):
                    peer_ids.add(msg["user_id"])

        dms_list = []
        if peer_ids:
            # 3. Fetch their profiles
            profiles_resp = db.table("profiles").select("id, username, avatar_url").in_("id", list(peer_ids)).execute()
            
            if profiles_resp.data:
                for p in profiles_resp.data:
                    # Regenerate the exact deterministic DM Room ID
                    ids = sorted([current_user_id, p["id"]])
                    room_id = f"dm_{ids[0]}_{ids[1]}"
                    
                    dms_list.append({
                        "id": room_id,
                        "name": p.get("username", "Family Member"),
                        "avatar": p.get("avatar_url"),
                        "type": "dm" 
                    })

        return dms_list
    except Exception as e:
        # This will print the exact crash reason to your Python terminal!
        print(f"DM Fetch Error: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
   


@router.get("/inbox")
def get_smart_inbox(db: Client = Depends(get_db), current_user_id: str = Depends(get_current_user_id)):
    """Fetches Silos and DMs, grabs the latest message, calculates unread counts, and sorts them."""
    try:
        inbox = []
        
        # 1. GET SILOS (Using your exact table names: group_members and groups)
        silos_resp = db.table("group_members").select("group_id, groups(id, name)").eq("user_id", current_user_id).execute()
        if silos_resp.data:
            for item in silos_resp.data:
                s = item.get("groups")
                if s:
                    inbox.append({"id": s["id"], "name": s["name"], "type": "group"})

        # 2. GET DMs
        sent = db.table("messages").select("receiver_id").eq("user_id", current_user_id).execute()
        received = db.table("messages").select("user_id").eq("receiver_id", current_user_id).execute()
        
        peer_ids = set()
        if sent.data:
            for m in sent.data: 
                if m.get("receiver_id"):
                    peer_ids.add(m["receiver_id"])
        if received.data:
            for m in received.data: 
                if m.get("user_id"):
                    peer_ids.add(m["user_id"])
            
        if peer_ids:
            profiles = db.table("profiles").select("id, username, avatar_url").in_("id", list(peer_ids)).execute()
            if profiles.data:
                for p in profiles.data:
                    ids = sorted([current_user_id, p["id"]])
                    inbox.append({
                        "id": f"dm_{ids[0]}_{ids[1]}",
                        "name": p.get("username", "Family Member"),
                        "avatar": p.get("avatar_url"),
                        "type": "dm"
                    })

        # 3. GET LATEST MESSAGE & UNREAD COUNT FOR EACH ROOM
        for chat in inbox:
            chat["last_message_time"] = "2000-01-01T00:00:00Z" 
            chat["last_message_preview"] = "No messages yet"
            chat["unread_count"] = 0

            if chat["type"] == "group":
                msg_resp = db.table("messages") \
                    .select("created_at, content, profiles!messages_user_id_fkey(username)") \
                    .eq("silo_id", chat["id"]) \
                    .order("created_at", desc=True) \
                    .limit(1) \
                    .execute()
                
                if msg_resp.data:
                    latest_msg = msg_resp.data[0]
                    chat["last_message_time"] = latest_msg["created_at"]
                    prof = latest_msg.get("profiles")
                    sender_name = prof.get("username", "Member") if prof else "Member"
                    chat["last_message_preview"] = f"{sender_name}: {latest_msg['content']}"
            else:
                # DM Logic
                u1, u2 = chat["id"].replace("dm_", "").split("_")
                
                msg_resp = db.table("messages").select("created_at, content").or_(f"and(user_id.eq.{u1},receiver_id.eq.{u2}),and(user_id.eq.{u2},receiver_id.eq.{u1})").order("created_at", desc=True).limit(1).execute()
                if msg_resp.data:
                    chat["last_message_time"] = msg_resp.data[0]["created_at"]
                    chat["last_message_preview"] = msg_resp.data[0]["content"]
                
                peer_id = u1 if u1 != current_user_id else u2
                unread_resp = db.table("messages").select("id", count="exact").eq("receiver_id", current_user_id).eq("user_id", peer_id).eq("is_read", False).execute()
                chat["unread_count"] = unread_resp.count if unread_resp.count else 0

        # 4. SORT BY MOST RECENT MESSAGE TIME
        inbox.sort(key=lambda x: x["last_message_time"], reverse=True)
        
        return inbox

    except Exception as e:
        print("Inbox Fetch Error:", e)
        raise HTTPException(status_code=400, detail=str(e))
    


@router.post("/{room_id}/read")
def mark_room_as_read(room_id: str, db: Client = Depends(get_db), current_user_id: str = Depends(get_current_user_id)):
    """Marks all unread DMs in a room as read when you open it."""
    try:
        if room_id.startswith("dm_"):
            db.table("messages").update({"is_read": True}).eq("receiver_id", current_user_id).eq("is_read", False).execute()
        return {"success": True}
    except Exception as e:
        pass # Silently fail, read receipts shouldn't break the app