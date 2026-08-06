"""
LLM Client for Agents
Handles Groq, Local Model, and Mercury with automatic fallback, circuit breaking, and caching.

Phase 6B: retry/timeout/breaker semantics are no longer hand-rolled here.
Each provider call carries ``@resilient(policy="external_llm")`` with a
per-provider breaker key, so a provider that is down is skipped for the
recovery window instead of costing every request its full retry budget.
The fallback chain in :meth:`AgentLLMClient.chat` treats a skipped provider
exactly like a failed one, so ordering is unchanged.
"""

import os
import logging
import threading
import time
from typing import Optional, Dict, Any
import requests
from groq import Groq
from openai import OpenAI

from cachetools import TTLCache

from .llm_config import (
    USE_LLM,
    ENABLE_FALLBACK,
    FALLBACK_CHAIN,
    GROQ_CONFIG,
    LOCAL_CONFIG,
    MERCURY_CONFIG,
    OPENAI_CONFIG,
)

try:  # backend/ on sys.path (how the app runs)
    from utils.resilience import CircuitOpenError, get_policy, resilient
except ImportError:  # repo root on sys.path (how the stress tests import us)
    from backend.utils.resilience import CircuitOpenError, get_policy, resilient

logger = logging.getLogger(__name__)

# The policy is the contract for the wall-clock budget of one attempt. The
# sync @resilient wrapper cannot interrupt a blocking socket, so every client
# below is constructed with this timeout explicitly.
_LLM_POLICY = get_policy("external_llm")

# Local Ollama is a deliberate exemption from the 8s policy timeout: it is a
# dev-only provider (last in FALLBACK_CHAIN) generating on CPU, where 8s is
# below the floor for a useful completion. Documented in
# docs/resilience_audit.md.
LOCAL_TIMEOUT_S = float(os.getenv("OLLAMA_TIMEOUT_S", "60"))


def _observe(provider: str, outcome: str, seconds: float, error_class: Optional[str] = None) -> None:
    """Best-effort Prometheus emission (Phase 7 instruments, same helper the
    RAG generator uses). Never let metrics break a call path."""
    try:
        from observability.metrics import observe_llm_call
    except Exception:  # noqa: BLE001
        return
    try:
        observe_llm_call(provider, outcome, seconds, error_class)
    except Exception:  # noqa: BLE001
        pass


def _error_class(exc: BaseException) -> str:
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status == 429:
        return "429"
    if isinstance(status, int) and 500 <= status < 600:
        return "5xx"
    name = type(exc).__name__.lower()
    if "timeout" in name:
        return "timeout"
    if "connect" in name:
        return "connection"
    return "other"

# Cache configuration (100 items, 5 minutes TTL)
llm_cache = TTLCache(maxsize=100, ttl=300)

class AgentLLMClient:
    """
    Unified LLM client for agents with automatic fallback
    Supports: Groq, Local (Ollama), Mercury, OpenAI
    """

    def __init__(self):
        self.primary_provider = USE_LLM
        self.enable_fallback = ENABLE_FALLBACK
        self.fallback_chain = FALLBACK_CHAIN

        # Initialize clients
        self.groq_client = None
        self.openai_client = None
        self._init_groq()
        self._init_openai()

    def _init_groq(self):
        """Initialize Groq client"""
        try:
            api_key = os.getenv("GROQ_API_KEY")
            if api_key:
                # max_retries=0: the SDK's own retry loop would multiply the
                # policy's retry budget and hide failures from the breaker.
                self.groq_client = Groq(
                    api_key=api_key,
                    timeout=_LLM_POLICY.timeout_s,
                    max_retries=0,
                )
                logger.info("Groq client initialized successfully")
            else:
                logger.warning("GROQ_API_KEY not found in environment")
        except Exception as e:
            logger.error(f"Failed to initialize Groq client: {e}")

    def _init_openai(self):
        """Initialize OpenAI client"""
        try:
            api_key = os.getenv("OPENAI_API_KEY")
            if api_key:
                self.openai_client = OpenAI(
                    api_key=api_key,
                    timeout=_LLM_POLICY.timeout_s,
                    max_retries=0,
                )
                logger.info("OpenAI client initialized successfully")
            else:
                logger.warning("OPENAI_API_KEY not found in environment")
        except Exception as e:
            logger.error(f"Failed to initialize OpenAI client: {e}")

    def chat(
        self,
        messages: list,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        json_mode: bool = False,
    ) -> Optional[str]:
        """
        Send chat completion request with automatic fallback
        """
        # Check cache
        cache_key = str((messages, temperature, max_tokens, json_mode))
        if cache_key in llm_cache:
            logger.info("LLM Cache hit")
            return llm_cache[cache_key]

        start_time = time.time()
        response = None

        # Try primary provider first
        try:
            response = self._try_provider_with_retry(
                self.primary_provider, messages, temperature, max_tokens, json_mode
            )
        except CircuitOpenError:
            # Not a failure of this request: the provider is already known
            # down, so we skip straight to the fallback chain.
            logger.warning(
                f"Primary provider {self.primary_provider} skipped (circuit open)"
            )
        except Exception as e:
            logger.warning(f"Primary provider {self.primary_provider} failed: {e}")

        # Try fallback chain if enabled and primary failed
        if not response and self.enable_fallback:
            for provider in self.fallback_chain:
                if provider != self.primary_provider:
                    logger.info(f"Falling back to {provider}")
                    try:
                        response = self._try_provider_with_retry(
                            provider, messages, temperature, max_tokens, json_mode
                        )
                        if response:
                            break
                    except CircuitOpenError:
                        logger.warning(
                            f"Fallback provider {provider} skipped (circuit open)"
                        )
                    except Exception as e:
                        logger.warning(f"Fallback provider {provider} failed: {e}")

        duration = time.time() - start_time
        
        if response:
            logger.info(f"LLM request completed in {duration:.2f}s using provider")
            llm_cache[cache_key] = response
            return response

        logger.error("All LLM providers failed")
        return None

    def _try_provider_with_retry(
        self,
        provider: str,
        messages: list,
        temperature: Optional[float],
        max_tokens: Optional[int],
        json_mode: bool,
    ) -> Optional[str]:
        """Dispatch to a provider under the external_llm policy.

        Retry, backoff, and the per-provider circuit breaker now live on the
        ``_call_*`` methods (Phase 6B). This used to carry a tenacity
        ``@retry`` that retried *every* exception three times with a 4s to
        10s wait and no breaker, so a downed provider cost ~14s of every
        request forever. Name kept because callers and tests reference it.
        """
        return self._try_provider(provider, messages, temperature, max_tokens, json_mode)

    def _try_provider(
        self,
        provider: str,
        messages: list,
        temperature: Optional[float],
        max_tokens: Optional[int],
        json_mode: bool,
    ) -> Optional[str]:
        """Try a specific provider, observing its latency and outcome.

        Timing spans the whole policy envelope (all attempts plus backoff),
        matching how the RAG generator reports ``llm_provider_latency_seconds``
        so the two sources are comparable on one dashboard panel.
        """
        if provider == "groq":
            call = self._call_groq
        elif provider == "local":
            call = self._call_local
        elif provider == "mercury":
            call = self._call_mercury
        elif provider == "openai":
            call = self._call_openai
        else:
            logger.error(f"Unknown provider: {provider}")
            raise ValueError(f"Unknown provider: {provider}")

        start = time.perf_counter()
        try:
            result = call(messages, temperature, max_tokens, json_mode)
        except CircuitOpenError:
            # A skipped call is not a latency sample and not a new failure:
            # the breaker gauge already tells that story.
            raise
        except Exception as exc:
            _observe(provider, "error", time.perf_counter() - start, _error_class(exc))
            raise
        _observe(provider, "success", time.perf_counter() - start)
        return result

    @resilient(policy="external_llm", breaker_key="groq")
    def _call_groq(
        self,
        messages: list,
        temperature: Optional[float],
        max_tokens: Optional[int],
        json_mode: bool,
    ) -> Optional[str]:
        """Call Groq API"""
        if not self.groq_client:
            raise RuntimeError("Groq client not initialized")

        kwargs = {
            "model": GROQ_CONFIG["model"],
            "messages": messages,
            "temperature": temperature or GROQ_CONFIG["temperature"],
            "max_tokens": max_tokens or GROQ_CONFIG["max_tokens"],
        }

        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        response = self.groq_client.chat.completions.create(**kwargs)
        return response.choices[0].message.content

    @resilient(policy="external_llm", breaker_key="local")
    def _call_local(
        self,
        messages: list,
        temperature: Optional[float],
        max_tokens: Optional[int],
        json_mode: bool,
    ) -> Optional[str]:
        """Call local Ollama model"""
        url = f"{LOCAL_CONFIG['url']}/api/chat"

        payload = {
            "model": LOCAL_CONFIG["model"],
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature or LOCAL_CONFIG["temperature"],
                "num_predict": max_tokens or LOCAL_CONFIG["max_tokens"],
            },
        }

        if json_mode:
            payload["format"] = "json"

        response = requests.post(url, json=payload, timeout=LOCAL_TIMEOUT_S)
        response.raise_for_status()

        data = response.json()
        return data.get("message", {}).get("content")

    @resilient(policy="external_llm", breaker_key="mercury")
    def _call_mercury(
        self,
        messages: list,
        temperature: Optional[float],
        max_tokens: Optional[int],
        json_mode: bool,
    ) -> Optional[str]:
        """Call Mercury API"""
        api_url = os.getenv("MERCURY_API_URL")
        api_key = os.getenv("MERCURY_API_KEY")

        if not api_url or not api_key:
            raise RuntimeError("Mercury credentials not configured")

        # Adjust this based on your Mercury API format
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": MERCURY_CONFIG["model"],
            "messages": messages,
            "temperature": temperature or MERCURY_CONFIG["temperature"],
            "max_tokens": max_tokens or MERCURY_CONFIG["max_tokens"],
        }

        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        response = requests.post(
            f"{api_url}/chat/completions",
            headers=headers,
            json=payload,
            timeout=_LLM_POLICY.timeout_s,
        )
        response.raise_for_status()

        data = response.json()
        return data.get("choices", [{}])[0].get("message", {}).get("content")

    @resilient(policy="external_llm", breaker_key="openai")
    def _call_openai(
        self,
        messages: list,
        temperature: Optional[float],
        max_tokens: Optional[int],
        json_mode: bool,
    ) -> Optional[str]:
        """Call OpenAI API"""
        if not self.openai_client:
            raise RuntimeError("OpenAI client not initialized")

        kwargs = {
            "model": OPENAI_CONFIG["model"],
            "messages": messages,
            "temperature": temperature or OPENAI_CONFIG["temperature"],
            "max_tokens": max_tokens or OPENAI_CONFIG["max_tokens"],
        }

        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        response = self.openai_client.chat.completions.create(**kwargs)
        return response.choices[0].message.content


# Singleton instance
_llm_client: Optional[AgentLLMClient] = None
_llm_client_lock = threading.Lock()


def get_llm_client() -> AgentLLMClient:
    """Get or create LLM client singleton (thread-safe double-checked locking)."""
    global _llm_client
    if _llm_client is None:
        with _llm_client_lock:
            if _llm_client is None:
                _llm_client = AgentLLMClient()
    return _llm_client