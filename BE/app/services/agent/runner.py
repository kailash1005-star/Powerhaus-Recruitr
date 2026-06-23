"""Run the agent for one user turn and yield SSE events for the chat UI.

Event types emitted (SSE `event:` name):
  start        — run began           {threadId}
  thinking     — reasoning delta      {text}
  token        — answer text delta    {text}
  tool_call    — a tool was invoked   {id, name, args}
  tool_result  — a tool returned       {id, result}
  done         — final answer ready    {text}
  error        — run failed            {message}

Memory: prior thread history is replayed in; the full new message list is saved
back after the run so the next turn is context-aware.
"""
from __future__ import annotations

import json
import logging
from typing import AsyncIterator

from pydantic_ai import Agent
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    TextPartDelta,
    ThinkingPartDelta,
)

from app.services.agent import memory
from app.services.agent.agent_factory import build_agent

logger = logging.getLogger(__name__)

_RESULT_PREVIEW_CHARS = 4000  # cap tool-result payloads sent to the UI


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _preview(content) -> str:
    try:
        text = content if isinstance(content, str) else json.dumps(content, default=str)
    except Exception:
        text = str(content)
    return text[:_RESULT_PREVIEW_CHARS] + (" …" if len(text) > _RESULT_PREVIEW_CHARS else "")


async def stream_agent_run(
    db, thread_id: str, user_message: str, model: str | None = None
) -> AsyncIterator[str]:
    """Async generator of SSE strings for one user turn."""
    history = await memory.load_history(db, thread_id)
    await memory.append_user_message(db, thread_id, user_message)
    await memory.rename_thread_if_default(db, thread_id, user_message)

    yield _sse("start", {"threadId": thread_id})

    agent = build_agent(model)
    tool_events: list[dict] = []
    final_text = ""

    try:
        async with agent:  # opens the MCP server connections (toolsets)
            async with agent.iter(user_message, message_history=history) as run:
                async for node in run:
                    if Agent.is_model_request_node(node):
                        async with node.stream(run.ctx) as request_stream:
                            async for event in request_stream:
                                if isinstance(event, PartDeltaEvent):
                                    delta = event.delta
                                    if isinstance(delta, TextPartDelta) and delta.content_delta:
                                        final_text += delta.content_delta
                                        yield _sse("token", {"text": delta.content_delta})
                                    elif isinstance(delta, ThinkingPartDelta) and delta.content_delta:
                                        yield _sse("thinking", {"text": delta.content_delta})

                    elif Agent.is_call_tools_node(node):
                        async with node.stream(run.ctx) as handle_stream:
                            async for event in handle_stream:
                                if isinstance(event, FunctionToolCallEvent):
                                    part = event.part
                                    try:
                                        args = part.args_as_dict()
                                    except Exception:
                                        args = part.args
                                    info = {
                                        "id": part.tool_call_id,
                                        "name": part.tool_name,
                                        "args": args,
                                    }
                                    tool_events.append({"kind": "call", **info})
                                    yield _sse("tool_call", info)
                                elif isinstance(event, FunctionToolResultEvent):
                                    result_part = event.part
                                    info = {
                                        "id": getattr(result_part, "tool_call_id", ""),
                                        "result": _preview(getattr(result_part, "content", None)),
                                    }
                                    tool_events.append({"kind": "result", **info})
                                    yield _sse("tool_result", info)

            result = run.result
            if result is not None:
                if result.output:
                    final_text = result.output
                await memory.save_history(db, thread_id, result.all_messages())

        await memory.append_assistant_message(db, thread_id, final_text, tool_events)
        yield _sse("done", {"text": final_text})

    except Exception as e:  # noqa: BLE001
        logger.exception("Agent run failed for thread %s", thread_id)
        # Persist whatever we have so the transcript isn't lost.
        try:
            await memory.append_assistant_message(
                db, thread_id, final_text or f"(error: {e})", tool_events
            )
        except Exception:
            pass
        yield _sse("error", {"message": str(e)})
