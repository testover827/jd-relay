"""Admin API — Phase 4.5 Web management panel backend.

Endpoints:
- GET  /api/admin/orders       — List work orders (filterable)
- GET  /api/admin/orders/{id}  — Work order detail
- GET  /api/admin/agents       — Agent status list
- GET  /api/admin/stats        — Approval/build statistics
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional

router = APIRouter(prefix="/api/admin", tags=["admin"])


class WorkOrderSummary(BaseModel):
    id: int
    order_no: str
    issue: str
    project: str
    branch: str
    state: str
    created_at: str
    created_by: str


class AgentSummary(BaseModel):
    agent_id: str
    projects: list[str]
    is_online: bool
    connected_at: Optional[str] = None
    last_seen_at: Optional[str] = None


class ApprovalSummary(BaseModel):
    id: int
    approver: str
    status: str
    review_type: str
    created_at: str
    responded_at: Optional[str] = None


class BuildResultSummary(BaseModel):
    id: int
    build_number: Optional[int] = None
    status: str
    log_url: str
    duration_seconds: Optional[int] = None
    finished_at: Optional[str] = None


class WorkOrderDetail(BaseModel):
    id: int
    order_no: str
    issue: str
    project: str
    branch: str
    build_cmd: str
    state: str
    created_at: str
    updated_at: str
    created_by: str
    approvals: list[ApprovalSummary] = []
    build_results: list[BuildResultSummary] = []


class StatsResponse(BaseModel):
    total_orders: int
    pending_approval: int
    building: int
    success: int
    failed: int
    online_agents: int


# ── Routes ───────────────────────────────────────────────────────

@router.get("/orders", response_model=list[WorkOrderSummary])
async def list_orders(
    state: Optional[str] = Query(None, description="Filter by state"),
    project: Optional[str] = Query(None, description="Filter by project"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List work orders with optional filters.

    TODO: Connect to database session.
    """
    raise HTTPException(501, "Database not connected — configure MySQL")


@router.get("/orders/{order_id}", response_model=WorkOrderDetail)
async def get_order(order_id: int):
    """Get work order detail with approvals and build results."""
    raise HTTPException(501, "Database not connected — configure MySQL")


@router.get("/agents", response_model=list[AgentSummary])
async def list_agents():
    """List all registered agents and their online status."""
    raise HTTPException(501, "Agent manager not available in this context")


@router.get("/stats", response_model=StatsResponse)
async def get_stats():
    """Get dashboard statistics."""
    raise HTTPException(501, "Database not connected — configure MySQL")
