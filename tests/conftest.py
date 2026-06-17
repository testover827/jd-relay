"""Pytest 全局 fixtures — 数据库、客户端、mock 服务"""

import asyncio
import os
import tempfile
from pathlib import Path
from typing import AsyncGenerator, Generator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# 确保项目路径在 sys.path 中
sys_path = str(Path(__file__).resolve().parent.parent / "server")
if sys_path not in __import__("sys").path:
    __import__("sys").path.insert(0, sys_path)

# ═══════════════════════════════════════════
# 测试环境变量（必须在 import app 之前设置）
# ═══════════════════════════════════════════
_TEST_API_KEY = "test-api-key-12345"
_TEST_SESSION_SECRET = "test-session-secret-for-unit-tests"

os.environ.setdefault("RELAY_API_KEY", _TEST_API_KEY)
os.environ.setdefault("SESSION_SECRET", _TEST_SESSION_SECRET)
os.environ.setdefault("DEBUG", "true")
# 确保加密密钥有值（避免 SecureConfig 使用空密钥导致问题）
os.environ.setdefault("AES_ENCRYPTION_KEY", "a" * 64)  # 32 bytes hex
os.environ.setdefault("HMAC_SECRET", "b" * 64)
os.environ.setdefault("CONFIG_MASTER_KEY", "c" * 64)

# ═══════════════════════════════════════════
# 测试常量 — 必须与 settings 默认值一致
# ═══════════════════════════════════════════
TEST_API_KEY = "test-api-key-12345"
# CryptoService 需要 hex-encoded 密钥 (32 bytes = 64 hex chars)
TEST_AES_KEY = "aa" * 32  # 64 hex chars = 32 bytes for AES-256
TEST_HMAC_SECRET = "bb" * 32  # 64 hex chars = 32 bytes for HMAC
TEST_SESSION_SECRET = "test-session-secret-for-signing"

# 预设环境变量，确保 settings 加载时使用测试值
os.environ.setdefault("RELAY_API_KEY", TEST_API_KEY)
os.environ.setdefault("AES_ENCRYPTION_KEY", TEST_AES_KEY)
os.environ.setdefault("HMAC_SECRET", TEST_HMAC_SECRET)
os.environ.setdefault("SESSION_SECRET", TEST_SESSION_SECRET)

# ═══════════════════════════════════════════
# 禁用日志中间件（避免 ASGITransport + BaseHTTPMiddleware 死锁）
#
# 问题根因:
#   RequestLoggingMiddleware 继承自 starlette.BaseHTTPMiddleware，
#   其 dispatch() 方法会调用 await request.body() 读取请求体。
#   在 httpx ASGITransport 测试环境中，BaseHTTPMiddleware 的 body 读取
#   与 ASGI 传输层存在已知的死锁问题（尤其是 POST/PUT 请求）。
#   表现为：GET 请求正常，但任何带 body 的写操作请求（PUT/POST）无限挂起。
#
# 解决方案:
#   将 dispatch 替换为直接调用 call_next 的空操作中间件，
#   完全绕过 BaseHTTPMiddleware 的 body 读取逻辑。
# ═══════════════════════════════════════════

import asyncio


async def _noop_dispatch(self, request, call_next):
    """测试环境：完全跳过日志中间件的 dispatch 逻辑，避免死锁"""
    return await call_next(request)


def _patch_logging_middleware():
    """在 app 导入后立即修补日志中间件 — 替换整个 dispatch 方法"""
    try:
        from app.middleware.logging import RequestLoggingMiddleware
        # 替换整个 dispatch 方法（不仅仅是 _write_log）
        # 因为死锁发生在 dispatch 中的 await request.body() 调用
        RequestLoggingMiddleware.dispatch = _noop_dispatch
    except (ImportError, Exception):
        pass  # 中间件不存在时静默忽略


# 立即执行修补（必须在 app 首次使用之前完成）
_patch_logging_middleware()

# 延迟导入避免循环引用
def _get_app():
    from app.main import app
    return app


def _get_db_dep():
    from app.database import get_db
    return get_db


def _get_base():
    from app.database import Base
    return Base


def _get_settings():
    from app.config import settings
    return settings


# ═══════════════════════════════════════════
# 测试用 SQLite 数据库（每个函数独立）
# ═══════════════════════════════════════════

@pytest_asyncio.fixture(scope="function")
async def db_engine():
    """每个测试函数使用独立的临时数据库文件"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False, dir="/tmp") as f:
        db_path = f.name

    db_url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(db_url, echo=False)
    Base = _get_base()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    await engine.dispose()
    try:
        os.unlink(db_path)
    except OSError:
        pass


@pytest_asyncio.fixture(scope="function")
async def db_session(db_engine) -> AsyncGenerator[AsyncSession, None]:
    """每个测试的独立 DB session"""
    Base = _get_base()
    session_maker = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False,
    )
    async with session_maker() as session:
        # 注入到 FastAPI 的依赖覆盖
        app = _get_app()
        get_db = _get_db_dep()
        app.dependency_overrides[get_db] = lambda: session
        yield session
        await session.rollback()

    # 清除依赖覆盖（避免影响其他测试）
    app = _get_app()
    app.dependency_overrides.clear()


@pytest_asyncio.fixture(scope="function")
async def client(db_session) -> AsyncGenerator[AsyncClient, None]:
    """异步 HTTP 测试客户端

    注意：移除 RequestLoggingMiddleware，因为该中间件在所有 POST 请求中
    调用 await request.body()，与 ASGITransport 存在已知的死锁问题。
    """
    from app.middleware.logging import RequestLoggingMiddleware

    app = _get_app()

    # 移除 logging 中间件（避免 request.body() + ASGITransport 死锁）
    app.user_middleware = [
        mw for mw in app.user_middleware
        if getattr(getattr(mw, 'cls', None), '__name__', '') != 'RequestLoggingMiddleware'
    ]

    # 重建 middleware stack（必须手动触发）
    app.build_middleware_stack()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


# ═══════════════════════════════════════════
# Mock 服务 fixtures
# ═══════════════════════════════════════════

@pytest.fixture
def mock_dingtalk_service():
    from app.services.dingtalk_service import DingTalkService
    mock = __import__("unittest.mock").MagicMock(spec=DingTalkService)
    mock.verify_callback_signature = __import__("unittest.mock").MagicMock(return_value=True)
    mock.create_process_instance = __import__("asyncio").coroutine(
        lambda *a, **kw: {"process_instance_id": "pi_12345"}
    ) or __import__("unittest.mock").AsyncMock(return_value={"process_instance_id": "pi_12345"})
    mock.get_process_instance = __import__("unittest.mock").AsyncMock(return_value={
        "status": "COMPLETED",
        "result": "agree",
    })
    mock.send_work_notification = __import__("unittest.mock").AsyncMock(return_value=True)
    return mock


@pytest.fixture
def mock_jenkins_service():
    from app.services.jenkins_service import JenkinsService
    mock = __import__("unittest.mock").MagicMock(spec=JenkinsService)
    mock.build_job = __import__("unittest.mock").AsyncMock(return_value={"queue_id": 42})
    mock.get_build_status = __import__("unittest.mock").AsyncMock(return_value={
        "status": "SUCCESS",
        "building": False,
    })
    return mock


@pytest.fixture
def mock_crypto_service():
    from app.services.crypto_service import CryptoService
    mock = __import__("unittest.mock").MagicMock(spec=CryptoService)
    mock.encrypt_json = __import__("unittest.mock").MagicMock(return_value={
        "ciphertext": "encrypted_data_here",
        "nonce": "nonce123",
        "signature": "sig456",
    })
    mock.decrypt_json = __import__("unittest.mock").MagicMock(return_value={
        "job_name": "test-job",
        "build_id": 1,
        "result": "SUCCESS",
    })
    mock.decrypt_json_without_sig = __import__("unittest.mock").MagicMock(return_value={
        "ENV": "production",
        "BRANCH": "main",
    })
    return mock


# ═══════════════════════════════════════════
# 认证辅助 fixtures
# ═══════════════════════════════════════════

@pytest.fixture
def auth_headers():
    """带 API Key 的请求头"""
    return {"X-API-Key": _TEST_API_KEY}


@pytest.fixture
def auth_session_cookie():
    """已登录的 session cookie（Admin API 用）

    注意：Admin API 路由（/api/v1/dashboard 等）在 /api/v1 前缀下，
    会被 APIKeyAuthMiddleware 拦截。因此同时需要提供 API Key。
    """
    try:
        from itsdangerous import TimestampSigner
        signer = TimestampSigner(_TEST_SESSION_SECRET)
        session_val = signer.sign(b"authenticated").decode("utf-8")
        return {"Cookie": f"jd_session={session_val}"}
    except (ImportError, Exception):
        return {"Cookie": "jd_session=dev-session"}


@pytest.fixture
def admin_auth_headers():
    """完整的 Admin 认证头（API Key + Session Cookie）"""
    headers = {"X-API-Key": _TEST_API_KEY}
    try:
        from itsdangerous import TimestampSigner
        signer = TimestampSigner(_TEST_SESSION_SECRET)
        session_val = signer.sign(b"authenticated").decode("utf-8")
        headers["Cookie"] = f"jd_session={session_val}"
    except (ImportError, Exception):
        headers["Cookie"] = "jd_session=dev-session"
    return headers


@pytest.fixture
def sample_approval_dict():
    return {
        "id": "approval-test-001",
        "type": "dingtalk_to_jenkins",
        "title": "部署审批 - test-service",
        "content": '{"service":"test","env":"prod"}',
        "status": "pending",
        "jenkins_job_name": "deploy/test-service",
        "dingtalk_process_instance_id": None,
        "approver_user_ids": '["manager01"]',
        "jenkins_build_id": None,
        "approved_by": None,
        "reject_reason": None,
    }


@pytest.fixture
def sample_build_dict():
    return {
        "id": 1,
        "jenkins_build_id": 15,
        "jenkins_queue_id": 42,
        "job_name": "deploy/test-service",
        "approval_id": "approval-test-001",
        "status": "queued",
        "result": None,
        "output_summary": None,
        "triggered_at": None,
        "started_at": None,
        "finished_at": None,
        "duration_ms": None,
    }
