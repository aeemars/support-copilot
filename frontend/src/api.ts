import type { Cart, ChatResponse, KnowledgeArticle, Product } from "./types";

const API_BASE = ((import.meta as unknown as { env?: { VITE_API_URL?: string } }).env?.VITE_API_URL || "").replace(/\/$/, "");

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
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
export const getProducts = () => request<Product[]>("/api/products");
export const getCart = (userId: string) => request<Cart>(`/api/cart/${encodeURIComponent(userId)}`);
export const addToCart = (userId: string, product_id: string, quantity = 1) =>
  request<Cart>(`/api/cart/${encodeURIComponent(userId)}/items`, { method: "POST", body: JSON.stringify({ product_id, quantity }) });
export const removeFromCart = (userId: string, productId: string) =>
  request<Cart>(`/api/cart/${encodeURIComponent(userId)}/items/${encodeURIComponent(productId)}`, { method: "DELETE" });
export const checkout = (body: { user_id: string; customer_name: string; email: string; phone?: string; line1: string; city: string; state: string; payment_method: string }) =>
  request<{ order_id: string; total_minor: number }>("/api/checkout", { method: "POST", body: JSON.stringify(body) });
export const getOrders = (userId: string) => request<unknown[]>(`/api/orders/${encodeURIComponent(userId)}`);
export const getReturns = (userId: string) => request<unknown[]>(`/api/returns/${encodeURIComponent(userId)}`);
export const getRefunds = (userId: string) => request<unknown[]>(`/api/refunds/${encodeURIComponent(userId)}`);
export const sendChat = (body: { session_id?: string; user_id: string; message: string }) =>
  request<ChatResponse>("/api/chat", { method: "POST", body: JSON.stringify(body) });
export const createTicket = (body: { user_id: string; subject: string; description: string }) =>
  request<unknown>("/api/tickets", { method: "POST", body: JSON.stringify(body) });
