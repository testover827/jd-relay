"""Jenkins Remote Access API 封装

封装 Jenkins API：触发构建、查询构建/队列状态、取消构建、Crumb 获取

特性:
- HTTP 客户端连接池复用
- 自动 CSRF Crumb 获取与注入
- Job 名称 URL 安全编码
- 网络异常自动重试（3 次，指数退避）
- Queue ID 从 Location 响应头正确解析
"""

import re
import base64
import logging
from urllib.parse import quote

import httpx
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from ..config import settings

logger = logging.getLogger(__name__)

# ── 超时配置（秒） ──
CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 60.0

# Jenkins API 路径
CRUMB_URL = "/crumbIssuer/api/json"

# ── 可重试的网络异常 ──
RETRYABLE_EXCEPTIONS = (
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.RemoteProtocolError,
    httpx.NetworkError,
)


class JenkinsError(Exception):
    """Jenkins API 调用异常"""
    pass


class JenkinsService:
    """Jenkins Remote Access API 服务

    Usage:
        svc = JenkinsService()
        result = await svc.build_job("my-job", {"param": "value"})
        await svc.close()  # 应用关闭时调用
    """

    def __init__(self):
        self._base_url = settings.JENKINS_URL.rstrip("/") if settings.JENKINS_URL else ""
        self._client: httpx.AsyncClient | None = None
        self._crumb_cache: tuple[str, str] | None = None  # (crumb_field, crumb_value)

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT),
                limits=httpx.Limits(max_connections=5, max_keepalive_connections=2),
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ── 内部方法 ──────────────────────────────

    @property
    def _auth_header(self) -> dict:
        credentials = f"{settings.JENKINS_USERNAME}:{settings.JENKINS_API_TOKEN}"
        encoded = base64.b64encode(credentials.encode()).decode()
        return {"Authorization": f"Basic {encoded}"}

    def _safe_job_path(self, job_name: str) -> str:
        """对 Job 名称进行 URL 安全编码，支持 folder 结构（/ 分隔）"""
        if "/" in job_name:
            parts = job_name.split("/")
            encoded_parts = [quote(part, safe="") for part in parts]
            return "/job/".join(encoded_parts)
        return f"/job/{quote(job_name, safe='')}"

    async def _ensure_crumb(self) -> dict:
        """获取 Jenkins CSRF Crumb（如果启用）"""
        if self._crumb_cache is not None:
            field, value = self._crumb_cache
            return {field: value}

        try:
            client = self._get_client()
            url = f"{self._base_url}{CRUMB_URL}"
            headers = {**self._auth_header, "Accept": "application/json"}
            resp = await client.get(url, headers=headers, timeout=10)

            if resp.status_code == 200:
                data = resp.json()
                field = data.get("crumbRequestField", ".crumb")
                value = data.get("crumb", "")
                self._crumb_cache = (field, value)
                logger.debug("Jenkins Crumb 已获取")
                return {field: value}
            else:
                # 404 或 403 说明未启用 CSRF Protection
                logger.debug("Jenkins 未启用 CSRF Protection (HTTP %s)", resp.status_code)
                return {}
        except Exception as e:
            logger.warning("获取 Jenkins Crumb 失败（可能未启用）: %s", e)
            return {}

    async def _request(
        self, method: str, path: str, include_crumb: bool = False, **kwargs
    ) -> httpx.Response:
        """统一 HTTP 请求封装，返回原始 Response 对象

        注意：此方法返回原始 httpx.Response（而非解析后的 JSON），
        以便调用方访问响应头（如 Location header 中的 queue URL）。

        Raises:
            JenkinsError: HTTP 错误状态码或网络异常
        """
        if not self._base_url:
            raise JenkinsError("Jenkins URL 未配置")

        url = f"{self._base_url}{path}"
        headers = kwargs.pop("headers", {})
        headers.update(self._auth_header)
        headers.setdefault("Accept", "application/json")

        if include_crumb:
            crumb = await self._ensure_crumb()
            headers.update(crumb)

        client = self._get_client()
        try:
            resp = await client.request(method, url, headers=headers, **kwargs)
        except RETRYABLE_EXCEPTIONS as e:
            logger.error("Jenkins API 网络异常 [%s %s]: %s", method, path, e)
            raise JenkinsError(f"网络请求失败: {e}") from e

        if resp.status_code >= 400:
            error_text = resp.text[:500]
            raise JenkinsError(
                f"Jenkins API 返回 {resp.status_code}: {error_text}"
            )

        return resp

    # ── 构建操作 ──────────────────────────────

    @retry(
        retry=retry_if_exception_type(RETRYABLE_EXCEPTIONS),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def build_job(self, job_name: str, parameters: dict | None = None) -> dict:
        """触发 Job 构建，返回 {queue_id, queue_url}

        从 Jenkins 201 Created 响应的 Location header 中提取 queue URL。
        """
        safe_path = self._safe_job_path(job_name)

        if parameters:
            path = f"{safe_path}/buildWithParameters"
            resp = await self._request("POST", path, params=parameters, include_crumb=True)
        else:
            path = f"{safe_path}/build"
            resp = await self._request("POST", path, include_crumb=True)

        # 从 Location 响应头解析 queue_id
        queue_url = resp.headers.get("Location", "")
        queue_id: int | None = None
        if queue_url:
            m = re.search(r"/queue/item/(\d+)", queue_url)
            if m:
                queue_id = int(m.group(1))

        logger.info("Jenkins Job 已触发: %s, queue_id=%s", job_name, queue_id)
        return {"queue_id": queue_id, "queue_url": queue_url}

    async def build_job_with_params(
        self, job_name: str, parameters: dict
    ) -> dict:
        """触发带参数的 Job 构建（JSON body 方式）"""
        safe_path = self._safe_job_path(job_name)
        params_json = {"parameter": [
            {"name": k, "value": str(v)} for k, v in parameters.items()
        ]}
        headers = {"Content-Type": "application/json"}
        resp = await self._request(
            "POST", f"{safe_path}/build",
            json=params_json, headers=headers, include_crumb=True
        )

        # 从 Location 响应头获取 queue URL
        queue_url = resp.headers.get("Location", "")
        queue_id: int | None = None
        if queue_url:
            m = re.search(r"/queue/item/(\d+)", queue_url)
            if m:
                queue_id = int(m.group(1))

        logger.info(
            "Jenkins Job 已触发 (params): %s, queue_id=%s, params=%s",
            job_name, queue_id, list(parameters.keys()),
        )
        return {"queue_id": queue_id, "queue_url": queue_url}

    # ── 状态查询 ──────────────────────────────

    @retry(
        retry=retry_if_exception_type(RETRYABLE_EXCEPTIONS),
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        reraise=True,
    )
    async def get_build_status(self, job_name: str, build_id: int) -> dict:
        """查询构建状态"""
        safe_path = self._safe_job_path(job_name)
        path = f"{safe_path}/{build_id}/api/json"

        try:
            resp = await self._request("GET", path)
            data = resp.json()
            return {
                "build_id": build_id,
                "job_name": job_name,
                "status": "building" if data.get("building") else "completed",
                "result": data.get("result"),
                "duration_ms": data.get("duration"),
                "estimated_duration_ms": data.get("estimatedDuration"),
                "timestamp": data.get("timestamp"),
                "url": data.get("url"),
            }
        except JenkinsError as e:
            # 区分「不存在」和其他错误
            err_msg = str(e)
            if "404" in err_msg:
                logger.info("Jenkins 构建 #%d 不存在于 job %s", build_id, job_name)
                return {
                    "build_id": build_id, "job_name": job_name, "status": "not_found"
                }
            elif "401" in err_msg or "403" in err_msg:
                logger.error("Jenkins 认证失败: %s", e)
                raise
            else:
                logger.warning("Jenkins 构建状态查询失败: %s", e)
                return {
                    "build_id": build_id, "job_name": job_name, "status": "unknown"
                }

    async def get_queue_status(self, queue_id: int) -> dict:
        """查询队列项状态"""
        path = f"/queue/item/{queue_id}/api/json"
        try:
            resp = await self._request("GET", path)
            data = resp.json()
            cancelled = data.get("cancelled", False)
            executable = data.get("executable")

            if cancelled:
                status = "cancelled"
            elif executable:
                status = "building"
                build_id = executable.get("number")
                return {"queue_id": queue_id, "status": status, "build_id": build_id}
            else:
                status = "waiting"

            return {"queue_id": queue_id, "status": status, "build_id": None}
        except JenkinsError:
            return {"queue_id": queue_id, "status": "unknown"}

    # ── 构建控制 ──────────────────────────────

    async def abort_build(self, job_name: str, build_id: int) -> bool:
        """取消构建"""
        safe_path = self._safe_job_path(job_name)
        path = f"{safe_path}/{build_id}/stop"
        try:
            await self._request("POST", path, include_crumb=True)
            logger.info("构建已取消: %s#%d", job_name, build_id)
            return True
        except JenkinsError as e:
            logger.error("取消构建失败: %s", e)
            return False
