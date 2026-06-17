"""FastAPI 应用入口 — Jenkins & 钉钉交互转发器

架构概览：
  Client (Jenkins/CLI/DingTalk/Web)
       │
       ▼
  ┌─────────────────────────┐
  │  CORS Middleware         │  ← 最外层：处理跨域
  │  Logging Middleware      │  ← 记录所有请求（含认证失败的）
  │  Auth Middleware         │  ← API Key / Session 验证
  │  Global Exception Handler│  ← 统一错误格式
  │  Routes                  │  ← 业务路由
  └─────────────────────────┘
"""

import time
import logging
import sys
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import settings, BASE_DIR
from .database import init_db
from .api.health import router as health_router
from .api.dingtalk import router as dingtalk_router
from .api.jenkins import router as jenkins_router
from .api.admin import router as admin_router, page_router
from .middleware.auth import APIKeyAuthMiddleware
from .middleware.logging import RequestLoggingMiddleware


# ── Logging 配置 ──
def _setup_logging():
    """配置应用日志格式和级别"""
    log_format = "%(asctime)s [%(levelname)-5s] %(name)s: %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        format=log_format,
        datefmt=date_format,
        stream=sys.stdout,
    )

    # 降低第三方库日志级别，减少噪音
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)


_setup_logging()

START_TIME = time.time()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理

    启动时：
    - 初始化数据库表结构
    - 预热服务连接

    关闭时：
    - 关闭 httpx 连接池
    """
    logger.info("正在初始化应用...")
    await init_db()
    logger.info("数据库初始化完成")

    yield

    # 清理资源（如果有全局服务实例）
    logger.info("应用关闭")


app = FastAPI(
    title="Jenkins & 钉钉 交互转发器",
    description=(
        "部署在中间服务器，桥接 Jenkins 与钉钉的双向审批触发流程。\n\n"
        "**核心功能：**\n"
        "- 钉钉审批 → 触发 Jenkins 构建\n"
        "- Jenkins 构建审批 → 发起钉钉审批\n"
        "- 加密通信 + 签名验证\n"
        "- Web 管理面板"
    ),
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)


# ── 全局异常处理器 ──
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """统一异常处理 - 返回标准 JSON 格式"""
    request_id = getattr(request.state, "request_id", "???")

    # 记录异常详情（含完整堆栈）
    logger.exception(
        "未处理异常: request_id=%s path=%s error=%s: %s",
        request_id, request.url.path, type(exc).__name__, str(exc),
    )

    return JSONResponse(
        status_code=500,
        content={
            "detail": "内部服务器错误",
            "error_type": type(exc).__name__,
            "request_id": request_id,
        },
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """HTTP 异常统一格式"""
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


# ── CORS 配置 ──
# 生产环境应根据实际前端域名严格限制
_cors_origins = ["http://localhost:3000", "http://localhost:8080"]
if settings.DEBUG:
    _cors_origins.append("*")  # 开发环境允许所有来源

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)

# ── 中间件注册（后添加的先执行）──
# 执行顺序（从外到内）：CORS → Logging → Auth → Route Handler
app.add_middleware(APIKeyAuthMiddleware)          # 内层：认证
app.add_middleware(RequestLoggingMiddleware)       # 外层：日志（记录含被拒绝的请求）

# ── 路由注册 ──
app.include_router(health_router)
app.include_router(dingtalk_router)
app.include_router(jenkins_router)
app.include_router(admin_router, prefix="/api/v1")
app.include_router(page_router)  # SSR 页面路由

# ── 静态文件 ──
static_dir = BASE_DIR / "app" / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


def get_uptime() -> int:
    """获取应用运行时间（秒）"""
    return int(time.time() - START_TIME)


@app.get("/", tags=["root"], include_in_schema=False)
async def root_redirect():
    """根路径重定向到 Web 面板"""
    return RedirectResponse(url="/admin/")
