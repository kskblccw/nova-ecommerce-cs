import { API_BASE } from "./api";
import type { SseEvent } from "./types";

interface StreamHandlers {
  onEvent: (event: SseEvent) => void;
  signal?: AbortSignal;
  clientMessageId?: string;
}

export interface ParsedSse {
  events: SseEvent[];
  rest: string;
}

/**
 * 解析一个 SSE 缓冲区：返回已完整的事件和尚未闭合的剩余片段。
 * 心跳注释行没有 data，直接忽略；单条坏数据不会中断整段流。
 */
export function parseSseBuffer(buffer: string): ParsedSse {
  const events: SseEvent[] = [];
  let rest = buffer;
  let index = rest.indexOf("\n\n");

  while (index >= 0) {
    const block = rest.slice(0, index);
    rest = rest.slice(index + 2);
    index = rest.indexOf("\n\n");
    if (!block.trim()) continue;

    let eventName = "message";
    const dataLines: string[] = [];
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) eventName = line.slice(6).trim();
      else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
    }
    if (!dataLines.length) continue;

    try {
      const payload = JSON.parse(dataLines.join("\n"));
      events.push({ type: eventName, ...payload } as SseEvent);
    } catch {
      // 忽略无法解析的数据块
    }
  }

  return { events, rest };
}

export async function streamChat(
  conversationId: string,
  message: string,
  { onEvent, signal, clientMessageId }: StreamHandlers,
): Promise<void> {
  const response = await fetch(`${API_BASE}/chat/${conversationId}/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    credentials: "include",
    body: JSON.stringify({ message, client_message_id: clientMessageId }),
    signal,
    cache: "no-store",
  });

  if (!response.ok) {
    let detail = "对话请求失败";
    try {
      const body = await response.json();
      detail = body?.error?.message ?? body?.detail ?? detail;
    } catch {
      detail = response.statusText || detail;
    }
    throw new Error(detail);
  }
  if (!response.body) throw new Error("当前浏览器不支持流式响应");

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const parsed = parseSseBuffer(buffer);
    buffer = parsed.rest;
    for (const event of parsed.events) onEvent(event);
  }
}
