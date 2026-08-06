"""
PTO Agent Graph Nodes
Each node represents a step in the PTO request workflow
"""
from datetime import datetime, date
import logging
import uuid
import asyncio
from sqlalchemy.orm import Session

from .state import PTOAgentState
from .tools import (
    parse_user_intent,
    calculate_business_days,
    get_company_holidays,
    check_blackout_periods,
    get_pto_balance,
    check_conflicting_requests,
    format_date_range
)
from db.models import PTORequest, PTOBalance, PTOStatus
from agents.utils.llm_client import get_llm_client

logger = logging.getLogger(__name__)


def _balance_year(state: PTOAgentState) -> int:
    """Calendar year a request's balance belongs to.

    Falls back to the current year when the dates have not been parsed yet,
    which is what happens if the balance check runs before validation.
    """
    start_date = state.get("start_date")
    return start_date.year if start_date else date.today().year


def parse_intent_node(state: PTOAgentState, db: Session) -> PTOAgentState:
    """
    Node 1: Parse user message and extract intent
    """
    logger.info(f"Parsing intent for message: {state['user_message']}")
    
    # Run async function in sync context
    parsed = asyncio.run(parse_user_intent(state["user_message"]))
    
    state["intent"] = parsed.get("intent", "general_query")
    
    # Extract dates if present
    if "start_date" in parsed and parsed["start_date"]:
        try:
            state["start_date"] = datetime.strptime(parsed["start_date"], "%Y-%m-%d").date()
        except:
            state["validation_errors"].append("Invalid start date format")
    
    if "end_date" in parsed and parsed["end_date"]:
        try:
            state["end_date"] = datetime.strptime(parsed["end_date"], "%Y-%m-%d").date()
        except:
            state["validation_errors"].append("Invalid end date format")
    
    if "reason" in parsed:
        state["reason"] = parsed.get("reason")
    
    logger.info(f"Parsed intent: {state['intent']}")
    return state


def validate_dates_node(state: PTOAgentState, db: Session) -> PTOAgentState:
    """
    Node 2: Validate requested dates
    """
    logger.info("Validating dates")
    
    start_date = state.get("start_date")
    end_date = state.get("end_date")
    company = state["company"]
    
    # Check if dates are provided
    if not start_date or not end_date:
        state["validation_errors"].append("Start date and end date are required")
        state["is_valid"] = False
        return state
    
    # Check if start date is before end date
    if start_date > end_date:
        state["validation_errors"].append("Start date must be before end date")
        state["is_valid"] = False
        return state
    
    # Check if dates are in the past
    today = date.today()
    if start_date < today:
        state["validation_errors"].append("Cannot request PTO for past dates")
        state["is_valid"] = False
        return state
    
    # Get company holidays
    year = start_date.year
    holidays = get_company_holidays(db, company, year)
    state["holiday_dates"] = holidays
    
    # Calculate business days
    business_days = calculate_business_days(start_date, end_date, holidays)
    state["total_business_days"] = business_days
    
    if business_days <= 0:
        state["validation_errors"].append("No business days in selected range (all weekends/holidays)")
        state["is_valid"] = False
        return state
    
    # Check blackout periods
    has_blackouts, blackout_conflicts = check_blackout_periods(db, company, start_date, end_date)
    if has_blackouts:
        state["blackout_conflicts"] = blackout_conflicts
        state["validation_errors"].append(f"Dates fall within restricted period: {', '.join(blackout_conflicts)}")
        state["is_valid"] = False
        return state
    
    # Check if reason is provided
    if not state.get("reason"):
        state["validation_errors"].append("Reason for PTO request is required")
        state["is_valid"] = False
        return state

    state["is_valid"] = True
    logger.info(f"Dates and reason validated successfully. Business days: {business_days}")
    return state


def check_balance_node(state: PTOAgentState, db: Session) -> PTOAgentState:
    """
    Node 3: Check if user has sufficient PTO balance
    """
    logger.info("Checking PTO balance")
    
    email = state["user_email"]
    # Balances are tracked per calendar year. Derive the year from the
    # requested dates rather than hardcoding it, so the agent keeps working
    # once the calendar rolls over.
    year = _balance_year(state)

    balance = get_pto_balance(db, email, year)
    
    if not balance:
        # Create default balance if doesn't exist
        new_balance = PTOBalance(
            email=email,
            company=state["company"],
            year=year,
            total_days=15.0,
            used_days=0.0,
            pending_days=0.0
        )
        db.add(new_balance)
        db.commit()
        balance = get_pto_balance(db, email, year)
    
    state["current_balance"] = balance["total_days"]
    state["used_days"] = balance["used_days"]
    state["pending_days"] = balance["pending_days"]
    state["remaining_days"] = balance["remaining_days"]
    
    # Check if sufficient balance
    requested_days = state.get("total_business_days", 0)
    if balance["remaining_days"] >= requested_days:
        state["has_sufficient_balance"] = True
        logger.info(f"Sufficient balance: {balance['remaining_days']} days available")
    else:
        state["has_sufficient_balance"] = False
        state["validation_errors"].append(
            f"Insufficient balance. You have {balance['remaining_days']} days available but requested {requested_days} days"
        )
        logger.warning("Insufficient PTO balance")
    
    return state


def check_conflicts_node(state: PTOAgentState, db: Session) -> PTOAgentState:
    """
    Node 4: Check for conflicting PTO requests
    """
    logger.info("Checking for conflicting requests")
    
    email = state["user_email"]
    start_date = state["start_date"]
    end_date = state["end_date"]
    
    has_conflicts, conflicts = check_conflicting_requests(db, email, start_date, end_date)
    
    state["has_conflicts"] = has_conflicts
    state["conflicting_requests"] = conflicts
    
    if has_conflicts:
        state["validation_errors"].append(
            f"You have {len(conflicts)} overlapping request(s) for these dates"
        )
        logger.warning(f"Found {len(conflicts)} conflicting requests")
    else:
        logger.info("No conflicts found")
    
    return state


def create_request_node(state: PTOAgentState, db: Session) -> PTOAgentState:
    """
    Node 5: Create PTO request in database
    """
    logger.info("Creating PTO request")
    
    request_id = str(uuid.uuid4())
    
    new_request = PTORequest(
        id=request_id,
        email=state["user_email"],
        company=state["company"],
        start_date=state["start_date"],
        end_date=state["end_date"],
        days_requested=state["total_business_days"],
        reason=state.get("reason"),
        status=PTOStatus.PENDING
    )
    
    db.add(new_request)
    
    # Update pending days in balance
    balance = db.query(PTOBalance).filter(
        PTOBalance.email == state["user_email"],
        PTOBalance.year == _balance_year(state)
    ).first()
    
    if balance:
        balance.pending_days += state["total_business_days"]
    
    db.commit()
    
    state["request_id"] = request_id
    state["request_created"] = True
    
    logger.info(f"PTO request created with ID: {request_id}")
    return state


def generate_response_node(state: PTOAgentState, db: Session) -> PTOAgentState:
    """
    Node 6: Generate final response to user
    """
    logger.info("Generating response")
    
    intent = state.get("intent")
    
    # Handle different intents
    if intent == "check_balance":
        email = state["user_email"]
        balance = get_pto_balance(db, email, 2025)
        if balance:
            response = f"""📊 Your PTO Balance:
- Total Annual Leave: {balance['total_days']} days
- Used: {balance['used_days']} days
- Pending Approval: {balance['pending_days']} days
- Available: {balance['remaining_days']} days"""
        else:
            response = "I couldn't find your PTO balance. Please contact your administrator."
    
    elif intent == "request_pto":
        if state.get("request_created"):
            date_range = format_date_range(state["start_date"], state["end_date"])
            response = f"""✅ PTO Request Submitted Successfully!

📅 Dates: {date_range}
📊 Business Days: {state['total_business_days']} days
📝 Request ID: {state['request_id']}
⏳ Status: Pending Admin Approval

Your request has been sent to your company admin for approval. You'll be notified once it's reviewed."""
        else:
            # Request failed validation
            errors = state.get("validation_errors", [])
            response = f"""❌ Cannot Process PTO Request

The following issues were found:
{chr(10).join(f"• {error}" for error in errors)}

Please review and submit a new request."""
    
    elif intent == "view_requests":
        # Query user's recent requests
        requests = db.query(PTORequest).filter(
            PTORequest.email == state["user_email"]
        ).order_by(PTORequest.created_at.desc()).limit(5).all()
        
        if requests:
            request_list = []
            for req in requests:
                status_emoji = {"pending": "⏳", "approved": "✅", "denied": "❌"}.get(req.status.value, "❓")
                request_list.append(
                    f"{status_emoji} {req.start_date} to {req.end_date} - {req.days_requested} days ({req.status.value})"
                )
            response = f"""📋 Your Recent PTO Requests:

{chr(10).join(request_list)}"""
        else:
            response = "You haven't submitted any PTO requests yet."
    
    else:
        # General query - use LLM
        llm_client = get_llm_client()
        messages = [
            {"role": "system", "content": "You are a helpful PTO assistant. Answer questions about time off and leave policies."},
            {"role": "user", "content": state["user_message"]}
        ]
        response = llm_client.chat(messages, temperature=0.7) or "I'm here to help with PTO requests. How can I assist you?"
    
    state["agent_response"] = response
    state["should_end"] = True
    
    return state