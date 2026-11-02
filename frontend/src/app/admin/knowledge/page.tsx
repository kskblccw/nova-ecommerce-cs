"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ArrowLeft,
  BookOpen,
  ChevronLeft,
  ChevronRight,
  Database,
  FileText,
  Loader2,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";

import { BrandMark } from "@/components/brand";
import { ThemeToggle } from "@/components/theme-toggle";
import { api, ApiError } from "@/lib/api";
import type { DocumentItem, KnowledgeStats, User } from "@/lib/types";
import { cn, formatDate } from "@/lib/utils";

const EMPTY_FORM = { title: "", category: "通用", doc_type: "custom", content: "" };

export default function KnowledgeAdminPage() {
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);
  const [docs, setDocs] = useState<DocumentItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize] = useState(10);
  const [category, setCategory] = useState("");
  const [docType, setDocType] = useState("");
  const [stats, setStats] = useState<KnowledgeStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [reindexing, setReindexing] = useState(false);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<any[]>([]);
  const [searching, setSearching] = useState(false);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editing, setEditing] = useState<DocumentItem | null>(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState<{ tone: "ok" | "error"; text: string } | null>(null);

  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const categories = useMemo(
    () => Object.keys(stats?.categories ?? {}).sort(),
    [stats],
  );

  useEffect(() => {
    async function bootstrap() {
      try {
        const me = await api.me();
        setUser(me);
        if (me.role !== "admin") {
          setReady(true);
          return;
        }
        const [list, stat] = await Promise.all([
          api.admin.documents({ page: 1, page_size: pageSize }),
          api.admin.stats(),
        ]);
        setDocs(list.items);
        setTotal(list.total);
        setStats(stat);
        setReady(true);
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) {
          router.replace("/login");
          return;
        }
        setReady(true);
      } finally {
        setLoading(false);
      }
    }
    void bootstrap();
  }, [pageSize, router]);

  async function loadDocs(nextPage = page, nextCategory = category, nextType = docType) {
    setLoading(true);
    try {
      const list = await api.admin.documents({
        page: nextPage,
        page_size: pageSize,
        category: nextCategory,
        doc_type: nextType,
      });
      setDocs(list.items);
      setTotal(list.total);
      setPage(nextPage);
    } catch {
      setToast({ tone: "error", text: "文档加载失败" });
    } finally {
      setLoading(false);
    }
  }

  async function runSearch() {
    if (!query.trim()) return;
    setSearching(true);
    try {
      const data = await api.admin.search(query.trim(), 5);
      setResults(data.results ?? []);
    } catch {
      setToast({ tone: "error", text: "检索失败" });
    } finally {
      setSearching(false);
    }
  }

  async function reindex() {
    if (!window.confirm("将从商品、FAQ 与手工文档重建全量向量索引，可能耗时数十秒。继续？")) return;
    setReindexing(true);
    try {
      const data = await api.admin.reindex();
      setToast({ tone: "ok", text: `重建完成：${data.documents} 篇文档 / ${data.chunks} 个切片` });
      const stat = await api.admin.stats();
      setStats(stat);
      await loadDocs(1);
    } catch {
      setToast({ tone: "error", text: "重建失败，请检查嵌入服务配置" });
    } finally {
      setReindexing(false);
    }
  }

  function openCreate() {
    setEditing(null);
    setForm(EMPTY_FORM);
    setEditorOpen(true);
  }

  function openEdit(doc: DocumentItem) {
    if (doc.source !== "manual") {
      setToast({ tone: "error", text: "系统文档由索引自动维护，不能直接编辑" });
      return;
    }
    setEditing(doc);
    setForm({
      title: doc.title,
      category: doc.category,
      doc_type: doc.doc_type,
      content: doc.content,
    });
    setEditorOpen(true);
  }

  async function save() {
    if (!form.title.trim() || !form.content.trim()) {
      setToast({ tone: "error", text: "标题与内容不能为空" });
      return;
    }
    setSaving(true);
    try {
      if (editing) {
        await api.admin.updateDocument(editing.id, form);
        setToast({ tone: "ok", text: "文档已更新并重新索引" });
      } else {
        await api.admin.createDocument(form);
        setToast({ tone: "ok", text: "文档已创建并加入索引" });
      }
      setEditorOpen(false);
      await loadDocs(editing ? page : 1);
      const stat = await api.admin.stats();
      setStats(stat);
    } catch {
      setToast({ tone: "error", text: "保存失败，请稍后重试" });
    } finally {
      setSaving(false);
    }
  }

  async function remove(doc: DocumentItem) {
    if (doc.source !== "manual") {
      setToast({ tone: "error", text: "系统文档由索引自动维护，不能直接删除" });
      return;
    }
    if (!window.confirm(`删除文档「${doc.title}」？`)) return;
    try {
      await api.admin.deleteDocument(doc.id);
      setToast({ tone: "ok", text: "文档已删除" });
      await loadDocs(page);
      const stat = await api.admin.stats();
      setStats(stat);
    } catch (err) {
      setToast({
        tone: "error",
        text:
          err instanceof ApiError && err.status === 403
            ? err.message
            : "删除失败",
      });
    }
  }

  if (!ready) {
    return (
      <div className="flex h-dvh items-center justify-center">
        <Loader2 className="h-5 w-5 animate-spin" style={{ color: "var(--brand)" }} />
      </div>
    );
  }

  if (user?.role !== "admin") {
    return (
      <div className="flex h-dvh items-center justify-center p-6">
        <div className="card max-w-md p-8 text-center">
          <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-2xl"
            style={{ background: "var(--danger-soft)", color: "var(--danger)" }}>
            <Database className="h-5 w-5" />
          </div>
          <h1 className="text-[18px] font-semibold">需要管理员权限</h1>
          <p className="mt-2 text-[13px]" style={{ color: "var(--muted)" }}>
            当前账号无权访问知识库后台，请使用管理员账号登录。
          </p>
          <button type="button" className="btn btn-primary mt-5" onClick={() => router.replace("/chat")}>
            返回对话
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-dvh" style={{ background: "var(--bg)" }}>
      <header className="sticky top-0 z-30 border-b backdrop-blur"
        style={{ borderColor: "var(--border)", background: "color-mix(in srgb, var(--surface) 85%, transparent)" }}>
        <div className="mx-auto flex h-16 w-full max-w-6xl items-center justify-between gap-4 px-4 sm:px-6">
          <div className="flex items-center gap-4">
            <BrandMark />
            <span className="hidden text-[12px] sm:block" style={{ color: "var(--faint)" }}>
              / 知识库后台
            </span>
          </div>
          <div className="flex items-center gap-2">
            <ThemeToggle />
            <button type="button" className="btn btn-ghost" onClick={() => router.push("/chat")}>
              <ArrowLeft className="h-4 w-4" />
              <span className="hidden sm:inline">返回对话</span>
            </button>
          </div>
        </div>
      </header>

      <main className="mx-auto w-full max-w-6xl px-4 py-6 sm:px-6">
        <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="text-[22px] font-semibold tracking-tight">知识库管理</h1>
            <p className="mt-1 text-[13px]" style={{ color: "var(--muted)" }}>
              管理商品、选购指南、售后政策与 FAQ，编辑后自动重建切片索引。
            </p>
          </div>
          <div className="flex gap-2">
            <button type="button" className="btn btn-outline" onClick={reindex} disabled={reindexing}>
              {reindexing ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
              重建索引
            </button>
            <button type="button" className="btn btn-primary" onClick={openCreate}>
              <Plus className="h-4 w-4" />
              新建文档
            </button>
          </div>
        </div>

        <div className="mb-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {[
            { label: "文档总数", value: stats?.total ?? 0, icon: BookOpen },
            {
              label: "文档类型",
              value: Object.keys(stats?.doc_types ?? {}).length,
              icon: FileText,
            },
            { label: "检索次数", value: stats?.retrieval?.queries ?? 0, icon: Search },
            {
              label: "平均召回分",
              value: stats?.retrieval?.avg_top_score ?? 0,
              icon: Sparkles,
            },
          ].map((item) => (
            <div key={item.label} className="card p-4">
              <div className="flex items-center justify-between">
                <span className="text-[12px]" style={{ color: "var(--muted)" }}>
                  {item.label}
                </span>
                <item.icon className="h-4 w-4" style={{ color: "var(--brand)" }} />
              </div>
              <div className="mt-2 text-[22px] font-semibold tracking-tight">{item.value}</div>
            </div>
          ))}
        </div>

        <div className="card mb-4 p-3">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
            <div className="relative flex-1">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2"
                style={{ color: "var(--faint)" }} />
              <input
                className="input pl-9"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") void runSearch();
                }}
                placeholder="语义检索：如「降噪耳机怎么选」「退货运费谁出」"
              />
            </div>
            <button type="button" className="btn btn-soft" onClick={runSearch} disabled={searching}>
              {searching ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
              检索
            </button>
            <select
              className="input sm:w-40"
              value={category}
              onChange={(event) => {
                setCategory(event.target.value);
                void loadDocs(1, event.target.value, docType);
              }}
            >
              <option value="">全部分类</option>
              {categories.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
            <select
              className="input sm:w-36"
              value={docType}
              onChange={(event) => {
                setDocType(event.target.value);
                void loadDocs(1, category, event.target.value);
              }}
            >
              <option value="">全部类型</option>
              {["product_detail", "buying_guide", "policy", "faq", "custom"].map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </div>

          {results.length > 0 && (
            <div className="mt-3 space-y-2 border-t pt-3" style={{ borderColor: "var(--border)" }}>
              <div className="flex items-center justify-between text-[12px]" style={{ color: "var(--muted)" }}>
                <span>检索结果（{results.length}）</span>
                <button type="button" onClick={() => setResults([])} className="icon-btn !h-7 !w-7" aria-label="清空结果">
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
              {results.map((hit) => (
                <div key={hit.document_id} className="rounded-xl p-3" style={{ background: "var(--surface-soft)" }}>
                  <div className="flex items-center gap-2 text-[12.5px] font-medium">
                    {hit.title}
                    <span className="chip">{hit.category}</span>
                    {(hit.sources ?? []).map((source: string) => (
                      <span key={source} className="chip">
                        {source === "vector" ? "向量" : "关键词"}
                      </span>
                    ))}
                    <span className="ml-auto text-[11px]" style={{ color: "var(--faint)" }}>
                      {Number(hit.score).toFixed(3)}
                    </span>
                  </div>
                  <div className="mt-1 text-[12px] leading-relaxed" style={{ color: "var(--muted)" }}>
                    {hit.content}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="card overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] text-left text-[13px]">
              <thead style={{ background: "var(--surface-soft)", color: "var(--muted)" }}>
                <tr>
                  <th className="px-4 py-3 font-medium">标题</th>
                  <th className="px-4 py-3 font-medium">类型</th>
                  <th className="px-4 py-3 font-medium">分类</th>
                  <th className="px-4 py-3 font-medium">更新时间</th>
                  <th className="px-4 py-3 text-right font-medium">操作</th>
                </tr>
              </thead>
              <tbody>
                {loading && (
                  <tr>
                    <td colSpan={5} className="px-4 py-10 text-center" style={{ color: "var(--muted)" }}>
                      <Loader2 className="mx-auto h-4 w-4 animate-spin" />
                    </td>
                  </tr>
                )}
                {!loading && docs.length === 0 && (
                  <tr>
                    <td colSpan={5} className="px-4 py-10 text-center" style={{ color: "var(--faint)" }}>
                      暂无文档
                    </td>
                  </tr>
                )}
                {!loading &&
                  docs.map((doc) => (
                    <tr key={doc.id} className="border-t" style={{ borderColor: "var(--border)" }}>
                      <td className="max-w-[320px] px-4 py-3">
                        <div className="truncate font-medium">{doc.title}</div>
                        <div className="mt-0.5 truncate text-[11.5px]" style={{ color: "var(--faint)" }}>
                          {doc.content.slice(0, 80)}
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <span className={cn("chip", doc.doc_type === "custom" && "chip-brand")}>
                          {doc.doc_type}
                        </span>
                        <span className="ml-1.5 chip" style={{ opacity: 0.8 }}>
                          {doc.source === "manual" ? "自定义" : "系统"}
                        </span>
                      </td>
                      <td className="px-4 py-3" style={{ color: "var(--muted)" }}>
                        {doc.category}
                      </td>
                      <td className="px-4 py-3" style={{ color: "var(--muted)" }}>
                        {formatDate(doc.updated_at)}
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex justify-end gap-1">
                          <button
                            type="button"
                            className="icon-btn disabled:cursor-not-allowed disabled:opacity-40"
                            onClick={() => openEdit(doc)}
                            aria-label="编辑"
                            disabled={doc.source !== "manual"}
                            title={doc.source !== "manual" ? "系统文档由索引自动维护" : "编辑文档"}
                          >
                            <Pencil className="h-4 w-4" />
                          </button>
                          <button
                            type="button"
                            className="icon-btn disabled:cursor-not-allowed disabled:opacity-40"
                            onClick={() => remove(doc)}
                            aria-label="删除"
                            disabled={doc.source !== "manual"}
                            title={doc.source !== "manual" ? "系统文档由索引自动维护" : "删除文档"}
                          >
                            <Trash2 className="h-4 w-4" />
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
          <div className="flex items-center justify-between border-t px-4 py-3 text-[12px]"
            style={{ borderColor: "var(--border)", color: "var(--muted)" }}>
            <span>
              共 {total} 篇 · 第 {page} / {totalPages} 页
            </span>
            <div className="flex gap-1">
              <button
                type="button"
                className="btn btn-ghost !px-2"
                disabled={page <= 1}
                onClick={() => void loadDocs(page - 1)}
              >
                <ChevronLeft className="h-4 w-4" />
              </button>
              <button
                type="button"
                className="btn btn-ghost !px-2"
                disabled={page >= totalPages}
                onClick={() => void loadDocs(page + 1)}
              >
                <ChevronRight className="h-4 w-4" />
              </button>
            </div>
          </div>
        </div>
      </main>

      {editorOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4 backdrop-blur-sm">
          <div className="card flex max-h-[90dvh] w-full max-w-2xl flex-col overflow-hidden">
            <div className="flex items-center justify-between border-b px-5 py-3.5" style={{ borderColor: "var(--border)" }}>
              <div>
                <div className="text-[15px] font-semibold">{editing ? "编辑文档" : "新建文档"}</div>
                <div className="text-[11.5px]" style={{ color: "var(--faint)" }}>
                  保存后会自动切分并重建该文档索引
                </div>
              </div>
              <button type="button" className="icon-btn" onClick={() => setEditorOpen(false)} aria-label="关闭">
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="scroll-area flex-1 space-y-3 p-5">
              <label className="block">
                <span className="mb-1.5 block text-[12px] font-medium" style={{ color: "var(--muted)" }}>
                  标题
                </span>
                <input
                  className="input"
                  value={form.title}
                  onChange={(event) => setForm({ ...form, title: event.target.value })}
                  placeholder="文档标题"
                />
              </label>
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="block">
                  <span className="mb-1.5 block text-[12px] font-medium" style={{ color: "var(--muted)" }}>
                    分类
                  </span>
                  <input
                    className="input"
                    value={form.category}
                    onChange={(event) => setForm({ ...form, category: event.target.value })}
                  />
                </label>
                <label className="block">
                  <span className="mb-1.5 block text-[12px] font-medium" style={{ color: "var(--muted)" }}>
                    类型
                  </span>
                  <select
                    className="input"
                    value={form.doc_type}
                    onChange={(event) => setForm({ ...form, doc_type: event.target.value })}
                  >
                    {["custom", "product_detail", "buying_guide", "policy", "faq"].map((item) => (
                      <option key={item} value={item}>
                        {item}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              <label className="block">
                <span className="mb-1.5 block text-[12px] font-medium" style={{ color: "var(--muted)" }}>
                  内容
                </span>
                <textarea
                  className="input min-h-[220px] resize-y leading-relaxed"
                  value={form.content}
                  onChange={(event) => setForm({ ...form, content: event.target.value })}
                  placeholder="支持 Markdown 纯文本，建议按段落组织"
                />
              </label>
            </div>
            <div className="flex justify-end gap-2 border-t px-5 py-3.5" style={{ borderColor: "var(--border)" }}>
              <button type="button" className="btn btn-outline" onClick={() => setEditorOpen(false)}>
                取消
              </button>
              <button type="button" className="btn btn-primary" onClick={save} disabled={saving}>
                {saving && <Loader2 className="h-4 w-4 animate-spin" />}
                保存
              </button>
            </div>
          </div>
        </div>
      )}

      {toast && (
        <div
          className="fixed bottom-5 left-1/2 z-[60] -translate-x-1/2 rounded-xl px-4 py-2.5 text-[13px] shadow-lg"
          style={{
            background: toast.tone === "ok" ? "var(--brand-soft)" : "var(--danger-soft)",
            color: toast.tone === "ok" ? "var(--brand-ink)" : "var(--danger)",
          }}
        >
          <button type="button" onClick={() => setToast(null)} className="flex items-center gap-2">
            {toast.text}
            <X className="h-3.5 w-3.5 opacity-60" />
          </button>
        </div>
      )}
    </div>
  );
}
