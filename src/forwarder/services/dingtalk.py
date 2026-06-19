"""DingTalk API service — Phase 3.1.

Based on legacy dingtalk_service.py. Provides:
- Access token management (auto-refresh, concurrent-safe)
- Approval process instance creation / query
- Work notification sending
- Callback signature verification
"""

import asyncio
import time
import hmac
import hashlib
import base64
import json
import logging
from typing import Optional

import httpx
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

logger = logging.getLogger(__name__)

DINGTALK_BASE = "https://api.dingtalk.com"
TOKEN_URL = f"{DINGTALK_BASE}/v1.0/oauth2/accessToken"
PROCESS_INSTANCE_URL = f"{DINGTALK_BASE}/v1.0/workflow/processInstances"
WORK_NOTICE_URL = f"{DINGTALK_BASE}/v1.0/robot/oToMessages/batchSend"

CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 30.0
TOKEN_TIMEOUT = 15.0

RETRYABLE = (httpx.TimeoutException, httpx.ConnectError,
             httpx.RemoteProtocolError, httpx.NetworkError)


class DingTalkError(Exception):
    """DingTalk API error."""
    pass


class DingTalkService:
    """DingTalk Open Platform API wrapper.

    Usage:
        svc = DingTalkService(app_key="...", app_secret="...", agent_id=123)
        await svc.get_access_token()
        instance_id = await svc.create_approval(...)
        await svc.close()
    """

    def __init__(self, app_key: str, app_secret: str, agent_id: int):
        self._app_key = app_key
        self._app_secret = app_secret
        self._agent_id = agent_id

        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0
        self._token_lock = asyncio.Lock()
        self._client: Optional[httpx.AsyncClient] = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT),
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ── Access Token ─────────────────────────────────────────

    async def get_access_token(self) -> str:
        """Get DingTalk access token (cached, auto-refresh, concurrent-safe)."""
        if self._access_token and time.time() < self._token_expires_at - 300:
            return self._access_token

        async with self._token_lock:
            # Double-check after acquiring lock
            if self._access_token and time.time() < self._token_expires_at - 300:
                return self._access_token

            client = self._get_client()
            try:
                resp = await client.post(
                    TOKEN_URL,
                    json={
                        "appKey": self._app_key,
                        "appSecret": self._app_secret,
                    },
                    timeout=TOKEN_TIMEOUT,
                )
            except RETRYABLE as e:
                raise DingTalkError(f"Token request failed: {e}") from e

            if resp.status_code != 200:
                raise DingTalkError(f"Token request HTTP {resp.status_code}")

            data = resp.json()
            if "accessToken" not in data:
                raise DingTalkError(f"Token response missing accessToken: {data}")

            self._access_token = data["accessToken"]
            self._token_expires_at = time.time() + data.get("expireIn", 7200)
            logger.info("DingTalk access token refreshed")
            return self._access_token

    # ── Approval ─────────────────────────────────────────────

    @retry(
        retry=retry_if_exception_type(RETRYABLE),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def create_approval(
        self,
        originator_user_id: str,
        process_code: str,
        title: str,
        form_values: list[dict],
        approvers: list[str],
    ) -> str:
        """Create an approval process instance. Returns instance_id.

        Args:
            originator_user_id: Initiator's DingTalk userId.
            process_code: Approval template processCode.
            title: Approval title.
            form_values: Form component values [{name, value}, ...].
            approvers: Approver userIds (3-person sequential approval).
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
            "microappAgentId": self._agent_id,
            "approvers": approvers,
            "title": title,
            "formComponentValues": form_values,
        }

        client = self._get_client()
        resp = await client.post(PROCESS_INSTANCE_URL, headers=headers, json=body)

        if resp.status_code != 200:
            raise DingTalkError(f"Create approval HTTP {resp.status_code}")

        data = resp.json()
        if "instanceId" not in data:
            raise DingTalkError(f"Create approval failed: {data}")

        instance_id = data["instanceId"]
        logger.info(f"DingTalk approval created: {instance_id}")
        return instance_id

    @retry(
        retry=retry_if_exception_type(RETRYABLE),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def get_approval(self, instance_id: str) -> dict:
        """Query approval instance details."""
        token = await self.get_access_token()
        headers = {"x-acs-dingtalk-access-token": token}

        client = self._get_client()
        resp = await client.get(
            f"{PROCESS_INSTANCE_URL}/{instance_id}",
            headers=headers,
        )

        if resp.status_code != 200:
            raise DingTalkError(f"Get approval HTTP {resp.status_code}")

        return resp.json()

    # ── Work Notification ────────────────────────────────────

    @retry(
        retry=retry_if_exception_type(RETRYABLE),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def send_notification(
        self, user_ids: list[str], title: str, content: str
    ) -> bool:
        """Send work notification (Markdown) to users."""
        token = await self.get_access_token()
        headers = {
            "x-acs-dingtalk-access-token": token,
            "Content-Type": "application/json",
        }
        body = {
            "robotCode": self._app_key,
            "userIds": user_ids,
            "msgKey": "sampleMarkdown",
            "msgParam": json.dumps(
                {"title": title, "text": content}, ensure_ascii=False
            ),
        }

        client = self._get_client()
        resp = await client.post(WORK_NOTICE_URL, headers=headers, json=body)
        data = resp.json()

        ok = resp.status_code == 200 and bool(data.get("processQueryKey"))
        if ok:
            logger.info(f"Notification sent: {title} → {user_ids}")
        else:
            logger.error(f"Notification failed: {data}")
        return ok

    # ── Callback Signature ───────────────────────────────────

    @staticmethod
    def verify_signature(
        timestamp: str, nonce: str, signature: str, app_secret: str
    ) -> bool:
        """Verify DingTalk callback signature (HMAC-SHA256, fail-closed).

        Also validates timestamp freshness (within 5 minutes).
        """
        if not app_secret:
            return False

        # Timestamp freshness check
        try:
            ts_ms = int(timestamp)
            now_ms = int(time.time() * 1000)
            if abs(now_ms - ts_ms) > 300_000:
                logger.warning(f"DingTalk callback timestamp expired: diff={abs(now_ms - ts_ms)}ms")
                return False
        except (ValueError, TypeError):
            return False

        message = f"{timestamp}\n{nonce}"
        expected = base64.b64encode(
            hmac.new(app_secret.encode(), message.encode(), hashlib.sha256).digest()
        ).decode()
        return hmac.compare_digest(expected, signature)
