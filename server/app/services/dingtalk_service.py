"""钉钉开放平台 SDK 封装

封装钉钉 API：access_token 获取、审批实例创建/查询、工作通知发送、回调签名验证

安全特性:
- Access Token 自动缓存与刷新（提前 5 分钟）
- 回调签名验证（HMAC-SHA256，fail-closed 策略）
- HTTP 客户端连接池复用
- 网络异常自动重试（3 次，指数退避）
"""

import asyncio
import time
import hmac as _hmac
import hashlib
import base64
import json
import logging
from typing import Literal

import httpx
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from ..config import settings

logger = logging.getLogger(__name__)

# ── 钉钉 API 端点 ──
DINGTALK_BASE = "https://api.dingtalk.com"
TOKEN_URL = f"{DINGTALK_BASE}/v1.0/oauth2/accessToken"
PROCESS_INSTANCE_URL = f"{DINGTALK_BASE}/v1.0/workflow/processInstances"
WORK_NOTICE_URL = f"{DINGTALK_BASE}/v1.0/robot/oToMessages/batchSend"

# ── 超时配置（秒） ──
CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 30.0
TOKEN_TIMEOUT = 15.0

# ── 可重试的网络异常 ──
RETRYABLE_EXCEPTIONS = (
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.RemoteProtocolError,
    httpx.NetworkError,
)


class DingTalkError(Exception):
    """钉钉 API 调用异常"""
    pass


class DingTalkService:
    """钉钉开放平台服务

    Usage:
        svc = DingTalkService()
        await svc.get_access_token()
        # ... use ...
        await svc.close()  # 应用关闭时调用
    """

    def __init__(self):
        self._access_token: str | None = None
        self._token_expires_at: float = 0
        self._token_lock = asyncio.Lock()
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        """懒初始化 HTTP 客户端（连接池复用）"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT),
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            )
        return self._client

    async def close(self):
        """关闭 HTTP 客户端（应在 lifespan 中调用）"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ── Access Token ──────────────────────────

    async def get_access_token(self) -> str:
        """获取钉钉 access_token（带缓存，提前 5 分钟刷新，并发安全）"""
        if self._access_token and time.time() < self._token_expires_at - 300:
            return self._access_token

        async with self._token_lock:
            # double-check after acquiring lock
            if self._access_token and time.time() < self._token_expires_at - 300:
                return self._access_token

            client = self._get_client()
            try:
                resp = await client.post(
                    TOKEN_URL,
                    json={
                        "appKey": settings.DINGTALK_APP_KEY,
                        "appSecret": settings.DINGTALK_APP_SECRET,
                    },
                    timeout=TOKEN_TIMEOUT,
                )
            except RETRYABLE_EXCEPTIONS as e:
                logger.error("获取 access_token 网络异常: %s", e)
                raise DingTalkError(f"网络请求失败: {e}") from e

            # 先检查状态码再解析 JSON
            if resp.status_code != 200:
                raise DingTalkError(
                    f"获取 access_token 失败: HTTP {resp.status_code}"
                )

            content_type = resp.headers.get("content-type", "")
            if "application/json" not in content_type:
                raise DingTalkError(
                    f"获取 access_token 返回非 JSON: Content-Type={content_type}"
                )

            data = resp.json()
            if "accessToken" not in data:
                raise DingTalkError(f"获取 access_token 失败: {data}")

            self._access_token = data["accessToken"]
            self._token_expires_at = time.time() + data.get("expireIn", 7200)
            logger.info("钉钉 access_token 已刷新")
            return self._access_token

    # ── 审批实例 ──────────────────────────────

    @retry(
        retry=retry_if_exception_type(RETRYABLE_EXCEPTIONS),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def create_process_instance(
        self,
        originator_user_id: str,
        process_code: str,
        title: str,
        form_component_values: list[dict],
        approvers: list[str],
    ) -> str:
        """发起审批实例，返回 instance_id

        Args:
            originator_user_id: 发起人钉钉 userId
            process_code: 审批模板 processCode
            title: 审批标题
            form_component_values: 表单组件值列表
            approvers: 审批人 userId 列表

        Returns:
            process_instance_id

        Raises:
            DingTalkError: API 调用失败或返回错误
        """
        token = await self.get_access_token()
        headers = {
            "x-acs-dingtalk-access-token": token,
            "Content-Type": "application/json",
        }
        body = {
            "originatorUserId": originator_user_id,
            "processCode": process_code,
            "deptId": -1,
            "microappAgentId": int(settings.DINGTALK_AGENT_ID),
            "approvers": approvers,
            "title": title,
            "formComponentValues": form_component_values,
        }

        client = self._get_client()
        resp = await client.post(PROCESS_INSTANCE_URL, headers=headers, json=body)

        if resp.status_code != 200:
            raise DingTalkError(f"发起审批失败: HTTP {resp.status_code}")

        data = resp.json()
        if "instanceId" not in data:
            raise DingTalkError(f"发起审批失败: {data}")

        instance_id = data["instanceId"]
        logger.info("钉钉审批实例已创建: %s", instance_id)
        return instance_id

    @retry(
        retry=retry_if_exception_type(RETRYABLE_EXCEPTIONS),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def get_process_instance(self, instance_id: str) -> dict:
        """查询审批实例详情"""
        token = await self.get_access_token()
        headers = {"x-acs-dingtalk-access-token": token}

        client = self._get_client()
        resp = await client.get(
            f"{PROCESS_INSTANCE_URL}/{instance_id}",
            headers=headers,
        )

        if resp.status_code != 200:
            raise DingTalkError(f"查询审批实例失败: HTTP {resp.status_code}")

        return resp.json()

    # ── 工作通知 ──────────────────────────────

    @retry(
        retry=retry_if_exception_type(RETRYABLE_EXCEPTIONS),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def send_work_notification(
        self, user_ids: list[str], title: str, content: str
    ) -> bool:
        """通过工作通知发送消息给指定用户"""
        token = await self.get_access_token()
        headers = {
            "x-acs-dingtalk-access-token": token,
            "Content-Type": "application/json",
        }
        # 安全：使用 json.dumps 避免 JSON 注入
        body = {
            "robotCode": settings.DINGTALK_APP_KEY,
            "userIds": user_ids,
            "msgKey": "sampleMarkdown",
            "msgParam": json.dumps({"title": title, "text": content}, ensure_ascii=False),
        }

        client = self._get_client()
        resp = await client.post(WORK_NOTICE_URL, headers=headers, json=body)
        data = resp.json()

        success = resp.status_code == 200 and bool(data.get("processQueryKey"))
        if success:
            logger.info("工作通知已发送: %s → %s", title, user_ids)
        else:
            logger.error("工作通知发送失败: %s", data)
        return success

    # ── 回调签名验证 ──────────────────────────

    @staticmethod
    def verify_callback_signature(
        timestamp: str, nonce_str: str, signature: str, body: str
    ) -> bool:
        """验证钉钉回调签名（HMAC-SHA256）

        采用 fail-closed 策略：配置缺失时拒绝验证通过。
        同时检查 timestamp 时效性（5 分钟内有效），防止重放攻击。

        Args:
            timestamp: 钉钉请求头中的 timestamp（毫秒）
            nonce_str: 钉钉请求头中的 nonce
            signature: 钉钉请求头中的签名
            body: 原始请求体

        Returns:
            True 验证通过, False 验证失败
        """
        app_secret = settings.DINGTALK_APP_SECRET
        if not app_secret:
            logger.error("钉钉 AppSecret 未配置，签名验证失败（fail-closed）")
            return False

        # 时效性检查：timestamp 在 5 分钟内有效
        try:
            ts_ms = int(timestamp)
            now_ms = int(time.time() * 1000)
            if abs(now_ms - ts_ms) > 300_000:  # 5 minutes
                logger.warning("钉钉回调 timestamp 超时: diff=%dms", abs(now_ms - ts_ms))
                return False
        except (ValueError, TypeError):
            logger.warning("钉钉 callback timestamp 格式无效: %s", timestamp)
            return False

        # 钉钉签名算法: HMAC-SHA256(appSecret, timestamp + "\\n" + nonce)
        message = f"{timestamp}\n{nonce_str}"
        expected = base64.b64encode(
            _hmac.new(app_secret.encode(), message.encode(), hashlib.sha256).digest()
        ).decode()

        is_valid = _hmac.compare_digest(expected, signature)
        if not is_valid:
            logger.warning("钉钉回调签名验证失败")
        return is_valid
