# Resilience Policy Matrix

Single source of truth for *which* retry/timeout/circuit-breaker pattern
applies to *which* kind of outbound call. New integrations pick a policy
name and stop there — they don't reinvent the wheel, and we avoid the
"scattered ad-hoc handling" failure mode that Phase 6 exists to fix.

Implementation: `backend/utils/resilience.py` exposes a `@resilient(policy=…)`
decorator plus a `get_policy(name)` lookup. Policies are named constants —
changing a timeout once updates everything that references that policy.

## The matrix

| Policy key          | Call type                              | Timeout | Retries | Backoff                 | Circuit breaker | Idempotency key |
| ------------------- | -------------------------------------- | ------- | ------- | ----------------------- | --------------- | --------------- |
| `external_llm`      | Mercury, Groq, OpenAI chat completions | 8s      | 3       | exponential (1→2→4s)    | per provider    | n/a (read-only) |
| `external_search`   | Brave Search, Deepgram STT/TTS (HTTP)  | 5s      | 2       | exponential             | per provider    | n/a             |
| `internal_db`       | SQLAlchemy queries from FastAPI        | 2s      | 1       | fast-fail               | ✗               | n/a             |
| `voice_tool`        | Voice worker → backend HTTP            | 8s      | 2       | linear (1s × attempt)   | ✗               | **required**    |
| `user_facing_http`  | Browser → backend (client-side fetch)  | 10s     | 0       | — (user retries)        | —               | for mutations   |
| `gcs_sync`          | ChromaDB tar download/sync             | 300s    | 3       | exponential             | ✗               | — (re-syncable) |
| `livekit_chain`     | STT/TTS provider fallback chains       | 5s      | 0       | handled by LiveKit SDK  | ✓               | —               |

## Why each choice?

- **External LLM APIs need circuit breakers.** Repeated retries against a
  downed provider waste seconds of every request before the fallback chain
  trips over to the next provider. A breaker short-circuits the call after
  3 consecutive failures and skips the provider for 60s.
- **External LLMs don't need idempotency keys.** Inference has no persistent
  side effect, so retrying is safe.
- **Internal DB queries fast-fail.** Retrying a DB that's already under load
  makes it worse. The right move is to bubble up and let the client retry.
- **Voice tool calls require idempotency keys** because they *mutate* state
  (POST `/api/pto/chat` creates rows) *and* we retry them. The combination
  is only safe with a key reused across retries — see Phase 0.5.
- **User-facing chains don't auto-retry.** A user clicking again is a
  stronger signal than a silent client retry — they know whether they
  actually want the action.
- **GCS sync re-runs are idempotent by construction** (`rsync`). No key needed.
- **LiveKit chains use LiveKit's `FallbackAdapter`** — we don't layer our own
  retries on top; we just configure the per-stage circuit breaker.

## Circuit-breaker mechanics

Three states: **closed** (pass-through) → **open** (fail-fast) → **half-open**
(allow one probe). Defaults:

- 3 consecutive failures → open
- 60s cooldown → half-open
- Success in half-open → closed
- Failure in half-open → re-open, reset the 60s cooldown

Breakers are **keyed per-call-target** (e.g. `mercury`, `groq`, `openai`,
`brave`). Mercury being down does not trip Groq. The key is shared across
callers on purpose: "Mercury is down" is one fact, whoever discovered it, so
the agent LLM client and the RAG generator feed the same breaker.

State is exposed as the Prometheus gauge `circuit_breaker_state{key="..."}`
(0 = closed, 1 = half-open, 2 = open), published on every transition from
`_publish_state`. Every decorated target registers its key at import time, so
a provider that has never been called still shows a `closed` series rather
than a gap in the dashboard.

### Two ways to attach a breaker

| Helper | Failure accounting | Use when |
|---|---|---|
| `@resilient(policy=…, breaker_key=…)` | one failure **per attempt**, so a single call can open the breaker | the call site has no retry logic of its own (the default) |
| `circuit_guard(key, policy=…)` | one failure **per guarded block** | the call site already owns provider-specific retry semantics (the RAG generator's 429 `Retry-After` handling) and must not be wrapped in a second retry loop |

Two further rules that matter for latency:

- **An open breaker abandons the remaining retry budget.** `@resilient`
  re-checks the breaker after each failure; once it opens mid-call, the
  remaining retries are skipped. Otherwise a 4-attempt policy would keep
  dialing a target it had already declared down.
- **A skipped call raises `CircuitOpenError`, not the provider's error.**
  Fallback chains must treat it like any other failure and advance to the
  next provider. That is the entire point: the skip costs microseconds
  instead of the provider's full retry budget.

### Timeout exemptions

The policy's `timeout_s` is the contract, and the sync `@resilient` path
cannot enforce it (no safe way to interrupt a blocking socket off the main
thread), so call sites pass it to their HTTP client explicitly. Two
deliberate exceptions, both recorded in `docs/resilience_audit.md`:

- **Local Ollama** (`OLLAMA_TIMEOUT_S`, default 60s): dev-only provider,
  last in the fallback chain, generating on CPU where 8s is below the floor
  for a useful completion.
- **RAG generator full completions** (`REMOTE_TIMEOUT`, default 45s): a
  1024-token non-streaming completion legitimately exceeds 8s. The breaker,
  not the timeout, is what keeps a dead provider cheap here.

## Correlation

Every failure logged by these helpers carries the request's `request_id`
(see `backend/observability/tracing.py`), so a breaker opening in the
dashboard can be traced to the exact requests that opened it.

## Enforcement

A CI grep test ([`.github/workflows/pre_commit_tenant_check.yml`] pattern)
should forbid direct `httpx.post`/`requests.post`/etc. outside
`backend/utils/resilience.py` and a short allow-list of legacy callers that
have their own orchestration (LLM generator fallback chain, voice
BackendClient, Modal session endpoints).

Use `@resilient(policy="external_llm")` at the call site.
