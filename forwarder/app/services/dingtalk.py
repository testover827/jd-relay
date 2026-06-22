"""DingTalk 服务 — 官方 alibabacloud-dingtalk SDK 封装

使用钉钉官方 SDK（alibabacloud-dingtalk）替代原 httpx 直调方案，
提供：
- OAuth2 AccessToken 获取与缓存（并发安全，提前 5 分钟刷新）
- OA 审批实例创建（StartProcessInstance）
- 审批实例查询（GetProcessInstance）
- 工作通知发送（机器人 oToMessages）
- 回调签名验证（HMAC-SHA256，fail-closed 策略）
"""

import asyncio
import time
import hmac as _hmac
import hashlib
import base64
import json
import logging
from typing import Optional

from alibabacloud_tea_openapi import models as open_api_models
from alibabacloud_tea_util import models as util_models
from alibabacloud_dingtalk.oauth2_1_0.client import Client as OAuthClient
from alibabacloud_dingtalk.oauth2_1_0 import models as oauth_models
from alibabacloud_dingtalk.workflow_1_0.client import Client as WorkflowClient
from alibabacloud_dingtalk.workflow_1_0 import models as workflow_models
from alibabacloud_dingtalk.robot_1_0.client import Client as RobotClient
from alibabacloud_dingtalk.robot_1_0 import models as robot_models

logger = logging.getLogger(__name__)


class DingTalkError(Exception):
    """钉钉 API 调用异常"""
    pass


def _make_base_config() -> open_api_models.Config:
    """创建 SDK 基础配置（无凭证，header 注入 token）"""
    config = open_api_models.Config()
    config.protocol = "https"
    config.region_id = "central"
    return config


class DingTalkService:
    """钉钉开放平台服务（官方 SDK 封装）

    Usage:
        svc = DingTalkService(app_key="...", app_secret="...", agent_id=123)
        token = await svc.get_access_token()
        instance_id = await svc.create_approval(...)
    """

    def __init__(self, app_key: str, app_secret: str, agent_id: int):
        self._app_key = app_key
        self._app_secret = app_secret
        self._agent_id = agent_id

        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0
        self._token_lock = asyncio.Lock()

        # SDK clients（同步，在 executor 中调用）
        self._oauth_client = OAuthClient(_make_base_config())
        self._workflow_client = WorkflowClient(_make_base_config())
        self._robot_client = RobotClient(_make_base_config())

    # ── AccessToken ───────────────────────────────────────────────

    async def get_access_token(self) -> str:
        """获取 AccessToken（带缓存，提前 5 分钟刷新，并发安全）"""
        if self._access_token and time.time() < self._token_expires_at - 300:
            return self._access_token

        async with self._token_lock:
            # double-check
            if self._access_token and time.time() < self._token_expires_at - 300:
                return self._access_token

            loop = asyncio.get_event_loop()
            token, expire_in = await loop.run_in_executor(None, self._fetch_token_sync)
            self._access_token = token
            self._token_expires_at = time.time() + expire_in
            logger.info("DingTalk AccessToken 已刷新")
            return self._access_token

    def _fetch_token_sync(self) -> tuple[str, int]:
        """同步获取 AccessToken（在 executor 中执行）"""
        req = oauth_models.GetAccessTokenRequest(
            app_key=self._app_key,
            app_secret=self._app_secret,
        )
        try:
            resp = self._oauth_client.get_access_token(req)
            return resp.body.access_token, resp.body.expire_in or 7200
        except Exception as e:
            raise DingTalkError(f"获取 AccessToken 失败: {e}") from e

    # ── 审批实例 ──────────────────────────────────────────────────

    async def create_approval(
        self,
        originator_user_id: str,
        process_code: str,
        title: str,
        form_values: list[dict],
        approvers: list[str],
    ) -> str:
        """创建 OA 审批实例，返回 instance_id

        Args:
            originator_user_id: 发起人钉钉 userId
            process_code:       审批模板 processCode（在钉钉审批管理页面 URL 中获取）
            title:              审批标题
            form_values:        表单字段列表 [{"name": "...", "value": "..."}]
            approvers:          审批人 userId 列表（三人会签）

        Returns:
            process_instance_id
        """
        token = await self.get_access_token()
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._create_approval_sync,
            token, originator_user_id, process_code, title, form_values, approvers
        )

    def _create_approval_sync(
        self,
        token: str,
        originator_user_id: str,
        process_code: str,
        title: str,
        form_values: list[dict],
        approvers: list[str],
    ) -> str:
        """同步创建审批实例"""
        headers = workflow_models.StartProcessInstanceHeaders()
        headers.x_acs_dingtalk_access_token = token

        # 构建表单字段
        form_component_values = [
            workflow_models.StartProcessInstanceRequestFormComponentValues(
                name=fv["name"],
                value=str(fv.get("value", "")),
            )
            for fv in form_values
        ]

        # 构建审批人（三人会签 → 每人一个 ApproversNode）
        approver_nodes = [
            workflow_models.StartProcessInstanceRequestApprovers(
                action_type="SEQUENTIAL",
                user_ids=approvers,
            )
        ]

        req = workflow_models.StartProcessInstanceRequest(
            originator_user_id=originator_user_id,
            process_code=process_code,
            dept_id=-1,
            micro_app_agent_id=self._agent_id,
            form_component_values=form_component_values,
            approvers=approver_nodes,
            title=title,
        )

        try:
            resp = self._workflow_client.start_process_instance_with_options(
                req, headers, util_models.RuntimeOptions()
            )
            instance_id = resp.body.instance_id
            logger.info("审批实例已创建: %s", instance_id)
            return instance_id
        except Exception as e:
            raise DingTalkError(f"创建审批失败: {e}") from e

    async def get_approval(self, instance_id: str) -> dict:
        """查询审批实例详情

        Returns:
            {
                "status": "RUNNING" | "TERMINATED" | "COMPLETED",
                "result": "agree" | "refuse",
                "business_id": str,
            }
        """
        token = await self.get_access_token()
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._get_approval_sync, token, instance_id
        )

    def _get_approval_sync(self, token: str, instance_id: str) -> dict:
        """同步查询审批实例"""
        headers = workflow_models.GetProcessInstanceHeaders()
        headers.x_acs_dingtalk_access_token = token

        req = workflow_models.GetProcessInstanceRequest(
            process_instance_id=instance_id
        )
        try:
            resp = self._workflow_client.get_process_instance_with_options(
                req, headers, util_models.RuntimeOptions()
            )
            if not resp.body.success:
                raise DingTalkError(f"查询审批失败: {resp.body}")

            result = resp.body.result
            return {
                "status": result.status,
                "result": result.result,
                "business_id": result.business_id,
                "title": result.title,
            }
        except DingTalkError:
            raise
        except Exception as e:
            raise DingTalkError(f"查询审批失败: {e}") from e

    # ── 工作通知 ──────────────────────────────────────────────────

    async def send_notification(
        self,
        user_ids: list[str],
        title: str,
        content: str,
    ) -> bool:
        """发送工作通知（Markdown 格式）"""
        token = await self.get_access_token()
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._send_notification_sync, token, user_ids, title, content
        )

    def _send_notification_sync(
        self, token: str, user_ids: list[str], title: str, content: str
    ) -> bool:
        """同步发送工作通知"""
        headers = robot_models.BatchSendOTOHeaders()
        headers.x_acs_dingtalk_access_token = token

        req = robot_models.BatchSendOTORequest(
            robot_code=self._app_key,
            user_ids=user_ids,
            msg_key="sampleMarkdown",
            msg_param=json.dumps(
                {"title": title, "text": content}, ensure_ascii=False
            ),
        )
        try:
            resp = self._robot_client.batch_send_otowith_options(
                req, headers, util_models.RuntimeOptions()
            )
            ok = bool(resp.body.process_query_key)
            if ok:
                logger.info("工作通知已发送: %s → %s", title, user_ids)
            else:
                logger.error("工作通知发送失败: %s", resp.body)
            return ok
        except Exception as e:
            logger.error("工作通知发送异常: %s", e)
            return False

    # ── 回调签名验证 ──────────────────────────────────────────────

    def verify_signature(
        self,
        timestamp: str,
        nonce: str,
        signature: str,
    ) -> bool:
        """验证钉钉事件回调签名（HMAC-SHA256，fail-closed）

        算法：base64(hmac_sha256(appSecret, timestamp + "\\n" + nonce))
        同时校验 timestamp 时效性（5 分钟内有效）。
        """
        if not self._app_secret:
            logger.error("DingTalk AppSecret 未配置，签名验证失败（fail-closed）")
            return False

        try:
            ts_ms = int(timestamp)
            now_ms = int(time.time() * 1000)
            if abs(now_ms - ts_ms) > 300_000:
                logger.warning(
                    "DingTalk 回调 timestamp 超时: diff=%dms", abs(now_ms - ts_ms)
                )
                return False
        except (ValueError, TypeError):
            return False

        message = f"{timestamp}\n{nonce}"
        expected = base64.b64encode(
            _hmac.new(
                self._app_secret.encode(), message.encode(), hashlib.sha256
            ).digest()
        ).decode()
        return _hmac.compare_digest(expected, signature)
