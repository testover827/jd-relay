"""DingTalk callback & API — Phase 3.1 (官方 SDK)

Endpoints:
- POST /api/dingtalk/callback       — 钉钉审批回调（驱动状态机）
- POST /api/dingtalk/card-callback  — 钉钉卡片回调
- POST /api/dingtalk/create-approval — 发起审批（内部调用）
"""

import json
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db_session
from ..models import WorkOrder, Approval
from ..state import WorkOrderState, transition, StateError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dingtalk", tags=["dingtalk"])


# ── Pydantic models ───────────────────────────────────────────────

class ApprovalCreateRequest(BaseModel):
    work_order_id: str
    issue: str
    project: str
    branch: str
    build_cmd: str = ""


# ── Helper: get DingTalk service from app state ───────────────────

def _get_dt_service(request: Request):
    """从 app.state 获取 DingTalkService 实例（在 ForwarderServer 中注入）"""
    svc = getattr(request.app.state, "dingtalk_service", None)
    if svc is None:
        raise HTTPException(503, "DingTalk service not initialized")
    return svc


# ── Routes ────────────────────────────────────────────────────────

@router.post("/callback")
async def dingtalk_callback(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """钉钉审批回调接收端点

    钉钉将审批状态变更（FINISH/TERMINATE）推送到此处。
    验证签名后，驱动工单状态机向前推进。
    """
    # 1. 读取原始 body
    try:
        body_bytes = await request.body()
        body = json.loads(body_bytes)
    except Exception:
        raise HTTPException(400, "Invalid JSON body")

    # 2. 签名验证
    timestamp = request.headers.get("timestamp", "")
    nonce = request.headers.get("nonce", "")
    sign = request.headers.get("sign", "")

    dt_svc = getattr(request.app.state, "dingtalk_service", None)
    if dt_svc is not None:
        if not dt_svc.verify_signature(timestamp, nonce, sign):
            raise HTTPException(401, "DingTalk signature verification failed")

    event_type = body.get("type", "")
    result = body.get("result", "")
    process_instance_id = body.get("processInstanceId", "")
    biz_id = body.get("businessId", "")

    logger.info(
        "[DingTalk] 回调: type=%s result=%s instanceId=%s bizId=%s",
        event_type, result, process_instance_id, biz_id
    )

    # 3. 只处理 FINISH 事件（审批完成）
    if event_type == "FINISH" and biz_id:
        await _handle_approval_finish(db, biz_id, result, process_instance_id)

    return {"result": "success"}


async def _handle_approval_finish(
    db: AsyncSession,
    biz_id: str,
    result: str,  # "agree" | "refuse"
    instance_id: str,
):
    """处理审批完成事件 → 驱动工单状态机"""
    from sqlalchemy import select

    # 查找工单（biz_id 即 order_no）
    stmt = select(WorkOrder).where(WorkOrder.order_no == biz_id)
    row = await db.execute(stmt)
    work_order = row.scalar_one_or_none()

    if not work_order:
        logger.warning("[DingTalk] 找不到工单 biz_id=%s", biz_id)
        return

    # 查找最新 PENDING 审批记录，更新状态
    stmt2 = (
        select(Approval)
        .where(
            Approval.work_order_id == work_order.id,
            Approval.status == "PENDING",
        )
        .order_by(Approval.created_at.desc())
    )
    row2 = await db.execute(stmt2)
    approval = row2.scalar_one_or_none()

    if approval:
        approval.status = "APPROVED" if result == "agree" else "REJECTED"
        import datetime
        approval.responded_at = datetime.datetime.utcnow()

    # 驱动状态机
    current_state = WorkOrderState(work_order.state)
    review_type = approval.review_type if approval else "first"

    try:
        if result == "agree":
            if review_type == "second":
                new_state = transition(current_state, WorkOrderState.SECOND_APPROVED)
            else:
                new_state = transition(current_state, WorkOrderState.APPROVED)
        else:
            if review_type == "second":
                new_state = transition(current_state, WorkOrderState.SECOND_REJECTED)
            else:
                new_state = transition(current_state, WorkOrderState.CLOSED)

        work_order.state = new_state.value
        await db.commit()
        logger.info(
            "[DingTalk] 工单 %s 状态 %s → %s",
            work_order.order_no, current_state.value, new_state.value
        )
    except StateError as e:
        logger.error("[DingTalk] 状态转换失败: %s", e)
        await db.rollback()


@router.post("/card-callback")
async def dingtalk_card_callback(request: Request):
    """钉钉卡片回调（审批状态变化推送）"""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON body")

    logger.info(
        "[DingTalk] 卡片回调: %s",
        json.dumps(body, ensure_ascii=False)[:200]
    )
    return {"result": "success"}


@router.post("/create-approval")
async def create_approval(
    req: ApprovalCreateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """为工单发起钉钉 OA 审批（三人会签）

    从 app.state 读取 config 中的审批人和 process_code 配置。
    """
    dt_svc = _get_dt_service(request)
    config = getattr(request.app.state, "config", None)

    if not config:
        raise HTTPException(503, "Forwarder config not available")

    approvers = getattr(config, "dingtalk_approvers", [])
    process_code = getattr(config, "dingtalk_process_code", "")

    if not approvers or not process_code:
        raise HTTPException(503, "DingTalk approvers or process_code not configured")

    form_values = [
        {"name": "工单编号", "value": req.work_order_id},
        {"name": "ISSUE", "value": req.issue},
        {"name": "项目", "value": req.project},
        {"name": "分支", "value": req.branch},
        {"name": "构建命令", "value": req.build_cmd},
    ]
    title = f"[JD-Relay] {req.issue} — {req.project}/{req.branch}"

    try:
        instance_id = await dt_svc.create_approval(
            originator_user_id=getattr(config, "dingtalk_originator", ""),
            process_code=process_code,
            title=title,
            form_values=form_values,
            approvers=approvers,
        )
    except Exception as e:
        logger.error("[DingTalk] 创建审批失败: %s", e)
        raise HTTPException(500, f"DingTalk create approval error: {e}")

    return {
        "status": "ok",
        "process_instance_id": instance_id,
        "message": "Approval initiated",
    }
