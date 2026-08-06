"""
RAG query schemas
"""
from pydantic import BaseModel
from typing import List, Dict, Optional

class RAGQueryRequest(BaseModel):
    query: str
    top_k: int = 5
    max_tokens: Optional[int] = None
    generation_backend: Optional[str] = None
    # Phase 5A: prompt template selector. The voice agent sends
    # "voice_prompt" for short, markdown-free spoken answers. Unknown keys
    # fall back to the default template inside the generator.
    template_key: Optional[str] = None


class RAGPrefetchRequest(BaseModel):
    """Retrieval-only warm-up request (Phase 5B)."""
    query: str
    top_k: int = 5


class RAGPrefetchResponse(BaseModel):
    """Result of a retrieval-only warm-up.

    ``cached`` reports whether the retrieval result is now sitting in the
    pipeline's retrieval cache, so a follow-up /api/rag/query for the same
    (normalized) query can skip retrieval entirely.
    """
    query: str
    company: str
    cached: bool
    documents: int = 0
    retrieval_duration_seconds: float = 0.0
    cache_hit: bool = False

class RAGQueryResponse(BaseModel):
    answer: str
    sources: List[Dict]
    query: str
    company: str
    duration_seconds: Optional[float] = None
    retrieval_duration_seconds: Optional[float] = None
    generation_duration_seconds: Optional[float] = None
    generation_backend: Optional[str] = None
    cache_hit: Optional[bool] = None