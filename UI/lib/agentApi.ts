// ─────────────────────────────────────────────────────────────────────────────
// AI Engineer — agent API client (thread CRUD + SSE streaming over fetch)
// Mirrors BE /api/v1/agent/*
//
// Same-origin via the authenticated proxy — see lib/api.ts for why.
// ─────────────────────────────────────────────────────────────────────────────

const API_BASE = '/api/proxy';

export type AgentId = 'engineer' | 'graph';

export interface ChatThread {
  id: string;
  title: string;
  model: string;
  agent: AgentId;
  createdAt: string;
  updatedAt: string;
}

export interface ToolEvent {
  kind: 'call' | 'result';
  id: string;
  name?: string;
  args?: unknown;
  result?: string;
}

export interface ChatMessage {
  id: string;
  threadId: string;
  role: 'user' | 'assistant';
  content: string;
  toolEvents: ToolEvent[];
  createdAt: string;
}

export interface ModelOption {
  id: string;
  label: string;
}

export interface AgentOption {
  id: AgentId;
  label: string;
  description: string;
  available: boolean;
}

async function responseError(res: Response, fallback: string): Promise<Error> {
  try {
    const body = await res.json();
    return new Error(body?.detail || fallback);
  } catch {
    return new Error(fallback);
  }
}

// ── Thread CRUD ────────────────────────────────────────────────────────────

export async function fetchModels(): Promise<{
  default: string;
  models: ModelOption[];
  agents: AgentOption[];
}> {
  const res = await fetch(`${API_BASE}/api/v1/agent/models`);
  if (!res.ok) throw await responseError(res, `Could not load AI workspace settings (${res.status}).`);
  return res.json();
}

export async function listThreads(): Promise<ChatThread[]> {
  const res = await fetch(`${API_BASE}/api/v1/agent/threads`);
  if (!res.ok) throw await responseError(res, `Could not load conversations (${res.status}).`);
  return res.json();
}

export async function createThread(
  agent: AgentId,
  model?: string,
  title = 'New chat',
): Promise<ChatThread> {
  const res = await fetch(`${API_BASE}/api/v1/agent/threads`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title, model, agent }),
  });
  if (!res.ok) throw await responseError(res, `Could not create conversation (${res.status}).`);
  return res.json();
}

export async function fetchThreadMessages(
  threadId: string,
): Promise<{ thread: ChatThread; messages: ChatMessage[] }> {
  const res = await fetch(`${API_BASE}/api/v1/agent/threads/${threadId}/messages`);
  if (!res.ok) throw await responseError(res, `Could not load this conversation (${res.status}).`);
  return res.json();
}

export async function deleteThread(threadId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/v1/agent/threads/${threadId}`, { method: 'DELETE' });
  if (!res.ok) throw await responseError(res, `Could not delete this conversation (${res.status}).`);
}

// ── Streaming a turn (SSE parsed from a POST response body) ──────────────────

export interface StreamHandlers {
  onStart?: () => void;
  onThinking?: (text: string) => void;
  onToken?: (text: string) => void;
  onToolCall?: (ev: { id: string; name: string; args: unknown }) => void;
  onToolResult?: (ev: { id: string; result: string }) => void;
  onDone?: (text: string) => void;
  onError?: (message: string) => void;
}

export async function streamTurn(
  threadId: string,
  message: string,
  model: string | undefined,
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${API_BASE}/api/v1/agent/threads/${threadId}/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, model }),
    signal,
  });
  if (!res.ok || !res.body) {
    const error = await responseError(res, `The agent could not start (${res.status}).`);
    handlers.onError?.(error.message);
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  const dispatch = (event: string, data: any) => {
    switch (event) {
      case 'start': handlers.onStart?.(); break;
      case 'thinking': handlers.onThinking?.(data.text ?? ''); break;
      case 'token': handlers.onToken?.(data.text ?? ''); break;
      case 'tool_call': handlers.onToolCall?.(data); break;
      case 'tool_result': handlers.onToolResult?.(data); break;
      case 'done': handlers.onDone?.(data.text ?? ''); break;
      case 'error': handlers.onError?.(data.message ?? 'Unknown error'); break;
    }
  };

  // Parse SSE frames: each frame is separated by a blank line; lines are
  // "event: <name>" and "data: <json>".
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let sep: number;
    while ((sep = buffer.indexOf('\n\n')) !== -1) {
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);

      let event = 'message';
      const dataLines: string[] = [];
      for (const line of frame.split('\n')) {
        if (line.startsWith('event:')) event = line.slice(6).trim();
        else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
      }
      if (dataLines.length === 0) continue;
      try {
        dispatch(event, JSON.parse(dataLines.join('\n')));
      } catch {
        /* ignore malformed frame */
      }
    }
  }
}
