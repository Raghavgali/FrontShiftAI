"""
PTO Agent State Definition
Tracks data flow through the LangGraph workflow

Reducers (Phase 5.5)
--------------------
``validation_errors``, ``holiday_dates``, ``blackout_conflicts`` and
``conflicting_requests`` used to be annotated with ``operator.add`` to
"accumulate". That reducer requires nodes to return *deltas*, and every node in
``nodes.py`` does the opposite: it mutates the state dict it was handed and
returns the whole thing. LangGraph then folded the full list into the channel
that already held it, so each list was concatenated with itself.

Measured on the real pattern, a single run produced six entries where two were
appended. Once Phase 5.5 gave a conversation a stable thread, the previous
turn's list became the starting point for the next fold and the growth turned
exponential: 6, 30, 126, 510 entries over four turns, with the user seeing
every stale validation error repeated in the response.

Plain (last-write-wins) channels are the semantically correct pairing for
nodes that return whole snapshots: the last node to run reports the complete
list, so that is what the channel should hold. The graph is strictly
sequential, so there is no fan-in that would need a merge. This also lets a
new turn reset a channel by passing ``[]``, which ``operator.add`` could not
express.
"""
from typing import TypedDict, Optional, List
from datetime import date


class PTOAgentState(TypedDict):
    """
    State that flows through the PTO agent graph
    Each node can read and update this state
    """
    
    # Input from user
    user_email: str
    company: str
    user_message: str
    
    # Parsed request data (extracted by LLM)
    start_date: Optional[date]
    end_date: Optional[date]
    reason: Optional[str]
    intent: Optional[str]  # "request_pto", "check_balance", "view_requests", "general_query"
    
    # Validation results
    is_valid: bool
    validation_errors: List[str]
    
    # Date calculations
    total_business_days: Optional[float]
    holiday_dates: List[date]
    blackout_conflicts: List[str]
    
    # Balance check
    current_balance: Optional[float]
    used_days: Optional[float]
    pending_days: Optional[float]
    remaining_days: Optional[float]
    has_sufficient_balance: bool
    
    # Conflict check
    has_conflicts: bool
    conflicting_requests: List[dict]
    
    # Request creation
    request_id: Optional[str]
    request_created: bool
    
    # Phase 5.5D seam: set by the wait_for_review node when the graph is paused
    # waiting for an admin decision. False on every normal run.
    awaiting_review: bool

    # Final response
    agent_response: str
    should_end: bool  # Signal to end the workflow
    
    # Error handling
    error_message: Optional[str]