"""Retriever agent — RAG search + Claude synthesis with citations."""

import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from src.agents.base import agent_call
from src.config import settings
from src.lib.embeddings import embed_text
from src.lib.store import vector_search, get_artifact, get_note

def build_system_prompt() -> str:
    prompt = """You are the Retriever agent for duSraBheja, Ahmad's personal second brain.

Answer the question using ONLY the provided context. If the context doesn't contain
enough information, say so honestly.

Rules:
- Cite sources using [1], [2], etc.
- Be concise and direct
- If you're synthesizing from multiple sources, make that clear
- Include dates when they're relevant to the answer
- Don't make up information not in the context"""

    voice = (settings.brain_voice_instructions or "").strip()
    if voice:
        prompt += f"\n- Match Ahmad's voice and tone using these instructions: {voice}"
    return prompt


async def answer_question(
    session: AsyncSession,
    question: str,
    category: str | None = None,
    use_opus: bool = False,
    trace_id: uuid.UUID | None = None,
) -> dict:
    """Full RAG pipeline: embed → search → rerank → synthesize.

    Returns:
        {
            "answer": str,
            "sources": [{"id", "title", "category", "similarity"}],
            "confidence": "high" | "medium" | "low",
            "model": str,
            "cost_usd": Decimal,
        }
    """
    trace_id = trace_id or uuid.uuid4()

    # Step 1: Embed query
    query_embedding = await embed_text(question)

    # Step 2: Vector search
    raw_results = await vector_search(
        session, query_embedding, limit=20, min_similarity=0.3, category=category
    )

    if not raw_results:
        return {
            "answer": "I don't have any relevant information about that in my brain yet.",
            "sources": [],
            "confidence": "low",
            "model": "none",
            "cost_usd": 0,
        }

    # Step 3: Rerank (similarity * 0.7 + recency * 0.3)
    now = datetime.now(timezone.utc)
    for r in raw_results:
        days_old = 0
        if r.get("created_at"):
            days_old = (now - r["created_at"]).days if hasattr(r.get("created_at"), "days") else 0
        recency = max(0, 1 - days_old / 365)
        r["rerank_score"] = 0.7 * r["similarity"] + 0.3 * recency

    raw_results.sort(key=lambda x: x["rerank_score"], reverse=True)
    top_results = raw_results[:8]

    # Step 4: Expand context — fetch parent metadata
    sources = []
    context_parts = []
    for i, chunk in enumerate(top_results, 1):
        title = "Unknown"
        cat = "unknown"

        if chunk.get("note_id"):
            note = await get_note(session, chunk["note_id"])
            if note:
                title = note.title
                cat = note.category
        elif chunk.get("artifact_id"):
            artifact = await get_artifact(session, chunk["artifact_id"])
            if artifact:
                title = artifact.summary or artifact.content_type
                cat = "artifact"

        sources.append({
            "id": str(chunk.get("note_id") or chunk.get("artifact_id")),
            "title": title,
            "category": cat,
            "similarity": round(chunk["similarity"], 3),
        })

        context_parts.append(f"[{i}] ({cat}: {title}) {chunk['content']}")

    context_text = "\n\n".join(context_parts)

    # Step 5: Synthesize with Claude
    prompt = f"""Question: {question}

Context:
{context_text}

Answer the question. Cite your sources using [1], [2], etc."""

    model = settings.opus_model if use_opus else settings.sonnet_model
    result = await agent_call(
        session,
        agent_name="retriever",
        action="synthesize",
        prompt=prompt,
        system=build_system_prompt(),
        model=model,
        max_tokens=2048,
        temperature=0.1,
        trace_id=trace_id,
    )

    # Determine confidence
    avg_similarity = sum(r["similarity"] for r in top_results) / len(top_results)
    if avg_similarity > 0.6 and len(top_results) >= 3:
        confidence = "high"
    elif avg_similarity > 0.4:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "answer": result["text"],
        "sources": sources,
        "confidence": confidence,
        "model": result["model"],
        "cost_usd": result["cost_usd"],
    }
