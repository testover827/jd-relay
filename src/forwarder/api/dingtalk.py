"""DingTalk callback & API integration — Phase 3.1.

Endpoints:
- POST /api/dingtalk/callback — 钉钉审批回调接收
- POST /api/dingtalk/card-callback — 钉钉卡片回调
- POST /api/approvals/create — 发起审批
"""

import json
import hmac
import hashlib
import base64
import time
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dingtalk", tags=["dingtalk"])


# ── Pydantic models ──────────────────────────────────────────────

class DingTalkCallback(BaseModel):
    """DingTalk approval callback payload."""
    process_instance_id: str = ""
    business_id: str = ""
    type: str = ""  # "START", "FINISH", "TERMINATE"
    result: str = ""  # "agree" / "refuse"
    remark: str = ""


class ApprovalCreateRequest(BaseModel):
    """Request to create a new approval."""
    work_order_id: str
    issue: str
    project: str
    branch: str
    build_cmd: str = ""


# ── Routes ───────────────────────────────────────────────────────

@router.post("/callback")
async def dingtalk_callback(request: Request):
    """DingTalk approval callback endpoint.

    DingTalk sends approval status changes (START/FINISH/TERMINATE) here.
    Validates the DingTalk signature before processing.

    TODO: Full signature validation and state machine integration.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON body")

    # Validate DingTalk signature
    timestamp = request.headers.get("timestamp", "")
    sign = request.headers.get("sign", "")
    app_secret = request.app.state.config.get("DINGTALK_APP_SECRET", "") if hasattr(request.app.state, 'config') else ""

    if app_secret:
        if not _verify_dingtalk_sign(timestamp, app_secret, sign):
            raise HTTPException(401, "DingTalk signature verification failed")

    logger.info(f"[DingTalk] Callback: type={body.get('type')}, "
                f"result={body.get('result')}, biz_id={body.get('business_id')}")

    return {"status": "ok", "message": "Callback received"}


@router.post("/card-callback")
async def dingtalk_card_callback(request: Request):
    """DingTalk card (卡片) callback — approval status update push."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON body")

    logger.info(f"[DingTalk] Card callback: {json.dumps(body, ensure_ascii=False)[:200]}")
    return {"status": "ok"}


@router.post("/create-approval")
async def create_approval(req: ApprovalCreateRequest):
    """Initiate a DingTalk approval process for a work order.

    Calls DingTalk API to create an approval instance with three reviewers.

    TODO: Call actual DingTalk API.
    """
    logger.info(f"[DingTalk] Creating approval for {req.work_order_id}")
    # TODO: Call DingTalk process instance create API
    return {
        "status": "ok",
        "process_instance_id": f"mock-{req.work_order_id}",
        "message": "Approval initiated (mock)",
    }


# ── Helpers ──────────────────────────────────────────────────────

def _verify_dingtalk_sign(timestamp: str, app_secret: str, sign: str) -> bool:
    """Verify DingTalk callback signature.

    DingTalk signature: base64(hmac_sha256(app_secret, timestamp))
    """
    if not timestamp or not sign:
        return False
    try:
        message = (timestamp + "\n" + app_secret).encode("utf-8")
        expected = base64.b64encode(
            hmac.new(app_secret.encode("utf-8"), message, hashlib.sha256).digest()
        ).decode("utf-8")
        return hmac.compare_digest(expected, sign)
    except Exception:
        return False
