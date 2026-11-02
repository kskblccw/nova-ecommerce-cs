import type {
  Conversation,
  DocumentItem,
  DocumentList,
  KnowledgeStats,
  MessagePage,
  PendingAction,
  User,
} from "./types";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE?.replace(/\/$/, "") ?? "/api";

export class ApiError extends Error {
  status: number;
  code: string;

  constructor(message: string, status: number, code = "error") {
    super(message);
    this.status = status;
    this.code = code;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
    credentials: "include",
    cache: "no-store",
  });

  if (!response.ok) {
    let message = "请求失败";
    let code = "error";
    try {
      const body = await response.json();
      message = body?.error?.message ?? body?.detail ?? message;
      code = body?.error?.code ?? code;
    } catch {
      message = response.statusText || message;
    }
    throw new ApiError(message, response.status, code);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  async register(phone: string, password: string, name: string) {
    return request<{ user: User; message: string }>("/auth/register", {
      method: "POST",
      body: JSON.stringify({ phone, password, name }),
    });
  },

  async login(phone: string, password: string) {
    return request<{ user: User; message: string }>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ phone, password }),
    });
  },

  async logout() {
    return request<{ message: string }>("/auth/logout", { method: "POST" });
  },

  async me() {
    return request<User>("/auth/me");
  },

  async changePassword(old_password: string, new_password: string) {
    return request<{ message: string }>("/auth/change-password", {
      method: "POST",
      body: JSON.stringify({ old_password, new_password }),
    });
  },

  async listConversations() {
    return request<Conversation[]>("/conversations");
  },

  async createConversation(title = "新会话") {
    return request<Conversation>("/conversations", {
      method: "POST",
      body: JSON.stringify({ title }),
    });
  },

  async renameConversation(id: string, title: string) {
    return request<Conversation>(`/conversations/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    });
  },

  async deleteConversation(id: string) {
    return request<{ deleted: string }>(`/conversations/${id}`, { method: "DELETE" });
  },

  async listMessages(id: string, options: { limit?: number; before?: string } = {}) {
    const query = new URLSearchParams();
    query.set("limit", String(options.limit ?? 30));
    if (options.before) query.set("before", options.before);
    return request<MessagePage>(`/conversations/${id}/messages?${query.toString()}`);
  },

  async pending(id: string) {
    return request<PendingAction>(`/chat/${id}/pending`);
  },

  async approval(id: string, decision: "approve" | "reject") {
    return request<{
      decision: string;
      message_id: string;
      content: string;
      parts: any[];
    }>(`/chat/${id}/approval`, {
      method: "POST",
      body: JSON.stringify({ decision }),
    });
  },

  async feedback(messageId: string, rating: 1 | -1, comment = "") {
    return request<{ message: string; rating: number }>(`/messages/${messageId}/feedback`, {
      method: "POST",
      body: JSON.stringify({ rating, comment }),
    });
  },

  admin: {
    async documents(params: { category?: string; doc_type?: string; page?: number; page_size?: number }) {
      const query = new URLSearchParams();
      if (params.category) query.set("category", params.category);
      if (params.doc_type) query.set("doc_type", params.doc_type);
      query.set("page", String(params.page ?? 1));
      query.set("page_size", String(params.page_size ?? 20));
      return request<DocumentList>(`/admin/documents?${query.toString()}`);
    },

    async createDocument(payload: { title: string; category: string; doc_type: string; content: string }) {
      return request<DocumentItem>("/admin/documents", {
        method: "POST",
        body: JSON.stringify(payload),
      });
    },

    async updateDocument(
      id: string,
      payload: Partial<{ title: string; category: string; doc_type: string; content: string }>,
    ) {
      return request<DocumentItem>(`/admin/documents/${id}`, {
        method: "PUT",
        body: JSON.stringify(payload),
      });
    },

    async deleteDocument(id: string) {
      return request<{ deleted: string }>(`/admin/documents/${id}`, { method: "DELETE" });
    },

    async search(query: string, top_k = 5) {
      return request<{ query: string; results: any[] }>("/admin/search", {
        method: "POST",
        body: JSON.stringify({ query, top_k }),
      });
    },

    async reindex() {
      return request<{ message: string; documents: number; chunks: number }>("/admin/reindex", {
        method: "POST",
      });
    },

    async stats() {
      return request<KnowledgeStats>("/admin/stats");
    },

    async audit(limit = 30) {
      return request<any[]>(`/admin/audit?limit=${limit}`);
    },
  },
};
