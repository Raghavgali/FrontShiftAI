"""
PTO Agent - Main LangGraph Workflow
Orchestrates the complete PTO request process
"""
import logging
import os
from typing import Any, Dict, Optional
from langgraph.graph import StateGraph, END
from sqlalchemy.orm import Session

from agents.utils.checkpointer import (
    PTO_THREAD_PREFIX,
    CheckpointTenantMismatch,
    get_checkpointer,
    thread_id_for,
)

from .state import PTOAgentState
from .nodes import (
    parse_intent_node,
    validate_dates_node,
    check_balance_node,
    check_conflicts_node,
    create_request_node,
    generate_response_node
)

logger = logging.getLogger(__name__)


def review_suspend_enabled() -> bool:
    """Whether the Phase 5.5D admin-review pause is switched on.

    Off by default, so the runtime path is byte-for-byte what it was before
    5.5D landed. See ``wait_for_review_node`` for what flipping this on means.
    """
    return os.getenv("PTO_REVIEW_SUSPEND", "false").strip().lower() in {
        "1", "true", "yes", "on",
    }


def wait_for_review_node(state: PTOAgentState) -> PTOAgentState:
    """Phase 5.5D seam: the point where the graph can pause for an admin.

    ==================== FOUNDATION ONLY, NOT WIRED UP ====================
    The durable half is done: the graph is checkpointed, so when compiled with
    ``interrupt_after=["wait_for_review"]`` a run stops here, its state is
    written to ``langgraph_checkpoints``, and ``resume_after_review()`` picks it
    up later in a different process. That path is exercised by
    ``stress_tests/test_phase5_5_checkpointing.py``.

    What is deliberately missing, per the plan ("stub for now"):

    - No ``POST /api/admin/pto/{id}/approve`` endpoint. Adding one means
      deciding who may approve, how the request id maps back to a thread id,
      and what happens to a request approved through the existing admin route
      while a thread is parked here.
    - No admin UI, and no notification when a request lands in review.
    - The response the user gets while parked is whatever the caller makes of
      ``awaiting_review``; today ``execute()`` reports the pause and stops.

    Turning it on before those exist would strand PTO requests in a paused
    graph with nothing able to resume them, which is why the flag defaults off.
    =======================================================================
    """
    logger.info(
        "PTO request %s reached wait_for_review", state.get("request_id")
    )
    state["awaiting_review"] = True
    return state


class PTOAgent:
    """
    PTO Agent using LangGraph for workflow orchestration
    
    Workflow:
    1. Parse user intent (request PTO, check balance, view requests)
    2. Validate dates (if requesting PTO)
    3. Check balance
    4. Check conflicts
    5. Create request
    6. Generate response
    """
    
    def __init__(self, db: Session):
        self.db = db
        # Phase 5.5A: process-wide, lazily built. None when checkpointing is
        # switched off, in which case the graph compiles exactly as before.
        self.checkpointer = get_checkpointer()
        self.graph = self._build_graph()

    def _build_graph(self) -> StateGraph:
        """Build the LangGraph workflow"""

        # Create the graph
        workflow = StateGraph(PTOAgentState)
        
        # Add nodes
        workflow.add_node("parse_intent", lambda state: parse_intent_node(state, self.db))
        workflow.add_node("validate_dates", lambda state: validate_dates_node(state, self.db))
        workflow.add_node("check_balance", lambda state: check_balance_node(state, self.db))
        workflow.add_node("check_conflicts", lambda state: check_conflicts_node(state, self.db))
        workflow.add_node("create_request", lambda state: create_request_node(state, self.db))
        workflow.add_node("generate_response", lambda state: generate_response_node(state, self.db))
        
        # Set entry point
        workflow.set_entry_point("parse_intent")
        
        # Define edges based on intent
        workflow.add_conditional_edges(
            "parse_intent",
            self._route_after_parse,
            {
                "validate": "validate_dates",
                "respond": "generate_response"
            }
        )
        
        # Validation flow
        workflow.add_conditional_edges(
            "validate_dates",
            self._route_after_validation,
            {
                "continue": "check_balance",
                "failed": "generate_response"
            }
        )
        
        # Balance check flow
        workflow.add_conditional_edges(
            "check_balance",
            self._route_after_balance,
            {
                "continue": "check_conflicts",
                "failed": "generate_response"
            }
        )
        
        # Conflict check flow
        workflow.add_conditional_edges(
            "check_conflicts",
            self._route_after_conflicts,
            {
                "create": "create_request",
                "failed": "generate_response"
            }
        )
        
        # After creating a request the graph optionally parks for an admin
        # decision (5.5D), otherwise it goes straight to the response.
        suspend = review_suspend_enabled() and self.checkpointer is not None
        if suspend:
            workflow.add_node("wait_for_review", wait_for_review_node)
            workflow.add_edge("create_request", "wait_for_review")
            workflow.add_edge("wait_for_review", "generate_response")
        else:
            workflow.add_edge("create_request", "generate_response")

        # End after response
        workflow.add_edge("generate_response", END)

        compile_kwargs: Dict[str, Any] = {}
        if self.checkpointer is not None:
            compile_kwargs["checkpointer"] = self.checkpointer
        if suspend:
            # interrupt_after, not interrupt_before: the node has to run so the
            # pause is recorded in the checkpoint that gets persisted.
            compile_kwargs["interrupt_after"] = ["wait_for_review"]

        return workflow.compile(**compile_kwargs)

    # ------------------------------------------------------------------ #
    # Phase 5.5B/5.5C: durable threads
    # ------------------------------------------------------------------ #

    @staticmethod
    def thread_config(conversation_id: Optional[str]) -> Dict[str, Any]:
        """LangGraph config pinning this run to the conversation's thread.

        The thread id is derived from ``conversation_id`` and never accepted
        from a client, so every turn of one chat shares durable state. Callers
        with no conversation (the standalone ``/api/pto/chat`` route) get a
        disposable thread rather than losing checkpointing entirely.
        """
        return {
            "configurable": {
                "thread_id": thread_id_for(PTO_THREAD_PREFIX, conversation_id)
            }
        }

    async def _carry_over(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Slots worth remembering from the previous turn of this conversation.

        This is what makes 5.5C more than durability. A user who says "I'd like
        to request some PTO" and then "next Thursday and Friday" should not have
        to restate the reason, so ``start_date``, ``end_date`` and ``reason``
        are left out of the new turn's input, which leaves the checkpointed
        values in place. Everything else is re-stated explicitly by
        ``_initial_state``, so no stale flag, balance or response can leak
        forward.

        Slots are dropped, not carried, once the previous turn finished the job:
        after a request has been created, "request PTO" in a later turn must not
        silently resubmit the dates from the completed one.
        """
        if self.checkpointer is None:
            return {}
        try:
            snapshot = await self.graph.aget_state(config)
        except CheckpointTenantMismatch:
            # Security boundary. Never degrade this into "start fresh".
            raise
        except Exception as exc:  # noqa: BLE001 - carry-over is best effort
            logger.warning("Could not read prior PTO state, starting fresh: %s", exc)
            return {}

        values = getattr(snapshot, "values", None) or {}
        if not values:
            return {}
        if values.get("request_created") or values.get("awaiting_review"):
            return {}
        if values.get("intent") != "request_pto":
            return {}

        carried = {
            key: values[key]
            for key in ("start_date", "end_date", "reason")
            if values.get(key) is not None
        }
        if carried:
            logger.info("Resuming PTO conversation with carried slots: %s", sorted(carried))
        return carried

    async def _turn_input(
        self, user_email: str, company: str, message: str, config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """The graph input for one turn: a full reset minus any carried slots."""
        turn = dict(self._initial_state(user_email, company, message))
        for key in await self._carry_over(config):
            # Omitting the key entirely is what preserves the checkpointed
            # value; passing None would overwrite it.
            turn.pop(key, None)
        return turn

    async def resume_after_review(self, conversation_id: Optional[str]) -> Dict[str, Any]:
        """Continue a run parked at ``wait_for_review`` (Phase 5.5D seam).

        Present so the durable half of 5.5D is real and testable. There is no
        caller in the app yet: see ``wait_for_review_node`` for what an admin
        approval endpoint would still have to decide.
        """
        if self.checkpointer is None:
            raise RuntimeError("Cannot resume a PTO review without a checkpointer")
        config = self.thread_config(conversation_id)
        final_state = await self.graph.ainvoke(None, config=config)
        return self._result_from_state(final_state)

    def _route_after_parse(self, state: PTOAgentState) -> str:
        """Route based on parsed intent"""
        intent = state.get("intent")
        
        if intent == "request_pto":
            # Need to validate dates for PTO request
            return "validate"
        else:
            # For check_balance, view_requests, or general_query
            # Skip directly to response generation
            return "respond"
    
    def _route_after_validation(self, state: PTOAgentState) -> str:
        """Route based on validation results"""
        if state.get("is_valid", False):
            return "continue"
        else:
            return "failed"
    
    def _route_after_balance(self, state: PTOAgentState) -> str:
        """Route based on balance check"""
        if state.get("has_sufficient_balance", False):
            return "continue"
        else:
            return "failed"
    
    def _route_after_conflicts(self, state: PTOAgentState) -> str:
        """Route based on conflict check"""
        if not state.get("has_conflicts", False):
            return "create"
        else:
            return "failed"
    
    def _initial_state(self, user_email: str, company: str, message: str) -> PTOAgentState:
        """Build the fully-populated initial graph state."""
        return PTOAgentState(
            user_email=user_email,
            company=company,
            user_message=message,
            start_date=None,
            end_date=None,
            reason=None,
            intent=None,
            is_valid=False,
            validation_errors=[],
            total_business_days=None,
            holiday_dates=[],
            blackout_conflicts=[],
            current_balance=None,
            used_days=None,
            pending_days=None,
            remaining_days=None,
            has_sufficient_balance=False,
            has_conflicts=False,
            conflicting_requests=[],
            request_id=None,
            request_created=False,
            awaiting_review=False,
            agent_response="",
            should_end=False,
            error_message=None
        )

    @staticmethod
    def _result_from_state(state: Dict[str, Any]) -> Dict[str, Any]:
        """Shape the API result dict from a (final) graph state."""
        return {
            "response": state["agent_response"],
            "request_created": state.get("request_created", False),
            "request_id": state.get("request_id"),
            "awaiting_review": state.get("awaiting_review", False),
            "balance_info": {
                "remaining_days": state.get("remaining_days"),
                "total_days": state.get("current_balance"),
                "used_days": state.get("used_days"),
                "pending_days": state.get("pending_days")
            } if state.get("remaining_days") is not None else None
        }

    async def execute(
        self,
        user_email: str,
        company: str,
        message: str,
        conversation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Execute the PTO agent workflow

        Args:
            user_email: User's email
            company: User's company
            message: User's message/request
            conversation_id: Chat thread this turn belongs to. Supplying it
                makes graph state durable and shared across turns (Phase 5.5).
                Omitting it still checkpoints, on a disposable thread.

        Returns:
            Dictionary with agent response and metadata
        """
        logger.info(f"Executing PTO agent for user: {user_email}")

        config = self.thread_config(conversation_id)

        try:
            turn_input = await self._turn_input(user_email, company, message, config)

            # Run the graph
            final_state = await self.graph.ainvoke(turn_input, config=config)

            if final_state.get("awaiting_review"):
                # 5.5D: the run is parked in the checkpoint, not finished.
                logger.info("PTO run parked for review on thread %s",
                            config["configurable"]["thread_id"])

            return self._result_from_state(final_state)

        except CheckpointTenantMismatch:
            # Security boundary: surface it, never answer from another tenant's
            # state and never fall through to the generic error response.
            raise
        except Exception as e:
            logger.error(f"Error executing PTO agent: {e}")
            return {
                "response": "I encountered an error processing your request. Please try again or contact support.",
                "request_created": False,
                "request_id": None,
                "awaiting_review": False,
                "balance_info": None,
                "error": str(e)
            }

    async def execute_stream(
        self,
        user_email: str,
        company: str,
        message: str,
        conversation_id: Optional[str] = None,
    ):
        """
        Execute the PTO agent workflow, streaming per-node progress.

        Yields ("status", payload) tuples as each LangGraph node completes,
        then a final ("done", result) with the same shape as execute().
        """
        logger.info(f"Streaming PTO agent for user: {user_email}")

        config = self.thread_config(conversation_id)

        try:
            state: Dict[str, Any] = await self._turn_input(
                user_email, company, message, config
            )
            # The streamed state starts from the resolved turn input, but any
            # slot carried over from a previous turn lives only in the
            # checkpoint, so seed the local mirror from there too.
            if self.checkpointer is not None:
                prior = await self.graph.aget_state(config)
                for key in ("start_date", "end_date", "reason"):
                    if key not in state and (getattr(prior, "values", None) or {}).get(key):
                        state[key] = prior.values[key]

            async for update in self.graph.astream(state, config=config, stream_mode="updates"):
                for node_name, delta in update.items():
                    if isinstance(delta, dict):
                        state.update(delta)
                    payload: Dict[str, Any] = {"stage": node_name}
                    if node_name == "check_balance" and state.get("remaining_days") is not None:
                        payload["remaining_days"] = state.get("remaining_days")
                    if node_name == "create_request" and state.get("request_id"):
                        payload["request_id"] = state.get("request_id")
                    yield "status", payload

            yield "done", self._result_from_state(state)

        except CheckpointTenantMismatch:
            raise
        except Exception as e:
            logger.error(f"Error streaming PTO agent: {e}")
            yield "done", {
                "response": "I encountered an error processing your request. Please try again or contact support.",
                "request_created": False,
                "request_id": None,
                "awaiting_review": False,
                "balance_info": None,
                "error": str(e)
            }