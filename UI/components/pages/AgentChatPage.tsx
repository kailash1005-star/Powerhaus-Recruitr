'use client';

import { useEffect, useReducer, useRef, useState } from 'react';
import { Icon } from '../Icon';
import { Markdown } from '../Markdown';
import {
  ChatMessage,
  ChatThread,
  ModelOption,
  ToolEvent,
  createThread,
  deleteThread,
  fetchModels,
  fetchThreadMessages,
  listThreads,
  streamTurn,
} from '@/lib/agentApi';

interface Draft {
  text: string;
  thinking: string;
  toolEvents: ToolEvent[];
}

export function AgentChatPage() {
  const [models, setModels] = useState<ModelOption[]>([]);
  const [model, setModel] = useState<string>('');
  const [threads, setThreads] = useState<ChatThread[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [railOpen, setRailOpen] = useState(true);

  // Stream accumulation kept in a ref (reliable across rapid events); a tick
  // forces re-render so the live draft shows.
  const draftRef = useRef<Draft | null>(null);
  const [, tick] = useReducer((x) => x + 1, 0);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  // ── Initial load ──────────────────────────────────────────────────────────
  useEffect(() => {
    (async () => {
      try {
        const m = await fetchModels();
        setModels(m.models);
        setModel(m.default || m.models[0]?.id || '');
      } catch {
        /* models endpoint optional */
      }
      try {
        const t = await listThreads();
        setThreads(t);
        if (t[0]) void selectThread(t[0].id);
      } catch (e) {
        setError(String(e));
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Auto-follow the stream, but only if the user is already near the bottom —
  // don't yank them down while they're scrolled up reading earlier output.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 140;
    if (nearBottom) el.scrollTo({ top: el.scrollHeight });
  });

  // ── Thread actions ──────────────────────────────────────────────────────────
  async function selectThread(id: string) {
    setActiveId(id);
    setError(null);
    draftRef.current = null;
    try {
      const { messages: msgs } = await fetchThreadMessages(id);
      setMessages(msgs);
    } catch (e) {
      setError(String(e));
    }
  }

  function newChat() {
    setActiveId(null);
    setMessages([]);
    draftRef.current = null;
    setError(null);
  }

  async function removeThread(id: string, e: React.MouseEvent) {
    e.stopPropagation();
    await deleteThread(id).catch(() => {});
    setThreads((ts) => ts.filter((t) => t.id !== id));
    if (activeId === id) newChat();
  }

  // ── Send a turn ─────────────────────────────────────────────────────────────
  async function send() {
    const text = input.trim();
    if (!text || streaming) return;

    let threadId = activeId;
    if (!threadId) {
      try {
        const t = await createThread(model);
        threadId = t.id;
        setThreads((ts) => [t, ...ts]);
        setActiveId(t.id);
      } catch (e) {
        setError(String(e));
        return;
      }
    }

    setInput('');
    setError(null);
    setMessages((m) => [
      ...m,
      {
        id: `local-${Date.now()}`,
        threadId: threadId!,
        role: 'user',
        content: text,
        toolEvents: [],
        createdAt: new Date().toISOString(),
      },
    ]);

    draftRef.current = { text: '', thinking: '', toolEvents: [] };
    setStreaming(true);
    tick();

    await streamTurn(threadId, text, model, {
      onThinking: (t) => { if (draftRef.current) { draftRef.current.thinking += t; tick(); } },
      onToken: (t) => { if (draftRef.current) { draftRef.current.text += t; tick(); } },
      onToolCall: (ev) => {
        draftRef.current?.toolEvents.push({ kind: 'call', id: ev.id, name: ev.name, args: ev.args });
        tick();
      },
      onToolResult: (ev) => {
        draftRef.current?.toolEvents.push({ kind: 'result', id: ev.id, result: ev.result });
        tick();
      },
      onDone: (finalText) => {
        const d = draftRef.current;
        const assistant: ChatMessage = {
          id: `local-a-${Date.now()}`,
          threadId: threadId!,
          role: 'assistant',
          content: finalText || d?.text || '',
          toolEvents: d?.toolEvents ?? [],
          createdAt: new Date().toISOString(),
        };
        draftRef.current = null;
        setStreaming(false);
        setMessages((m) => [...m, assistant]);
        // refresh thread list (title may have been derived from first message)
        listThreads().then(setThreads).catch(() => {});
      },
      onError: (msg) => {
        draftRef.current = null;
        setStreaming(false);
        setError(msg);
      },
    }).catch((e) => {
      draftRef.current = null;
      setStreaming(false);
      setError(String(e));
    });
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      void send();
    }
  }

  const draft = draftRef.current;
  const showEmpty = messages.length === 0 && !draft;

  // ── Render ──────────────────────────────────────────────────────────────────
  return (
    <div style={S.root}>
      {/* Header */}
      <div style={S.header}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <button
            style={S.iconBtn}
            onClick={() => setRailOpen((o) => !o)}
            title={railOpen ? 'Hide conversations' : 'Show conversations'}
          >
            <Icon name="panel-left" size={16} style={{ color: 'var(--fg-secondary)' }} />
          </button>
          {!railOpen && (
            <button style={S.iconBtn} onClick={newChat} title="New chat">
              <Icon name="plus" size={16} style={{ color: 'var(--fg-secondary)' }} />
            </button>
          )}
          <Icon name="sparkles" size={16} style={{ color: 'var(--fg-primary)' }} />
          <span style={S.headerTitle}>AI Engineer</span>
        </div>
        <select
          value={model}
          onChange={(e) => setModel(e.target.value)}
          style={S.modelSelect}
          title="Model provider — swap any time"
        >
          {models.length === 0 && <option value="">Default model</option>}
          {models.map((m) => (
            <option key={m.id} value={m.id}>{m.label}</option>
          ))}
        </select>
      </div>

      <div style={S.body}>
        {/* Thread rail (collapsible) */}
        {railOpen && (
        <div style={S.rail}>
          <button style={S.newBtn} onClick={newChat}>
            <Icon name="plus" size={14} /> New chat
          </button>
          <div style={S.threadList}>
            {threads.map((t) => (
              <div
                key={t.id}
                onClick={() => selectThread(t.id)}
                style={{
                  ...S.threadItem,
                  background: t.id === activeId ? 'var(--bg-nav-active)' : 'transparent',
                }}
              >
                <Icon name="message-square" size={13} style={{ color: 'var(--fg-muted)' }} />
                <span style={S.threadTitle}>{t.title || 'New chat'}</span>
                <span style={S.threadDel} onClick={(e) => removeThread(t.id, e)}>
                  <Icon name="trash-2" size={12} style={{ color: 'var(--fg-subtle)' }} />
                </span>
              </div>
            ))}
            {threads.length === 0 && (
              <div style={S.railEmpty}>No conversations yet.</div>
            )}
          </div>
        </div>
        )}

        {/* Chat column */}
        <div style={S.chatCol}>
          <div ref={scrollRef} style={S.scroll}>
            <div style={S.thread}>
              {showEmpty && (
                <div style={S.empty}>
                  <Icon name="sparkles" size={28} style={{ color: 'var(--fg-subtle)' }} />
                  <div style={S.emptyTitle}>How can I help you recruit?</div>
                  <div style={S.emptySub}>
                    Ask me to research companies and people, find jobs, or pull data —
                    I&apos;ll use the connected tools to do it.
                  </div>
                </div>
              )}

              {messages.map((m) =>
                m.role === 'user' ? (
                  <UserBubble key={m.id} text={m.content} />
                ) : (
                  <AssistantTurn key={m.id} text={m.content} toolEvents={m.toolEvents} />
                ),
              )}

              {draft && (
                <AssistantTurn
                  text={draft.text}
                  toolEvents={draft.toolEvents}
                  thinking={draft.thinking}
                  streaming
                />
              )}

              {error && <div style={S.error}>⚠ {error}</div>}
            </div>
          </div>

          {/* Composer */}
          <div style={S.composerWrap}>
            <div style={S.composer}>
              <textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={onKeyDown}
                placeholder="Message the AI Engineer…  (Enter to send, Shift+Enter for newline)"
                rows={1}
                style={S.textarea}
                disabled={streaming}
              />
              <button
                style={{ ...S.sendBtn, opacity: input.trim() && !streaming ? 1 : 0.5 }}
                onClick={() => void send()}
                disabled={!input.trim() || streaming}
              >
                {streaming ? <Icon name="loader" size={15} /> : <Icon name="arrow-up" size={15} />}
              </button>
            </div>
            <div style={S.hint}>{streaming ? 'Agent is working…' : 'Powered by Linkedin MCP'}</div>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Message components ────────────────────────────────────────────────────────

function UserBubble({ text }: { text: string }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
      <div style={S.userBubble}>{text}</div>
    </div>
  );
}

function AssistantTurn({
  text,
  toolEvents,
  thinking,
  streaming,
}: {
  text: string;
  toolEvents: ToolEvent[];
  thinking?: string;
  streaming?: boolean;
}) {
  return (
    <div style={S.assistantRow}>
      <div style={S.avatar}>
        <Icon name="sparkles" size={13} style={{ color: 'var(--primary-fg)' }} />
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        {toolEvents.length > 0 && <AgentRunFlow events={toolEvents} />}
        {thinking ? <div style={S.thinking}>{thinking}</div> : null}
        {text ? <Markdown>{text}</Markdown> : null}
        {streaming && !text && <div style={S.working}>Working…</div>}
        {streaming && text && <span style={S.cursor}>▋</span>}
        {!streaming && !text && toolEvents.length > 0 && (
          <div style={S.working}>Done — see the tool output above.</div>
        )}
      </div>
    </div>
  );
}

function AgentRunFlow({ events }: { events: ToolEvent[] }) {
  const [open, setOpen] = useState(true);
  const calls = events.filter((e) => e.kind === 'call').length;
  return (
    <div style={S.flow}>
      <div style={S.flowHead} onClick={() => setOpen((o) => !o)}>
        <Icon name={open ? 'chevron-down' : 'chevron-right'} size={13} style={{ color: 'var(--fg-muted)' }} />
        <Icon name="wrench" size={12} style={{ color: 'var(--fg-muted)' }} />
        <span>Agent run · {calls} tool {calls === 1 ? 'call' : 'calls'}</span>
      </div>
      {open && (
        <div style={S.flowBody}>
          {events.map((e, i) =>
            e.kind === 'call' ? (
              <div key={i} style={S.flowCall}>
                <Icon name="arrow-right" size={12} style={{ color: 'var(--status-info)' }} />
                <span style={S.flowToolName}>{e.name}</span>
                <code style={S.flowArgs}>{shortJson(e.args)}</code>
              </div>
            ) : (
              <ToolResultRow key={i} result={e.result ?? ''} />
            ),
          )}
        </div>
      )}
    </div>
  );
}

function ToolResultRow({ result }: { result: string }) {
  const [open, setOpen] = useState(false);
  const preview = result.length > 120 ? result.slice(0, 120) + '…' : result;
  return (
    <div style={S.flowResult}>
      <div style={S.flowResultHead} onClick={() => setOpen((o) => !o)}>
        <Icon name="corner-down-right" size={12} style={{ color: 'var(--status-success)' }} />
        <span style={S.flowResultLabel}>result</span>
        <code style={S.flowArgs}>{open ? '' : preview}</code>
      </div>
      {open && <pre style={S.flowResultPre}>{result}</pre>}
    </div>
  );
}

function shortJson(v: unknown): string {
  try {
    const s = JSON.stringify(v);
    return s.length > 90 ? s.slice(0, 90) + '…' : s;
  } catch {
    return String(v);
  }
}

// ── Styles ────────────────────────────────────────────────────────────────────
const S: Record<string, React.CSSProperties> = {
  root: { display: 'flex', flexDirection: 'column', height: '100%', minWidth: 0 },
  header: {
    height: 48, flexShrink: 0, display: 'flex', alignItems: 'center',
    justifyContent: 'space-between', padding: '0 16px',
    borderBottom: '1px solid var(--border-default)', background: 'var(--bg-app)',
  },
  headerTitle: { fontSize: 14, fontWeight: 600, color: 'var(--fg-primary)' },
  modelSelect: {
    fontSize: 12, color: 'var(--fg-secondary)', background: 'var(--bg-app)',
    border: '1px solid var(--border-card)', borderRadius: 6, padding: '5px 8px', cursor: 'pointer',
  },
  body: { flex: 1, display: 'flex', minHeight: 0 },

  rail: {
    width: 240, minWidth: 240, borderRight: '1px solid var(--border-default)',
    background: 'var(--bg-sidebar)', display: 'flex', flexDirection: 'column', padding: 10,
  },
  newBtn: {
    display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
    padding: '8px 10px', border: '1px solid var(--border-card)', borderRadius: 8,
    background: 'var(--bg-app)', color: 'var(--fg-primary)', fontSize: 13, fontWeight: 500,
    cursor: 'pointer', marginBottom: 10,
  },
  threadList: { flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 1 },
  threadItem: {
    display: 'flex', alignItems: 'center', gap: 8, padding: '7px 8px', borderRadius: 6,
    cursor: 'pointer', fontSize: 13, color: 'var(--fg-secondary)',
  },
  threadTitle: { flex: 1, minWidth: 0, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' },
  threadDel: { display: 'inline-flex', padding: 2 },
  railEmpty: { fontSize: 12, color: 'var(--fg-subtle)', padding: '8px 6px' },

  chatCol: { flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 },
  scroll: { flex: 1, overflowY: 'auto', overflowX: 'hidden', padding: '24px 0', minWidth: 0 },
  thread: { maxWidth: 'none', width: '100%', boxSizing: 'border-box', margin: '0 auto', padding: '0 24px', display: 'flex', flexDirection: 'column', gap: 22, minWidth: 0 },

  empty: { textAlign: 'center', marginTop: 80, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10 },
  emptyTitle: { fontSize: 20, fontWeight: 600, color: 'var(--fg-primary)' },
  emptySub: { fontSize: 13, color: 'var(--fg-muted)', maxWidth: 420, lineHeight: 1.5 },

  userBubble: {
    background: 'var(--bg-chip)', color: 'var(--fg-primary)', padding: '10px 14px',
    borderRadius: 14, borderBottomRightRadius: 4, fontSize: 14, lineHeight: 1.5,
    maxWidth: '80%', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', wordBreak: 'break-word',
  },
  working: { fontSize: 13, color: 'var(--fg-muted)', fontStyle: 'italic' },
  assistantRow: { display: 'flex', gap: 12, alignItems: 'flex-start' },
  avatar: {
    width: 26, height: 26, borderRadius: 7, background: 'var(--primary)', flexShrink: 0,
    display: 'flex', alignItems: 'center', justifyContent: 'center', marginTop: 2,
  },
  assistantText: { fontSize: 14, lineHeight: 1.6, color: 'var(--fg-primary)', whiteSpace: 'pre-wrap' },
  thinking: {
    fontSize: 12, color: 'var(--fg-subtle)', fontStyle: 'italic', whiteSpace: 'pre-wrap',
    marginBottom: 8, borderLeft: '2px solid var(--border-default)', paddingLeft: 8,
  },
  cursor: { color: 'var(--fg-subtle)' },

  flow: {
    border: '1px solid var(--border-default)', borderRadius: 8, marginBottom: 10,
    background: 'var(--bg-muted)', overflow: 'hidden', maxWidth: '100%',
  },
  flowHead: {
    display: 'flex', alignItems: 'center', gap: 6, padding: '7px 10px', cursor: 'pointer',
    fontSize: 12, fontWeight: 500, color: 'var(--fg-secondary)',
  },
  flowBody: { padding: '4px 10px 8px 10px', display: 'flex', flexDirection: 'column', gap: 6 },
  flowCall: { display: 'flex', alignItems: 'flex-start', gap: 6, fontSize: 12, flexWrap: 'wrap', minWidth: 0 },
  flowToolName: { fontFamily: 'var(--font-mono)', fontWeight: 500, color: 'var(--fg-primary)', flexShrink: 0 },
  flowArgs: {
    fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg-muted)', minWidth: 0,
    whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', wordBreak: 'break-all', maxWidth: '100%',
  },
  flowResult: { marginLeft: 18, minWidth: 0 },
  flowResultHead: { display: 'flex', alignItems: 'flex-start', gap: 6, cursor: 'pointer', minWidth: 0, flexWrap: 'wrap' },
  flowResultLabel: { fontSize: 11, color: 'var(--status-success)', fontWeight: 500 },
  flowResultPre: {
    fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg-secondary)',
    background: 'var(--bg-app)', border: '1px solid var(--border-default)', borderRadius: 6,
    padding: 8, marginTop: 4, maxHeight: 260, overflow: 'auto', whiteSpace: 'pre-wrap',
    overflowWrap: 'anywhere', wordBreak: 'break-word', maxWidth: '100%',
  },

  error: { fontSize: 13, color: 'var(--status-danger)', background: 'var(--status-danger-bg)', padding: '8px 12px', borderRadius: 8 },

  composerWrap: { flexShrink: 0, padding: '10px 24px 16px 24px', borderTop: '1px solid var(--border-default)', background: 'var(--bg-app)' },
  composer: {
    maxWidth: 'none', margin: '0 auto', display: 'flex', alignItems: 'flex-end', gap: 8,
    border: '1px solid var(--border-card)', borderRadius: 14, padding: '8px 8px 8px 14px',
    background: 'var(--bg-app)', boxShadow: '0 1px 2px rgba(0,0,0,0.04)',
  },
  textarea: {
    flex: 1, resize: 'none', border: 'none', outline: 'none', background: 'transparent',
    fontSize: 14, lineHeight: 1.5, color: 'var(--fg-primary)', fontFamily: 'var(--font-sans)',
    maxHeight: 200, padding: '4px 0',
  },
  sendBtn: {
    width: 32, height: 32, borderRadius: 8, border: 'none', background: 'var(--primary)',
    color: 'var(--primary-fg)', display: 'flex', alignItems: 'center', justifyContent: 'center',
    cursor: 'pointer', flexShrink: 0,
  },
  hint: { maxWidth: 'none', margin: '6px auto 0 auto', fontSize: 11, color: 'var(--fg-subtle)', textAlign: 'center' },
  iconBtn: {
    width: 28, height: 28, borderRadius: 6, border: 'none', background: 'transparent',
    display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer', flexShrink: 0,
  },
};
