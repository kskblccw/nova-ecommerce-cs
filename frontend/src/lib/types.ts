export interface MessagePart {
  kind: string;
  summary: string;
  data: any;
}

export interface Message {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  parts: MessagePart[];
  created_at: string;
}

export interface MessagePage {
  items: Message[];
  has_more: boolean;
  next_cursor: string | null;
}

export interface Conversation {
  id: string;
  title: string;
  created_at: string;
  last_message_at: string | null;
  message_count: number;
  preview: string;
}

export interface User {
  id: number;
  name: string;
  phone_masked: string;
  role: "user" | "admin";
  created_at: string;
}

export interface PendingAction {
  has_pending: boolean;
  action_id: string;
  type: string;
  summary: string;
  payload: Record<string, any>;
}

export interface DocumentItem {
  id: string;
  title: string;
  category: string;
  doc_type: string;
  content: string;
  source: string;
  meta: Record<string, any>;
  updated_at: string;
}

export interface DocumentList {
  total: number;
  page: number;
  page_size: number;
  items: DocumentItem[];
}

export interface KnowledgeStats {
  total: number;
  doc_types: Record<string, number>;
  categories: Record<string, number>;
  retrieval: Record<string, number>;
}

export type SseEvent =
  | { type: "meta"; conversation_id: string; title: string; run_id: string }
  | { type: "status"; stage: string; label: string }
  | { type: "token"; text: string }
  | { type: "reset"; reason?: string }
  | { type: "approval"; action: any; action_id: string; summary: string; title: string }
  | {
      type: "done";
      message_id: string;
      content: string;
      parts: MessagePart[];
      degraded?: boolean;
    }
  | {
      type: "error";
      code: string;
      message: string;
      degraded?: boolean;
      retryable?: boolean;
    };
