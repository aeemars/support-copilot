export interface Citation {
  slug: string;
  title: string;
  excerpt: string;
}

export interface ChatResponse {
  session_id: string;
  answer: string;
  citations: Citation[];
  tool_used?: "get_order_status";
  usage: Record<string, unknown>;
}

export interface KnowledgeArticle {
  slug: string;
  title: string;
  excerpt: string;
}
