"""
RAG query API endpoints
"""
from fastapi import APIRouter, HTTPException, Depends
from schemas import RAGQueryRequest, RAGQueryResponse
from services import normalize_metadata_company_name
from api.auth import get_current_user
from chat_pipeline.rag.pipeline import RAGPipeline
from chat_pipeline.rag.generator import get_last_backend_used
from sse_starlette import EventSourceResponse
import logging
import time
import json

# Configure structured logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/rag", tags=["RAG"])

# Create RAG pipeline instance
pipeline = RAGPipeline()

@router.post("/query", response_model=RAGQueryResponse)
async def rag_query(
    request: RAGQueryRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    RAG query endpoint - requires authentication
    Automatically filters results by user's company
    """
    start_time = time.time()
    try:
        company_name = current_user.get("company")

        if current_user["role"] != "super_admin" and not company_name:
            raise HTTPException(
                status_code=403,
                detail="No company associated with this user"
            )

        logger.info(
            "RAG Query initiated",
            extra={
                "user_email": current_user['email'],
                "company": company_name,
                "query": request.query,
                "top_k": request.top_k,
                "metric_type": "rag_query_start"
            }
        )

        # Run RAG pipeline with timing
        rag_company_filter = company_name if current_user["role"] != "super_admin" else None

        pipeline_start = time.time()
        streaming_overrides = (
            {"max_tokens": request.max_tokens} if request.max_tokens else None
        )
        result = pipeline.run(
            query=request.query,
            top_k=request.top_k,
            company_name=rag_company_filter,
            streaming_overrides=streaming_overrides,
            generation_backend=request.generation_backend,
        )
        pipeline_duration = time.time() - pipeline_start

        answer = result.answer
        metadata = result.metadata
        timings = result.timings or {}

        # Format sources
        sources = [
            {
                "company": m.get("company", "unknown"),
                "filename": m.get("filename", "unknown"),
                "chunk_id": m.get("chunk_id", "?"),
                "text": m.get("text", ""),
                "doc_title": m.get("doc_title", ""),
                "section_title": m.get("section_title", ""),
            }
            for m in metadata
        ]

        total_duration = time.time() - start_time

        # Calculate detailed breakdown
        retrieval_time = timings.get("retrieval", 0.0)
        generation_time = timings.get("generation", 0.0)
        cache_hit = timings.get("cache_hit", 0.0) == 1.0
        overhead_time = total_duration - pipeline_duration

        logger.info(
            "RAG Query completed",
            extra={
                "user_email": current_user['email'],
                "company": company_name,
                "query": request.query,
                "total_duration_seconds": total_duration,
                "pipeline_duration_seconds": pipeline_duration,
                "retrieval_duration_seconds": retrieval_time,
                "generation_duration_seconds": generation_time,
                "overhead_duration_seconds": overhead_time,
                "cache_hit": cache_hit,
                "sources_count": len(sources),
                "top_k": request.top_k,
                "metric_type": "rag_query_complete"
            }
        )

        return RAGQueryResponse(
            answer=answer,
            sources=sources,
            query=request.query,
            company=company_name or "All Companies",
            duration_seconds=total_duration,
            retrieval_duration_seconds=retrieval_time,
            generation_duration_seconds=generation_time,
            generation_backend=result.generation_backend,
            cache_hit=cache_hit
        )

    except Exception as e:
        duration = time.time() - start_time
        logger.error(
            "RAG Query failed",
            extra={
                "user_email": current_user.get('email'),
                "company": current_user.get("company"),
                "query": request.query,
                "duration_seconds": duration,
                "error": str(e),
                "metric_type": "rag_query_error"
            },
            exc_info=True
        )
        raise HTTPException(status_code=500, detail=str(e))
    
@router.post("/query/stream")
async def rag_query_stream(
    request: RAGQueryRequest,
    current_user: dict = Depends(get_current_user),
):
    company_name = current_user.get("company")

    if current_user["role"] != "super_admin" and not company_name:
        raise HTTPException(
            status_code=403,
            detail="No company associated with this user",
        )
    
    company_filters = (
        company_name if current_user["role"] != "super_admin" else None
    )

    logger.info(
        "RAG stream initiated",
        extra={
            "user_email": current_user["email"],
            "company": company_name,
            "query": request.query,
            "top_k": request.top_k,
            "metric_type": "rag_stream_start",
        },
    )

    def event_stream():
        # Note: streamed runs bypass the pipeline response cache
        # (use_cache = not stream), so an interrupted stream can never
        # poison the cache with a partial answer (Phase 2C).
        try:
            result = pipeline.run(
                query=request.query,
                top_k=request.top_k,
                company_name=company_filters,
                stream=True,
                streaming_overrides=(
                    {"max_tokens": request.max_tokens} if request.max_tokens else None
                ),
                generation_backend=request.generation_backend,
            )
        except Exception as exc:
            logger.error(
                "RAG stream failed before generation",
                extra={"query": request.query, "metric_type": "rag_stream_error"},
                exc_info=True,
            )
            yield {
                "event": "error",
                "data": json.dumps({"detail": str(exc), "partial": False}),
            }
            return

        sources = [
            {
                "company": item.get("company", "unknown"),
                "filename": item.get("filename", "unknown"),
                "chunk_id": item.get("chunk_id", "?"),
                "text": item.get("text", ""),
                "doc_title": item.get("doc_title", ""),
                "section_title": item.get("section_title", ""),
            }
            for item in result.metadata
        ]

        yield {
            "event": "sources",
            "data": json.dumps({"sources": sources}),
        }

        generation_start = time.perf_counter()
        emitted_tokens = 0

        try:
            if result.is_stream():
                for token in result.answer:
                    if token:
                        emitted_tokens += 1
                        yield {
                            "event": "token",
                            "data": json.dumps({"token": token}),
                        }
            else:
                emitted_tokens += 1
                yield {
                    "event": "token",
                    "data": json.dumps({"token": result.answer}),
                }
        except Exception as exc:
            logger.error(
                "RAG stream interrupted mid-generation",
                extra={
                    "query": request.query,
                    "emitted_tokens": emitted_tokens,
                    "metric_type": "rag_stream_error",
                },
                exc_info=True,
            )
            yield {
                "event": "error",
                "data": json.dumps({"detail": str(exc), "partial": emitted_tokens > 0}),
            }
            return

        yield {
            "event": "done",
            "data": json.dumps({
                "query": request.query,
                "company": company_name or "All Companies",
                # For streamed runs the backend is only known after the
                # generator has been consumed, so read it here, not from result.
                "generation_backend": get_last_backend_used(),
                "retrieval_duration_seconds": result.timings.get("retrieval", 0.0),
                "generation_duration_seconds": (
                    time.perf_counter() - generation_start
                ),
            }),
        }

    return EventSourceResponse(
        event_stream(),
        ping=15,
        send_timeout=10,
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )