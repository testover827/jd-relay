"""ForwarderServer — FastAPI 应用（WebSocket + REST + Web 管理面板）

v3.0 改动：
- 集成 Jinja2 Web 管理面板（/admin/）
- 集成 Admin API（/api/admin/）
- 集成 DingTalk API（/api/dingtalk/）
- 启动时初始化数据库连接，注入 DingTalkService 到 app.state
- 保持 WebSocket 加密通信（/agent-ws）
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, WebSocket, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import os

from ..crypto.envelope import MessageType
from .agent_manager import AgentManager
from .agent_handler import handle_agent_connection, MessageCallback, ConnectCallback

logger = logging.getLogger(__name__)

# Templates directory (relative to this file: ../templates)
_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "..", "templates")
_STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "static")


class ForwarderServer:
    """JD-Relay Forwarder 服务器

    集成：
    - WebSocket /agent-ws（ECDH 握手 + 加密通信）
    - REST API /api/dingtalk/, /api/admin/
    - Web 管理面板 /admin/（Jinja2 + Tailwind）
    """

    def __init__(
        self,
        ecdsa_priv_file: str,
        ecdsa_pub_file: str,
        config=None,
        message_cb: MessageCallback | None = None,
        connect_cb: ConnectCallback | None = None,
    ):
        self._ecdsa_priv_file = ecdsa_priv_file
        self._ecdsa_pub_file = ecdsa_pub_file
        self._config = config
        self._user_message_cb = message_cb
        self._user_connect_cb = connect_cb

        self.manager = AgentManager()
        self._app = self._create_app()

    @property
    def app(self) -> FastAPI:
        return self._app

    # ── Send helpers ──────────────────────────────────────────────

    async def send_to_agent(
        self, agent_id: str, msg_type: MessageType, payload: str
    ) -> bool:
        return await self.manager.send_to_agent(agent_id, msg_type, payload)

    async def send_to_project(
        self, project: str, msg_type: MessageType, payload: str
    ) -> bool:
        return await self.manager.send_to_project(project, msg_type, payload)

    # ── App Factory ───────────────────────────────────────────────

    def _create_app(self) -> FastAPI:

        @asynccontextmanager
        async def lifespan(app: FastAPI):
            logger.info("[Forwarder] Starting up v3.0 ...")

            # 注入 config
            app.state.config = self._config
            app.state.agent_manager = self.manager

            # 初始化数据库
            if self._config and self._config.mysql_url:
                try:
                    from ..database import create_engine, create_session_factory, init_db
                    engine = create_engine(self._config.mysql_url)
                    create_session_factory(engine)
                    await init_db(engine)
                    app.state.db_engine = engine
                    logger.info("[Forwarder] Database initialized: %s", self._config.mysql_url)
                except Exception as e:
                    logger.warning("[Forwarder] Database init failed (continuing): %s", e)

            # 初始化 DingTalk SDK service
            if self._config and self._config.dingtalk_app_key:
                try:
                    from ..services.dingtalk import DingTalkService
                    dt_svc = DingTalkService(
                        app_key=self._config.dingtalk_app_key,
                        app_secret=self._config.dingtalk_app_secret,
                        agent_id=self._config.dingtalk_agent_id,
                    )
                    app.state.dingtalk_service = dt_svc
                    logger.info("[Forwarder] DingTalk SDK service initialized")
                except Exception as e:
                    logger.warning("[Forwarder] DingTalk SDK init failed: %s", e)

            yield

            logger.info("[Forwarder] Shutting down ...")
            await self.manager.stop_all()
            if hasattr(app.state, "db_engine"):
                await app.state.db_engine.dispose()

        app = FastAPI(
            title="JD-Relay Forwarder",
            version="3.0.0",
            lifespan=lifespan,
        )

        # ── Static files ─────────────────────────────────────────
        static_dir = os.path.abspath(_STATIC_DIR)
        if os.path.isdir(static_dir):
            app.mount("/static", StaticFiles(directory=static_dir), name="static")

        # ── Jinja2 templates ──────────────────────────────────────
        templates_dir = os.path.abspath(_TEMPLATES_DIR)
        templates = Jinja2Templates(directory=templates_dir)

        # ── WebSocket endpoint ────────────────────────────────────
        @app.websocket("/agent-ws")
        async def agent_ws(websocket: WebSocket):
            await handle_agent_connection(
                websocket=websocket,
                manager=self.manager,
                ecdsa_priv_file=self._ecdsa_priv_file,
                ecdsa_pub_file=self._ecdsa_pub_file,
                message_cb=self._user_message_cb,
                connect_cb=self._user_connect_cb,
            )

        # ── Health check ──────────────────────────────────────────
        @app.get("/health")
        async def health():
            return {"status": "ok", "agents": await self.manager.count(), "version": "3.0.0"}

        # ── Agent status API ──────────────────────────────────────
        @app.get("/api/agents")
        async def list_agents_api():
            agents = await self.manager.list_agents()
            return {"agents": agents, "count": len(agents)}

        # ── Build trigger API ─────────────────────────────────────
        @app.post("/api/build/trigger")
        async def trigger_build_api(project: str, payload: dict):
            import json
            success = await self.send_to_project(
                project, MessageType.BUILD_TRIGGER, json.dumps(payload)
            )
            if not success:
                raise HTTPException(404, f"No agent for project '{project}'")
            return {"status": "sent", "project": project}

        # ── REST API routers ──────────────────────────────────────
        from ..api.dingtalk import router as dt_router
        from ..api.admin import router as admin_api_router
        app.include_router(dt_router)
        app.include_router(admin_api_router)

        # ── Web 管理面板 ──────────────────────────────────────────

        @app.get("/", response_class=HTMLResponse)
        async def page_dashboard(request: Request):
            return templates.TemplateResponse(
                "dashboard.html", {"request": request, "active_page": "dashboard"}
            )

        @app.get("/admin/orders", response_class=HTMLResponse)
        async def page_orders(request: Request):
            return templates.TemplateResponse(
                "orders.html", {"request": request, "active_page": "orders"}
            )

        @app.get("/admin/agents", response_class=HTMLResponse)
        async def page_agents(request: Request):
            return templates.TemplateResponse(
                "agents.html", {"request": request, "active_page": "agents"}
            )

        return app


def create_app(
    ecdsa_priv_file: str,
    ecdsa_pub_file: str,
    config=None,
    message_cb: MessageCallback | None = None,
    connect_cb: ConnectCallback | None = None,
) -> FastAPI:
    """Convenience factory: create ForwarderServer and return the FastAPI app."""
    server = ForwarderServer(
        ecdsa_priv_file=ecdsa_priv_file,
        ecdsa_pub_file=ecdsa_pub_file,
        config=config,
        message_cb=message_cb,
        connect_cb=connect_cb,
    )
    return server.app
