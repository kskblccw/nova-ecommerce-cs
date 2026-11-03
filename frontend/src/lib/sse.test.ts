import { afterEach, describe, expect, it, vi } from "vitest";

import type { SseEvent } from "./types";
import { parseSseBuffer, streamChat } from "./sse";

describe("parseSseBuffer", () => {
  it("splits complete events and keeps the unfinished remainder", () => {
    const buffer =
      'event: token\ndata: {"text":"你"}\n\n' +
      'event: done\ndata: {"message_id":"m1","content":"你好"}\n\n' +
      'event: token\ndata: {"text":"残';

    const parsed = parseSseBuffer(buffer);

    expect(parsed.events.map((event) => event.type)).toEqual(["token", "done"]);
    expect(parsed.events[0]).toMatchObject({ type: "token", text: "你" });
    expect(parsed.rest).toContain("残");
  });

  it("ignores heartbeat comments and malformed payloads", () => {
    const buffer =
      ": ping\n\n" +
      "event: token\ndata: not-json\n\n" +
      'event: status\ndata: {"label":"正在查询"}\n\n';

    const parsed = parseSseBuffer(buffer);

    expect(parsed.events).toHaveLength(1);
    expect(parsed.events[0]).toMatchObject({ type: "status", label: "正在查询" });
  });

  it("joins multi-line data before parsing", () => {
    const parsed = parseSseBuffer('event: done\ndata: {"content":\ndata: "hi"}\n\n');

    expect(parsed.events[0]).toMatchObject({ type: "done", content: "hi" });
  });
});

describe("streamChat", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("forwards parsed events and sends the idempotency key", async () => {
    const encoder = new TextEncoder();
    const body = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode('event: token\ndata: {"text":"你"}\n\n'));
        controller.enqueue(
          encoder.encode(
            'event: done\ndata: {"message_id":"m1","content":"你好","parts":[]}\n\n',
          ),
        );
        controller.close();
      },
    });
    const fetchMock = vi.fn().mockResolvedValue(new Response(body, { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    const received: SseEvent[] = [];
    await streamChat("c1", "你好", {
      clientMessageId: "client-1",
      onEvent: (event) => received.push(event),
    });

    expect(received.map((event) => event.type)).toEqual(["token", "done"]);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/chat/c1/stream");
    expect(JSON.parse(String(init.body))).toMatchObject({
      message: "你好",
      client_message_id: "client-1",
    });
  });

  it("throws a readable error for non-2xx responses", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ error: { message: "会话不存在" } }), { status: 404 }),
      ),
    );

    await expect(
      streamChat("c1", "hi", { onEvent: () => undefined }),
    ).rejects.toThrow("会话不存在");
  });
});
