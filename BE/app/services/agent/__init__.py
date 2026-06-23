"""AI Engineer agent — Pydantic AI agent with MCP tool servers + thread memory.

Layers:
  • agent_factory.build_agent(model)  → provider-swappable Pydantic AI Agent,
        tools sourced from connected MCP server(s).
  • memory.*                          → per-thread message history in Mongo.
  • runner.stream_agent_run(...)      → runs the agent and yields SSE events
        (token / tool_call / tool_result / done) for the chat UI.
"""
