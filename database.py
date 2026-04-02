import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "db.json"
_lock = threading.Lock()


def load_db() -> dict:
    with _lock:
        try:
            with open(DB_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            data = {"messages": [], "users": []}
        # Ensure keys exist
        data.setdefault("messages", [])
        data.setdefault("users", [])
        return data


def save_db(data: dict) -> None:
    with _lock:
        with open(DB_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)


def get_messages(limit: int = 50) -> list[dict]:
    data = load_db()
    return data["messages"][-limit:]


ALLOWED_REACTIONS = {'❤️', '🤣', '👍', '😒', '😢'}


def save_message(username: str, text: str, timestamp: str | None = None, reply: dict | None = None) -> dict:
    if timestamp is None:
        timestamp = datetime.now(timezone.utc).isoformat()
    message = {
        "id": str(uuid.uuid4()), "username": username, "text": text,
        "timestamp": timestamp, "reactions": {}, "read_by": [username],
    }
    if reply:
        message["reply"] = reply
    with _lock:
        try:
            with open(DB_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            data = {"messages": [], "users": []}
        data.setdefault("messages", [])
        data.setdefault("users", [])
        data["messages"].append(message)
        with open(DB_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    return message


def get_users() -> list[str]:
    data = load_db()
    return data.get("users", [])


def add_user(username: str) -> bool:
    """Add a user if not already present. Returns True if newly added."""
    with _lock:
        try:
            with open(DB_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            data = {"messages": [], "users": []}
        data.setdefault("messages", [])
        data.setdefault("users", [])
        if username in data["users"]:
            return False
        data["users"].append(username)
        with open(DB_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    return True


def get_room_password() -> str:
    """Return the current room password (empty string = no password)."""
    data = load_db()
    return data.get("room_password", "")


def set_room_password(password: str) -> None:
    """Set (or clear) the room password."""
    with _lock:
        try:
            with open(DB_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            data = {"messages": [], "users": []}
        data.setdefault("messages", [])
        data.setdefault("users", [])
        data["room_password"] = password
        with open(DB_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)


def update_reaction(message_id: str, username: str, emoji: str) -> dict | None:
    """Toggle emoji reaction for username. Returns updated reactions dict, or None if not found."""
    if emoji not in ALLOWED_REACTIONS:
        return None
    with _lock:
        try:
            with open(DB_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return None
        for msg in data.get("messages", []):
            if msg.get("id") == message_id:
                reactions = msg.setdefault("reactions", {})
                users = reactions.setdefault(emoji, [])
                if username in users:
                    users.remove(username)
                else:
                    users.append(username)
                with open(DB_PATH, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                return reactions
    return None


def mark_messages_read(message_ids: list[str], username: str) -> list[dict]:
    """Mark given messages as read by username. Returns list of {id, read_by} for changed messages."""
    updated = []
    with _lock:
        try:
            with open(DB_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []
        id_set = set(message_ids)
        changed = False
        for msg in data.get("messages", []):
            if msg.get("id") in id_set:
                read_by = msg.setdefault("read_by", [])
                if username not in read_by:
                    read_by.append(username)
                    changed = True
                    updated.append({"id": msg["id"], "read_by": list(read_by)})
        if changed:
            with open(DB_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
    return updated


def delete_message(message_id: str, username: str) -> bool:
    """Delete a message only if it belongs to username. Returns True if deleted."""
    with _lock:
        try:
            with open(DB_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return False
        messages = data.get("messages", [])
        for i, msg in enumerate(messages):
            if msg.get("id") == message_id:
                if msg.get("username") != username:
                    return False  # not the owner
                messages.pop(i)
                data["messages"] = messages
                with open(DB_PATH, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                return True
    return False  # not found
