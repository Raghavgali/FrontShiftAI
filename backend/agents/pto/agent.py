"""
PTO Agent - Main LangGraph Workflow
Orchestrates the complete PTO request process
"""
import logging
from typing import Dict, Any
from langgraph.graph import StateGraph, END
from sqlalchemy.orm import Session

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
        
        # After creating request, generate response
        workflow.add_edge("create_request", "generate_response")
        
        # End after response
        workflow.add_edge("generate_response", END)
        
        return workflow.compile()
    
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
            "balance_info": {
                "remaining_days": state.get("remaining_days"),
                "total_days": state.get("current_balance"),
                "used_days": state.get("used_days"),
                "pending_days": state.get("pending_days")
            } if state.get("remaining_days") is not None else None
        }

    async def execute(self, user_email: str, company: str, message: str) -> Dict[str, Any]:
        """
        Execute the PTO agent workflow

        Args:
            user_email: User's email
            company: User's company
            message: User's message/request

        Returns:
            Dictionary with agent response and metadata
        """
        logger.info(f"Executing PTO agent for user: {user_email}")

        # Initialize state
        initial_state = self._initial_state(user_email, company, message)

        try:
            # Run the graph
            final_state = await self.graph.ainvoke(initial_state)

            return self._result_from_state(final_state)

        except Exception as e:
            logger.error(f"Error executing PTO agent: {e}")
            return {
                "response": "I encountered an error processing your request. Please try again or contact support.",
                "request_created": False,
                "request_id": None,
                "balance_info": None,
                "error": str(e)
            }

    async def execute_stream(self, user_email: str, company: str, message: str):
        """
        Execute the PTO agent workflow, streaming per-node progress.

        Yields ("status", payload) tuples as each LangGraph node completes,
        then a final ("done", result) with the same shape as execute().
        """
        logger.info(f"Streaming PTO agent for user: {user_email}")

        state: Dict[str, Any] = dict(self._initial_state(user_email, company, message))

        try:
            async for update in self.graph.astream(state, stream_mode="updates"):
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

        except Exception as e:
            logger.error(f"Error streaming PTO agent: {e}")
            yield "done", {
                "response": "I encountered an error processing your request. Please try again or contact support.",
                "request_created": False,
                "request_id": None,
                "balance_info": None,
                "error": str(e)
            }