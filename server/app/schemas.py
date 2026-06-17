"""Pydantic 请求/响应模型 (v2)

设计原则：
- 严格的输入验证（不允许多余字段：extra='forbid' 在需要的地方）
- 明确的字段描述和示例
- 类型安全的枚举约束
- 前后端一致的命名风格
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Any, Literal
from pydantic import BaseModel, Field, field_validator


# ═══════════════════════════════════════════
# 钉钉侧 Schema
# ═══════════════════════════════════════════

class SendApprovalRequest(BaseModel):
    """Jenkins → 转发器：发起钉钉审批请求

    由 Jenkins Pipeline 中的 CLI 工具发送，包含加密的回调参数。
    """
    jenkins_job_name: str = Field(..., description="Jenkins Job 名称", min_length=1, max_length=255)
    build_id: int = Field(..., ge=1, description="Jenkins 构建号")
    title: str = Field(..., description="审批标题", min_length=1, max_length=200)
    content: str = Field(default="", description="审批内容详情（Markdown 格式）", max_length=10000)
    approver_user_ids: list[str] = Field(..., min_length=1, description="审批人钉钉 user_id 列表")
    encrypted_payload: str = Field(..., description="AES-GCM 加密的构建回调参数（Base64 编码）")
    signature: str = Field(..., description="HMAC-SHA256 签名（用于验签）")


class SendApprovalResponse(BaseModel):
    """发起审批响应"""
    approval_id: str = Field(..., description="生成的审批单 ID")
    process_instance_id: Optional[str] = Field(None, description="钉钉流程实例 ID")
    status: Literal["pending"] = Field(default="pending", description="审批状态")


class DingTalkCallbackRequest(BaseModel):
    """钉钉 → 转发器：审批结果回调

    钉钉回调格式由钉钉开放平台定义，
    此 schema 仅作占位（实际解析在路由层以原始 JSON 处理）。
    """
    model_config = {"extra": "allow"}


# ═══════════════════════════════════════════
# Jenkins 侧 Schema
# ═══════════════════════════════════════════

class TriggerBuildRequest(BaseModel):
    """转发器 → Jenkins：触发构建请求"""
    job_name: str = Field(..., description="Jenkins Job 名称/路径", min_length=1, max_length=512)
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="构建参数（会传递给 Jenkins Job）",
    )
    encrypted_payload: Optional[str] = Field(
        None,
        description="加密的附加构建参数（可选，会解密后合并到 parameters）",
    )


class TriggerBuildResponse(BaseModel):
    """触发构建响应"""
    build_id: Optional[int] = Field(None, description="Jenkins 构建号（构建完成后填充）")
    queue_id: Optional[int] = Field(None, description="Jenkins 构建队列 ID")
    status: Literal["queued", "error"] = Field(..., description="触发结果状态")
    jenkins_url: Optional[str] = Field(None, description="Jenkins Job 详情页 URL")
    error: Optional[str] = Field(None, description="错误信息（当 status=error 时）")


class BuildCallbackRequest(BaseModel):
    """Jenkins → 转发器：构建结果回调"""
    job_name: str = Field(..., min_length=1, description="Jenkins Job 名称")
    build_id: int = Field(..., ge=0, description="Jenkins 构建号")
    result: Literal["SUCCESS", "FAILURE", "ABORTED"] = Field(..., description="构建结果")
    duration_ms: Optional[int] = Field(None, ge=0, description="构建耗时（毫秒）")
    output_summary: Optional[str] = Field(None, max_length=2000, description="输出摘要")
    related_approval_id: Optional[str] = Field(None, description="关联的审批单 ID")


class BuildStatusResponse(BaseModel):
    """构建状态查询响应"""
    build_id: int = Field(..., ge=0)
    job_name: str = Field(...)
    status: Literal[
        "pending", "queued", "building", "success", "failure",
        "aborted", "not_built", "unknown",
    ] = Field(..., description="Jenkins 构建状态")
    progress_pct: Optional[int] = Field(None, ge=0, le=100, description="进度百分比估算")
    started_at: Optional[datetime] = Field(None)
    estimated_remaining_s: Optional[int] = Field(None, ge=0)


# ═══════════════════════════════════════════
# 加密相关 Schema
# ═══════════════════════════════════════════

class DecryptRequest(BaseModel):
    """解密请求"""
    ciphertext: str = Field(..., description="Base64 编码的密文")
    nonce: str = Field(..., description="Base64 编码的 nonce（12 bytes）")
    signature: str = Field(..., description="HMAC-SHA256 签名")


class DecryptResponse(BaseModel):
    """解密响应"""
    plaintext: str = Field(..., description="解密后的明文")
    verified: bool = Field(..., description="签名是否验证通过")


# ═══════════════════════════════════════════
# Web 面板 Admin API Schema
# ═══════════════════════════════════════════

class DashboardStats(BaseModel):
    """仪表盘统计数据"""
    total_approvals: int = Field(0, ge=0)
    pending_approvals: int = Field(0, ge=0)
    total_builds: int = Field(0, ge=0)
    running_builds: int = Field(0, ge=0)
    success_rate_pct: float = Field(0.0, ge=0.0, le=100.0)


class DashboardResponse(BaseModel):
    """仪表盘完整响应"""
    stats: DashboardStats
    recent_approvals: list[dict] = Field(default_factory=list)
    recent_builds: list[dict] = Field(default_factory=list)
    uptime_seconds: int = Field(0, ge=0)


class ApprovalItem(BaseModel):
    """审批条目"""
    id: str
    type: Literal["dingtalk_to_jenkins", "jenkins_to_dingtalk"]
    title: str
    status: Literal["pending", "approved", "rejected", "cancelled", "expired"]
    jenkins_job_name: Optional[str] = None
    requested_by: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class PaginatedApprovals(BaseModel):
    """分页审批列表"""
    items: list[dict]
    total: int = Field(..., ge=0)
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1, le=100)


class BuildItem(BaseModel):
    """构建条目"""
    id: int
    jenkins_build_id: Optional[int] = None
    job_name: str
    status: Literal["pending", "queued", "building", "success", "failure", "aborted"]
    result: Optional[Literal["SUCCESS", "FAILURE", "ABORTED"]] = None
    triggered_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    duration_ms: Optional[int] = None


class PaginatedBuilds(BaseModel):
    """分页构建列表"""
    items: list[dict]
    total: int = Field(..., ge=0)
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1, le=100)


class LogItem(BaseModel):
    """日志条目"""
    id: int
    timestamp: datetime
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(...)
    source: Literal["dingtalk", "jenkins", "relay", "system"] = Field(...)
    action: str
    detail: Optional[str] = None
    is_encrypted: bool = False
    duration_ms: Optional[int] = None


class PaginatedLogs(BaseModel):
    """分页日志列表"""
    items: list[dict]
    total: int = Field(..., ge=0)
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1, le=100)


class ConfigUpdateRequest(BaseModel):
    """配置更新请求"""
    updates: dict[str, str] = Field(..., description="key-value 映射，value 会被加密存储")

    @field_validator("updates")
    @classmethod
    def validate_updates_not_empty(cls, v: dict) -> dict:
        if not v:
            raise ValueError("updates 不能为空")
        if len(v) > 20:
            raise ValueError("单次更新不能超过 20 个配置项")
        return v


# ═══════════════════════════════════════════
# 通用 Schema
# ═══════════════════════════════════════════

class HealthResponse(BaseModel):
    """健康检查响应"""
    status: Literal["ok", "degraded"] = Field(default="ok")
    version: str = "2.0.0"
    uptime_seconds: Optional[int] = Field(None, ge=0)


class ErrorResponse(BaseModel):
    """标准错误响应"""
    detail: str
    error_type: Optional[str] = None
    request_id: Optional[str] = None
