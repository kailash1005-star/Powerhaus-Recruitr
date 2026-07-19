# AI Engineer — chat agent (Pydantic AI + MCP tools)

A Claude-style chat screen ("AI Engineer" in the left nav) backed by a
**provider-swappable Pydantic AI agent** whose tools come from connected **MCP
server(s)**. Streams the agent run (tool calls → results → answer) over **SSE**
and is **thread-memory aware** (per-thread history persisted in Mongo).

## How it's wired

```
UI  components/pages/AgentChatPage.tsx ── fetch SSE ──▶  BE  /api/v1/agent/*
    lib/agentApi.ts (thread CRUD + SSE)                    services/agent/
    Sidebar.tsx  (+ "AI Engineer" nav)                       agent_factory.build_agent(model)  ← provider swap
                                                              runner.stream_agent_run()        ← SSE events
                                                              memory.*                          ← Mongo thread memory
                                                            Mongo: chatThreads, chatMessages
```

- **Provider swapping** is one string (`AGENT_MODEL`, or the per-message model the
  UI picker sends): `openai:gpt-4o` → `anthropic:claude-sonnet-4-6` →
  `google-gla:gemini-2.5-pro` → `openrouter:...`. No code change.
- **Tools = MCP servers.** `build_mcp_toolsets()` connects the LinkedIn MCP server
  we built. Add more recruiter capabilities later by appending servers there —
  the agent picks them up automatically.
- **Memory:** each turn replays the thread's prior Pydantic AI message history and
  saves the updated history back, so the agent is context-aware across turns.

## Setup

### 1. Backend deps
```bash
cd BE
pip install -r requirements.txt   # adds pydantic-ai-slim[openai,anthropic,google,mcp]
```

### 2. Backend `.env` (BE/.env  (the one loaded when you run uvicorn from BE/))
```bash
# Model (provider-swappable). Default provider = OpenAI.
AGENT_MODEL=openai:gpt-4o
OPENAI_API_KEY=sk-...            # already used elsewhere in the app
# Optional other providers (only needed if you switch AGENT_MODEL to them):
# ANTHROPIC_API_KEY=...
# GEMINI_API_KEY=...
# OPENROUTER_API_KEY=...

# Connect the LinkedIn MCP server (pick ONE transport):
# A) HTTP — run the MCP server as a service, then point at it:
#    (in the MCP project)  uv run linkedin-mcp --transport http     # serves /mcp
AGENT_MCP_LINKEDIN_HTTP_URL=http://127.0.0.1:8765/mcp
# AGENT_MCP_AUTH_TOKEN=...        # if the MCP server has MCP_AUTH_TOKEN set
#
# B) stdio — let the backend spawn it (needs `uv` + the project on this machine):
# AGENT_MCP_LINKEDIN_DIR=C:/Users/WELCOME/Desktop/Linked-MCP/ai-version
#
# Leave both blank to run the agent as a plain chat assistant (no tools).
```

### 3. Run
```bash
# backend
cd BE && uvicorn app.main:app --reload --port 8000
# frontend
cd UI && npm install && npm run dev      # http://localhost:3000/agent
```

Open **AI Engineer** in the left nav, pick a model, and chat. Tool calls appear
in a collapsible "Agent run" timeline under each answer.

## API (BE)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/agent/models` | default model + suggested swap list |
| GET | `/api/v1/agent/threads` | list chat threads |
| POST | `/api/v1/agent/threads` | create a thread |
| GET | `/api/v1/agent/threads/{id}/messages` | thread transcript |
| DELETE | `/api/v1/agent/threads/{id}` | delete a thread |
| POST | `/api/v1/agent/threads/{id}/stream` | run a turn, stream SSE (`token` / `tool_call` / `tool_result` / `done`) |

## Recommended models for this agentic tool-calling flow
- **`openai:gpt-4o`** — solid default (your current provider).
- **`anthropic:claude-sonnet-4-6`** — strongest tool-calling; great agent default.
- **`anthropic:claude-haiku-4-5`** — fast/cheap turns.
- **`google-gla:gemini-2.5-pro`** — cross-provider option.

## Notes / next steps
- The streaming bridge lives in `BE/app/services/agent/runner.py` and is the one
  place tied to Pydantic AI's event API (verified against pydantic-ai 1.107.0).
- Exact model ids (e.g. the GPT/Gemini version strings) should be confirmed
  against what your keys can access — they're just strings in `AGENT_MODEL` and
  the picker list (`BE/app/api/v1/agent.py: SUGGESTED_MODELS`).
- To expose recruiter actions (start a run, manage pipelines) to the agent, wrap
  them as MCP tools and add the server in `build_mcp_toolsets()`.
