"""
HR Ticket Agent - LangGraph Workflow

Phase 5.5B note
---------------
Until Phase 5.5 this class compiled a graph in ``__init__`` and then never used
it: ``process_message`` re-implemented the same node sequence by hand, because
the nodes take ``(state, db)`` and the graph was built with bare one-argument
node references. That compiled graph could not even be invoked (it raised
``TypeError: parse_intent_node() missing 1 required positional argument: 'db'``),
so attaching a checkpointer to it would have checkpointed nothing.

The nodes are now bound to the session the same way the PTO agent binds them,
and the graph is what actually runs. The edges are unchanged from the ones that
were already declared, and they match the old manual sequence step for step;
``HRTicketState`` has no reducer annotations, so LangGraph's channel merge is
last-write-wins, which is exactly what sequential ``state = node(state, db)``
assignment did.

Because the graph closes over a request-scoped ``Session``, it is compiled per
call rather than cached. That is the same trade-off the PTO agent already makes,
and caching would risk running a node against a closed session.
"""
import logging
from typing import Any, Dict, Optional

from langgraph.graph import StateGraph, END
from agents.hr_ticket.state import HRTicketState
from agents.hr_ticket.nodes import (
    parse_intent_node,
    validate_request_node,
    check_duplicates_node,
    create_ticket_node,
    generate_response_node
)
from agents.utils.checkpointer import (
    HR_THREAD_PREFIX,
    CheckpointTenantMismatch,
    get_checkpointer,
    thread_id_for,
)
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class HRTicketAgent:
    """
    HR Ticket Agent that handles employee HR inquiries and meeting requests.
    
    Workflow:
    1. Parse user intent and extract ticket details
    2. Validate the request
    3. Check for duplicate open tickets (informational)
    4. Create ticket in database
    5. Generate response to user
    """
    
    def __init__(self):
        # Phase 5.5A: process-wide, lazily built. None when checkpointing is off.
        self.checkpointer = get_checkpointer()

    def _build_graph(self, db: Session):
        """Build the LangGraph workflow with ``db`` bound into every node."""
        workflow = StateGraph(HRTicketState)

        # Add nodes
        workflow.add_node("parse_intent", lambda state: parse_intent_node(state, db))
        workflow.add_node("validate_request", lambda state: validate_request_node(state, db))
        workflow.add_node("check_duplicates", lambda state: check_duplicates_node(state, db))
        workflow.add_node("create_ticket", lambda state: create_ticket_node(state, db))
        workflow.add_node("generate_response", lambda state: generate_response_node(state, db))

        # Define flow
        workflow.set_entry_point("parse_intent")
        
        # After parsing, validate
        workflow.add_edge("parse_intent", "validate_request")
        
        # After validation, route based on validity
        workflow.add_conditional_edges(
            "validate_request",
            self._validation_router,
            {
                "valid": "check_duplicates",
                "invalid": "generate_response"
            }
        )
        
        # After duplicate check, create ticket
        workflow.add_edge("check_duplicates", "create_ticket")
        
        # After creating ticket, generate response
        workflow.add_edge("create_ticket", "generate_response")
        
        # End after response
        workflow.add_edge("generate_response", END)

        compile_kwargs: Dict[str, Any] = {}
        if self.checkpointer is not None:
            compile_kwargs["checkpointer"] = self.checkpointer
        return workflow.compile(**compile_kwargs)

    @staticmethod
    def thread_config(conversation_id: Optional[str]) -> Dict[str, Any]:
        """LangGraph config pinning this run to the conversation's thread.

        Unlike the PTO agent, HR does not carry slots across turns. A ticket is
        a single-shot artefact, so remembering a half-filled subject or
        description would risk filing a ticket the user did not ask for on a
        later turn. What the durable thread buys HR is state that outlives the
        request: a replayable history, and a run that could be resumed.
        """
        return {
            "configurable": {
                "thread_id": thread_id_for(HR_THREAD_PREFIX, conversation_id)
            }
        }

    def _validation_router(self, state: HRTicketState) -> str:
        """Route based on validation result"""
        if state["is_valid"]:
            return "valid"
        else:
            return "invalid"
    
    async def process_message(
        self,
        user_email: str,
        company: str,
        message: str,
        db: Session,
        conversation_id: Optional[str] = None,
    ) -> dict:
        """
        Process a user message and create an HR ticket.

        Args:
            user_email: Email of the user
            company: User's company
            message: User's message
            db: Database session
            conversation_id: Chat thread this turn belongs to. Supplying it
                makes graph state durable across turns (Phase 5.5).

        Returns:
            dict with response and ticket info
        """
        initial_state = self._initial_state(user_email, company, message)
        graph = self._build_graph(db)
        config = self.thread_config(conversation_id)

        try:
            final_state = await graph.ainvoke(initial_state, config=config)
        except CheckpointTenantMismatch:
            # Security boundary: never answer from another tenant's state.
            raise
        except Exception as e:
            logger.error("Error in HR ticket workflow: %s", e)
            # Preserve the pre-5.5 contract: report the failure with whatever
            # the run managed to persist, rather than propagating.
            final_state = dict(initial_state)
            if self.checkpointer is not None:
                try:
                    partial = await graph.aget_state(config)
                    final_state.update(getattr(partial, "values", None) or {})
                except Exception:  # noqa: BLE001 - best effort only
                    pass
            final_state["agent_response"] = "I'm sorry, there was an error processing your request. Please try again."
            final_state["is_valid"] = False

        return self._result_from_state(final_state)

    @staticmethod
    def _initial_state(user_email: str, company: str, message: str) -> HRTicketState:
        """Build the initial workflow state."""
        return {
            "user_email": user_email,
            "company": company,
            "user_message": message,
            "intent": "",
            "subject": None,
            "description": None,
            "category": None,
            "meeting_type": None,
            "preferred_date": None,
            "preferred_time_slot": None,
            "urgency": "normal",
            "is_valid": False,
            "validation_errors": [],
            "has_open_tickets": False,
            "open_ticket_ids": [],
            "ticket_id": None,
            "queue_position": None,
            "agent_response": ""
        }

    @staticmethod
    def _result_from_state(final_state: dict) -> dict:
        """Shape the API result dict from a final workflow state."""
        return {
            "response": final_state["agent_response"],
            "ticket_created": final_state.get("ticket_id") is not None,
            "ticket_id": final_state.get("ticket_id"),
            "queue_position": final_state.get("queue_position"),
            "has_open_tickets": final_state.get("has_open_tickets", False),
            "open_ticket_ids": final_state.get("open_ticket_ids", [])
        }

    async def process_message_stream(
        self,
        user_email: str,
        company: str,
        message: str,
        db: Session,
        conversation_id: Optional[str] = None,
    ):
        """
        Process a user message, streaming per-node progress.

        Streams the checkpointed graph, mirroring ``PTOAgent.execute_stream``.
        Yields ("status", payload) as each node completes, then ("done", result)
        with the same shape as process_message().

        Note the change in timing from the pre-5.5 version: a status event now
        arrives *after* its node finishes rather than before it starts, which is
        what ``stream_mode="updates"`` reports and what the PTO stream already
        did. The stage names are unchanged.
        """
        state: Dict[str, Any] = dict(self._initial_state(user_email, company, message))
        graph = self._build_graph(db)
        config = self.thread_config(conversation_id)

        try:
            async for update in graph.astream(state, config=config, stream_mode="updates"):
                for node_name, delta in update.items():
                    if isinstance(delta, dict):
                        state.update(delta)
                    yield "status", {"stage": node_name}

        except CheckpointTenantMismatch:
            raise
        except Exception as e:
            logger.error("Error in HR ticket workflow: %s", e)
            state["agent_response"] = "I'm sorry, there was an error processing your request. Please try again."
            state["is_valid"] = False

        yield "done", self._result_from_state(state)


# Singleton instance
_agent_instance = None

def get_hr_ticket_agent() -> HRTicketAgent:
    """Get or create the HR Ticket Agent singleton"""
    global _agent_instance
    if _agent_instance is None:
        _agent_instance = HRTicketAgent()
    return _agent_instance