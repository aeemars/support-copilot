export interface Customer {
  customer_id: string;
  name: string;
  email: string;
}

export interface Order {
  order_id: string;
  status: string;
  items: { name: string; quantity: number }[];
  tracking_number?: string | null;
  courier?: string;
  estimated_delivery?: string;
}

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
