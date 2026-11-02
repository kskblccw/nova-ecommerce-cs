"use client";

import { useState } from "react";
import { Check, LogOut, MessageSquarePlus, Pencil, Settings2, Trash2, X } from "lucide-react";

import type { Conversation, User } from "@/lib/types";
import { cn, formatDate } from "@/lib/utils";
import { BrandMark } from "./brand";
import { ThemeToggle } from "./theme-toggle";

interface Props {
  conversations: Conversation[];
  activeId: string | null;
  user: User | null;
  open: boolean;
  onClose: () => void;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
  onRename: (id: string, title: string) => void;
  onLogout: () => void;
}

export function Sidebar({
  conversations,
  activeId,
  user,
  open,
  onClose,
  onSelect,
  onNew,
  onDelete,
  onRename,
  onLogout,
}: Props) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  function startRename(conversation: Conversation) {
    setEditingId(conversation.id);
    setDraft(conversation.title);
  }

  function commitRename() {
    if (editingId && draft.trim()) onRename(editingId, draft.trim());
    setEditingId(null);
  }

  return (
    <>
      {open && (
        <button
          type="button"
          aria-label="关闭侧边栏"
          onClick={onClose}
          className="fixed inset-0 z-40 bg-black/40 backdrop-blur-sm lg:hidden"
        />
      )}
      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-50 flex w-[280px] flex-col border-r transition-transform duration-200 lg:static lg:translate-x-0",
          open ? "translate-x-0" : "-translate-x-full",
        )}
        style={{ background: "var(--surface)", borderColor: "var(--border)" }}
      >
        <div className="flex items-center justify-between px-4 pb-3 pt-4">
          <BrandMark />
          <div className="flex items-center gap-1">
            <ThemeToggle />
            <button type="button" className="icon-btn lg:hidden" onClick={onClose} aria-label="关闭">
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        <div className="px-3 pb-3">
          <button type="button" onClick={onNew} className="btn btn-primary w-full">
            <MessageSquarePlus className="h-4 w-4" />
            新建会话
          </button>
        </div>

        <div className="px-4 pb-1 text-[11px] font-medium uppercase tracking-wider" style={{ color: "var(--faint)" }}>
          最近会话
        </div>
        <div className="scroll-area flex-1 px-2 pb-3">
          {conversations.length === 0 && (
            <div className="mx-2 mt-3 rounded-xl border border-dashed px-3 py-6 text-center text-[12px]"
              style={{ borderColor: "var(--border-strong)", color: "var(--faint)" }}>
              还没有会话，点击上方按钮开始
            </div>
          )}
          {conversations.map((conversation) => {
            const active = conversation.id === activeId;
            const editing = editingId === conversation.id;
            return (
              <div key={conversation.id} className="group relative">
                <button
                  type="button"
                  onClick={() => onSelect(conversation.id)}
                  className={cn("sidebar-item", active && "sidebar-item-active")}
                >
                  {editing ? (
                    <input
                      autoFocus
                      value={draft}
                      onChange={(event) => setDraft(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") commitRename();
                        if (event.key === "Escape") setEditingId(null);
                      }}
                      onClick={(event) => event.stopPropagation()}
                      className="w-full bg-transparent text-[13px] outline-none"
                      style={{ color: "var(--text)" }}
                    />
                  ) : (
                    <div className="truncate text-[13px] font-medium">{conversation.title}</div>
                  )}
                  <div className="mt-1 flex items-center justify-between gap-2">
                    <span className="truncate text-[11px]" style={{ color: "inherit", opacity: 0.72 }}>
                      {conversation.preview || "暂无消息"}
                    </span>
                    <span className="shrink-0 text-[10px]" style={{ opacity: 0.6 }}>
                      {formatDate(conversation.last_message_at ?? conversation.created_at)}
                    </span>
                  </div>
                </button>
                <div className="absolute right-1 top-2 hidden items-center gap-0.5 group-hover:flex">
                  {editing ? (
                    <button
                      type="button"
                      className="icon-btn !h-7 !w-7"
                      aria-label="保存标题"
                      onClick={(event) => {
                        event.stopPropagation();
                        commitRename();
                      }}
                    >
                      <Check className="h-3.5 w-3.5" />
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="icon-btn !h-7 !w-7"
                      aria-label="重命名"
                      onClick={(event) => {
                        event.stopPropagation();
                        startRename(conversation);
                      }}
                    >
                      <Pencil className="h-3.5 w-3.5" />
                    </button>
                  )}
                  <button
                    type="button"
                    className="icon-btn !h-7 !w-7"
                    aria-label="删除会话"
                    onClick={(event) => {
                      event.stopPropagation();
                      onDelete(conversation.id);
                    }}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
            );
          })}
        </div>

        <div className="border-t p-3" style={{ borderColor: "var(--border)" }}>
          {user ? (
            <div className="flex items-center gap-2.5">
              <div
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-[13px] font-semibold"
                style={{ background: "var(--brand-soft)", color: "var(--brand-ink)" }}
              >
                {user.name.slice(0, 1)}
              </div>
              <div className="min-w-0 flex-1">
                <div className="truncate text-[13px] font-medium">{user.name}</div>
                <div className="text-[11px]" style={{ color: "var(--faint)" }}>
                  {user.phone_masked}
                  {user.role === "admin" && " · 管理员"}
                </div>
              </div>
              {user.role === "admin" && (
                <a href="/admin/knowledge" className="icon-btn" title="知识库后台">
                  <Settings2 className="h-4 w-4" />
                </a>
              )}
              <button type="button" onClick={onLogout} className="icon-btn" title="退出登录" aria-label="退出登录">
                <LogOut className="h-4 w-4" />
              </button>
            </div>
          ) : (
            <div className="text-[12px]" style={{ color: "var(--faint)" }}>
              未登录
            </div>
          )}
        </div>
      </aside>
    </>
  );
}
