"""AgentManager — asyncio-safe registry of connected Agent sessions.

Matches C++ AgentManager API:
- by_agent: agent_id → AgentSession
- by_project: project → agent_id (1:N routing for BUILD_TRIGGER)
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Callable, Awaitable

from ..crypto.envelope import MessageType

logger = logging.getLogger(__name__)

# Send callback: async (msg_type, payload_json) -> bool (success)
SendCallback = Callable[[MessageType, str], Awaitable[bool]]


@dataclass
class AgentInfo:
    """Metadata about a connected agent."""
    agent_id: str
    projects: list[str]
    connected_at: float
    send: SendCallback


class AgentManager:
    """Asyncio-safe registry of connected Agent connections.

    Supports:
    - Lookup by agent_id
    - Lookup by project name (1:N routing)
    - Message sending (delegates to AgentSession's encrypted send)
    """

    def __init__(self):
        self._lock = asyncio.Lock()
        self._by_agent: dict[str, AgentInfo] = {}
        self._project_to_agent: dict[str, str] = {}
        self._stopped = False

    # ── Registration ─────────────────────────────────────────────

    async def add_agent(
        self, agent_id: str, projects: list[str], send_cb: SendCallback
    ) -> None:
        """Register a newly-handshaked agent."""
        async with self._lock:
            if self._stopped:
                return
            loop = asyncio.get_running_loop()
            info = AgentInfo(
                agent_id=agent_id,
                projects=list(projects),
                connected_at=loop.time(),
                send=send_cb,
            )
            self._by_agent[agent_id] = info
            for p in projects:
                self._project_to_agent[p] = agent_id
            logger.info(
                f"[AgentManager] agent '{agent_id}' registered "
                f"with projects {projects}"
            )

    async def remove_agent(self, agent_id: str) -> None:
        """Remove an agent (on disconnect)."""
        async with self._lock:
            if self._stopped:
                return
            self._by_agent.pop(agent_id, None)
            to_remove = [
                p for p, a in self._project_to_agent.items() if a == agent_id
            ]
            for p in to_remove:
                del self._project_to_agent[p]
            logger.info(f"[AgentManager] agent '{agent_id}' removed")

    # ── Lookup ───────────────────────────────────────────────────

    async def get_by_agent(self, agent_id: str) -> AgentInfo | None:
        async with self._lock:
            return self._by_agent.get(agent_id)

    async def get_by_project(self, project: str) -> AgentInfo | None:
        async with self._lock:
            agent_id = self._project_to_agent.get(project)
            if agent_id is None:
                return None
            return self._by_agent.get(agent_id)

    # ── Send ─────────────────────────────────────────────────────

    async def send_to_agent(
        self, agent_id: str, msg_type: MessageType, payload: str
    ) -> bool:
        """Send encrypted message to a specific agent."""
        info = await self.get_by_agent(agent_id)
        if info is None:
            logger.warning(f"[AgentManager] send_to_agent: '{agent_id}' not found")
            return False
        return await info.send(msg_type, payload)

    async def send_to_project(
        self, project: str, msg_type: MessageType, payload: str
    ) -> bool:
        """Send encrypted message to the agent handling a project."""
        info = await self.get_by_project(project)
        if info is None:
            logger.warning(f"[AgentManager] send_to_project: '{project}' not found")
            return False
        return await info.send(msg_type, payload)

    # ── Introspection ────────────────────────────────────────────

    async def list_agents(self) -> list[str]:
        async with self._lock:
            return list(self._by_agent.keys())

    async def count(self) -> int:
        async with self._lock:
            return len(self._by_agent)

    async def stop_all(self) -> None:
        async with self._lock:
            self._stopped = True
            self._by_agent.clear()
            self._project_to_agent.clear()

    @property
    def stopped(self) -> bool:
        return self._stopped
