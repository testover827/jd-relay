"""SQLAlchemy ORM 模型 — 4 张核心表

ER 关系：
  approvals ◄────┬──► builds (1:1, optional)
               │
              logs (独立审计)
              config (键值存储)

索引策略：
  - approvals: status + created_at (高频查询)
  - builds: status + job_name + approval_id
  - logs: timestamp + source + level (时序查询)
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column, Integer, Text, DateTime, Index, ForeignKey,
    ForeignKeyConstraint, String, Boolean,
)
from sqlalchemy.orm import relationship

from .database import Base


def _uuid() -> str:
    """生成 UUID 主键"""
    return str(uuid.uuid4())


def _now() -> datetime:
    """UTC 当前时间"""
    return datetime.now(timezone.utc)


class Approval(Base):
    """审批记录表

    记录所有钉钉/Jenkins 双向审批流程的审批单据。
    支持两种流程方向：
    - dingtalk_to_jenkins: 钉钉审批 → Jenkins 构建
    - jenkins_to_dingtalk: Jenkins 构建 → 钉钉审批
    """
    __tablename__ = "approvals"

    id = Column(Text, primary_key=True, default=_uuid)
    type = Column(String(50), nullable=False)  # 'dingtalk_to_jenkins' | 'jenkins_to_dingtalk'
    title = Column(Text, nullable=False)
    content = Column(Text)                      # JSON 字符串（审批详情）

    # 钉钉侧字段
    dingtalk_process_instance_id = Column(Text)  # 钉钉流程实例 ID
    approver_user_ids = Column(Text)              # JSON 数组（审批人 ID 列表）

    # Jenkins 侧字段
    jenkins_job_name = Column(Text)
    jenkins_build_id = Column(Integer)            # ★ 添加外键关联
    callback_payload_encrypted = Column(Text)     # 回调数据（AES-GCM 加密存储）
    callback_nonce = Column(Text)

    # 状态机: pending → approved/rejected/cancelled/expired
    status = Column(String(20), nullable=False, default="pending")

    # 审批结果
    approved_by = Column(Text)                    # 审批人标识
    approved_at = Column(DateTime)
    reject_reason = Column(Text)                  # 驳回原因

    # 审计时间戳
    created_at = Column(DateTime, nullable=False, default=_now)
    updated_at = Column(DateTime, nullable=False, default=_now, onupdate=_now)

    # ── 关系 ──
    # 单向关系：Approval → Build（通过 jenkins_build_id FK）
    # Build.approval 是独立的关系（通过 approval_id FK）
    # 使用 viewonly=True 避免双向 many-to-one 冲突，仅用于查询
    build = relationship("Build", uselist=False,
                         foreign_keys=[jenkins_build_id],
                         primaryjoin="Approval.jenkins_build_id == Build.id",
                         viewonly=True)

    __table_args__ = (
        # ★ 外键约束
        ForeignKeyConstraint(["jenkins_build_id"], ["builds.id"], ondelete="SET NULL"),
        Index("idx_approvals_status", "status"),
        Index("idx_approvals_jenkins_build_id", "jenkins_build_id"),
        Index("idx_approvals_created_at", "created_at"),
        Index("idx_approvals_type_status", "type", "status"),
    )


class Build(Base):
    """Jenkins 构建记录表

    记录每次 Jenkins Job 的触发、执行、结果全生命周期。
    """
    __tablename__ = "builds"

    id = Column(Integer, primary_key=True, autoincrement=True)
    jenkins_build_id = Column(Integer, index=True)       # Jenkins 内部构建号
    jenkins_queue_id = Column(Integer, index=True)        # Jenkins 队列 ID
    job_name = Column(Text, nullable=False, index=True)

    approval_id = Column(Text, ForeignKey("approvals.id", ondelete="SET NULL"))

    # 构建参数（加密存储）
    params_encrypted = Column(Text)

    # 状态机: pending → queued → building → success/failure/aborted
    status = Column(String(20), nullable=False, default="pending")

    # 时间线
    triggered_at = Column(DateTime)                        # 入队时间
    started_at = Column(DateTime)                          # 开始执行
    finished_at = Column(DateTime)                        # 完成/终止
    duration_ms = Column(Integer)                         # 执行时长(ms)

    # 结果
    result = Column(String(20))                            # SUCCESS | FAILURE | ABORTED
    output_summary = Column(Text)                          # 输出摘要（最后 1KB）

    # 审计
    created_at = Column(DateTime, nullable=False, default=_now)
    updated_at = Column(DateTime, nullable=False, default=_now, onupdate=_now)

    # ── 关系 ──
    # 单向关系：Build → Approval（通过 approval_id FK）
    approval = relationship("Approval", foreign_keys=[approval_id])

    __table_args__ = (
        Index("idx_builds_status", "status"),
        Index("idx_builds_approval_id", "approval_id"),
        Index("idx_builds_job_status", "job_name", "status"),
    )


class Log(Base):
    """请求/操作审计日志表

    由 RequestLoggingMiddleware 自动写入，
    记录所有 API 调用的详细信息用于审计和排障。
    """
    __tablename__ = "logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, nullable=False, default=_now, index=True)

    # 合法值: DEBUG | INFO | WARNING | ERROR
    level = Column(String(10), nullable=False, default="INFO")

    # 来源: dingtalk | jenkins | relay | system
    source = Column(String(20), nullable=False, index=True)
    action = Column(Text, nullable=False)                 # "POST /api/v1/dingtalk/callback"
    detail = Column(Text)                                 # "HTTP 200"
    payload_snippet = Column(Text)                        # 脱敏后的请求体摘要
    is_encrypted = Column(Boolean, default=False)         # ★ Boolean 替代 Integer
    duration_ms = Column(Integer)                         # 请求耗时(ms)
    request_id = Column(String(8))                        # 关联请求追踪 ID

    __table_args__ = (
        Index("idx_logs_source_level", "source", "level"),
        Index("idx_logs_timestamp", "timestamp"),
    )


class Config(Base):
    """运行时配置表

    敏感值（密码/token/密钥）使用 AES 加密存储。
    通过 Admin API 管理，支持热更新（无需重启）。
    """
    __tablename__ = "config"

    key = Column(Text, primary_key=True)
    value = Column(Text, nullable=False)                   # 敏感值加密存储
    description = Column(Text)                             # 用途说明
    updated_at = Column(DateTime, nullable=False, default=_now, onupdate=_now)
