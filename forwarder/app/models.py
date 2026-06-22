"""SQLAlchemy models for JD-Relay Forwarder — Phase 3.4.

Tables:
- work_orders: 工单主表
- agents: Agent 注册信息
- approvals: 审批记录
- build_results: 构建结果
"""

import datetime
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, ForeignKey, Enum as SAEnum, Index,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ── Work Order ───────────────────────────────────────────────────

class WorkOrder(Base):
    __tablename__ = "work_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_no: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    issue: Mapped[str] = mapped_column(String(128), nullable=False, comment="JIRA/ISSUE ID")
    project: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    branch: Mapped[str] = mapped_column(String(256), nullable=False)
    build_cmd: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # State
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT", index=True)

    # Relationships
    approvals: Mapped[list["Approval"]] = relationship(back_populates="work_order", cascade="all, delete-orphan")
    build_results: Mapped[list["BuildResult"]] = relationship(back_populates="work_order", cascade="all, delete-orphan")

    # Timestamps
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow, nullable=False
    )

    # Audit
    created_by: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    def __repr__(self):
        return f"<WorkOrder {self.order_no} state={self.state}>"


# ── Agent ────────────────────────────────────────────────────────

class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    projects: Mapped[str] = mapped_column(Text, nullable=False, comment="JSON array of project names")
    ecdsa_pub_pem: Mapped[str] = mapped_column(Text, nullable=False)

    is_online: Mapped[bool] = mapped_column(default=False)
    last_seen_at: Mapped[datetime.datetime | None] = mapped_column(DateTime)
    connected_at: Mapped[datetime.datetime | None] = mapped_column(DateTime)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow, nullable=False
    )

    def __repr__(self):
        return f"<Agent {self.agent_id} online={self.is_online}>"


# ── Approval ─────────────────────────────────────────────────────

class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    work_order_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("work_orders.id"), nullable=False, index=True
    )
    work_order: Mapped["WorkOrder"] = relationship(back_populates="approvals")

    # Approval details
    approver: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="PENDING", comment="PENDING/APPROVED/REJECTED"
    )
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # Review type: "first" (三人审批) or "second" (敏感文件二次审核)
    review_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="first"
    )

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow, nullable=False
    )
    responded_at: Mapped[datetime.datetime | None] = mapped_column(DateTime)

    def __repr__(self):
        return f"<Approval {self.id} by={self.approver} status={self.status}>"


# ── Build Result ─────────────────────────────────────────────────

class BuildResult(Base):
    __tablename__ = "build_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    work_order_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("work_orders.id"), nullable=False, index=True
    )
    work_order: Mapped["WorkOrder"] = relationship(back_populates="build_results")

    build_number: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="PENDING", comment="PENDING/BUILDING/SUCCESS/FAILED"
    )
    log_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    duration_seconds: Mapped[int | None] = mapped_column(Integer)

    started_at: Mapped[datetime.datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime.datetime | None] = mapped_column(DateTime)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow, nullable=False
    )

    def __repr__(self):
        return f"<BuildResult #{self.build_number} status={self.status}>"
