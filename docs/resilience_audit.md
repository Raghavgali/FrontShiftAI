# External Call-Site Audit — Phase 6.5C

This table enumerates every outbound HTTP/subprocess call in the Python code
and its assigned resilience policy. Migration is rolling — not every site
needs the `@resilient` decorator today, but each one must have a documented
rationale.

| # | Call site | File:line | Policy | Migration status | Notes |
|---|---|---|---|---|---|
| 1 | Brave Search | `backend/agents/website_extraction/tools.py:42` | `external_search` | ✅ migrated | Reference implementation. Wrapped via `_brave_get`; breaker key `brave`. |
| 2 | Mercury chat completions | `chat_pipeline/rag/generator.py` `_call_mercury_api` | `external_llm` | ✅ migrated (6B) | `circuit_guard("mercury")`, not `@resilient`: the existing 429 `Retry-After` loop stays and must not be nested in a second one. Socket timeout stays `REMOTE_TIMEOUT` (45s), exempted in the policy doc. |
| 3 | Groq chat completions | `chat_pipeline/rag/generator.py` `_call_groq_api` | `external_llm` | ✅ migrated (6B) | `circuit_guard("groq")`. Same rationale. |
| 4 | OpenAI chat completions | `chat_pipeline/rag/generator.py` `_call_openai_api` | `external_llm` | ✅ migrated (6B) | `circuit_guard("openai")`. Same rationale. |
| 4b | Streaming completions (all three) | `chat_pipeline/rag/generator.py` `_stream_openai_compatible_api` | `external_llm` | ✅ migrated (6B) | One guard keyed on the `provider` argument. A consumer abandoning the stream (`GeneratorExit`) records neither success nor failure. |
| 5 | Local Ollama (llama.cpp HTTP) | `backend/agents/utils/llm_client.py` `_call_local` | `external_llm` | ✅ migrated (6B) | `@resilient(breaker_key="local")`. Timeout exempted at `OLLAMA_TIMEOUT_S` (60s): dev-only, CPU generation. |
| 6 | Mercury / Groq / OpenAI via llm_client.py | `backend/agents/utils/llm_client.py` `_call_*` | `external_llm` | ✅ migrated (6B) | `@resilient` per provider with the policy's 8s timeout passed into `requests` and into the Groq/OpenAI SDK constructors (`max_retries=0`, so the SDK cannot multiply the policy's budget). Replaced a blanket tenacity `@retry(3, wait 4-10s)` that had no breaker. |
| 7 | LLM judge API | `chat_pipeline/evaluation/judge_client.py:160` | `external_llm` | 🟡 optional | Evaluation tooling, not a hot path; wrap if the evaluator starts running in CI. |
| 8 | GCS sync (ChromaDB tar) | `chat_pipeline/rag/data_loader.py:162` | `gcs_sync` | 🟡 Phase 4C | Phase 4 already schedules this migration; noted here for completeness. |
| 9 | Voice → backend (`BackendClient.post_with_retry`) | `voice_pipeline/scripts/main.py` | `voice_tool` | ⚠ parallel impl | Already has idempotency + linear backoff matching the policy. Decorator port would be redundant; the plan explicitly notes "voice_tool: own infra, no breaker". Keep as-is. |
| 10 | Internal SQLAlchemy queries | backend/* | `internal_db` | 🟡 partial (6C) | Policy is fast-fail. Phase 6C landed the pool: QueuePool(5, 10) + `pool_pre_ping` + `pool_recycle=1800` for PostgreSQL, `pool_timeout=10` so a saturated pool fails instead of queueing. A server-side `statement_timeout` is still unset, so the 2s query budget is not yet enforced. |
| 11 | User-facing HTTP (axios) | `frontend/src/services/api.js` | `user_facing_http` | ✅ compliant | 10s timeout matches policy; no retries; refresh-on-401 is orthogonal. |
| 12 | LiveKit STT/TTS chains | `voice_pipeline/utils/config.py`, `scripts/main.py` | `livekit_chain` | ⚠ n/a | LiveKit's `FallbackAdapter` handles this inside the library; we just pick the provider list. |

## Net result

- **All 7 external LLM/search call sites are now breaker-protected** (Brave in
  6.5C, the six LLM sites in 6B). Nothing that talks to a third-party LLM is
  left with an unbounded, breaker-less retry loop.
- **Two attachment styles, one breaker registry.** Sites without their own
  retry logic use `@resilient`; the RAG generator uses `circuit_guard` so its
  429 handling is not wrapped in a second retry loop. Both share the per-
  provider keys (`mercury`, `groq`, `openai`, `local`), so either caller
  discovering an outage protects the other.
- **Voice and GCS** are out of scope per the policy matrix; **internal DB** is
  partially covered by the Phase 6C pool.
- **Frontend** already matches the `user_facing_http` policy.
- **Remaining gap**: no server-side `statement_timeout`, so the `internal_db`
  2s budget is documented but unenforced.

## Reviewer checklist for new PRs

Before merging a PR that adds a new outbound call:

1. Pick a policy from `docs/resilience_policy.md` (or add one).
2. Wrap the innermost network call with `@resilient(policy="...", breaker_key="<target>")`.
3. Bubble `CircuitOpenError` to a sensible user-facing fallback.
4. If the call mutates external state, add an `Idempotency-Key` (see Phase 0.5).
