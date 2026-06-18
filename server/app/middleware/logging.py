"""请求日志记录中间件 — 将每个请求写入 logs 表

关键设计决策：
- 使用纯 ASGI 中间件（不使用 BaseHTTPMiddleware）
- BaseHTTPMiddleware 在 call_next 中会消耗 body 流，导致 Form() 无法读取
- 纯 ASGI 模式只拦截 send 通道，不触碰 receive 通道，body 流完整保留给下游
- 日志写入失败不阻塞主流程但记录警告
"""

import logging
import time
import uuid
import asyncio
import re

from starlette.types import ASGIApp, Receive, Scope, Send, Message

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


class RequestLoggingMiddleware:
    """纯 ASGI 中间件：记录每个 API 请求到 logs 表

    不使用 BaseHTTPMiddleware，避免 body 流消耗导致 Form() 无法读取。

    工作原理：
    1. receive 通道完全透传（不缓存、不消耗 body）
    2. send 通道拦截，记录响应状态码和耗时
    3. 请求完成后，异步写入日志（不含 body 内容）
    """

    # 不需要记录日志的路径前缀
    _SKIP_PATHS = ("/static/", "/favicon.ico")

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")

        # 跳过不需要日志的路径
        for skip in self._SKIP_PATHS:
            if path.startswith(skip):
                await self.app(scope, receive, send)
                return

        request_id = str(uuid.uuid4())[:8]
        start_time = time.monotonic()
        status_code = 0

        async def _send_with_logging(message: Message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 0)
            await send(message)

            # 最终响应体 → 写日志
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                duration_ms = int((time.monotonic() - start_time) * 1000)

                # 确定 source
                if "/dingtalk" in path:
                    source = "dingtalk"
                elif "/jenkins" in path:
                    source = "jenkins"
                elif "/admin" in path:
                    source = "relay"
                else:
                    source = "system"

                asyncio.create_task(
                    self._write_log(
                        level="ERROR" if status_code >= 400 else "INFO",
                        source=source,
                        action=f"{scope.get('method', '?')} {path}",
                        detail=f"HTTP {status_code}",
                        payload_snippet=None,
                        is_encrypted=False,
                        duration_ms=duration_ms,
                        request_id=request_id,
                    ),
                    name=f"log-write-{request_id}",
                )

        # receive 完全透传，不触碰 body 流
        await self.app(scope, receive, _send_with_logging)

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
            logger.warning(
                "日志写入失败(不影响业务): request_id=%s, error=%s",
                kwargs.get("request_id"), e,
            )
