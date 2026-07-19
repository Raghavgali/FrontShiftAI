"""
HR Ticket Agent - LangGraph Workflow
"""
from langgraph.graph import StateGraph, END
from agents.hr_ticket.state import HRTicketState
from agents.hr_ticket.nodes import (
    parse_intent_node,
    validate_request_node,
    check_duplicates_node,
    create_ticket_node,
    generate_response_node
)
from sqlalchemy.orm import Session


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
        self.graph = self._build_graph()
    
    def _build_graph(self):
        """Build the LangGraph workflow"""
        workflow = StateGraph(HRTicketState)
        
        # Add nodes
        workflow.add_node("parse_intent", parse_intent_node)
        workflow.add_node("validate_request", validate_request_node)
        workflow.add_node("check_duplicates", check_duplicates_node)
        workflow.add_node("create_ticket", create_ticket_node)
        workflow.add_node("generate_response", generate_response_node)
        
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
        
        return workflow.compile()
    
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
        db: Session
    ) -> dict:
        """
        Process a user message and create an HR ticket.
        
        Args:
            user_email: Email of the user
            company: User's company
            message: User's message
            db: Database session
        
        Returns:
            dict with response and ticket info
        """
        # Initialize state
        initial_state = self._initial_state(user_email, company, message)

        # Run the workflow
        # Pass db to each node via config
        config = {"configurable": {"db": db}}
        
        # Since nodes need db, we'll invoke with a wrapper
        final_state = None
        current_state = initial_state
        
        # Manually execute nodes with db
        try:
            # Parse intent
            current_state = parse_intent_node(current_state, db)
            
            # Validate
            current_state = validate_request_node(current_state, db)
            
            # If valid, continue
            if current_state["is_valid"]:
                # Check duplicates
                current_state = check_duplicates_node(current_state, db)
                
                # Create ticket
                current_state = create_ticket_node(current_state, db)
            
            # Generate response
            current_state = generate_response_node(current_state, db)
            
            final_state = current_state
            
        except Exception as e:
            print(f"Error in workflow: {e}")
            final_state = current_state
            final_state["agent_response"] = "I'm sorry, there was an error processing your request. Please try again."
            final_state["is_valid"] = False
        
        # Return result
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
        db: Session
    ):
        """
        Process a user message, streaming per-node progress.

        Mirrors process_message's manual node sequence (the compiled graph is
        not used because the nodes need the db session). Yields ("status",
        payload) before each node runs, then ("done", result) with the same
        shape as process_message().
        """
        state = self._initial_state(user_email, company, message)

        try:
            yield "status", {"stage": "parse_intent"}
            state = parse_intent_node(state, db)

            yield "status", {"stage": "validate_request"}
            state = validate_request_node(state, db)

            if state["is_valid"]:
                yield "status", {"stage": "check_duplicates"}
                state = check_duplicates_node(state, db)

                yield "status", {"stage": "create_ticket"}
                state = create_ticket_node(state, db)

            yield "status", {"stage": "generate_response"}
            state = generate_response_node(state, db)

        except Exception as e:
            print(f"Error in workflow: {e}")
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