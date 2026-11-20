import type { ChatResponse, KnowledgeArticle } from "./types";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...options?.headers },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || "The request could not be completed.");
  }
  return response.json();
}

export const searchKnowledge = (query: string) =>
  request<{ articles: KnowledgeArticle[] }>(`/api/knowledge/search?q=${encodeURIComponent(query)}`);
export const sendChat = (body: { session_id?: string; user_id: string; message: string }) =>
  request<ChatResponse>("/api/chat", { method: "POST", body: JSON.stringify(body) });
export const createTicket = (body: { user_id: string; subject: string; description: string }) =>
  request<unknown>("/api/tickets", { method: "POST", body: JSON.stringify(body) });
