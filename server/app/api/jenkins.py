"""Jenkins 侧 API 路由

- POST /api/v1/jenkins/trigger          触发构建（钉钉审批通过后）
- POST /api/v1/jenkins/callback         Jenkins 构建结果回调
- GET  /api/v1/jenkins/build/{id}/status 查询构建状态
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from ..database import get_db
from ..config import settings
from ..models import Build, Approval
from ..schemas import (
    TriggerBuildRequest, TriggerBuildResponse,
    BuildCallbackRequest, BuildStatusResponse,
)
from ..services.crypto_service import CryptoService
from ..services.dingtalk_service import DingTalkService
from ..services.jenkins_service import JenkinsService, JenkinsError
from ..services.relay_service import RelayService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/jenkins", tags=["jenkins"])

# ── 服务单例 ──
_crypto_instance: CryptoService | None = None
_dingtalk_instance: DingTalkService | None = None
_jenkins_instance: JenkinsService | None = None


def _get_crypto() -> CryptoService:
    global _crypto_instance
    if _crypto_instance is None:
        _crypto_instance = CryptoService(settings.AES_ENCRYPTION_KEY, settings.HMAC_SECRET)
    return _crypto_instance


def _get_dingtalk() -> DingTalkService:
    global _dingtalk_instance
    if _dingtalk_instance is None:
        _dingtalk_instance = DingTalkService()
    return _dingtalk_instance


def _get_jenkins() -> JenkinsService:
    global _jenkins_instance
    if _jenkins_instance is None:
        _jenkins_instance = JenkinsService()
    return _jenkins_instance


def _get_relay(db: AsyncSession) -> RelayService:
    return RelayService(
        db=db,
        dingtalk=_get_dingtalk(),
        jenkins=_get_jenkins(),
        crypto=_get_crypto(),
    )


@router.post("/trigger", response_model=TriggerBuildResponse)
async def trigger_build(
    req: TriggerBuildRequest,
    db: AsyncSession = Depends(get_db),
):
    """触发 Jenkins Job 构建（流程1 的最后一步）
    
    由 RelayService 在钉钉审批通过后内部调用，
    也可由外部通过 API Key 直接调用。
    
    如果提供了 encrypted_payload，会先解密再合并到 parameters。
    """
    jenkins = _get_jenkins()

    # 合并加密 payload（如有）
    parameters = dict(req.parameters or {})
    if req.encrypted_payload:
        crypto = _get_crypto()
        try:
            decrypted = crypto.decrypt_json_without_sig(req.encrypted_payload)
            if isinstance(decrypted, dict):
                parameters.update(decrypted)
                logger.info(
                    "解密构建参数成功: job=%s keys=%s",
                    req.job_name, list(decrypted.keys()),
                )
        except Exception as e:
            logger.warning("解密构建参数失败: job=%s error=%s", req.job_name, e)
            raise HTTPException(status_code=400, detail=f"解密 payload 失败: {e}")

    # 调用 Jenkins 构建接口
    try:
        result = await jenkins.build_job(req.job_name, parameters)

        queue_id = result.get("queue_id")
        if queue_id is None:
            logger.error("Jenkins 未返回 queue_id: job=%s response=%s", req.job_name, result)
            raise HTTPException(status_code=502, detail="Jenkins 构建响应异常")

        return TriggerBuildResponse(
            queue_id=queue_id,
            status="queued",
            jenkins_url=f"{settings.JENKINS_URL.rstrip('/')}/job/{req.job_name}/",
        )
    except HTTPException:
        raise  # 重新抛出我们自己的 HTTPException
    except JenkinsError as e:
        logger.error("Jenkins 构建失败: job=%s error=%s", req.job_name, e)
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        logger.exception("触发构建异常: job=%s", req.job_name)
        raise HTTPException(status_code=500, detail="触发构建失败，请稍后重试")


@router.post("/callback")
async def jenkins_callback(
    req: BuildCallbackRequest,
    db: AsyncSession = Depends(get_db),
):
    """Jenkins 构建结果回调
    
    Jenkins Pipeline 构建完成后，通过 CLI 或直接 HTTP 通知转发器。
    """
    relay = _get_relay(db)
    try:
        result = await relay.handle_jenkins_callback(req.model_dump())
        return result
    except ValueError as e:
        logger.warning("Jenkins 回调参数错误: %s", e)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("处理 Jenkins 回调异常")
        raise HTTPException(status_code=500, detail="处理回调失败，请稍后重试")


@router.get("/build/{build_id}/status", response_model=BuildStatusResponse)
async def get_build_status(
    build_id: int,
    job_name: str = Query(..., description="Jenkins Job 名称"),
    db: AsyncSession = Depends(get_db),
):
    """查询 Jenkins 构建状态（合并 Jenkins 远程 + 本地数据库）"""
    jenkins = _get_jenkins()

    try:
        # 查询 Jenkins 远程状态
        jenkins_status = await jenkins.get_build_status(job_name, build_id)

        # 查询本地数据库补充信息
        stmt = select(Build).where(
            Build.jenkins_build_id == build_id,
            Build.job_name == job_name,
        )
        result = await db.execute(stmt)
        local_build = result.scalar_one_or_none()

        # ★ 修复类型混淆：progress_pct 是整数百分比，不是状态字符串
        progress_pct: int | None = None
        if local_build:
            # 根据状态估算进度
            status_map = {
                "pending": 0,
                "queued": 10,
                "building": 50,  # 构建中默认 50%
                "success": 100,
                "failure": 100,
                "aborted": 100,
            }
            progress_pct = status_map.get(local_build.status, None)

        return BuildStatusResponse(
            build_id=build_id,
            job_name=job_name,
            status=jenkins_status.get("status", "unknown"),
            progress_pct=progress_pct,
        )
    except JenkinsError as e:
        logger.error("查询 Jenkins 构建状态失败: job=%s build=%d error=%s", job_name, build_id, e)
        raise HTTPException(status_code=502, detail=f"Jenkins 查询失败: {e}")
