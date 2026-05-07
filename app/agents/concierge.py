"""
Interactive Concierge Agent (RAG-powered)
Answers user questions using silo posts as the knowledge base.
Uses pgvector cosine similarity to find relevant context.
"""
from supabase import Client
from app.utils.ai_agent import generate_embedding, stream_text
from typing import AsyncGenerator

_CONCIERGE_SYSTEM = """You are the FamSilo AI Concierge — a warm, knowledgeable assistant
embedded in a private family social network. You help family members navigate their shared
memories, understand proposals, find information from past posts, and engage with their community.

Rules:
1. ONLY answer based on the context provided from the family's actual posts.
2. If the answer is not in the context, say so honestly and suggest the user ask their family.
3. Be warm, friendly, and concise.
4. Never reveal personal details beyond what the user already has access to.
5. Keep answers under 150 words unless the question truly requires more detail."""


async def stream_concierge_answer(
    query: str,
    user_id: str,
    silo_id: str | None,
    db: Client,
) -> AsyncGenerator[str, None]:
    """
    Main entry point: embed the query, find similar posts, stream a Gemini answer.
    If silo_id is None, searches across all user's silos.
    """
    # ── 1. Build context from RAG ─────────────────────────────────────────────
    context_posts = await _retrieve_context(query, user_id, silo_id, db)

    if context_posts:
        context_block = "\n".join(
            f"[{p['silo_name']}] {p['author']}: {p['snippet']}"
            for p in context_posts
        )
        prompt = (
            f"Context from the family's posts:\n{context_block}\n\n"
            f"User question: {query}\n\n"
            f"Answer based only on the context above."
        )
    else:
        prompt = (
            f"No relevant posts found in the family's history for this question.\n\n"
            f"User question: {query}\n\n"
            f"Let the user know there's no relevant context and suggest alternatives."
        )

    # ── 2. Stream Gemini response ─────────────────────────────────────────────
    async for chunk in stream_text(prompt, system_instruction=_CONCIERGE_SYSTEM):
        yield chunk


async def _retrieve_context(
    query: str, user_id: str, silo_id: str | None, db: Client
) -> list[dict]:
    """
    Embed the query and find the top-5 similar posts via pgvector.
    """
    embedding = generate_embedding(query)
    if not embedding:
        return []

    try:
        if silo_id:
            # Search within a specific silo
            res = db.rpc("match_silo_posts", {
                "query_embedding": embedding,
                "match_silo_id": silo_id,
                "match_count": 5,
            }).execute()
        else:
            # Search across all user's silos — use a broader query
            memberships = (
                db.table("group_members")
                .select("group_id")
                .eq("user_id", user_id)
                .execute()
            )
            silo_ids = [m["group_id"] for m in (memberships.data or [])]
            if not silo_ids:
                return []
            # Use first silo for now; multi-silo search can be added later
            res = db.rpc("match_silo_posts", {
                "query_embedding": embedding,
                "match_silo_id": silo_ids[0],
                "match_count": 5,
            }).execute()

        # Enrich with author info
        post_ids = [r["post_id"] for r in (res.data or [])]
        if not post_ids:
            return []

        posts_res = (
            db.table("posts")
            .select("id, caption, group_id, profiles(username), groups(name)")
            .in_("id", post_ids)
            .execute()
        )

        result = []
        for p in (posts_res.data or []):
            result.append({
                "silo_name": (p.get("groups") or {}).get("name", "Family"),
                "author": (p.get("profiles") or {}).get("username", "Member"),
                "snippet": (p.get("caption") or "")[:200],
            })
        return result

    except Exception as e:
        print(f"[Concierge] _retrieve_context error: {e}")
        return []


def index_silo_posts(silo_id: str, db: Client) -> dict:
    """
    Index all approved text/proposal posts in a silo into pgvector.
    Called once on silo creation and can be re-triggered by admin.
    """
    from app.utils.ai_agent import generate_embedding

    try:
        # Fetch posts that have text content
        posts_res = (
            db.table("posts")
            .select("id, caption, group_id")
            .eq("group_id", silo_id)
            .eq("moderation_status", "approved")
            .not_.is_("caption", "null")
            .not_.like("caption", "__%%__")  # Skip marker captions
            .execute()
        )

        indexed = 0
        for post in (posts_res.data or []):
            caption = post.get("caption", "").strip()
            if not caption or len(caption) < 10:
                continue

            embedding = generate_embedding(caption)
            if not embedding:
                continue

            db.table("post_embeddings").upsert({
                "post_id": post["id"],
                "silo_id": silo_id,
                "embedding": embedding,
                "content_snippet": caption[:500],
            }, on_conflict="post_id").execute()
            indexed += 1

        return {"indexed": indexed, "silo_id": silo_id}

    except Exception as e:
        print(f"[Concierge] index_silo_posts error: {e}")
        return {"indexed": 0, "error": str(e)}
