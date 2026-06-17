"""API Key 认证中间件

安全措施：
- 使用 hmac.compare_digest 进行时序安全的 API Key 比较
- 白名单路径精确匹配 + 前缀匹配
- 中间件中直接返回 JSON 响应（而非 raise HTTPException）
- 支持从查询参数传递 API Key（某些 webhook 场景无法设置 header）
"""

import hmac
import logging

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response
from starlette.status import HTTP_401_UNAUTHORIZED

from ..config import settings

logger = logging.getLogger(__name__)


class APIKeyAuthMiddleware(BaseHTTPMiddleware):
    """验证 X-API-Key header，白名单路径跳过

    跳过规则（按优先级）：
    1. 精确匹配的路径
    2. 前缀匹配的路径（如 /static/, /api/v1/dingtalk/callback）
    3. 非 /api/ 开头的 Web 页面路由
    4. Admin API（由 Session 中间件独立处理）
    """

    # 精确跳过的完整路径
    _EXACT_SKIP = {
        "/",
        "/login",
        "/health",
        "/docs",
        "/openapi.json",
        "/redoc",
    }

    # 前缀跳过的路径
    _PREFIX_SKIP = {
        "/static/",
        "/api/v1/dingtalk/callback",  # 钉钉回调使用自有签名验证
        "/api/v1/admin/",             # Admin 由 Session 认证
    }

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        path = request.url.path

        # 1. 精确匹配白名单
        if path in self._EXACT_SKIP:
            return await call_next(request)

        # 2. 前缀匹配白名单
        for prefix in self._PREFIX_SKIP:
            if path.startswith(prefix):
                return await call_next(request)

        # 3. 非 API 路径 → Web 面板页面（由 Session 处理）
        if not path.startswith("/api/"):
            return await call_next(request)

        # 4. API 调用需要 API Key（支持 header 或 query param）
        api_key = request.headers.get("X-API-Key", "") or request.query_params.get("api_key", "")

        if not api_key:
            logger.warning(
                "未提供 API Key: path=%s method=%s client=%s",
                path, request.method, request.client.host if request.client else "unknown",
            )
            return JSONResponse(
                status_code=HTTP_401_UNAUTHORIZED,
                content={"detail": "缺少 API Key 认证信息"},
            )

        # ★ 时序安全的比较（防止时序攻击）
        if not hmac.compare_digest(api_key, settings.RELAY_API_KEY):
            logger.warning(
                "无效的 API Key: path=%s method=%s client=%s",
                path, request.method, request.client.host if request.client else "unknown",
            )
            return JSONResponse(
                status_code=HTTP_401_UNAUTHORIZED,
                content={"detail": "无效的 API Key"},
            )

        return await call_next(request)
