"""钉钉侧 API 路由

- POST /api/v1/dingtalk/callback     钉钉审批结果回调
- POST /api/v1/dingtalk/send-approval  发起钉钉审批流程

安全特性：
- 钉钉回调签名验证（timestamp + nonce + token HMAC-SHA256）
- 时间戳重放防御（5分钟窗口）
- 内部错误信息脱敏（生产环境不暴露 traceback）
"""

import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..config import settings
from ..schemas import SendApprovalRequest, SendApprovalResponse
from ..services.crypto_service import CryptoService
from ..services.dingtalk_service import DingTalkService
from ..services.jenkins_service import JenkinsService
from ..services.relay_service import RelayService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/dingtalk", tags=["dingtalk"])

# ── 服务单例（懒加载 + 可复用）──
_crypto_instance: CryptoService | None = None
_dingtalk_instance: DingTalkService | None = None
_jenkins_instance: JenkinsService | None = None


def _get_crypto() -> CryptoService:
    """获取/创建加密服务单例"""
    global _crypto_instance
    if _crypto_instance is None:
        _crypto_instance = CryptoService(settings.AES_ENCRYPTION_KEY, settings.HMAC_SECRET)
    return _crypto_instance


def _get_dingtalk() -> DingTalkService:
    """获取/创建钉钉服务单例"""
    global _dingtalk_instance
    if _dingtalk_instance is None:
        _dingtalk_instance = DingTalkService()
    return _dingtalk_instance


def _get_jenkins() -> JenkinsService:
    """获取/创建 Jenkins 服务单例"""
    global _jenkins_instance
    if _jenkins_instance is None:
        _jenkins_instance = JenkinsService()
    return _jenkins_instance


def _get_relay(db: AsyncSession) -> RelayService:
    """获取 relay 服务（每次新实例因依赖 db session）"""
    return RelayService(
        db=db,
        dingtalk=_get_dingtalk(),
        jenkins=_get_jenkins(),
        crypto=_get_crypto(),
    )


# ── 时间戳常量 ─_
_TIMESTAMP_MAX_AGE_S = 300  # 5 分钟重放窗口


@router.post("/callback")
async def dingtalk_callback(request: Request, db: AsyncSession = Depends(get_db)):
    """钉钉审批结果回调
    
    钉钉在审批完成后向此端点 POST 审批结果。
    需要在钉钉开放平台配置此回调 URL。
    
    安全流程：
    1. 读取请求体
    2. 校验 timestamp（防重放）
    3. 验证签名（HMAC-SHA256）
    4. 解析 JSON 并处理
    """
    # 1. 读取请求体
    try:
        body = await request.body()
        body_str = body.decode("utf-8")
    except Exception:
        logger.warning("钉钉回调: 无法读取请求体")
        return {"errcode": 1, "errmsg": "无法读取请求体"}

    # 2. 提取签名头
    timestamp = request.headers.get("timestamp", "")
    nonce = request.headers.get("nonce", "")
    signature = request.headers.get("signature", "")

    # 3. 时间戳重放检查（路由层二次校验）
    if timestamp:
        try:
            ts_int = int(timestamp)
            now = int(time.time())
            if abs(now - ts_int) > _TIMESTAMP_MAX_AGE_S:
                logger.warning(
                    "钉钉回调: 时间戳过期 diff=%ds", abs(now - ts_int)
                )
                return {"errcode": 1, "errmsg": "请求已过期"}
        except (ValueError, TypeError):
            logger.warning("钉钉回调: 无效的时间戳格式")
            return {"errcode": 1, "errmsg": "无效的时间戳"}

    # 4. 验证签名
    dingtalk = _get_dingtalk()
    if not dingtalk.verify_callback_signature(timestamp, nonce, signature, body_str):
        logger.warning("钉钉回调: 签名验证失败 ts=%s", timestamp)
        return {"errcode": 1, "errmsg": "签名验证失败"}

    # 5. 解析并处理
    try:
        callback_data = json.loads(body_str)
    except json.JSONDecodeError:
        logger.warning("钉钉回调: JSON 解析失败")
        return {"errcode": 1, "errmsg": "JSON 解析失败"}

    # 6. 交由 RelayService 处理
    relay = _get_relay(db)
    try:
        result = await relay.handle_dingtalk_callback(callback_data)
        return result
    except ValueError as e:
        logger.warning("钉钉回调: 业务逻辑错误: %s", e)
        return {"errcode": 1, "errmsg": str(e)}
    except Exception as e:
        logger.exception("钉钉回调: 处理异常")
        errmsg = "内部处理错误" if not settings.DEBUG else str(e)
        return {"errcode": 1, "errmsg": errmsg}


@router.post("/send-approval", response_model=SendApprovalResponse)
async def send_approval(
    req: SendApprovalRequest,
    db: AsyncSession = Depends(get_db),
):
    """Jenkins/CLI → 转发器：发起钉钉审批（流程2 入口）
    
    此端点由 Jenkins Pipeline 中的 CLI 工具调用，
    携带加密的构建回调参数。
    
    请求需携带有效的 API Key（由中间件验证）。
    """
    relay = _get_relay(db)

    try:
        result = await relay.handle_jenkins_approval_request(req.model_dump())
        return SendApprovalResponse(**result)
    except ValueError as e:
        logger.warning("发起审批: 参数错误: %s", e)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("发起审批: 内部异常")
        raise HTTPException(
            status_code=500,
            detail="发起审批失败，请稍后重试",
        )
