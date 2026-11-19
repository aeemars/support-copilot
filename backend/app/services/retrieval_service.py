from typing import Protocol

from app.db import get_db
from app.models import Citation


class SearchProvider(Protocol):
    async def search(self, query: str, limit: int = 4) -> list[Citation]: ...


class MongoTextRetrieval:
    async def search(self, query: str, limit: int = 4) -> list[Citation]:
        db = get_db()
        cursor = db.knowledge_articles.find(
            {"$text": {"$search": query}},
            {"slug": 1, "title": 1, "body": 1, "score": {"$meta": "textScore"}},
        ).sort([("score", {"$meta": "textScore"})]).limit(min(limit, 4))
        articles = await cursor.to_list(length=limit)
        return [
            Citation(
                slug=article["slug"],
                title=article["title"],
                excerpt=(article.get("body", "")[:240].rstrip() + ("..." if len(article.get("body", "")) > 240 else "")),
            )
            for article in articles
        ]

    async def articles_for_context(self, query: str, limit: int = 4) -> tuple[list[Citation], str]:
        citations = await self.search(query, limit)
        db = get_db()
        slugs = [citation.slug for citation in citations]
        articles = await db.knowledge_articles.find({"slug": {"$in": slugs}}).to_list(length=4)
        by_slug = {article["slug"]: article for article in articles}
        context_parts = []
        for citation in citations:
            article = by_slug.get(citation.slug)
            if article:
                context_parts.append(f"<article slug=\"{citation.slug}\" title=\"{citation.title}\">\n{article['body']}\n</article>")
        return citations, "\n\n".join(context_parts)

