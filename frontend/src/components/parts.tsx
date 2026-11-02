"use client";

import {
  BadgeCheck,
  Boxes,
  ClipboardList,
  FileQuestion,
  HandCoins,
  Info,
  PackageCheck,
  ShieldCheck,
  Sparkles,
  Truck,
  Undo2,
  UserRound,
} from "lucide-react";

import type { MessagePart } from "@/lib/types";
import { cn, formatMoney, statusLabel, statusTone } from "@/lib/utils";

const KIND_ICONS: Record<string, any> = {
  orders: ClipboardList,
  order_detail: PackageCheck,
  shipment: Truck,
  products: Boxes,
  product_detail: Boxes,
  comparison: Boxes,
  returns: Undo2,
  return_detail: Undo2,
  faq: FileQuestion,
  knowledge: Sparkles,
  handoff: HandCoins,
  user: UserRound,
  info: Info,
};

function PartShell({
  kind,
  children,
  accent,
}: {
  kind: string;
  children: React.ReactNode;
  accent?: boolean;
}) {
  const Icon = KIND_ICONS[kind] ?? BadgeCheck;
  return (
    <div
      className={cn(
        "overflow-hidden rounded-2xl border text-sm",
        accent ? "border-transparent" : "",
      )}
      style={{
        background: accent ? "var(--brand-soft)" : "var(--surface-soft)",
        borderColor: "var(--border)",
      }}
    >
      <div className="flex items-center gap-2 px-4 py-2.5 text-xs font-medium"
        style={{ color: accent ? "var(--brand-ink)" : "var(--muted)" }}>
        <Icon className="h-3.5 w-3.5" />
        {statusLabel(kind)}
      </div>
      <div className="border-t px-4 py-3" style={{ borderColor: "var(--border)" }}>
        {children}
      </div>
    </div>
  );
}

function OrderList({ data }: { data: any }) {
  const orders: any[] = data?.orders ?? [];
  if (!orders.length) return <p style={{ color: "var(--muted)" }}>暂无订单记录</p>;
  return (
    <div className="space-y-2.5">
      {orders.map((order) => (
        <div
          key={order.order_no}
          className="rounded-xl border px-3.5 py-3"
          style={{ borderColor: "var(--border)", background: "var(--surface)" }}
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="font-mono text-[12px] tracking-tight" style={{ color: "var(--muted)" }}>
              {order.order_no}
            </span>
            <span className={cn("chip", statusTone(order.status))}>{order.status}</span>
          </div>
          <div className="mt-2 space-y-1">
            {(order.items ?? []).slice(0, 3).map((item: any) => (
              <div key={`${order.order_no}-${item.product_id}`} className="flex items-center justify-between gap-3 text-[13px]">
                <span className="truncate">{item.name}</span>
                <span style={{ color: "var(--muted)" }}>×{item.quantity}</span>
              </div>
            ))}
            {(order.items ?? []).length > 3 && (
              <div className="text-[12px]" style={{ color: "var(--faint)" }}>
                另 {order.items.length - 3} 件商品
              </div>
            )}
          </div>
          <div className="mt-2.5 flex items-center justify-between border-t pt-2 text-[13px]"
            style={{ borderColor: "var(--border)" }}>
            <span style={{ color: "var(--muted)" }}>{order.created_at?.slice(0, 10)}</span>
            <span className="font-semibold">{formatMoney(order.total)}</span>
          </div>
        </div>
      ))}
    </div>
  );
}

function ShipmentTimeline({ data }: { data: any }) {
  const events: any[] = data?.events ?? [];
  if (!data?.tracking_no) {
    return <p style={{ color: "var(--muted)" }}>{data?.status ?? "暂无物流信息"}</p>;
  }
  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2 text-[13px]">
        <span className="chip chip-brand">{data.carrier}</span>
        <span className="font-mono text-[12px]" style={{ color: "var(--muted)" }}>
          {data.tracking_no}
        </span>
      </div>
      <ol className="relative ml-1.5 space-y-3 border-l pl-4" style={{ borderColor: "var(--border-strong)" }}>
        {events.map((event, index) => {
          const active = index === events.length - 1;
          return (
            <li key={`${event.time}-${index}`} className="relative">
              <span
                className="absolute -left-[21px] top-1 h-2.5 w-2.5 rounded-full border-2"
                style={{
                  background: active ? "var(--brand)" : "var(--surface)",
                  borderColor: active ? "var(--brand)" : "var(--border-strong)",
                }}
              />
              <div className="text-[13px]" style={{ color: active ? "var(--text)" : "var(--muted)" }}>
                {event.label}
              </div>
              <div className="text-[11px]" style={{ color: "var(--faint)" }}>
                {event.time}
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function ProductGrid({ data }: { data: any }) {
  const products: any[] = data?.products ?? [];
  if (!products.length) return <p style={{ color: "var(--muted)" }}>没有匹配的商品</p>;
  return (
    <div className="grid gap-2.5 sm:grid-cols-2">
      {products.map((product) => (
        <div
          key={product.id}
          className="rounded-xl border p-3"
          style={{ borderColor: "var(--border)", background: "var(--surface)" }}
        >
          <div className="flex items-start justify-between gap-2">
            <span className="text-[13px] font-medium leading-snug">{product.name}</span>
            <span className={cn("chip", product.stock > 0 ? "chip-brand" : "chip-warn")}>
              {product.stock > 0 ? "有货" : "缺货"}
            </span>
          </div>
          <div className="mt-2 flex items-center justify-between text-[12px]">
            <span style={{ color: "var(--muted)" }}>{product.category}</span>
            <span className="text-[14px] font-semibold">{formatMoney(product.price)}</span>
          </div>
        </div>
      ))}
    </div>
  );
}

function ComparisonTable({ data }: { data: any }) {
  const products: any[] = data?.products ?? [];
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[420px] text-[12px]">
        <thead>
          <tr style={{ color: "var(--muted)" }}>
            <th className="py-1.5 text-left font-medium">商品</th>
            <th className="py-1.5 text-right font-medium">价格</th>
            <th className="py-1.5 text-right font-medium">库存</th>
          </tr>
        </thead>
        <tbody>
          {products.map((product) => (
            <tr key={product.id} className="border-t" style={{ borderColor: "var(--border)" }}>
              <td className="py-2 pr-3">{product.name}</td>
              <td className="py-2 text-right font-semibold">{formatMoney(product.price)}</td>
              <td className="py-2 text-right" style={{ color: "var(--muted)" }}>
                {product.stock}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ReturnList({ data }: { data: any }) {
  const items: any[] = data?.returns ?? [data].filter(Boolean);
  if (!items.length) return <p style={{ color: "var(--muted)" }}>暂无售后申请</p>;
  return (
    <div className="space-y-2">
      {items.map((item) => (
        <div key={item.return_no} className="rounded-xl border p-3"
          style={{ borderColor: "var(--border)", background: "var(--surface)" }}>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="font-mono text-[12px]">{item.return_no}</span>
            <span className={cn("chip", statusTone(item.status))}>{item.status}</span>
          </div>
          <div className="mt-1.5 text-[12px]" style={{ color: "var(--muted)" }}>
            {item.type} · {item.reason}
          </div>
        </div>
      ))}
    </div>
  );
}

function KnowledgeList({ data }: { data: any }) {
  const faqs: any[] = data?.faqs ?? [];
  const hits: any[] = data?.hits ?? [];
  if (faqs.length) {
    return (
      <div className="space-y-3">
        {faqs.map((faq) => (
          <div key={`${faq.question}-${faq.category}`}>
            <div className="text-[13px] font-medium">{faq.question}</div>
            <div className="mt-1 text-[12.5px] leading-relaxed" style={{ color: "var(--muted)" }}>
              {faq.answer}
            </div>
          </div>
        ))}
      </div>
    );
  }
  if (hits.length) {
    return (
      <div className="space-y-3">
        {hits.map((hit) => (
          <div key={hit.document_id}>
            <div className="flex items-center gap-2 text-[13px] font-medium">
              {hit.title}
              <span className="chip">{hit.category}</span>
            </div>
            <div className="mt-1 text-[12.5px] leading-relaxed" style={{ color: "var(--muted)" }}>
              {hit.content?.slice(0, 220)}
            </div>
          </div>
        ))}
      </div>
    );
  }
  return <p style={{ color: "var(--muted)" }}>暂未检索到内容</p>;
}

function HandoffCard({ data }: { data: any }) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2 text-[13px]">
        <ShieldCheck className="h-4 w-4" style={{ color: "var(--amber)" }} />
        人工工单已创建
      </div>
      <div className="font-mono text-[12px]" style={{ color: "var(--muted)" }}>
        {data?.ticket_id}
      </div>
      <div className="text-[12px]" style={{ color: "var(--muted)" }}>
        原因：{data?.reason} · 状态：排队中
      </div>
    </div>
  );
}

function SimpleInfo({ data, summary }: { data: any; summary: string }) {
  if (data?.name) {
    return (
      <div className="flex items-center gap-3">
        <div className="flex h-9 w-9 items-center justify-center rounded-full text-sm font-semibold"
          style={{ background: "var(--brand-soft)", color: "var(--brand-ink)" }}>
          {String(data.name).slice(0, 1)}
        </div>
        <div>
          <div className="text-[13px] font-medium">{data.name}</div>
          <div className="text-[12px]" style={{ color: "var(--muted)" }}>
            {data.phone_masked}
          </div>
        </div>
      </div>
    );
  }
  return <p className="text-[13px]" style={{ color: "var(--muted)" }}>{summary}</p>;
}

export function PartsView({ parts }: { parts: MessagePart[] }) {
  if (!parts?.length) return null;
  return (
    <div className="mt-3 space-y-2.5">
      {parts.map((part, index) => {
        const key = `${part.kind}-${index}`;
        const data = part.data as any;
        switch (part.kind) {
          case "orders":
            return (
              <PartShell key={key} kind={part.kind}>
                <OrderList data={data} />
              </PartShell>
            );
          case "order_detail":
            return (
              <PartShell key={key} kind={part.kind} accent>
                <OrderList data={{ orders: [data] }} />
                <div className="mt-2 text-[12px]" style={{ color: "var(--muted)" }}>
                  {data?.address}
                </div>
              </PartShell>
            );
          case "shipment":
            return (
              <PartShell key={key} kind={part.kind}>
                <ShipmentTimeline data={data} />
              </PartShell>
            );
          case "products":
          case "product_detail": {
            const payload = data?.products ? data : { products: [data] };
            return (
              <PartShell key={key} kind={part.kind}>
                <ProductGrid data={payload} />
              </PartShell>
            );
          }
          case "comparison":
            return (
              <PartShell key={key} kind={part.kind}>
                <ComparisonTable data={data} />
              </PartShell>
            );
          case "returns":
          case "return_detail":
            return (
              <PartShell key={key} kind={part.kind}>
                <ReturnList data={part.kind === "returns" ? data : { returns: [data] }} />
              </PartShell>
            );
          case "faq":
          case "knowledge":
            return (
              <PartShell key={key} kind={part.kind}>
                <KnowledgeList data={data} />
              </PartShell>
            );
          case "handoff":
            return (
              <PartShell key={key} kind={part.kind} accent>
                <HandoffCard data={data} />
              </PartShell>
            );
          default:
            return (
              <PartShell key={key} kind={part.kind}>
                <SimpleInfo data={data} summary={part.summary} />
              </PartShell>
            );
        }
      })}
    </div>
  );
}
