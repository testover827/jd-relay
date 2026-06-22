"""Admin API — Web 管理面板后端

Endpoints:
- GET  /api/admin/orders       — 工单列表（支持过滤分页）
- GET  /api/admin/orders/{id}  — 工单详情（含审批和构建记录）
- GET  /api/admin/agents       — Agent 在线状态列表
- GET  /api/admin/stats        — 仪表盘统计数据
"""

import datetime
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request, Depends
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..database import get_db_session
from ..models import WorkOrder, Agent, Approval, BuildResult

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin"])


# ── Pydantic Schemas ──────────────────────────────────────────────

class WorkOrderSummary(BaseModel):
    id: int
    order_no: str
    issue: str
    project: str
    branch: str
    state: str
    created_at: str
    created_by: str

    model_config = {"from_attributes": True}


class ApprovalSummary(BaseModel):
    id: int
    approver: str
    status: str
    review_type: str
    created_at: str
    responded_at: Optional[str] = None

    model_config = {"from_attributes": True}


class BuildResultSummary(BaseModel):
    id: int
    build_number: Optional[int] = None
    status: str
    log_url: str
    duration_seconds: Optional[int] = None
    finished_at: Optional[str] = None

    model_config = {"from_attributes": True}


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

    model_config = {"from_attributes": True}


class AgentSummary(BaseModel):
    agent_id: str
    projects: list[str]
    is_online: bool
    connected_at: Optional[str] = None
    last_seen_at: Optional[str] = None

    model_config = {"from_attributes": True}


class StatsResponse(BaseModel):
    total_orders: int
    pending_approval: int
    building: int
    success: int
    failed: int
    online_agents: int


# ── Helpers ───────────────────────────────────────────────────────

def _fmt_dt(dt: Optional[datetime.datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.isoformat()


def _order_to_summary(o: WorkOrder) -> WorkOrderSummary:
    return WorkOrderSummary(
        id=o.id,
        order_no=o.order_no,
        issue=o.issue,
        project=o.project,
        branch=o.branch,
        state=o.state,
        created_at=_fmt_dt(o.created_at) or "",
        created_by=o.created_by,
    )


# ── Routes ────────────────────────────────────────────────────────

@router.get("/orders", response_model=list[WorkOrderSummary])
async def list_orders(
    state: Optional[str] = Query(None, description="按状态过滤"),
    project: Optional[str] = Query(None, description="按项目过滤"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_session),
):
    """获取工单列表，支持按状态/项目过滤和分页"""
    stmt = select(WorkOrder).order_by(WorkOrder.created_at.desc())
    if state:
        stmt = stmt.where(WorkOrder.state == state.upper())
    if project:
        stmt = stmt.where(WorkOrder.project == project)
    stmt = stmt.limit(limit).offset(offset)

    result = await db.execute(stmt)
    orders = result.scalars().all()
    return [_order_to_summary(o) for o in orders]


@router.get("/orders/{order_id}", response_model=WorkOrderDetail)
async def get_order(
    order_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    """获取工单详情，包含所有审批记录和构建结果"""
    stmt = (
        select(WorkOrder)
        .where(WorkOrder.id == order_id)
        .options(
            selectinload(WorkOrder.approvals),
            selectinload(WorkOrder.build_results),
        )
    )
    result = await db.execute(stmt)
    order = result.scalar_one_or_none()

    if not order:
        raise HTTPException(404, f"Work order {order_id} not found")

    return WorkOrderDetail(
        id=order.id,
        order_no=order.order_no,
        issue=order.issue,
        project=order.project,
        branch=order.branch,
        build_cmd=order.build_cmd,
        state=order.state,
        created_at=_fmt_dt(order.created_at) or "",
        updated_at=_fmt_dt(order.updated_at) or "",
        created_by=order.created_by,
        approvals=[
            ApprovalSummary(
                id=a.id,
                approver=a.approver,
                status=a.status,
                review_type=a.review_type,
                created_at=_fmt_dt(a.created_at) or "",
                responded_at=_fmt_dt(a.responded_at),
            )
            for a in order.approvals
        ],
        build_results=[
            BuildResultSummary(
                id=b.id,
                build_number=b.build_number,
                status=b.status,
                log_url=b.log_url,
                duration_seconds=b.duration_seconds,
                finished_at=_fmt_dt(b.finished_at),
            )
            for b in order.build_results
        ],
    )


@router.get("/agents", response_model=list[AgentSummary])
async def list_agents(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """获取所有 Agent 状态（结合内存 AgentManager + 数据库注册信息）"""
    import json as _json

    # 从内存 manager 获取实时在线状态
    manager = getattr(request.app.state, "agent_manager", None)
    online_ids: set[str] = set()
    online_projects: dict[str, list[str]] = {}

    if manager is not None:
        agents_live = await manager.list_agents()
        for a in agents_live:
            online_ids.add(a.get("agent_id", ""))
            online_projects[a.get("agent_id", "")] = a.get("projects", [])

    # 数据库中的注册信息
    stmt = select(Agent).order_by(Agent.created_at.desc())
    result = await db.execute(stmt)
    db_agents = result.scalars().all()

    summaries = []
    seen: set[str] = set()

    for a in db_agents:
        seen.add(a.agent_id)
        try:
            projects = _json.loads(a.projects)
        except Exception:
            projects = []

        is_online = a.agent_id in online_ids
        summaries.append(AgentSummary(
            agent_id=a.agent_id,
            projects=online_projects.get(a.agent_id, projects),
            is_online=is_online,
            connected_at=_fmt_dt(a.connected_at) if is_online else None,
            last_seen_at=_fmt_dt(a.last_seen_at),
        ))

    # 追加内存中在线但数据库未注册的 Agent（临时）
    for agent_id in online_ids:
        if agent_id not in seen:
            summaries.insert(0, AgentSummary(
                agent_id=agent_id,
                projects=online_projects.get(agent_id, []),
                is_online=True,
            ))

    return summaries


@router.get("/stats", response_model=StatsResponse)
async def get_stats(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """获取仪表盘统计数据"""
    # 工单各状态计数
    stmt = select(WorkOrder.state, func.count(WorkOrder.id)).group_by(WorkOrder.state)
    result = await db.execute(stmt)
    state_counts: dict[str, int] = {row[0]: row[1] for row in result.all()}

    total = sum(state_counts.values())
    pending = state_counts.get("PENDING_APPROVAL", 0) + state_counts.get("PENDING_SECOND_REVIEW", 0)
    building = state_counts.get("BUILDING", 0)
    success = state_counts.get("SUCCESS", 0)
    failed = state_counts.get("FAILED", 0)

    # 在线 Agent 数量
    manager = getattr(request.app.state, "agent_manager", None)
    online_agents = 0
    if manager is not None:
        online_agents = await manager.count()

    return StatsResponse(
        total_orders=total,
        pending_approval=pending,
        building=building,
        success=success,
        failed=failed,
        online_agents=online_agents,
    )
