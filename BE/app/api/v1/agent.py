"""AI Engineer agent API.

GET    /api/v1/agent/threads                       — list chat threads
POST   /api/v1/agent/threads                       — create a thread
GET    /api/v1/agent/threads/{id}/messages         — transcript of a thread
DELETE /api/v1/agent/threads/{id}                  — delete a thread
POST   /api/v1/agent/threads/{id}/stream           — run a turn, stream SSE
GET    /api/v1/agent/models                         — suggested model strings
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.config import settings
from app.database import get_database
from app.security.tenant import require_admin
from app.services.agent import memory
from app.services.agent.runner import stream_agent_run

# The AI Engineer is an internal system-engineering assistant with tool access and
# shared chat threads (no per-user scoping). It is an operator tool, not a client
# feature — gate the whole router to admins so a client can never read or run it.
router = APIRouter(dependencies=[Depends(require_admin)])


async def get_db():
    return await get_database()


class CreateThreadBody(BaseModel):
    title: str = "New chat"
    model: str | None = None


class StreamBody(BaseModel):
    message: str
    model: str | None = None


# Suggested provider-swappable models for the UI picker.
SUGGESTED_MODELS = [
    {"id": "openai:gpt-4o", "label": "OpenAI · GPT-4o"},
    {"id": "openai:gpt-4o-mini", "label": "OpenAI · GPT-4o mini"},
    {"id": "anthropic:claude-sonnet-4-6", "label": "Anthropic · Claude Sonnet 4.6"},
    {"id": "anthropic:claude-haiku-4-5", "label": "Anthropic · Claude Haiku 4.5"},
    {"id": "google-gla:gemini-2.5-pro", "label": "Google · Gemini 2.5 Pro"},
]


@router.get("/models")
async def list_models():
    return {"default": settings.AGENT_MODEL, "models": SUGGESTED_MODELS}


@router.get("/threads")
async def list_threads(db=Depends(get_db)):
    return await memory.list_threads(db)


@router.post("/threads")
async def create_thread(body: CreateThreadBody, db=Depends(get_db)):
    return await memory.create_thread(db, body.title, body.model or settings.AGENT_MODEL)


@router.get("/threads/{thread_id}/messages")
async def thread_messages(thread_id: str, db=Depends(get_db)):
    thread = await memory.get_thread(db, thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    return {"thread": thread, "messages": await memory.list_messages(db, thread_id)}


@router.delete("/threads/{thread_id}")
async def delete_thread(thread_id: str, db=Depends(get_db)):
    await memory.delete_thread(db, thread_id)
    return {"ok": True}


@router.post("/threads/{thread_id}/stream")
async def stream_thread(thread_id: str, body: StreamBody, db=Depends(get_db)):
    thread = await memory.get_thread(db, thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    if not body.message.strip():
        raise HTTPException(status_code=400, detail="message is required")

    generator = stream_agent_run(db, thread_id, body.message, body.model)
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
