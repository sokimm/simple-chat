from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Dict

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import database

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
app = FastAPI(title="Real-Time Chat")

app.mount("/static", StaticFiles(directory="static"), name="static")

# Seed room password from env var on first run (only if db has no password yet)
_env_pw = os.environ.get("ROOM_PASSWORD", "")
if _env_pw and not database.get_room_password():
    database.set_room_password(_env_pw)


# ---------------------------------------------------------------------------
# WebSocket connection manager
# ---------------------------------------------------------------------------
class ConnectionManager:
    def __init__(self) -> None:
        # Maps username -> WebSocket
        self._connections: Dict[str, WebSocket] = {}

    @property
    def online_users(self) -> list[str]:
        return list(self._connections.keys())

    async def connect(self, username: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections[username] = websocket

    def disconnect(self, username: str) -> None:
        self._connections.pop(username, None)

    async def send_personal(self, message: dict, websocket: WebSocket) -> None:
        try:
            await websocket.send_text(json.dumps(message))
        except Exception:
            pass

    async def broadcast(self, message: dict, exclude: str | None = None) -> None:
        dead: list[str] = []
        for uname, ws in list(self._connections.items()):
            if uname == exclude:
                continue
            try:
                await ws.send_text(json.dumps(message))
            except Exception:
                dead.append(uname)
        for uname in dead:
            self.disconnect(uname)

    async def broadcast_all(self, message: dict) -> None:
        await self.broadcast(message, exclude=None)


manager = ConnectionManager()


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------
@app.get("/")
async def index():
    return FileResponse("static/index.html")


@app.get("/messages")
async def get_messages(limit: int = 50):
    messages = database.get_messages(limit=limit)
    return JSONResponse({"messages": messages})


@app.get("/room-info")
async def room_info():
    return {"password_required": bool(database.get_room_password())}


class JoinRequest(BaseModel):
    username: str
    password: str = ""


@app.post("/join")
async def join(body: JoinRequest):
    username = body.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="Username cannot be empty.")
    if len(username) > 32:
        raise HTTPException(status_code=400, detail="Username too long (max 32 chars).")
    room_pw = database.get_room_password()
    if room_pw and body.password != room_pw:
        raise HTTPException(status_code=403, detail="Incorrect room password.")
    database.add_user(username)
    return {"ok": True, "username": username}


class PasswordChangeRequest(BaseModel):
    current_password: str = ""
    new_password: str = ""


@app.post("/room-password")
async def change_room_password(body: PasswordChangeRequest):
    current_pw = database.get_room_password()
    if current_pw and body.current_password != current_pw:
        raise HTTPException(status_code=403, detail="현재 비밀번호가 올바르지 않습니다.")
    database.set_room_password(body.new_password)
    return {"ok": True}


@app.get("/users/online")
async def online_users():
    return {"users": manager.online_users}


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------
@app.websocket("/ws/{username}")
async def websocket_endpoint(websocket: WebSocket, username: str):
    await manager.connect(username, websocket)

    # Send message history to the newly connected user
    history = database.get_messages(limit=50)
    await manager.send_personal(
        {"type": "history", "messages": history, "online_users": manager.online_users},
        websocket,
    )

    # Broadcast join event to everyone else, then update all with new user list
    join_msg = {
        "type": "system",
        "text": f"{username} joined the chat",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "online_users": manager.online_users,
    }
    await manager.broadcast(join_msg, exclude=username)
    # Send updated online list to the joining user too
    await manager.send_personal(
        {"type": "online_update", "online_users": manager.online_users},
        websocket,
    )

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue

            # ── Delete action ──────────────────────────────────────────────
            if data.get("action") == "delete":
                msg_id = (data.get("id") or "").strip()
                if msg_id:
                    success = database.delete_message(msg_id, username)
                    if success:
                        await manager.broadcast_all({"type": "delete", "id": msg_id})
                continue

            # ── React action ───────────────────────────────────────────────
            if data.get("action") == "react":
                msg_id = (data.get("id") or "").strip()
                emoji   = (data.get("emoji") or "").strip()
                if msg_id and emoji:
                    reactions = database.update_reaction(msg_id, username, emoji)
                    if reactions is not None:
                        await manager.broadcast_all({"type": "reaction", "id": msg_id, "reactions": reactions})
                continue

            # ── Mark-read action ───────────────────────────────────────────
            if data.get("action") == "mark_read":
                ids = data.get("ids") or []
                if isinstance(ids, list) and ids:
                    updates = database.mark_messages_read(ids, username)
                    for upd in updates:
                        await manager.broadcast_all({"type": "read_update", "id": upd["id"], "read_by": upd["read_by"]})
                continue

            # ── Chat message ───────────────────────────────────────────────
            text = (data.get("text") or "").strip()
            if not text:
                continue

            reply = data.get("reply") or None
            if reply and not isinstance(reply, dict):
                reply = None

            # Persist to DB
            saved = database.save_message(username, text, reply=reply)

            # Broadcast chat message to all clients
            chat_msg = {
                "type": "chat",
                "id": saved["id"],
                "username": username,
                "text": text,
                "timestamp": saved["timestamp"],
                "online_users": manager.online_users,
            }
            if reply:
                chat_msg["reply"] = reply
            await manager.broadcast_all(chat_msg)

    except WebSocketDisconnect:
        manager.disconnect(username)
        leave_msg = {
            "type": "system",
            "text": f"{username} left the chat",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "online_users": manager.online_users,
        }
        await manager.broadcast_all(leave_msg)
