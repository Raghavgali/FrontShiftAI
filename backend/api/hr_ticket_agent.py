"""
HR Ticket API endpoints with Monitoring
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional, List
from datetime import datetime
import json
import logging
import time  # ADD THIS

from sse_starlette import EventSourceResponse

from db.connection import get_db
from db.tenant_context import set_tenant_context, clear_tenant_context
from db.models import HRTicket, TicketStatus, TicketCategory, Urgency, User, UserRole
from schemas.hr_ticket import (
    HRTicketChatRequest,
    HRTicketChatResponse,
    HRTicketResponse,
    HRTicketListResponse,
    PickTicketRequest,
    ScheduleMeetingRequest,
    ResolveTicketRequest,
    AddNoteRequest,
    TicketStatsResponse,
    SimpleResponse
)
from agents.hr_ticket import get_hr_ticket_agent
from agents.hr_ticket.tools import (
    get_ticket_by_id,
    get_user_tickets,
    is_company_admin,
    get_ticket_stats
)
from api.auth import get_current_user
from api.idempotency import IdempotencyGuard, idempotency_guard
from monitoring.production_logger import production_monitor  # ADD THIS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/hr-tickets", tags=["HR Tickets"])


# ==========================================
# USER ENDPOINTS
# ==========================================

@router.post("/chat", response_model=HRTicketChatResponse)
async def create_ticket_via_chat(
    request: HRTicketChatRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
    idem: IdempotencyGuard = Depends(idempotency_guard),
):
    """
    Create an HR ticket via chat interface.
    User sends a natural language message, agent parses and creates ticket.
    """
    # Idempotency: replayed voice tool calls (same key) return the cached response
    # without creating a duplicate HRTicket.
    cached = idem.reserve(db, current_user["company"], endpoint="/api/hr-tickets/chat")
    if cached is not None:
        return cached

    start_time = time.time()  # ADD THIS
    success = False  # ADD THIS

    try:
        agent = get_hr_ticket_agent()

        result = await agent.process_message(
            user_email=current_user["email"],
            company=current_user["company"],
            message=request.message,
            db=db
        )

        success = True  # ADD THIS

        # Log agent execution
        execution_time_ms = (time.time() - start_time) * 1000
        production_monitor.log_agent_execution(
            agent_name="hr_ticket",
            execution_time_ms=execution_time_ms,
            success=True,
            company_id=current_user["company"]
        )

        # Log ticket creation metric
        if result["ticket_created"] and production_monitor.run:
            production_monitor.run.log({
                "business/hr_ticket_created": 1,
                "business/company": current_user["company"],
                "timestamp": time.time()
            })

        # Get ticket details if created
        ticket_details = None
        if result["ticket_created"] and result["ticket_id"]:
            ticket = get_ticket_by_id(db, result["ticket_id"], current_user["company"])
            if ticket:
                ticket_details = HRTicketResponse.model_validate(ticket)

        response = HRTicketChatResponse(
            response=result["response"],
            ticket_created=result["ticket_created"],
            ticket_id=result.get("ticket_id"),
            queue_position=result.get("queue_position"),
            ticket_details=ticket_details
        )
        idem.store(
            db,
            current_user["company"],
            endpoint="/api/hr-tickets/chat",
            status_code=200,
            body=response.model_dump(),
        )
        return response

    except Exception as e:
        # Log failure
        execution_time_ms = (time.time() - start_time) * 1000
        production_monitor.log_agent_execution(
            agent_name="hr_ticket",
            execution_time_ms=execution_time_ms,
            success=False,
            company_id=current_user["company"]
        )
        # Drop the reservation so a retry with the same key gets a real attempt.
        idem.release(db, current_user["company"])
        raise


@router.post("/chat/stream")
async def create_ticket_via_chat_stream(
    request: HRTicketChatRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
    idem: IdempotencyGuard = Depends(idempotency_guard),
):
    """
    Chat with the HR ticket agent, streaming per-node progress as SSE.

    Events: `status` per workflow node, then `done` with the same body as
    POST /api/hr-tickets/chat. Errors emit an `error` event.
    """
    user_email = current_user["email"]
    company = current_user["company"]
    is_admin = current_user.get("role") == "super_admin"

    # Same endpoint key as the batch route so retries dedupe across both.
    # reserve() makes this concurrency-safe: an in-flight duplicate is
    # rejected with 409 before any work happens.
    cached = idem.reserve(db, company, endpoint="/api/hr-tickets/chat")

    async def event_stream():
        # The tenant ContextVar set by middleware is cleared before this
        # generator body runs (it executes after call_next returns), so
        # re-establish it for all DB access below (Phase 0.6 auto-filter).
        set_tenant_context(company=company, is_super_admin=is_admin)
        start_time = time.time()
        try:
            if cached is not None:
                yield {"event": "done", "data": json.dumps(cached)}
                return

            agent = get_hr_ticket_agent()
            result = None
            async for kind, payload in agent.process_message_stream(
                user_email=user_email, company=company, message=request.message, db=db
            ):
                if kind == "status":
                    yield {"event": "status", "data": json.dumps(payload)}
                else:
                    result = payload

            execution_time_ms = (time.time() - start_time) * 1000
            if result is None:
                production_monitor.log_agent_execution(
                    agent_name="hr_ticket",
                    execution_time_ms=execution_time_ms,
                    success=False,
                    company_id=company,
                )
                idem.release(db, company)
                yield {
                    "event": "error",
                    "data": json.dumps({
                        "detail": "agent produced no result",
                        "response": "Failed to process your request. Please try again.",
                    }),
                }
                return

            production_monitor.log_agent_execution(
                agent_name="hr_ticket",
                execution_time_ms=execution_time_ms,
                success=True,
                company_id=company,
            )
            if result["ticket_created"]:
                production_monitor.run.log({
                    "business/hr_ticket_created": 1,
                    "business/company": company,
                    "timestamp": time.time()
                })

            ticket_details = None
            if result["ticket_created"] and result["ticket_id"]:
                ticket = get_ticket_by_id(db, result["ticket_id"], company)
                if ticket:
                    ticket_details = HRTicketResponse.model_validate(ticket)

            response = HRTicketChatResponse(
                response=result["response"],
                ticket_created=result["ticket_created"],
                ticket_id=result.get("ticket_id"),
                queue_position=result.get("queue_position"),
                ticket_details=ticket_details,
            )
            body = response.model_dump(mode="json")
            idem.store(
                db,
                company,
                endpoint="/api/hr-tickets/chat",
                status_code=200,
                body=body,
            )
            yield {"event": "done", "data": json.dumps(body)}

        except Exception as e:
            logger.error(f"Error in HR ticket stream: {e}", exc_info=True)
            idem.release(db, company)
            yield {
                "event": "error",
                "data": json.dumps({
                    "detail": str(e),
                    "response": "Failed to process your request. Please try again.",
                }),
            }
        finally:
            clear_tenant_context()

    return EventSourceResponse(
        event_stream(),
        ping=15,
        send_timeout=10,
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/my-tickets", response_model=HRTicketListResponse)
def get_my_tickets(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get all tickets for the current user"""
    start_time = time.time()  # ADD THIS
    
    tickets = get_user_tickets(db, current_user["email"], current_user["company"])
    
    # Log query performance
    query_time_ms = (time.time() - start_time) * 1000
    production_monitor.log_database_query(
        query_type="hr_tickets_user_list",
        execution_time_ms=query_time_ms,
        rows_affected=len(tickets)
    )
    
    return HRTicketListResponse(
        tickets=[HRTicketResponse.model_validate(ticket) for ticket in tickets],
        total_count=len(tickets)
    )


@router.get("/{ticket_id}", response_model=HRTicketResponse)
def get_ticket_details(
    ticket_id: str,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get details of a specific ticket"""
    start_time = time.time()  # ADD THIS
    
    ticket = get_ticket_by_id(db, ticket_id, current_user["company"])
    
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    
    # Users can only see their own tickets
    if current_user["role"] == "user" and ticket.email != current_user["email"]:
        raise HTTPException(status_code=403, detail="Not authorized to view this ticket")
    
    # Log query performance
    query_time_ms = (time.time() - start_time) * 1000
    production_monitor.log_database_query(
        query_type="hr_ticket_detail",
        execution_time_ms=query_time_ms
    )
    
    return HRTicketResponse.model_validate(ticket)


@router.delete("/{ticket_id}", response_model=SimpleResponse)
def cancel_ticket(
    ticket_id: str,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Cancel a ticket (only if pending or scheduled)"""
    start_time = time.time()  # ADD THIS
    
    ticket = get_ticket_by_id(db, ticket_id, current_user["company"])
    
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    
    # Only ticket owner can cancel
    if ticket.email != current_user["email"]:
        raise HTTPException(status_code=403, detail="Not authorized to cancel this ticket")
    
    # Can only cancel pending or scheduled tickets
    if ticket.status not in [TicketStatus.PENDING, TicketStatus.SCHEDULED]:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel ticket with status: {ticket.status}"
        )
    
    # Mark as closed
    ticket.status = TicketStatus.CLOSED
    ticket.resolved_at = datetime.utcnow()
    ticket.resolution_notes = "Cancelled by user"
    
    db.commit()
    
    # Log cancellation metric
    execution_time_ms = (time.time() - start_time) * 1000
    if production_monitor.run:
        production_monitor.run.log({
            "business/hr_ticket_cancelled": 1,
            "business/company": current_user["company"],
            "business/cancellation_time_ms": execution_time_ms,
            "timestamp": time.time()
        })
    
    return SimpleResponse(
        message="Ticket cancelled successfully",
        ticket_id=ticket_id
    )


# ==========================================
# ADMIN ENDPOINTS
# ==========================================

@router.get("/admin/queue", response_model=HRTicketListResponse)
def get_ticket_queue(
    status_filter: Optional[TicketStatus] = Query(None),
    category_filter: Optional[TicketCategory] = Query(None),
    urgency_filter: Optional[Urgency] = Query(None),
    sort_by: str = Query("created_at", pattern="^(created_at|urgency|category)$"),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get ticket queue for admin dashboard.
    Supports filtering and sorting.
    """
    # Only company admins can access
    if current_user["role"] != "company_admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    start_time = time.time()  # ADD THIS
    
    # Build query
    query = db.query(HRTicket).filter(HRTicket.company == current_user["company"])
    
    # Apply filters
    if status_filter:
        query = query.filter(HRTicket.status == status_filter)
    
    if category_filter:
        query = query.filter(HRTicket.category == category_filter)
    
    if urgency_filter:
        query = query.filter(HRTicket.urgency == urgency_filter)
    
    # Apply sorting
    if sort_by == "urgency":
        # Urgent first, then by created_at
        query = query.order_by(HRTicket.urgency.desc(), HRTicket.created_at.asc())
    elif sort_by == "category":
        query = query.order_by(HRTicket.category, HRTicket.created_at.asc())
    else:  # created_at (default)
        query = query.order_by(HRTicket.created_at.asc())
    
    tickets = query.all()
    
    # Log admin queue query
    query_time_ms = (time.time() - start_time) * 1000
    production_monitor.log_database_query(
        query_type="admin_hr_ticket_queue",
        execution_time_ms=query_time_ms,
        rows_affected=len(tickets)
    )
    
    return HRTicketListResponse(
        tickets=[HRTicketResponse.model_validate(ticket) for ticket in tickets],
        total_count=len(tickets)
    )


@router.post("/admin/pick-ticket", response_model=SimpleResponse)
def pick_ticket(
    request: PickTicketRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Admin picks up a ticket (assigns to themselves)"""
    if current_user["role"] != "company_admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    start_time = time.time()  # ADD THIS
    
    ticket = get_ticket_by_id(db, request.ticket_id, current_user["company"])
    
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    
    if ticket.status != TicketStatus.PENDING:
        raise HTTPException(
            status_code=400,
            detail=f"Can only pick up pending tickets. Current status: {ticket.status}"
        )
    
    # Assign to admin
    ticket.assigned_to = current_user["email"]
    ticket.status = TicketStatus.IN_PROGRESS
    ticket.picked_up_at = datetime.utcnow()
    
    db.commit()
    
    # Log ticket pickup
    execution_time_ms = (time.time() - start_time) * 1000
    if production_monitor.run:
        production_monitor.run.log({
            "business/hr_ticket_picked_up": 1,
            "business/company": current_user["company"],
            "business/pickup_time_ms": execution_time_ms,
            "timestamp": time.time()
        })
    
    return SimpleResponse(
        message="Ticket assigned to you",
        ticket_id=request.ticket_id
    )


@router.post("/admin/schedule-meeting", response_model=SimpleResponse)
def schedule_meeting(
    request: ScheduleMeetingRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Admin schedules a meeting for a ticket"""
    if current_user["role"] != "company_admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    start_time = time.time()  # ADD THIS
    
    ticket = get_ticket_by_id(db, request.ticket_id, current_user["company"])
    
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    
    # Update ticket
    ticket.scheduled_datetime = request.scheduled_datetime
    ticket.meeting_link = request.meeting_link
    ticket.meeting_location = request.meeting_location
    ticket.status = TicketStatus.SCHEDULED
    
    if request.admin_notes:
        if ticket.admin_notes:
            ticket.admin_notes += f"\n\n[{datetime.utcnow().strftime('%Y-%m-%d %H:%M')}] {request.admin_notes}"
        else:
            ticket.admin_notes = request.admin_notes
    
    # Assign to admin if not already assigned
    if not ticket.assigned_to:
        ticket.assigned_to = current_user["email"]
    
    db.commit()
    
    # Log meeting scheduled
    execution_time_ms = (time.time() - start_time) * 1000
    if production_monitor.run:
        production_monitor.run.log({
            "business/hr_meeting_scheduled": 1,
            "business/company": current_user["company"],
            "business/schedule_time_ms": execution_time_ms,
            "timestamp": time.time()
        })
    
    return SimpleResponse(
        message="Meeting scheduled successfully",
        ticket_id=request.ticket_id
    )


@router.post("/admin/resolve", response_model=SimpleResponse)
def resolve_ticket(
    request: ResolveTicketRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Admin resolves or closes a ticket"""
    if current_user["role"] != "company_admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    start_time = time.time()  # ADD THIS
    
    ticket = get_ticket_by_id(db, request.ticket_id, current_user["company"])
    
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    
    # Calculate resolution time if ticket was created
    resolution_time_hours = None
    if ticket.created_at:
        resolution_time_hours = (datetime.utcnow() - ticket.created_at).total_seconds() / 3600
    
    # Update ticket
    ticket.status = request.status
    ticket.resolved_at = datetime.utcnow()
    ticket.resolution_notes = request.resolution_notes
    
    # Assign to admin if not already assigned
    if not ticket.assigned_to:
        ticket.assigned_to = current_user["email"]
    
    db.commit()
    
    # Log ticket resolution
    execution_time_ms = (time.time() - start_time) * 1000
    metrics = {
        "business/hr_ticket_resolved": 1,
        "business/company": current_user["company"],
        "business/resolution_action_time_ms": execution_time_ms,
        "timestamp": time.time()
    }
    
    if resolution_time_hours:
        metrics["business/hr_ticket_resolution_time_hours"] = resolution_time_hours
    
    if production_monitor.run:
        production_monitor.run.log(metrics)
    
    return SimpleResponse(
        message=f"Ticket {request.status.value} successfully",
        ticket_id=request.ticket_id
    )


@router.post("/admin/add-note", response_model=SimpleResponse)
def add_note(
    request: AddNoteRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Admin adds a note to a ticket"""
    if current_user["role"] != "company_admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    ticket = get_ticket_by_id(db, request.ticket_id, current_user["company"])
    
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    
    # Add timestamped note
    timestamp = datetime.utcnow().strftime('%Y-%m-%d %H:%M')
    new_note = f"[{timestamp}] {request.note}"
    
    if ticket.admin_notes:
        ticket.admin_notes += f"\n\n{new_note}"
    else:
        ticket.admin_notes = new_note
    
    db.commit()
    
    return SimpleResponse(
        message="Note added successfully",
        ticket_id=request.ticket_id
    )


@router.get("/admin/stats", response_model=TicketStatsResponse)
def get_admin_stats(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get statistics for admin dashboard"""
    if current_user["role"] != "company_admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    start_time = time.time()  # ADD THIS
    
    stats = get_ticket_stats(db, current_user["company"])
    
    # Log stats query
    query_time_ms = (time.time() - start_time) * 1000
    production_monitor.log_database_query(
        query_type="hr_ticket_stats",
        execution_time_ms=query_time_ms
    )
    
    return TicketStatsResponse(**stats)