"""Per-thread chat memory in Mongo.

Two views of a conversation are stored:
  • chatThreads.pydanticHistory — the serialized Pydantic AI ModelMessage list,
        the source of truth replayed into each run for thread-aware memory.
  • chatMessages — a UI-friendly transcript (role, content, tool events) used to
        render the thread when it's reopened.
"""
from __future__ import annotations

from datetime import datetime, timezone

from bson import ObjectId
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter

THREADS = "chatThreads"
MESSAGES = "chatMessages"


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _thread_public(doc: dict) -> dict:
    return {
        "id": str(doc["_id"]),
        "title": doc.get("title", "New chat"),
        "model": doc.get("model", ""),
        "createdAt": (doc.get("createdAt") or _now()).isoformat(),
        "updatedAt": (doc.get("updatedAt") or _now()).isoformat(),
    }


def _message_public(doc: dict) -> dict:
    return {
        "id": str(doc["_id"]),
        "threadId": str(doc["threadId"]),
        "role": doc["role"],
        "content": doc.get("content", ""),
        "toolEvents": doc.get("toolEvents", []),
        "createdAt": (doc.get("createdAt") or _now()).isoformat(),
    }


# ── Threads ────────────────────────────────────────────────────────────────

async def create_thread(db, title: str, model: str) -> dict:
    doc = {
        "title": title or "New chat",
        "model": model,
        "pydanticHistory": "",
        "createdAt": _now(),
        "updatedAt": _now(),
    }
    res = await db[THREADS].insert_one(doc)
    doc["_id"] = res.inserted_id
    return _thread_public(doc)


async def list_threads(db, limit: int = 100) -> list[dict]:
    cur = db[THREADS].find().sort("updatedAt", -1).limit(limit)
    return [_thread_public(d) async for d in cur]


async def get_thread(db, thread_id: str) -> dict | None:
    doc = await db[THREADS].find_one({"_id": ObjectId(thread_id)})
    return _thread_public(doc) if doc else None


async def delete_thread(db, thread_id: str) -> None:
    oid = ObjectId(thread_id)
    await db[THREADS].delete_one({"_id": oid})
    await db[MESSAGES].delete_many({"threadId": oid})


async def rename_thread_if_default(db, thread_id: str, first_message: str) -> None:
    """Give a brand-new thread a title derived from its first user message."""
    oid = ObjectId(thread_id)
    doc = await db[THREADS].find_one({"_id": oid})
    if doc and (doc.get("title") in (None, "", "New chat")):
        title = (first_message or "New chat").strip().splitlines()[0][:60]
        await db[THREADS].update_one({"_id": oid}, {"$set": {"title": title}})


# ── Messages (UI transcript) ────────────────────────────────────────────────

async def list_messages(db, thread_id: str) -> list[dict]:
    cur = db[MESSAGES].find({"threadId": ObjectId(thread_id)}).sort("createdAt", 1)
    return [_message_public(d) async for d in cur]


async def append_user_message(db, thread_id: str, content: str) -> dict:
    doc = {
        "threadId": ObjectId(thread_id),
        "role": "user",
        "content": content,
        "toolEvents": [],
        "createdAt": _now(),
    }
    res = await db[MESSAGES].insert_one(doc)
    doc["_id"] = res.inserted_id
    return _message_public(doc)


async def append_assistant_message(
    db, thread_id: str, content: str, tool_events: list[dict]
) -> dict:
    doc = {
        "threadId": ObjectId(thread_id),
        "role": "assistant",
        "content": content,
        "toolEvents": tool_events,
        "createdAt": _now(),
    }
    res = await db[MESSAGES].insert_one(doc)
    doc["_id"] = res.inserted_id
    return _message_public(doc)


# ── Pydantic AI history (agent memory) ──────────────────────────────────────

async def load_history(db, thread_id: str) -> list[ModelMessage]:
    doc = await db[THREADS].find_one({"_id": ObjectId(thread_id)})
    raw = (doc or {}).get("pydanticHistory") or ""
    if not raw:
        return []
    try:
        return list(ModelMessagesTypeAdapter.validate_json(raw))
    except Exception:
        return []


async def save_history(db, thread_id: str, messages: list[ModelMessage]) -> None:
    blob = ModelMessagesTypeAdapter.dump_json(messages).decode("utf-8")
    await db[THREADS].update_one(
        {"_id": ObjectId(thread_id)},
        {"$set": {"pydanticHistory": blob, "updatedAt": _now()}},
    )
