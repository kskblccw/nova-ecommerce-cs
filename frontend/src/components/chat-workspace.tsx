"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { AlertCircle, Check, Loader2, Menu, ShieldCheck, Sparkles, X } from "lucide-react";

import { api, ApiError } from "@/lib/api";
import { streamChat } from "@/lib/sse";
import type { Conversation, Message, PendingAction, User } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Composer } from "./composer";
import { InsightPanel } from "./insight-panel";
import { MessageBubble } from "./message-bubble";
import { Sidebar } from "./sidebar";

const WELCOME_PROMPTS = [
  { title: "查订单", desc: "订单状态、金额与商品明细", prompt: "帮我查一下我的订单" },
  { title: "查物流", desc: "实时轨迹与预计送达", prompt: "我的快递到哪了？帮我查查物流" },
  { title: "退换货", desc: "7 天无理由与售后进度", prompt: "我要退货，商品有质量问题" },
  { title: "智能推荐", desc: "按预算与场景找商品", prompt: "推荐一款 3000 元以内的降噪耳机" },
];

function newClientId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `c_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
}

export function ChatWorkspace({ initialId }: { initialId?: string }) {
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(initialId ?? null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [streamingText, setStreamingText] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingAction | null>(null);
  const [approvalBusy, setApprovalBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [canRetry, setCanRetry] = useState(false);
  const [degraded, setDegraded] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [lastText, setLastText] = useState("");
  const [hasOlder, setHasOlder] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const lastClientIdRef = useRef("");
  const skipAutoScrollRef = useRef(false);

  const activeConversation = useMemo(
    () => conversations.find((item) => item.id === activeId) ?? null,
    [conversations, activeId],
  );

  const refreshConversations = useCallback(async () => {
    try {
      const list = await api.listConversations();
      setConversations(list);
    } catch {
      /* silent */
    }
  }, []);

  const loadMessages = useCallback(async (conversationId: string) => {
    setLoading(true);
    setHasOlder(false);
    skipAutoScrollRef.current = false;
    try {
      const [page, pendingData] = await Promise.all([
        api.listMessages(conversationId),
        api.pending(conversationId).catch(() => null),
      ]);
      setMessages(page.items);
      setHasOlder(page.has_more);
      setPending(pendingData?.has_pending ? pendingData : null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        router.replace("/login");
        return;
      }
      setError(err instanceof Error ? err.message : "加载会话失败");
    } finally {
      setLoading(false);
    }
  }, [router]);

  useEffect(() => {
    let cancelled = false;
    async function bootstrap() {
      try {
        const me = await api.me();
        if (cancelled) return;
        setUser(me);
      } catch {
        if (!cancelled) router.replace("/login");
        return;
      }

      try {
        const list = await api.listConversations();
        if (cancelled) return;
        setConversations(list);

        const target = initialId ?? list[0]?.id;
        if (target) {
          if (!initialId) router.replace(`/chat/${target}`);
          setActiveId(target);
          await loadMessages(target);
        } else {
          setLoading(false);
        }
      } catch {
        if (!cancelled) setLoading(false);
      }
    }
    void bootstrap();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!initialId) return;
    if (initialId === activeId && messages.length > 0) return;
    setActiveId(initialId);
    void loadMessages(initialId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialId]);

  useEffect(() => {
    const node = scrollRef.current;
    if (!node) return;
    if (skipAutoScrollRef.current) {
      skipAutoScrollRef.current = false;
      return;
    }
    node.scrollTop = node.scrollHeight;
  }, [messages, streamingText, status]);

  const loadOlder = useCallback(async () => {
    if (!activeId || !hasOlder || loadingOlder) return;
    const oldest = messages[0]?.created_at;
    if (!oldest) return;

    setLoadingOlder(true);
    const scroller = scrollRef.current;
    const previousHeight = scroller?.scrollHeight ?? 0;
    try {
      const page = await api.listMessages(activeId, { before: oldest, limit: 30 });
      if (page.items.length > 0) {
        skipAutoScrollRef.current = true;
        setMessages((prev) => [...page.items, ...prev]);
        requestAnimationFrame(() => {
          if (scroller) scroller.scrollTop = scroller.scrollHeight - previousHeight;
        });
      }
      setHasOlder(page.has_more);
    } catch {
      setError("加载更早的消息失败");
    } finally {
      setLoadingOlder(false);
    }
  }, [activeId, hasOlder, loadingOlder, messages]);

  async function send(textOverride?: string, isRetry = false) {
    const text = (textOverride ?? input).trim();
    if (!text || busy) return;
    if (pending?.has_pending) {
      setError("请先处理上方的待确认操作");
      return;
    }

    let conversationId = activeId;
    if (!conversationId) {
      try {
        const conversation = await api.createConversation();
        conversationId = conversation.id;
        setConversations((prev) => [conversation, ...prev]);
        setActiveId(conversation.id);
        router.replace(`/chat/${conversation.id}`);
      } catch {
        setError("创建会话失败，请稍后重试");
        return;
      }
    }

    const clientId =
      isRetry && lastClientIdRef.current ? lastClientIdRef.current : newClientId();
    lastClientIdRef.current = clientId;

    if (!isRetry) {
      const optimistic: Message = {
        id: `temp-${clientId}`,
        role: "user",
        content: text,
        parts: [],
        created_at: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, optimistic]);
    }
    setInput("");
    setBusy(true);
    setStreamingText("");
    setStatus("正在理解您的问题");
    setError(null);
    setCanRetry(false);
    setDegraded(false);
    setLastText(text);

    const controller = new AbortController();
    abortRef.current = controller;

    let buffer = "";
    try {
      await streamChat(conversationId, text, {
        signal: controller.signal,
        clientMessageId: clientId,
        onEvent: (event) => {
          switch (event.type) {
            case "meta":
              setConversations((prev) =>
                prev.map((item) =>
                  item.id === event.conversation_id ? { ...item, title: event.title } : item,
                ),
              );
              break;
            case "status":
              setStatus(event.label);
              break;
            case "token":
              buffer += event.text;
              setStreamingText(buffer);
              break;
            case "reset":
              buffer = "";
              setStreamingText("");
              break;
            case "approval":
              setPending({
                has_pending: true,
                action_id: event.action_id,
                type: event.action?.type ?? "",
                summary: event.summary || event.action?.summary || "",
                payload: event.action?.payload ?? {},
              });
              break;
            case "done":
              setMessages((prev) => [
                ...prev,
                {
                  id: event.message_id,
                  role: "assistant",
                  content: event.content,
                  parts: event.parts ?? [],
                  created_at: new Date().toISOString(),
                },
              ]);
              setDegraded(Boolean(event.degraded));
              setStreamingText("");
              buffer = "";
              break;
            case "error":
              setError(event.message);
              setCanRetry(Boolean(event.retryable));
              buffer = "";
              setStreamingText("");
              break;
          }
        },
      });
      void refreshConversations();
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") {
        setError("已停止生成，可重试");
        setCanRetry(true);
      } else {
        setError(err instanceof Error ? err.message : "对话失败，请稍后重试");
        setCanRetry(true);
      }
      setStreamingText("");
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
      setBusy(false);
      setStatus(null);
    }
  }

  function stop() {
    abortRef.current?.abort();
    abortRef.current = null;
    setBusy(false);
    setStatus(null);
    setStreamingText("");
  }

  function retry() {
    if (!lastText) return;
    setError(null);
    void send(lastText, true);
  }

  async function handleApproval(decision: "approve" | "reject") {
    if (!activeId) return;
    setApprovalBusy(true);
    try {
      const result = await api.approval(activeId, decision);
      setMessages((prev) => [
        ...prev,
        {
          id: result.message_id,
          role: "assistant",
          content: result.content,
          parts: result.parts ?? [],
          created_at: new Date().toISOString(),
        },
      ]);
      setPending(null);
      void refreshConversations();
    } catch (err) {
      setError(err instanceof Error ? err.message : "操作失败");
    } finally {
      setApprovalBusy(false);
    }
  }

  async function handleNew() {
    try {
      const conversation = await api.createConversation();
      setConversations((prev) => [conversation, ...prev]);
      setActiveId(conversation.id);
      setMessages([]);
      setHasOlder(false);
      setPending(null);
      setSidebarOpen(false);
      router.push(`/chat/${conversation.id}`);
    } catch {
      setError("创建会话失败");
    }
  }

  async function handleDelete(conversationId: string) {
    if (!window.confirm("删除该会话及全部消息？")) return;
    try {
      await api.deleteConversation(conversationId);
      const rest = conversations.filter((item) => item.id !== conversationId);
      setConversations(rest);
      if (conversationId === activeId) {
        if (rest[0]) {
          setActiveId(rest[0].id);
          router.replace(`/chat/${rest[0].id}`);
          void loadMessages(rest[0].id);
        } else {
          setActiveId(null);
          setMessages([]);
          router.replace("/chat");
        }
      }
    } catch {
      setError("删除会话失败");
    }
  }

  async function handleRename(conversationId: string, title: string) {
    try {
      const updated = await api.renameConversation(conversationId, title);
      setConversations((prev) =>
        prev.map((item) => (item.id === updated.id ? { ...item, title: updated.title } : item)),
      );
    } catch {
      setError("重命名失败");
    }
  }

  async function handleLogout() {
    try {
      await api.logout();
    } finally {
      router.replace("/login");
    }
  }

  const showWelcome = !loading && messages.length === 0 && !streamingText;

  return (
    <div className="flex h-dvh w-full overflow-hidden" style={{ background: "var(--bg)" }}>
      <Sidebar
        conversations={conversations}
        activeId={activeId}
        user={user}
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
        onSelect={(id) => {
          setActiveId(id);
          setSidebarOpen(false);
          router.push(`/chat/${id}`);
        }}
        onNew={handleNew}
        onDelete={handleDelete}
        onRename={handleRename}
        onLogout={handleLogout}
      />

      <main className="flex min-w-0 flex-1 flex-col">
        <header
          className="flex h-14 shrink-0 items-center justify-between gap-3 border-b px-4 lg:px-6"
          style={{ borderColor: "var(--border)", background: "var(--surface)" }}
        >
          <div className="flex min-w-0 items-center gap-3">
            <button
              type="button"
              className="icon-btn lg:hidden"
              onClick={() => setSidebarOpen(true)}
              aria-label="打开侧边栏"
            >
              <Menu className="h-[18px] w-[18px]" />
            </button>
            <div className="min-w-0">
              <div className="truncate text-[14px] font-semibold">
                {activeConversation?.title ?? "新会话"}
              </div>
              <div className="flex items-center gap-1.5 text-[11px]" style={{ color: "var(--faint)" }}>
                <span className="dot" />
                Nova 智能客服在线
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <span className="chip chip-brand hidden sm:inline-flex">
              <Sparkles className="h-3 w-3" />
              多智能体
            </span>
            {user && (
              <span className="chip hidden md:inline-flex">{user.phone_masked}</span>
            )}
          </div>
        </header>

        <div
          ref={scrollRef}
          className="scroll-area flex-1 px-4 py-5 lg:px-8"
          onScroll={(event) => {
            if (event.currentTarget.scrollTop < 40) void loadOlder();
          }}
        >
          <div className="mx-auto flex w-full max-w-3xl flex-col gap-4">
            {hasOlder && (
              <div
                className="flex justify-center py-1 text-[12px]"
                style={{ color: "var(--faint)" }}
              >
                {loadingOlder ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  "向上滚动加载更早消息"
                )}
              </div>
            )}

            {showWelcome && (
              <div className="animate-fade-up py-6">
                <div className="mb-8 text-center">
                  <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl text-white"
                    style={{
                      background: "linear-gradient(135deg, var(--brand-strong), var(--brand))",
                      boxShadow: "0 18px 40px -22px var(--brand)",
                    }}>
                    <Sparkles className="h-6 w-6" />
                  </div>
                  <h1 className="text-[26px] font-semibold tracking-tight">
                    你好，我是 <span className="text-gradient">Nova</span>
                  </h1>
                  <p className="mt-2 text-[14px]" style={{ color: "var(--muted)" }}>
                    订单、物流、售后、商品推荐，一句话帮你搞定。
                  </p>
                </div>
                <div className="grid gap-2.5 sm:grid-cols-2">
                  {WELCOME_PROMPTS.map((item) => (
                    <button
                      key={item.title}
                      type="button"
                      onClick={() => void send(item.prompt)}
                      className="card group px-4 py-4 text-left transition hover:-translate-y-0.5"
                    >
                      <div className="text-[13.5px] font-semibold">{item.title}</div>
                      <div className="mt-1 text-[12px]" style={{ color: "var(--muted)" }}>
                        {item.desc}
                      </div>
                    </button>
                  ))}
                </div>
              </div>
            )}

            {loading && (
              <div className="space-y-3">
                <div className="skeleton h-16 w-2/3" />
                <div className="skeleton ml-auto h-12 w-1/2" />
                <div className="skeleton h-24 w-3/4" />
              </div>
            )}

            {!loading &&
              messages.map((message) => (
                <MessageBubble
                  key={message.id}
                  message={message}
                  onFeedback={(messageId, rating) => {
                    void api.feedback(messageId, rating).catch(() => undefined);
                  }}
                />
              ))}

            {status && !streamingText && (
              <div className="status-pill w-fit" role="status" aria-live="polite">
                <span className="dot animate-pulse-soft" />
                {status}
              </div>
            )}

            {streamingText && (
              <div
                className="flex max-w-[92%] items-start gap-3"
                role="status"
                aria-live="polite"
                aria-atomic="false"
              >
                <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl text-white"
                  style={{ background: "linear-gradient(135deg, var(--brand-strong), var(--brand))" }}>
                  <Sparkles className="h-4 w-4" />
                </div>
                <div className="bubble bubble-ai">
                  <div className="markdown whitespace-pre-wrap">
                    {streamingText}
                    <span className="streaming-cursor" />
                  </div>
                </div>
              </div>
            )}

            {degraded && !busy && (
              <div
                className="flex items-start gap-2 rounded-xl px-3.5 py-2.5 text-[12.5px]"
                style={{ background: "var(--surface-soft)", color: "var(--muted)" }}
                role="note"
              >
                <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" style={{ color: "var(--amber)" }} />
                <span>智能客服当前繁忙，已启用降级回复。可稍后重试，或发送「转人工」联系客服。</span>
              </div>
            )}

            {pending?.has_pending && (
              <div className="card animate-fade-up p-4" style={{ borderColor: "var(--amber)" }}>
                <div className="flex items-center gap-2 text-[13.5px] font-semibold" style={{ color: "var(--amber)" }}>
                  <ShieldCheck className="h-4 w-4" />
                  需要您确认
                </div>
                <p className="mt-2 text-[13px] leading-relaxed">{pending.summary}</p>
                <div className="mt-3 flex gap-2">
                  <button
                    type="button"
                    className="btn btn-primary"
                    disabled={approvalBusy}
                    onClick={() => void handleApproval("approve")}
                  >
                    {approvalBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
                    确认执行
                  </button>
                  <button
                    type="button"
                    className="btn btn-outline"
                    disabled={approvalBusy}
                    onClick={() => void handleApproval("reject")}
                  >
                    <X className="h-4 w-4" />
                    取消
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>

        <div className="shrink-0 px-4 pb-4 lg:px-8">
          <div className="mx-auto w-full max-w-3xl">
            <Composer
              value={input}
              onChange={setInput}
              onSend={() => void send()}
              onStop={stop}
              busy={busy}
              disabled={loading}
            />
            <p className="mt-2 text-center text-[11px]" style={{ color: "var(--faint)" }}>
              内容由 AI 生成，请核对订单与金额信息后确认操作。
            </p>
          </div>
        </div>
      </main>

      <div className="hidden shrink-0 p-4 pl-0 xl:block">
        <InsightPanel
          pending={pending}
          messageCount={messages.length}
          onQuick={(prompt) => void send(prompt)}
        />
      </div>

      {error && (
        <div
          className="fixed bottom-5 left-1/2 z-[60] flex -translate-x-1/2 items-center gap-2 rounded-xl px-4 py-2.5 text-[13px] shadow-lg"
          style={{ background: "var(--danger-soft)", color: "var(--danger)" }}
          role="alert"
        >
          <AlertCircle className="h-4 w-4" />
          <span>{error}</span>
          {canRetry && lastText && (
            <button
              type="button"
              className="ml-1 rounded-lg px-2 py-1 text-[12px] font-medium"
              style={{ background: "var(--surface)", color: "var(--text)" }}
              onClick={retry}
            >
              重试
            </button>
          )}
          <button type="button" className="ml-2 opacity-70" onClick={() => setError(null)} aria-label="关闭提示">
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      )}
    </div>
  );
}
