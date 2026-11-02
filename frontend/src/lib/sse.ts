import { API_BASE } from "./api";
import type { SseEvent } from "./types";

interface StreamHandlers {
  onEvent: (event: SseEvent) => void;
  signal?: AbortSignal;
  clientMessageId?: string;
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

    let index = buffer.indexOf("\n\n");
    while (index >= 0) {
      const block = buffer.slice(0, index);
      buffer = buffer.slice(index + 2);
      index = buffer.indexOf("\n\n");
      if (!block.trim()) continue;

      let eventName = "message";
      const dataLines: string[] = [];
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) eventName = line.slice(6).trim();
        else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
      }
      if (!dataLines.length) continue;

      let payload: any;
      try {
        payload = JSON.parse(dataLines.join("\n"));
      } catch {
        continue;
      }
      onEvent({ type: eventName, ...payload } as SseEvent);
    }
  }
}
