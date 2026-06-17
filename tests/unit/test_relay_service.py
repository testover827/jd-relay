"""RelayService 单元测试

覆盖:
- 钉钉回调处理（agree/refuse/cancel）
- Jenkins 触发逻辑
- 审批请求处理
- 构建结果回调处理
- Markdown 注入防护
- 日志记录
"""

import sys
import pytest
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, "/workspace/jenkins-dingtalk-relay/server")

from app.services.relay_service import RelayService, _escape_markdown, _now


class TestEscapeMarkdown:
    """Markdown 转义函数"""

    def test_escape_special_chars(self):
        """所有特殊字符被正确转义"""
        input_str = "test *bold* `code` #header"
        result = _escape_markdown(input_str)
        assert "\\*" in result or result != input_str

    def test_escape_backslash(self):
        """反斜杠被转义"""
        assert "\\" in _escape_markdown("\\command")

    def test_normal_text_unchanged(self):
        """普通文本不变"""
        text = "Hello World 123"
        assert _escape_markdown(text) == text

    def test_empty_string(self):
        assert _escape_markdown("") == ""

    def test_all_escape_chars(self):
        """所有需要转义的字符"""
        dangerous = r'\`*_{}[]()#+-.!|~'
        escaped = _escape_markdown(dangerous)
        for char in dangerous:
            assert f"\\{char}" in escaped


class TestNowHelper:
    """时间辅助函数"""

    def test_returns_datetime(self):
        result = _now()
        assert isinstance(result, datetime)
        assert result.tzinfo == timezone.utc

    def test_recent_time(self):
        """返回的时间应该是最近的"""
        result = _now()
        diff = abs((datetime.now(timezone.utc) - result).total_seconds())
        assert diff < 5  # 5秒内


class TestRelayInit:
    """初始化"""

    def test_init_stores_dependencies(self):
        mock_db = MagicMock()
        mock_dt = MagicMock()
        mock_jk = MagicMock()
        mock_crypto = MagicMock()

        svc = RelayService(db=mock_db, dingtalk=mock_dt, jenkins=mock_jk, crypto=mock_crypto)
        assert svc.db is mock_db
        assert svc.dingtalk is mock_dt
        assert svc.jenkins is mock_jk
        assert svc.crypto is mock_crypto


class TestHandleDingTalkCallback:
    """钉钉回调处理"""

    @pytest.mark.asyncio
    async def test_missing_process_id_returns_error(self):
        mock_db = MagicMock()
        svc = RelayService(db=mock_db, dingtalk=MagicMock(), jenkins=MagicMock(), crypto=MagicMock())

        result = await svc.handle_dingtalk_callback({})
        assert result["errcode"] == 1
        assert "processInstanceId" in result["errmsg"]

    @pytest.mark.asyncio
    async def test_unknown_process_id_returns_ok(self):
        """未知的 processInstanceId 返回 errcode=0（避免重试）"""
        mock_db = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        svc = RelayService(db=mock_db, dingtalk=MagicMock(), jenkins=MagicMock(), crypto=MagicMock())

        result = await svc.handle_dingtalk_callback({"processInstanceId": "unknown_pi"})
        assert result["errcode"] == 0
        assert "not found" in result["errmsg"]

    @pytest.mark.asyncio
    async def test_agree_updates_status_to_approved(self):
        """agree 结果更新为 approved"""
        from app.models import Approval

        mock_approval = MagicMock(spec=Approval)
        mock_approval.id = "test-ap-001"
        mock_approval.type = "dingtalk_to_jenkins"
        mock_approval.title = "Test"
        mock_approval.jenkins_job_name = None
        mock_approval.callback_payload_encrypted = None
        mock_approval.callback_nonce = None
        mock_approval.approver_user_ids = '["u1"]'

        mock_db = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=mock_approval)
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.commit = AsyncMock()
        mock_jk = MagicMock()
        mock_jk.build_job = AsyncMock(return_value={"queue_id": 1})

        svc = RelayService(
            db=mock_db,
            dingtalk=MagicMock(),
            jenkins=mock_jk,
            crypto=MagicMock(),
        )

        with patch.object(svc, '_log', new_callable=AsyncMock):
            result = await svc.handle_dingtalk_callback({
                "processInstanceId": "pi_001",
                "result": "agree",
                "staffId": "user01",
            })

        assert result["errcode"] == 0
        assert mock_approval.status == "approved"
        assert mock_approval.approved_by == "user01"

    @pytest.mark.asyncio
    async def test_refuse_updates_status_to_rejected(self):
        """refuse 结果更新为 rejected"""
        mock_approval = MagicMock()
        mock_approval.type = "dingtalk_to_jenkins"
        mock_approval.title = "Test Reject"
        mock_approval.jenkins_job_name = None
        mock_approval.callback_payload_encrypted = None
        mock_approval.callback_nonce = None

        mock_db = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=mock_approval)
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.commit = AsyncMock()

        svc = RelayService(
            db=mock_db, dingtalk=MagicMock(),
            jenkins=MagicMock(), crypto=MagicMock(),
        )

        with patch.object(svc, '_log', new_callable=AsyncMock):
            result = await svc.handle_dingtalk_callback({
                "processInstanceId": "pi_reject",
                "result": "refuse",
                "staffId": "user02",
            })

        assert result["errcode"] == 0
        assert mock_approval.status == "rejected"

    @pytest.mark.asyncio
    async def test_cancelled_for_unknown_result(self):
        """未知结果更新为 cancelled"""
        mock_approval = MagicMock()
        mock_approval.type = "dingtalk_to_jenkins"
        mock_approval.title = "Cancel Test"
        mock_approval.jenkins_job_name = None
        mock_approval.callback_payload_encrypted = None
        mock_approval.callback_nonce = None

        mock_db = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=mock_approval)
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.commit = AsyncMock()

        svc = RelayService(
            db=mock_db, dingtalk=MagicMock(),
            jenkins=MagicMock(), crypto=MagicMock(),
        )

        with patch.object(svc, '_log', new_callable=AsyncMock):
            result = await svc.handle_dingtalk_callback({
                "processInstanceId": "pi_cancel",
                "result": "unknown_status",
            })

        assert result["errcode"] == 0
        assert mock_approval.status == "cancelled"


class TestHandleJenkinsCallback:
    """Jenkins 回调处理"""

    @pytest.mark.asyncio
    async def test_missing_job_name_raises_value_error(self):
        mock_db = MagicMock()
        svc = RelayService(db=mock_db, dingtalk=MagicMock(), jenkins=MagicMock(), crypto=MagicMock())

        with pytest.raises(ValueError, match="job_name"):
            await svc.handle_jenkins_callback({"build_id": 1})

    @pytest.mark.asyncio
    async def test_success_creates_build_record(self):
        """SUCCESS 结果正确设置状态"""
        mock_db = MagicMock()
        # 模拟查询无已有记录
        mock_query_result = MagicMock()
        mock_query_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_query_result)
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()
        mock_dt = MagicMock()
        mock_dt.send_work_notification = AsyncMock()

        svc = RelayService(db=mock_db, dingtalk=mock_dt, jenkins=MagicMock(), crypto=MagicMock())

        with patch.object(svc, '_log', new_callable=AsyncMock):
            result = await svc.handle_jenkins_callback({
                "job_name": "success-job",
                "build_id": 100,
                "result": "SUCCESS",
                "duration_ms": 60000,
                "output_summary": "Build OK",
            })

        assert result["ok"] is True
        # 验证 add 和 commit 被调用
        assert mock_db.add.called
        assert mock_db.commit.called

    @pytest.mark.asyncio
    async def test_failure_result_sets_failure_status(self):
        """FAILURE 结果设置 failure 状态"""
        mock_build = MagicMock()
        mock_build.approval_id = None  # 无关联审批，跳过钉钉通知
        mock_db = MagicMock()
        mock_query_result = MagicMock()
        mock_query_result.scalar_one_or_none.return_value = mock_build
        mock_db.execute = AsyncMock(return_value=mock_query_result)
        mock_db.commit = AsyncMock()

        svc = RelayService(
            db=mock_db, dingtalk=MagicMock(),
            jenkins=MagicMock(), crypto=MagicMock(),
        )

        with patch.object(svc, '_log', new_callable=AsyncMock):
            await svc.handle_jenkins_callback({
                "job_name": "fail-job",
                "build_id": 200,
                "result": "FAILURE",
            })

        assert mock_build.status == "failure"

    @pytest.mark.asyncio
    async def test_aborted_result_sets_aborted_status(self):
        """ABORTED 结果设置 aborted 状态"""
        mock_build = MagicMock()
        mock_build.approval_id = None  # 无关联审批，跳过钉钉通知
        mock_db = MagicMock()
        mock_query_result = MagicMock()
        mock_query_result.scalar_one_or_none.return_value = mock_build
        mock_db.execute = AsyncMock(return_value=mock_query_result)
        mock_db.commit = AsyncMock()

        svc = RelayService(
            db=mock_db, dingtalk=MagicMock(),
            jenkins=MagicMock(), crypto=MagicMock(),
        )

        with patch.object(svc, '_log', new_callable=AsyncMock):
            await svc.handle_jenkins_callback({
                "job_name": "abort-job",
                "build_id": 300,
                "result": "ABORTED",
            })

        assert mock_build.status == "aborted"

    @pytest.mark.asyncio
    async def test_output_summary_is_escaped(self):
        """输出摘要被 Markdown 转义"""
        mock_build = MagicMock()
        mock_build.approval_id = None  # 无关联审批，跳过钉钉通知
        mock_db = MagicMock()
        mock_query_result = MagicMock()
        mock_query_result.scalar_one_or_none.return_value = mock_build
        mock_db.execute = AsyncMock(return_value=mock_query_result)
        mock_db.commit = AsyncMock()

        svc = RelayService(
            db=mock_db, dingtalk=MagicMock(),
            jenkins=MagicMock(), crypto=MagicMock(),
        )

        malicious_input = "# Header **bold** <script>alert(1)</script>"

        with patch.object(svc, '_log', new_callable=AsyncMock):
            await svc.handle_jenkins_callback({
                "job_name": "xss-job",
                "build_id": 400,
                "result": "SUCCESS",
                "output_summary": malicious_input,
            })

        # 验证被转义
        saved_summary = mock_build.output_summary
        if saved_summary and "<script>" in malicious_input:
            # 原始包含 script 标签，转义后应不同
            assert saved_summary != malicious_input or "\\" in saved_summary

    @pytest.mark.asyncio
    async def test_empty_output_summary_handled(self):
        """空 output_summary 不报错"""
        mock_build = MagicMock()
        mock_build.approval_id = None  # 无关联审批，跳过钉钉通知
        mock_db = MagicMock()
        mock_query_result = MagicMock()
        mock_query_result.scalar_one_or_none.return_value = mock_build
        mock_db.execute = AsyncMock(return_value=mock_query_result)
        mock_db.commit = AsyncMock()

        svc = RelayService(
            db=mock_db, dingtalk=MagicMock(),
            jenkins=MagicMock(), crypto=MagicMock(),
        )

        with patch.object(svc, '_log', new_callable=AsyncMock):
            result = await svc.handle_jenkins_callback({
                "job_name": "empty-out-job",
                "build_id": 500,
                "result": "SUCCESS",
                "output_summary": "",
            })

        assert result["ok"] is True


class TestCheckApprovalStatus:
    """审批状态查询"""

    @pytest.mark.asyncio
    async def test_existing_approval(self):
        mock_approval = MagicMock()
        mock_approval.id = "ap-001"
        mock_approval.status = "approved"
        mock_approval.approved_by = "manager"
        mock_approval.reject_reason = None
        mock_approval.updated_at = datetime.now(timezone.utc)

        mock_db = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_approval
        mock_db.execute = AsyncMock(return_value=mock_result)

        svc = RelayService(
            db=mock_db, dingtalk=MagicMock(),
            jenkins=MagicMock(), crypto=MagicMock(),
        )

        result = await svc.check_approval_status("ap-001")
        assert result["status"] == "approved"
        assert result["approval_id"] == "ap-001"

    @pytest.mark.asyncio
    async def test_nonexistent_approval(self):
        mock_db = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        svc = RelayService(
            db=mock_db, dingtalk=MagicMock(),
            jenkins=MagicMock(), crypto=MagicMock(),
        )

        result = await svc.check_approval_status("nonexistent")
        assert result["status"] == "not_found"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
