"""核心转发编排服务

实现两个核心数据流的业务逻辑：
  流程1: 钉钉审批通过 → 触发 Jenkins Job
  流程2: Jenkins 发起审批 → 钉钉审批 → 回调 Jenkins 继续构建
"""

import json
import logging
import re
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from .crypto_service import CryptoService, SecurityError
from .dingtalk_service import DingTalkService, DingTalkError
from .jenkins_service import JenkinsService, JenkinsError
from ..models import Approval, Build, Log

logger = logging.getLogger(__name__)

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


def _now():
    return datetime.now(timezone.utc)


def _escape_markdown(text: str) -> str:
    """转义 Markdown 特殊字符，防止注入"""
    escape_chars = r"\`*_{}[]()#+-.!|~"
    for char in escape_chars:
        text = text.replace(char, f"\\{char}")
    return text


class RelayService:
    """核心转发编排"""

    def __init__(
        self,
        db: AsyncSession,
        dingtalk: DingTalkService,
        jenkins: JenkinsService,
        crypto: CryptoService,
    ):
        self.db = db
        self.dingtalk = dingtalk
        self.jenkins = jenkins
        self.crypto = crypto

    # ═══════════════════════════════════════════
    # 流程1: 钉钉审批 → Jenkins
    # ═══════════════════════════════════════════

    async def handle_dingtalk_callback(self, callback_data: dict) -> dict:
        """处理钉钉审批结果回调

        Args:
            callback_data: 包含 processInstanceId, result(agree/refuse), staffId 等

        Returns:
            钉钉期望的错误码格式 {"errcode": 0, "errmsg": "ok"}
        """
        process_instance_id = callback_data.get("processInstanceId", "")
        result = callback_data.get("result", "")  # agree / refuse
        approver_id = callback_data.get("staffId", "")

        if not process_instance_id:
            return {"errcode": 1, "errmsg": "缺少 processInstanceId"}

        # 查找对应的审批记录
        stmt = select(Approval).where(
            Approval.dingtalk_process_instance_id == process_instance_id
        )
        result_set = await self.db.execute(stmt)
        approval = result_set.scalar_one_or_none()

        if not approval:
            logger.warning("未找到审批记录: %s", process_instance_id)
            return {"errcode": 0, "errmsg": "approval not found"}

        # 更新审批状态
        if result == "agree":
            approval.status = "approved"
            approval.approved_by = approver_id
            approval.approved_at = _now()
            await self._log("relay", "approval_approved",
                           f"审批通过: {approval.title} by {approver_id}")
        elif result == "refuse":
            approval.status = "rejected"
            approval.reject_reason = "审批人拒绝"
            await self._log("relay", "approval_rejected",
                           f"审批被拒: {approval.title}")
        else:
            approval.status = "cancelled"
            await self._log("relay", "approval_cancelled",
                           f"审批取消: {approval.title}")

        approval.updated_at = _now()

        try:
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise

        # 如果审批通过，触发 Jenkins Job
        if approval.status == "approved":
            if approval.type == "dingtalk_to_jenkins":
                await self._trigger_jenkins_from_approval(approval)

        return {"errcode": 0, "errmsg": "ok"}

    async def _trigger_jenkins_from_approval(self, approval: Approval):
        """从审批记录中解密参数并触发 Jenkins Job"""
        try:
            # 解密回调 payload（使用无签名模式，因为此数据由 API Key 保护）
            if approval.callback_payload_encrypted and approval.callback_nonce:
                try:
                    payload = self.crypto.decrypt_json_without_sig(
                        approval.callback_payload_encrypted,
                        approval.callback_nonce,
                    )
                except SecurityError as e:
                    logger.error("解密审批 payload 失败（安全异常）: %s", e)
                    await self._log("jenkins", "decrypt_failed",
                                   f"解密失败: SecurityError", level="ERROR")
                    return
            else:
                payload = {}

            job_name = payload.get("job_name") or approval.jenkins_job_name
            raw_parameters = payload.get("parameters", {})

            # 不修改原始字典，创建副本添加额外字段
            parameters = {**raw_parameters, "APPROVED_BY": approval.approved_by or "dingtalk"}

            # 触发构建
            result = await self.jenkins.build_job(job_name, parameters)

            # 创建构建记录
            build = Build(
                jenkins_queue_id=result.get("queue_id"),
                job_name=job_name,
                approval_id=approval.id,
                params_encrypted=approval.callback_payload_encrypted,
                status="queued",
                triggered_at=_now(),
            )
            self.db.add(build)
            await self.db.commit()
            await self.db.refresh(build)

            approval.jenkins_build_id = build.id
            try:
                await self.db.commit()
            except Exception:
                await self.db.rollback()
                raise

            await self._log("jenkins", "job_triggered",
                           f"Job 已触发: {job_name}, queue_id={result.get('queue_id')}")

        except JenkinsError as e:
            logger.error("触发 Jenkins Job 失败: %s", e)
            await self._log("jenkins", "job_trigger_failed",
                           f"触发失败: {e}", level="ERROR")
            # 不向上抛出——钉钉回调必须返回成功，避免钉钉重试
        except Exception:
            logger.exception("触发 Jenkins Job 出现未知异常")
            await self._log("jenkins", "job_trigger_unknown_error",
                           "触发出现未知异常", level="ERROR")

    # ═══════════════════════════════════════════
    # 流程2: Jenkins → 钉钉审批 → Jenkins
    # ═══════════════════════════════════════════

    async def handle_jenkins_approval_request(self, request_data: dict) -> dict:
        """Jenkins 发起审批请求

        Args:
            request_data: 包含 jenkins_job_name, build_id, title, content,
                         approver_user_ids, encrypted_payload, signature 等

        Returns:
            包含 approval_id 的字典

        Raises:
            ValueError: 签名验证失败或解密失败
            DingTalkError: 钉钉 API 调用失败
        """
        # 验证 HMAC 签名
        encrypted_payload = request_data.get("encrypted_payload", "")
        signature = request_data.get("signature", "")

        if not self.crypto._verify(encrypted_payload, signature):
            raise ValueError("HMAC 签名验证失败")

        # 解密回调 payload
        try:
            callback_data = self.crypto.decrypt_json(
                encrypted_payload,
                request_data.get("nonce", ""),
                signature,
            )
        except SecurityError as e:
            raise ValueError(f"解密回调 payload 失败: {e}")

        # 存储加密的回调数据
        encrypted_storage = self.crypto.encrypt_json(callback_data)

        # 创建审批记录
        approval = Approval(
            type="jenkins_to_dingtalk",
            title=request_data["title"],
            content=request_data.get("content", ""),
            approver_user_ids=json.dumps(request_data.get("approver_user_ids", [])),
            jenkins_job_name=request_data["jenkins_job_name"],
            jenkins_build_id=None,
            callback_payload_encrypted=encrypted_storage["ciphertext"],
            callback_nonce=encrypted_storage["nonce"],
            status="pending",
        )
        self.db.add(approval)

        # 创建构建记录（pending 状态）
        build = Build(
            jenkins_build_id=request_data.get("build_id"),
            job_name=request_data["jenkins_job_name"],
            status="pending",
        )

        # 合并为单次事务提交
        self.db.add(build)
        await self.db.flush()
        approval.jenkins_build_id = build.id
        build.approval_id = approval.id
        await self.db.commit()
        await self.db.refresh(approval)
        await self.db.refresh(build)

        # 向钉钉发起审批
        try:
            process_instance_id = await self.dingtalk.create_process_instance(
                originator_user_id=request_data.get("originator_user_id", ""),
                process_code=request_data.get("process_code", ""),
                title=request_data["title"],
                form_component_values=[
                    {"name": "审批内容", "value": request_data.get("content", "")},
                    {"name": "Jenkins Job", "value": request_data["jenkins_job_name"]},
                    {"name": "Build ID", "value": str(request_data.get("build_id", ""))},
                ],
                approvers=request_data.get("approver_user_ids", []),
            )
            approval.dingtalk_process_instance_id = process_instance_id
            await self.db.commit()

            await self._log("dingtalk", "approval_sent",
                           f"审批已发起: {approval.title}, instance={process_instance_id}")
        except DingTalkError as e:
            logger.error("发起钉钉审批失败: %s", e)
            approval.status = "failed"
            await self.db.commit()
            await self._log("dingtalk", "approval_failed",
                           f"发起审批失败: {e}", level="ERROR")
            raise

        return {
            "approval_id": approval.id,
            "process_instance_id": approval.dingtalk_process_instance_id,
            "status": approval.status,
        }

    async def check_approval_status(self, approval_id: str) -> dict:
        """查询审批状态（供 CLI 轮询使用）

        Args:
            approval_id: 审批记录 UUID

        Returns:
            状态信息字典
        """
        stmt = select(Approval).where(Approval.id == approval_id)
        result = await self.db.execute(stmt)
        approval = result.scalar_one_or_none()

        if not approval:
            return {"approval_id": approval_id, "status": "not_found"}

        return {
            "approval_id": approval.id,
            "status": approval.status,
            "approved_by": approval.approved_by,
            "reject_reason": approval.reject_reason,
            "updated_at": approval.updated_at.isoformat() if approval.updated_at else None,
        }

    # ═══════════════════════════════════════════
    # Jenkins 构建结果回调
    # ═══════════════════════════════════════════

    async def handle_jenkins_callback(self, callback_data: dict) -> dict:
        """Jenkins 构建结果回调

        Args:
            callback_data: 包含 job_name, build_id, result, duration_ms,
                          output_summary, related_approval_id 等

        Returns:
            操作结果字典
        """
        # 安全校验必填字段
        job_name = callback_data.get("job_name")
        if not job_name:
            raise ValueError("callback_data 缺少 job_name")

        result_val = callback_data.get("result", "")
        related_approval_id = callback_data.get("related_approval_id")

        # 查找构建记录
        if related_approval_id:
            stmt = select(Build).where(Build.approval_id == related_approval_id)
        else:
            build_id = callback_data.get("build_id")
            stmt = select(Build).where(
                Build.jenkins_build_id == build_id,
                Build.job_name == job_name,
            )
        db_result = await self.db.execute(stmt)
        build = db_result.scalar_one_or_none()

        if not build:
            # 创建新记录
            build = Build(
                jenkins_build_id=callback_data.get("build_id"),
                job_name=job_name,
                approval_id=related_approval_id,
                status="completed",
            )
            self.db.add(build)
            await self.db.commit()
            await self.db.refresh(build)

        # 更新构建记录
        build.status = "success" if result_val == "SUCCESS" else "failure"
        if result_val == "ABORTED":
            build.status = "aborted"
        build.result = result_val
        build.duration_ms = callback_data.get("duration_ms")
        output_raw = callback_data.get("output_summary", "")
        # Markdown 转义防止注入
        build.output_summary = _escape_markdown(output_raw) if output_raw else output_raw
        build.finished_at = _now()
        build.updated_at = _now()

        try:
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            raise

        await self._log("jenkins", "build_completed",
                       f"构建完成: {job_name}#{callback_data.get('build_id')} → {result_val}")

        # 通知钉钉构建结果
        notify_dingtalk = True
        if notify_dingtalk and build.approval_id:
            stmt2 = select(Approval).where(Approval.id == build.approval_id)
            result2 = await self.db.execute(stmt2)
            approval = result2.scalar_one_or_none()
            if approval and approval.approver_user_ids:
                try:
                    user_ids = json.loads(approval.approver_user_ids)
                except (TypeError, json.JSONDecodeError):
                    user_ids = []
                status_emoji = "\u2705" if result_val == "SUCCESS" else "\u274c"
                # 对用户输入做 Markdown 转义
                safe_title = _escape_markdown(callback_data.get("job_name", ""))
                safe_output = _escape_markdown(output_raw[:500]) if output_raw else ""
                await self.dingtalk.send_work_notification(
                    user_ids=user_ids,
                    title=f"构建结果: {safe_title}",
                    content=f"{status_emoji} **{safe_title}** "
                           f"构建{result_val}\n\n"
                           f"{safe_output}",
                )

        return {"ok": True, "notify_dingtalk": notify_dingtalk}

    # ═══════════════════════════════════════════
    # 辅助方法
    # ═══════════════════════════════════════════

    async def _log(
        self,
        source: str,
        action: str,
        detail: str,
        level: LogLevel = "INFO",
    ):
        """写入日志到数据库（同时输出到 logger）"""
        log_method = getattr(logger, level.lower(), None)
        if log_method is None:
            log_method = logger.info
        log_method(detail)

        try:
            log_entry = Log(
                source=source,
                action=action,
                detail=detail,
                level=level,
            )
            self.db.add(log_entry)
            await self.db.commit()
        except Exception:
            logger.warning("写入数据库日志失败: %s", action, exc_info=True)
