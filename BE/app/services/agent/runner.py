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


def _flatten_exc(e: BaseException) -> str:
    """Unwrap an ExceptionGroup/TaskGroup error down to its real cause(s).

    pydantic-ai runs MCP toolsets inside an anyio TaskGroup, so a failed MCP
    connection surfaces as the opaque "unhandled errors in a TaskGroup
    (1 sub-exception)". Drill into `.exceptions` to report what actually broke.
    """
    subs = getattr(e, "exceptions", None)
    if subs:
        inner = "; ".join(_flatten_exc(s) for s in subs)
        if inner:
            return inner
    return f"{type(e).__name__}: {e}"


def _preview(content) -> str:
    try:
        text = content if isinstance(content, str) else json.dumps(content, default=str)
    except Exception:
        text = str(content)
    return text[:_RESULT_PREVIEW_CHARS] + (" …" if len(text) > _RESULT_PREVIEW_CHARS else "")


async def _run_once(
    db, thread_id: str, agent: Agent, user_message: str, history, state: dict
) -> AsyncIterator[str]:
    """Run one agent turn, yielding SSE strings.

    `state["tokens"]` is incremented as answer tokens stream, so the caller can
    tell whether a failure happened at setup (0 tokens — safe to retry without
    tools) or mid-generation (already streamed — not retryable).
    """
    tool_events: list[dict] = []
    final_text = ""

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
                                    state["tokens"] += 1
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


async def stream_agent_run(
    db, thread_id: str, user_message: str, model: str | None = None
) -> AsyncIterator[str]:
    """Async generator of SSE strings for one user turn.

    Resilience: the first attempt runs with the MCP (LinkedIn) toolsets. If that
    fails BEFORE any token is streamed — almost always the MCP server being
    unreachable (Cloud Run cold start, network) — we fall back to a plain-chat
    agent so the turn still answers. A mid-stream failure is reported as-is.
    """
    history = await memory.load_history(db, thread_id)
    await memory.append_user_message(db, thread_id, user_message)
    await memory.rename_thread_if_default(db, thread_id, user_message)

    yield _sse("start", {"threadId": thread_id})

    state = {"tokens": 0}

    # ── Attempt 1: full agent (with LinkedIn/MCP tools) ──────────────────────
    try:
        async for ev in _run_once(db, thread_id, build_agent(model), user_message, history, state):
            yield ev
        return
    except Exception as e:  # noqa: BLE001
        real = _flatten_exc(e)
        logger.exception("Agent run (with tools) failed for thread %s: %s", thread_id, real)
        if state["tokens"] > 0:
            # Already answered partially — can't cleanly retry; report the cause.
            try:
                await memory.append_assistant_message(db, thread_id, f"(error: {real})", [])
            except Exception:
                pass
            yield _sse("error", {"message": real})
            return
        # Nothing streamed → setup failure (usually the MCP connection). Degrade.
        yield _sse("warning", {"message": "LinkedIn tools are temporarily unavailable — answering without them."})

    # ── Attempt 2: plain chat (no MCP tools) ─────────────────────────────────
    try:
        async for ev in _run_once(
            db, thread_id, build_agent(model, with_tools=False), user_message, history, state
        ):
            yield ev
    except Exception as e:  # noqa: BLE001
        real = _flatten_exc(e)
        logger.exception("Plain-chat fallback failed for thread %s: %s", thread_id, real)
        try:
            await memory.append_assistant_message(db, thread_id, f"(error: {real})", [])
        except Exception:
            pass
        yield _sse("error", {"message": real})
