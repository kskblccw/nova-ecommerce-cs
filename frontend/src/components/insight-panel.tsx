"use client";

import { Clock3, LifeBuoy, PackageSearch, ReceiptText, ShieldCheck, Sparkles, Truck } from "lucide-react";

import type { PendingAction } from "@/lib/types";

const QUICK = [
  { label: "查询我的订单", icon: PackageSearch, prompt: "帮我查一下我的订单" },
  { label: "物流到哪了", icon: Truck, prompt: "我的快递到哪了？帮我查查物流" },
  { label: "退货 / 换货", icon: ReceiptText, prompt: "我要退货，商品有质量问题" },
  { label: "商品推荐", icon: Sparkles, prompt: "推荐一款降噪耳机" },
];

interface Props {
  pending: PendingAction | null;
  onQuick: (prompt: string) => void;
  messageCount: number;
}

export function InsightPanel({ pending, onQuick, messageCount }: Props) {
  return (
    <aside className="hidden w-[300px] shrink-0 flex-col gap-3 xl:flex">
      <div className="card p-4">
        <div className="flex items-center gap-2 text-[13px] font-semibold">
          <Sparkles className="h-4 w-4" style={{ color: "var(--brand)" }} />
          快捷服务
        </div>
        <div className="mt-3 grid grid-cols-2 gap-2">
          {QUICK.map((item) => (
            <button
              key={item.label}
              type="button"
              onClick={() => onQuick(item.prompt)}
              className="rounded-xl border px-3 py-3 text-left text-[12px] transition hover:-translate-y-0.5"
              style={{
                borderColor: "var(--border)",
                background: "var(--surface-soft)",
              }}
            >
              <item.icon className="mb-2 h-4 w-4" style={{ color: "var(--brand-strong)" }} />
              {item.label}
            </button>
          ))}
        </div>
      </div>

      {pending?.has_pending && (
        <div className="card p-4" style={{ borderColor: "var(--amber)" }}>
          <div className="flex items-center gap-2 text-[13px] font-semibold" style={{ color: "var(--amber)" }}>
            <ShieldCheck className="h-4 w-4" />
            待确认操作
          </div>
          <p className="mt-2 text-[12.5px] leading-relaxed" style={{ color: "var(--muted)" }}>
            {pending.summary}
          </p>
          <p className="mt-1 text-[11px]" style={{ color: "var(--faint)" }}>
            请在对话中确认或取消
          </p>
        </div>
      )}

      <div className="card p-4">
        <div className="flex items-center gap-2 text-[13px] font-semibold">
          <LifeBuoy className="h-4 w-4" style={{ color: "var(--brand)" }} />
          会话概览
        </div>
        <div className="mt-3 space-y-2 text-[12.5px]">
          <div className="flex items-center justify-between">
            <span style={{ color: "var(--muted)" }}>消息数</span>
            <span className="font-medium">{messageCount}</span>
          </div>
          <div className="flex items-center justify-between">
            <span style={{ color: "var(--muted)" }}>服务状态</span>
            <span className="chip chip-brand">在线</span>
          </div>
          <div className="flex items-center justify-between">
            <span style={{ color: "var(--muted)" }}>平均响应</span>
            <span className="font-medium">秒级</span>
          </div>
        </div>
      </div>

      <div className="card p-4 text-[12px] leading-relaxed" style={{ color: "var(--muted)" }}>
        <div className="mb-2 flex items-center gap-2 text-[13px] font-semibold" style={{ color: "var(--text)" }}>
          <Clock3 className="h-4 w-4" style={{ color: "var(--brand)" }} />
          服务时间
        </div>
        智能客服 7×24 小时在线；人工客服每天 9:00-22:00，热线 400-888-8888。
      </div>
    </aside>
  );
}
