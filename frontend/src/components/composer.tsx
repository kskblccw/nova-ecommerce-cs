"use client";

import { useEffect, useRef } from "react";
import { ArrowUp, Square } from "lucide-react";

interface Props {
  value: string;
  onChange: (value: string) => void;
  onSend: () => void;
  onStop?: () => void;
  disabled?: boolean;
  busy?: boolean;
  placeholder?: string;
}

export function Composer({ value, onChange, onSend, onStop, disabled, busy, placeholder }: Props) {
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    node.style.height = "auto";
    node.style.height = `${Math.min(node.scrollHeight, 160)}px`;
  }, [value]);

  function handleKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (!disabled && !busy && value.trim()) onSend();
    }
  }

  return (
    <div className="card p-2.5" style={{ borderRadius: 20 }}>
      <textarea
        ref={ref}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={handleKeyDown}
        rows={1}
        placeholder={placeholder ?? "描述您的问题，例如：帮我查一下订单 ORD202607160001"}
        className="w-full resize-none bg-transparent px-3 py-2 text-[15px] outline-none placeholder:opacity-60"
        style={{ color: "var(--text)", maxHeight: 160 }}
        disabled={disabled}
      />
      <div className="flex items-center justify-between px-2 pb-0.5">
        <span className="text-[11px]" style={{ color: "var(--faint)" }}>
          {busy ? "生成中，可点击停止" : "Enter 发送 · Shift + Enter 换行"}
        </span>
        {busy ? (
          <button
            type="button"
            onClick={onStop}
            className="btn btn-outline h-9 w-9 !px-0"
            style={{ borderRadius: 12 }}
            aria-label="停止生成"
          >
            <Square className="h-3.5 w-3.5" />
          </button>
        ) : (
          <button
            type="button"
            onClick={onSend}
            disabled={disabled || busy || !value.trim()}
            className="btn btn-primary h-9 w-9 !px-0"
            style={{ borderRadius: 12 }}
            aria-label="发送消息"
          >
            <ArrowUp className="h-4 w-4" />
          </button>
        )}
      </div>
    </div>
  );
}
