"""CLI 工具 — 转发器 HTTP 客户端"""

import time
import httpx

from .crypto import CLICrypto


class RelayClient:
    """转发器 HTTP 客户端"""

    def __init__(self, relay_url: str, api_key: str, crypto: CLICrypto):
        self.relay_url = relay_url.rstrip("/")
        self.api_key = api_key
        self.crypto = crypto
        self._headers = {
            "X-API-Key": api_key,
            "Content-Type": "application/json",
        }

    # ── 流程2: Jenkins → 发起审批 ────────────

    def request_approval(
        self,
        job_name: str,
        build_id: int,
        title: str,
        content: str,
        approver_ids: list[str],
        callback_params: dict | None = None,
        originator_user_id: str = "",
        process_code: str = "",
    ) -> dict:
        """向转发器发起钉钉审批请求

        Returns: {"approval_id": "...", "process_instance_id": "...", "status": "pending"}
        """
        # 构建回调 payload（审批通过后需要传给 Jenkins 的参数）
        callback_data = callback_params or {
            "job_name": job_name,
            "build_id": build_id,
            "parameters": {},
        }

        # 加密
        encrypted = self.crypto.encrypt_json(callback_data)

        body = {
            "jenkins_job_name": job_name,
            "build_id": build_id,
            "title": title,
            "content": content,
            "approver_user_ids": approver_ids,
            "encrypted_payload": encrypted["ciphertext"],
            "nonce": encrypted["nonce"],
            "signature": encrypted["signature"],
            "originator_user_id": originator_user_id,
            "process_code": process_code,
        }

        with httpx.Client(timeout=30) as client:
            resp = client.post(
                f"{self.relay_url}/api/v1/dingtalk/send-approval",
                headers=self._headers,
                json=body,
            )
            resp.raise_for_status()
            return resp.json()

    # ── 轮询审批状态 ─────────────────────────

    def wait_for_approval(
        self,
        approval_id: str,
        timeout: int = 3600,
        poll_interval: int = 5,
    ) -> str:
        """轮询等待审批结果（阻塞调用，用于 Jenkins Pipeline）

        Returns: "approved" | "rejected" | "cancelled" | "expired"
        """
        deadline = time.time() + timeout

        while time.time() < deadline:
            status = self.check_approval_status(approval_id)
            state = status.get("status", "pending")

            if state == "approved":
                return "approved"
            elif state in ("rejected", "cancelled", "expired"):
                return state

            time.sleep(poll_interval)

        return "expired"

    def check_approval_status(self, approval_id: str) -> dict:
        """查询审批状态"""
        with httpx.Client(timeout=15) as client:
            resp = client.get(
                f"{self.relay_url}/api/v1/admin/approvals/{approval_id}",
                headers=self._headers,
            )
            resp.raise_for_status()
            return resp.json()

    # ── 构建结果通知 ─────────────────────────

    def notify_build_result(
        self,
        job_name: str,
        build_id: int,
        result: str,
        output_summary: str = "",
        approval_id: str | None = None,
        duration_ms: int | None = None,
    ) -> dict:
        """通知转发器构建结果"""
        body = {
            "job_name": job_name,
            "build_id": build_id,
            "result": result,
            "output_summary": output_summary,
            "related_approval_id": approval_id,
            "duration_ms": duration_ms,
        }

        with httpx.Client(timeout=15) as client:
            resp = client.post(
                f"{self.relay_url}/api/v1/jenkins/callback",
                headers=self._headers,
                json=body,
            )
            resp.raise_for_status()
            return resp.json()
