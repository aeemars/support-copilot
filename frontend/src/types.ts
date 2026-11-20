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

export interface Product {
  product_id: string;
  name: string;
  description: string;
  category: string;
  price_minor: number;
  currency: string;
  stock_quantity: number;
}

export interface Cart {
  items: { product_id: string; quantity: number; unit_price_minor: number }[];
}
