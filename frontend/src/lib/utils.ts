import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatMoney(value: number | string | undefined) {
  const num = Number(value ?? 0);
  if (!Number.isFinite(num)) return "¥0.00";
  return `¥${num.toLocaleString("zh-CN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

export function formatDate(value: string | null | undefined) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const now = new Date();
  const diff = now.getTime() - date.getTime();
  if (diff < 60_000) return "刚刚";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`;
  if (diff < 7 * 86_400_000) return `${Math.floor(diff / 86_400_000)} 天前`;
  return date.toLocaleDateString("zh-CN", { month: "short", day: "numeric" });
}

export function statusTone(status: string) {
  if (status.includes("已完成") || status.includes("已通过")) return "chip-brand";
  if (status.includes("已取消") || status.includes("已拒绝")) return "chip-danger";
  if (status.includes("待")) return "chip-warn";
  return "chip";
}

export function statusLabel(kind: string) {
  const labels: Record<string, string> = {
    orders: "订单列表",
    order_detail: "订单详情",
    shipment: "物流轨迹",
    products: "商品推荐",
    product_detail: "商品详情",
    comparison: "商品对比",
    returns: "售后记录",
    return_detail: "售后进度",
    faq: "常见问题",
    knowledge: "知识库",
    handoff: "人工工单",
    user: "账户信息",
    info: "提示",
  };
  return labels[kind] ?? "";
}
