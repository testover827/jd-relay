"""ForwarderServer — FastAPI application with WebSocket and REST endpoints.

Matches C++ WsServer functionality:
- /agent-ws: WebSocket endpoint for Agent connections
- REST API stubs for Phase 3 (DingTalk / Jenkins)
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, WebSocket, HTTPException
from fastapi.responses import JSONResponse

from ..crypto.envelope import MessageType
from .agent_manager import AgentManager
from .agent_handler import handle_agent_connection, MessageCallback, ConnectCallback

logger = logging.getLogger(__name__)


class ForwarderServer:
    """JD-Relay Forwarder server (Python).

    Wraps a FastAPI app with WebSocket support for Agent connections.
    Provides the same API as C++ WsServer: send_to_agent, send_to_project.
    """

    def __init__(
        self,
        ecdsa_priv_file: str,
        ecdsa_pub_file: str,
        message_cb: MessageCallback | None = None,
        connect_cb: ConnectCallback | None = None,
    ):
        self._ecdsa_priv_file = ecdsa_priv_file
        self._ecdsa_pub_file = ecdsa_pub_file
        self._user_message_cb = message_cb
        self._user_connect_cb = connect_cb

        self.manager = AgentManager()
        self._app = self._create_app()

    @property
    def app(self) -> FastAPI:
        """The FastAPI application instance."""
        return self._app

    # ── Send helpers (delegate to AgentManager) ──────────────────

    async def send_to_agent(
        self, agent_id: str, msg_type: MessageType, payload: str
    ) -> bool:
        return await self.manager.send_to_agent(agent_id, msg_type, payload)

    async def send_to_project(
        self, project: str, msg_type: MessageType, payload: str
    ) -> bool:
        return await self.manager.send_to_project(project, msg_type, payload)

    # ── App factory ──────────────────────────────────────────────

    def _create_app(self) -> FastAPI:
        """Build the FastAPI application with all routes."""

        @asynccontextmanager
        async def lifespan(app: FastAPI):
            logger.info("[Forwarder] Starting up...")
            yield
            logger.info("[Forwarder] Shutting down...")
            await self.manager.stop_all()

        app = FastAPI(
            title="JD-Relay Forwarder",
            version="2.0.0",
            lifespan=lifespan,
        )

        # ── WebSocket endpoint ───────────────────────────────────
        @app.websocket("/agent-ws")
        async def agent_ws(websocket: WebSocket):
            """Agent WebSocket connection endpoint.

            Matches C++ WsServer's accept loop:
            - Accepts WebSocket upgrade
            - Performs ECDH+ECDSA handshake (in handle_agent_connection)
            - Registers agent with manager
            - Runs encrypted I/O loop
            """
            await handle_agent_connection(
                websocket=websocket,
                manager=self.manager,
                ecdsa_priv_file=self._ecdsa_priv_file,
                ecdsa_pub_file=self._ecdsa_pub_file,
                message_cb=self._user_message_cb,
                connect_cb=self._user_connect_cb,
            )

        # ── Health check ─────────────────────────────────────────
        @app.get("/health")
        async def health():
            return {"status": "ok", "agents": await self.manager.count()}

        # ── Agent status ─────────────────────────────────────────
        @app.get("/api/agents")
        async def list_agents():
            agents = await self.manager.list_agents()
            return {"agents": agents, "count": len(agents)}

        # ── REST API routes (Phase 3) ────────────────────────────
        from ..api.dingtalk import router as dt_router
        app.include_router(dt_router)

        @app.post("/api/build/trigger")
        async def trigger_build(agent_id: str, project: str, payload: dict):
            """Trigger a build via an agent."""
            import json
            success = await self.send_to_project(
                project,
                MessageType.BUILD_TRIGGER,
                json.dumps(payload),
            )
            if not success:
                raise HTTPException(404, f"No agent for project '{project}'")
            return {"status": "sent", "project": project}

        return app


def create_app(
    ecdsa_priv_file: str,
    ecdsa_pub_file: str,
    message_cb: MessageCallback | None = None,
    connect_cb: ConnectCallback | None = None,
) -> FastAPI:
    """Convenience: create a ForwarderServer and return the FastAPI app."""
    server = ForwarderServer(
        ecdsa_priv_file=ecdsa_priv_file,
        ecdsa_pub_file=ecdsa_pub_file,
        message_cb=message_cb,
        connect_cb=connect_cb,
    )
    return server.app
