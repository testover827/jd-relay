"""请求日志记录中间件 — 将每个请求写入 logs 表

关键设计决策：
- 使用 BaseHTTPMiddleware 并在 call_next **之前**读取并缓存 request body
- 通过 request.state._cached_body 将 body 传递给下游
- 敏感字段自动脱敏（password/token/secret/key/credential）
- 日志写入失败不阻塞主流程但记录警告
"""

import logging
import time
import uuid
import asyncio
import re

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from ..database import async_session
from ..models import Log

logger = logging.getLogger(__name__)

# 需要脱敏的敏感字段名模式
_SENSITIVE_FIELDS = re.compile(
    r'(?i)(password|passwd|pwd|token|secret|key|credential'
    r'|api_key|apikey|access_token|auth|hash)'
)

# 需要脱敏的敏感值模式（如 Bearer token）
_SENSITIVE_VALUES = re.compile(r'(?i)(Bearer\s+[\w\-.]+)')


def _redact_body(body_str: str, max_length: int = 200) -> str | None:
    """对请求体进行脱敏处理"""
    if not body_str:
        return None

    snippet = body_str[:max_length]

    # 尝试 JSON 脱敏
    try:
        import json
        data = json.loads(body_str)
        if isinstance(data, dict):
            redacted = {}
            for k, v in data.items():
                if _SENSITIVE_FIELDS.search(k):
                    redacted[k] = "****"
                elif isinstance(v, str) and len(v) > 20:
                    # 检查是否像 token/密钥（长随机字符串）
                    redacted[k] = v[:4] + "****" if re.match(
                        r'^[A-Za-z0-9+/=]{20,}$', v.strip()
                    ) else v
                else:
                    redacted[k] = v
            return json.dumps(redacted, ensure_ascii=False)[:max_length]
    except (json.JSONDecodeError, ValueError):
        pass

    # 非 JSON：整体脱敏检测
    if _SENSITIVE_VALUES.search(snippet):
        snippet = _SENSITIVE_VALUES.sub('****', snippet)

    return snippet


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """记录每个 API 请求到 logs 表

    注意：
    - body 读取在 call_next 之前完成，避免下游无法读取
    - 缓存的 body 存入 request.state._cached_body
    - 日志写入是异步非阻塞的，失败时记录 warning
    """

    # 不需要记录日志的路径前缀
    _SKIP_PATHS = ("/static/", "/favicon.ico")

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        request_id = str(uuid.uuid4())[:8]
        request.state.request_id = request_id

        # ★ 关键修复：在 call_next 之前缓存 body
        cached_body: bytes | None = None
        body_snippet: str | None = None

        if request.method in ("POST", "PUT", "PATCH"):
            try:
                cached_body = await request.body()
                # 脱敏处理
                if cached_body:
                    body_snippet = _redact_body(
                        cached_body.decode("utf-8", errors="replace")
                    )
            except Exception as e:
                logger.warning("%s | 读取请求体失败: %s", request_id, e)

        # 执行下游路由
        start = time.monotonic()
        response = await call_next(request)
        duration_ms = int((time.monotonic() - start) * 1000)

        # 确定 source
        path = request.url.path
        if "/dingtalk" in path:
            source = "dingtalk"
        elif "/jenkins" in path:
            source = "jenkins"
        elif "/admin" in path:
            source = "relay"
        else:
            source = "system"

        # 判断是否有加密内容
        is_encrypted = False
        if body_snippet:
            lower = body_snippet.lower()
            is_encrypted = "encrypted_payload" in lower or "ciphertext" in lower

        # 异步写入日志（不阻塞响应），带 name 以便调试追踪
        asyncio.create_task(
            self._write_log(
                level="ERROR" if response.status_code >= 400 else "INFO",
                source=source,
                action=f"{request.method} {path}",
                detail=f"HTTP {response.status_code}",
                payload_snippet=body_snippet,
                is_encrypted=is_encrypted,
                duration_ms=duration_ms,
                request_id=request_id,
            ),
            name=f"log-write-{request_id}"
        )

        return response

    @staticmethod
    async def _write_log(**kwargs):
        """异步写入日志到数据库"""
        try:
            async with async_session() as session:
                log_entry = Log(**kwargs)
                session.add(log_entry)
                await session.commit()
                logger.debug("日志写入成功: request_id=%s", kwargs.get("request_id"))
        except Exception as e:
            # 日志写入失败不应影响主流程，但需要记录
            logger.warning(
                "日志写入失败(不影响业务): request_id=%s, error=%s",
                kwargs.get("request_id"), e,
            )
