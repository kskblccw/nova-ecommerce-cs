"use client";

import { useState } from "react";
import { Sparkles, ThumbsDown, ThumbsUp } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type { Message } from "@/lib/types";
import { cn, formatDate } from "@/lib/utils";
import { PartsView } from "./parts";

interface Props {
  message: Message;
  onFeedback?: (messageId: string, rating: 1 | -1) => void;
}

export function MessageBubble({ message, onFeedback }: Props) {
  const [rating, setRating] = useState<1 | -1 | null>(null);
  const isUser = message.role === "user";

  if (isUser) {
    return (
      <div className="bubble bubble-user whitespace-pre-wrap">{message.content}</div>
    );
  }

  return (
    <div className="flex max-w-[92%] items-start gap-3">
      <div
        className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl text-white"
        style={{
          background: "linear-gradient(135deg, var(--brand-strong), var(--brand))",
          boxShadow: "0 8px 18px -12px var(--brand)",
        }}
      >
        <Sparkles className="h-4 w-4" />
      </div>
      <div className="min-w-0 flex-1">
        <div className="bubble bubble-ai">
          {message.content ? (
            <div className="markdown">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
            </div>
          ) : (
            <span style={{ color: "var(--muted)" }}>正在为您整理…</span>
          )}
          <PartsView parts={message.parts} />
        </div>
        <div className="mt-1.5 flex items-center gap-3 pl-1 text-[11px]" style={{ color: "var(--faint)" }}>
          <span>{formatDate(message.created_at)}</span>
          {onFeedback && !message.id.startsWith("temp-") && (
            <>
              <button
                type="button"
                aria-label="有帮助"
                onClick={() => {
                  setRating(1);
                  onFeedback(message.id, 1);
                }}
                className={cn("transition hover:opacity-100", rating === 1 ? "opacity-100" : "opacity-50")}
                style={{ color: rating === 1 ? "var(--brand)" : "var(--faint)" }}
              >
                <ThumbsUp className="h-3.5 w-3.5" />
              </button>
              <button
                type="button"
                aria-label="没帮助"
                onClick={() => {
                  setRating(-1);
                  onFeedback(message.id, -1);
                }}
                className={cn("transition hover:opacity-100", rating === -1 ? "opacity-100" : "opacity-50")}
                style={{ color: rating === -1 ? "var(--danger)" : "var(--faint)" }}
              >
                <ThumbsDown className="h-3.5 w-3.5" />
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
