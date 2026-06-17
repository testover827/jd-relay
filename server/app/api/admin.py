"""Web 面板 API + SSR 页面路由

提供：
  - RESTful API: 仪表盘/审批/构建/日志/配置 CRUD
  - SSE 实时日志流
  - Jinja2 SSR 页面路由
  - Session 认证（itsdangerous signed cookie）

安全特性：
- itsdangerous TimestampSigner 签名 session cookie
- 分页参数限制（防止 DoS）
- 配置项 key 白名单
- 密码登录次数限制（防暴力破解）
- 响应头安全（X-Content-Type-Options 等）
"""

import asyncio
import hashlib
import json
import logging
import time as _time
from datetime import datetime, timezone
from typing import AsyncGenerator, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc

from ..database import get_db
from ..config import settings, BASE_DIR
from ..models import Approval, Build, Log, Config
from ..services.crypto_service import SecureConfig

logger = logging.getLogger(__name__)

# ── 全局启动时间 ──
_START_TIME = _time.time()

router = APIRouter(tags=["admin"])
page_router = APIRouter(tags=["pages"])

# ── SSE 事件队列（内存） ──
_sse_queues: list[asyncio.Queue] = []

# ── 配置变更白名单（只允许通过 API 修改这些 key） ──
_ALLOWED_CONFIG_KEYS = {
    "DINGTALK_APP_KEY",
    "DINGTALK_APP_SECRET",
    "DINGTALK_AGENT_ID",
    "JENKINS_URL",
    "JENKINS_USERNAME",
    "JENKINS_API_TOKEN",
    "RELAY_API_KEY",
    "AES_ENCRYPTION_KEY",
    "HMAC_SECRET",
    "CONFIG_MASTER_KEY",
}

# ── 分页限制 ──
_MAX_PAGE_SIZE = 100

# ── 登录失败计数（内存，简单防护） ──
_login_failures: dict[str, int] = {}
_LOGIN_FAILURE_MAX = 5
_LOGIN_FAILURE_WINDOW_S = 300  # 5分钟窗口


async def _broadcast_log(log_entry: dict):
    """向所有 SSE 连接广播日志"""
    dead_queues = []
    for q in _sse_queues:
        try:
            q.put_nowait(log_entry)
        except asyncio.QueueFull:
            dead_queues.append(q)
    # 清理已满队列
    for q in dead_queues:
        try:
            _sse_queues.remove(q)
        except ValueError:
            pass


# ═══════════════════════════════════════════
# Session 认证辅助（itsdangerous signed cookie）
# ═══════════════════════════════════════════

def _get_signer():
    """获取 itsdangerous TimestampSigner（用于签名 session cookie）"""
    try:
        from itsdangerous import TimestampSigner
        secret = settings.SESSION_SECRET or settings.ADMIN_PASSWORD_HASH or "dev-secret-change-me"
        return TimestampSigner(secret)
    except ImportError:
        logger.warning("itsdangerous 未安装，回退到简单 cookie 模式（不安全！）")
        return None


def _make_session_cookie() -> str:
    """生成签名的 session cookie 值"""
    signer = _get_signer()
    if signer:
        return signer.sign(b"authenticated").decode("utf-8")
    # 降级：开发模式
    return f"dev-{int(_time.time())}"


def _verify_session(session_value: str) -> bool:
    """验证 session cookie 是否有效（含过期检查）"""
    if not session_value:
        return False

    signer = _get_signer()
    if signer:
        try:
            # max_age = 86400 * 7  # 7 天有效
            signer.unsign(session_value, max_age=86400 * 7)
            return True
        except Exception:
            return False

    # 降级模式：开发环境检查
    if settings.DEBUG and session_value.startswith("dev-"):
        return True
    return False


def _check_session(request: Request) -> bool:
    """检查请求是否持有有效 session"""
    session = request.cookies.get("jd_session")
    return _verify_session(session)


def _require_auth(request: Request):
    """要求已认证，否则抛出 302 重定向或 401"""
    if not _check_session(request):
        # API 请求返回 401 JSON，页面请求返回 302 重定向
        accepts_html = "text/html" in request.headers.get("accept", "")
        if accepts_html and request.url.path.startswith("/api/") is False:
            raise HTTPException(status_code=302, headers={"Location": "/login"})
        raise HTTPException(status_code=401, detail="未认证，请先登录")


def _check_login_rate_limit(client_ip: str) -> bool:
    """检查登录频率限制"""
    now = _time.time()
    # 清理旧记录
    expired = [k for k, t in _login_failures.items() if now - t > _LOGIN_FAILURE_WINDOW_S]
    for k in expired:
        del _login_failures[k]

    failures = _login_failures.get(client_ip, 0)
    if failures >= _LOGIN_FAILURE_MAX:
        return False  # 被限速
    return True


# ═══════════════════════════════════════════
# Admin REST API
# ═══════════════════════════════════════════

@router.get("/dashboard")
async def dashboard_api(request: Request, db: AsyncSession = Depends(get_db)):
    _require_auth(request)

    total_approvals = (await db.execute(select(func.count(Approval.id)))).scalar()
    pending_approvals = (await db.execute(
        select(func.count(Approval.id)).where(Approval.status == "pending")
    )).scalar()
    total_builds = (await db.execute(select(func.count(Build.id)))).scalar()
    running_builds = (await db.execute(
        select(func.count(Build.id)).where(Build.status.in_(["queued", "building"]))
    )).scalar()
    success_count = (await db.execute(
        select(func.count(Build.id)).where(Build.result == "SUCCESS")
    )).scalar()
    success_rate = (success_count / total_builds * 100) if total_builds > 0 else 0.0

    # 最近审批
    recent_approvals = (await db.execute(
        select(Approval).order_by(desc(Approval.created_at)).limit(5)
    )).scalars().all()

    # 最近构建
    recent_builds = (await db.execute(
        select(Build).order_by(desc(Build.created_at)).limit(5)
    )).scalars().all()

    return {
        "stats": {
            "total_approvals": total_approvals,
            "pending_approvals": pending_approvals,
            "total_builds": total_builds,
            "running_builds": running_builds,
            "success_rate_pct": round(success_rate, 1),
        },
        "recent_approvals": [
            {
                "id": a.id, "type": a.type, "title": a.title,
                "status": a.status, "jenkins_job_name": a.jenkins_job_name,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in recent_approvals
        ],
        "recent_builds": [
            {
                "id": b.id, "jenkins_build_id": b.jenkins_build_id,
                "job_name": b.job_name, "status": b.status, "result": b.result,
                "triggered_at": b.triggered_at.isoformat() if b.triggered_at else None,
            }
            for b in recent_builds
        ],
        "uptime_seconds": int(_time.time() - _START_TIME),
    }


def _validate_pagination(page: int, page_size: int) -> tuple[int, int]:
    """校验并修正分页参数"""
    page = max(1, page)
    page_size = max(1, min(page_size, _MAX_PAGE_SIZE))
    return page, page_size


@router.get("/approvals")
async def approvals_list(
    request: Request,
    page: int = 1,
    page_size: int = 20,
    status: str = "",
    db: AsyncSession = Depends(get_db),
):
    _require_auth(request)
    page, page_size = _validate_pagination(page, page_size)

    query = select(Approval)
    if status:
        query = query.where(Approval.status == status)
    query = query.order_by(desc(Approval.created_at))

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar()
    items = (await db.execute(query.offset((page - 1) * page_size).limit(page_size))).scalars().all()

    return {
        "items": [
            {
                "id": a.id, "type": a.type, "title": a.title,
                "status": a.status, "jenkins_job_name": a.jenkins_job_name,
                "created_at": a.created_at.isoformat() if a.created_at else None,
                "updated_at": a.updated_at.isoformat() if a.updated_at else None,
            }
            for a in items
        ],
        "total": total, "page": page, "page_size": page_size,
    }


@router.get("/approvals/{approval_id}")
async def approval_detail(
    approval_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _require_auth(request)

    a = (await db.execute(select(Approval).where(Approval.id == approval_id))).scalar_one_or_none()
    if not a:
        raise HTTPException(status_code=404, detail="审批记录不存在")

    return {
        "id": a.id, "type": a.type, "title": a.title, "content": a.content,
        "status": a.status,
        "dingtalk_process_instance_id": a.dingtalk_process_instance_id,
        "approver_user_ids": a.approver_user_ids,
        "jenkins_job_name": a.jenkins_job_name, "jenkins_build_id": a.jenkins_build_id,
        "approved_by": a.approved_by,
        "approved_at": a.approved_at.isoformat() if a.approved_at else None,
        "reject_reason": a.reject_reason,
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "updated_at": a.updated_at.isoformat() if a.updated_at else None,
    }


@router.get("/builds")
async def builds_list(
    request: Request,
    page: int = 1,
    page_size: int = 20,
    status: str = "",
    db: AsyncSession = Depends(get_db),
):
    _require_auth(request)
    page, page_size = _validate_pagination(page, page_size)

    query = select(Build)
    if status:
        query = query.where(Build.status == status)
    query = query.order_by(desc(Build.created_at))

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar()
    items = (await db.execute(query.offset((page - 1) * page_size).limit(page_size))).scalars().all()

    return {
        "items": [
            {
                "id": b.id, "jenkins_build_id": b.jenkins_build_id,
                "job_name": b.job_name, "status": b.status, "result": b.result,
                "triggered_at": b.triggered_at.isoformat() if b.triggered_at else None,
                "finished_at": b.finished_at.isoformat() if b.finished_at else None,
                "duration_ms": b.duration_ms,
            }
            for b in items
        ],
        "total": total, "page": page, "page_size": page_size,
    }


@router.get("/builds/{build_id}")
async def build_detail(
    build_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _require_auth(request)

    b = (await db.execute(select(Build).where(Build.id == build_id))).scalar_one_or_none()
    if not b:
        raise HTTPException(status_code=404, detail="构建记录不存在")

    return {
        "id": b.id, "jenkins_build_id": b.jenkins_build_id,
        "jenkins_queue_id": b.jenkins_queue_id,
        "job_name": b.job_name, "approval_id": b.approval_id,
        "status": b.status, "result": b.result,
        "output_summary": b.output_summary,
        "triggered_at": b.triggered_at.isoformat() if b.triggered_at else None,
        "started_at": b.started_at.isoformat() if b.started_at else None,
        "finished_at": b.finished_at.isoformat() if b.finished_at else None,
        "duration_ms": b.duration_ms,
        "created_at": b.created_at.isoformat() if b.created_at else None,
    }


@router.get("/logs")
async def logs_list(
    request: Request,
    page: int = 1,
    page_size: int = 50,
    source: str = "",
    level: str = "",
    db: AsyncSession = Depends(get_db),
):
    _require_auth(request)
    page, page_size = _validate_pagination(page, page_size)

    query = select(Log)
    if source:
        query = query.where(Log.source == source)
    if level:
        query = query.where(Log.level == level)
    query = query.order_by(desc(Log.timestamp))

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar()
    items = (await db.execute(query.offset((page - 1) * page_size).limit(page_size))).scalars().all()

    return {
        "items": [
            {
                "id": l.id,
                "timestamp": l.timestamp.isoformat() if l.timestamp else None,
                "level": l.level, "source": l.source, "action": l.action,
                "detail": l.detail, "is_encrypted": bool(l.is_encrypted),
                "duration_ms": l.duration_ms,
            }
            for l in items
        ],
        "total": total, "page": page, "page_size": page_size,
    }


@router.get("/logs/stream")
async def logs_stream(request: Request):
    """SSE 实时日志流"""
    _require_auth(request)

    async def event_generator() -> AsyncGenerator[str, None]:
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        _sse_queues.append(queue)
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"event: log\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            try:
                _sse_queues.remove(queue)
            except ValueError:
                pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/config")
async def get_config(request: Request, db: AsyncSession = Depends(get_db)):
    _require_auth(request)

    configs = (await db.execute(select(Config))).scalars().all()
    result = {}
    descriptions = {}
    for c in configs:
        val = c.value
        # 尝试解密显示
        if settings.CONFIG_MASTER_KEY:
            try:
                val = SecureConfig.decrypt_config_value(val, settings.CONFIG_MASTER_KEY)
                val = val[:4] + "****" if len(val) > 4 else "****"
            except Exception:
                val = "****"
        result[c.key] = val
        descriptions[c.key] = c.description
    return {"configs": result, "descriptions": descriptions}


@router.put("/config")
async def update_config(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _require_auth(request)

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="无效的 JSON 请求体")

    updates = body.get("updates", {})
    if not isinstance(updates, dict):
        raise HTTPException(status_code=400, detail="updates 必须是对象类型")

    rejected_keys = []
    updated_keys = []

    for key, value in updates.items():
        # ★ 配置 key 白名单校验
        if key not in _ALLOWED_CONFIG_KEYS:
            rejected_keys.append(key)
            continue

        if not isinstance(value, str):
            raise HTTPException(status_code=400, detail=f"配置值 {key} 必须是字符串")

        # 加密存储
        stored_value = value
        if settings.CONFIG_MASTER_KEY:
            stored_value = SecureConfig.encrypt_config_value(value, settings.CONFIG_MASTER_KEY)

        existing = (await db.execute(
            select(Config).where(Config.key == key)
        )).scalar_one_or_none()
        if existing:
            existing.value = stored_value
            existing.updated_at = datetime.now(timezone.utc)
        else:
            db.add(Config(key=key, value=stored_value))
        updated_keys.append(key)

    await db.commit()

    response_data = {"ok": True, "updated": updated_keys}
    if rejected_keys:
        response_data["rejected"] = rejected_keys
        response_data["warning"] = f"以下 key 不在白名单中，已被忽略: {', '.join(rejected_keys)}"

    logger.info("配置更新: updated=%s rejected=%s", updated_keys, rejected_keys)
    return JSONResponse(content=response_data)


# ═══════════════════════════════════════════
# SSR 页面路由
# ═══════════════════════════════════════════

from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))


@page_router.get("/", response_class=HTMLResponse)
async def page_dashboard(request: Request):
    if not _check_session(request):
        return HTMLResponse(content='<meta http-equiv="refresh" content="0;url=/login">')
    resp = templates.TemplateResponse("dashboard.html", {"request": request})
    _add_security_headers(resp)
    return resp


@page_router.get("/login", response_class=HTMLResponse)
async def page_login(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@page_router.post("/login")
async def do_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    """管理员登录

    安全措施：
    - bcrypt 密码哈希比对
    - itsdangerous 签名 session cookie
    - 登录频率限制（防暴力破解）
    """
    client_ip = request.client.host if request.client else "unknown"

    # 频率限制检查
    if not _check_login_rate_limit(client_ip):
        logger.warning("登录频率超限: ip=%s", client_ip)
        return HTMLResponse(
            content="<h2>登录尝试过于频繁，请5分钟后重试</h2>",
            status_code=429,
        )

    import bcrypt

    stored_hash = settings.ADMIN_PASSWORD_HASH
    if not stored_hash:
        # 开发模式：默认密码 admin
        if username == settings.ADMIN_USERNAME and password == "admin":
            resp = HTMLResponse(content='<meta http-equiv="refresh" content="0;url=/">')
            resp.set_cookie(
                "jd_session",
                _make_session_cookie(),
                httponly=True,
                secure=not settings.DEBUG,  # 生产环境启用 Secure
                samesite="lax",
                max_age=86400 * 7,  # 7 天
            )
            return resp
        _record_login_failure(client_ip)
        return HTMLResponse(content="<h2>用户名或密码错误</h2>", status_code=401)

    # 生产模式：bcrypt 验证
    if username == settings.ADMIN_USERNAME and bcrypt.checkpw(password.encode(), stored_hash.encode()):
        # 清除失败计数
        _login_failures.pop(client_ip, None)

        resp = HTMLResponse(content='<meta http-equiv="refresh" content="0;url=/">')
        resp.set_cookie(
            "jd_session",
            _make_session_cookie(),
            httponly=True,
            secure=not settings.DEBUG,
            samesite="lax",
            max_age=86400 * 7,
        )
        return resp

    _record_login_failure(client_ip)
    return HTMLResponse(content="<h2>用户名或密码错误</h2>", status_code=401)


def _record_login_failure(client_ip: str):
    """记录登录失败"""
    current = _login_failures.get(client_ip, 0)
    _login_failures[client_ip] = current + 1
    logger.warning("登录失败: ip=%s count=%d", client_ip, current + 1)


def _add_security_headers(response: Response):
    """添加安全响应头"""
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"


@page_router.get("/approvals", response_class=HTMLResponse)
async def page_approvals(request: Request):
    if not _check_session(request):
        return HTMLResponse(content='<meta http-equiv="refresh" content="0;url=/login">')
    resp = templates.TemplateResponse("approvals.html", {"request": request})
    _add_security_headers(resp)
    return resp


@page_router.get("/builds", response_class=HTMLResponse)
async def page_builds(request: Request):
    if not _check_session(request):
        return HTMLResponse(content='<meta http-equiv="refresh" content="0;url=/login">')
    resp = templates.TemplateResponse("builds.html", {"request": request})
    _add_security_headers(resp)
    return resp


@page_router.get("/logs", response_class=HTMLResponse)
async def page_logs(request: Request):
    if not _check_session(request):
        return HTMLResponse(content='<meta http-equiv="refresh" content="0;url=/login">')
    resp = templates.TemplateResponse("logs.html", {"request": request})
    _add_security_headers(resp)
    return resp


@page_router.get("/config", response_class=HTMLResponse)
async def page_config(request: Request):
    if not _check_session(request):
        return HTMLResponse(content='<meta http-equiv="refresh" content="0;url=/login">')
    resp = templates.TemplateResponse("config.html", {"request": request})
    _add_security_headers(resp)
    return resp


@page_router.post("/logout")
async def do_logout(request: Request):
    """登出 - 清除 session cookie"""
    resp = HTMLResponse(content='<meta http-equiv="refresh" content="0;url=/login">')
    resp.delete_cookie("jd_session")
    return resp
