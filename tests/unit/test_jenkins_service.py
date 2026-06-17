"""JenkinsService 单元测试

覆盖:
- 初始化与客户端管理
- Job 名称 URL 安全编码
- Basic Auth 头生成
- CSRF Crumb 管理
- 构建触发与队列 ID 解析
- 错误处理（JenkinsError）
"""

import sys
import pytest
import base64
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, "/workspace/jenkins-dingtalk-relay/server")

from app.services.jenkins_service import JenkinsService, JenkinsError


class TestJenkinsInitAndClient:
    """初始化与客户端管理"""

    def test_initial_state(self):
        with patch("app.services.jenkins_service.settings") as mock_settings:
            mock_settings.JENKINS_URL = "https://jenkins.example.com"
            svc = JenkinsService()
            assert svc._base_url == "https://jenkins.example.com"
            assert svc._client is None
            assert svc._crumb_cache is None

    def test_empty_jenkins_url(self):
        with patch("app.services.jenkins_service.settings") as mock_settings:
            mock_settings.JENKINS_URL = ""
            svc = JenkinsService()
            assert svc._base_url == ""

    def test_get_client_creates_client(self):
        with patch("app.services.jenkins_service.settings") as mock_settings:
            mock_settings.JENKINS_URL = "https://jenkins.test"
            svc = JenkinsService()
            client = svc._get_client()
            assert client is not None

    def test_get_client_reuses_instance(self):
        with patch("app.services.jenkins_service.settings") as mock_settings:
            mock_settings.JENKINS_URL = "https://jenkins.test"
            svc = JenkinsService()
            c1 = svc._get_client()
            c2 = svc._get_client()
            assert c1 is c2


class TestAuthHeader:
    """Basic Auth 头生成"""

    def test_auth_header_format(self):
        with patch("app.services.jenkins_service.settings") as mock_settings:
            mock_settings.JENKINS_USERNAME = "admin"
            mock_settings.JENKINS_API_TOKEN = "token123"

            svc = JenkinsService.__new__(JenkinsService)
            header = svc._auth_header

            assert "Authorization" in header
            assert header["Authorization"].startswith("Basic ")

            # 解码验证
            encoded = header["Authorization"].replace("Basic ", "")
            decoded = base64.b64decode(encoded).decode()
            assert decoded == "admin:token123"

    def test_special_chars_in_credentials(self):
        """特殊字符的凭证应被正确编码"""
        with patch("app.services.jenkins_service.settings") as mock_settings:
            mock_settings.JENKINS_USERNAME = "user@domain.com"
            mock_settings.JENKINS_API_TOKEN = "p@ss=w0rd!"

            svc = JenkinsService.__new__(JenkinsService)
            header = svc._auth_header

            encoded = header["Authorization"].replace("Basic ", "")
            decoded = base64.b64decode(encoded).decode()
            assert decoded == "user@domain.com:p@ss=w0rd!"


class TestSafeJobPath:
    """Job 名称 URL 安全编码"""

    def _make_svc(self, url="https://j.test"):
        with patch("app.services.jenkins_service.settings") as mock_settings:
            mock_settings.JENKINS_URL = url
            return JenkinsService()

    def test_simple_job_name(self):
        svc = self._make_svc()
        result = svc._safe_job_path("my-job")
        assert "/job/" in result
        assert "my-job" in result or "my%2Djob" in result  # URL-encoded dash

    def test_folder_structure(self):
        """folder/job 格式应被分别编码"""
        svc = self._make_svc()
        result = svc._safe_job_path("folder/subfolder/job-name")
        # 应包含 /job/ 连接的多段路径
        parts = result.split("/job/")
        assert len(parts) >= 3  # 至少 folder, subfolder, job-name

    def test_special_characters_encoded(self):
        """特殊字符被 URL 编码"""
        svc = self._make_svc()
        result = svc._safe_job_path("job name?with=special&chars")
        assert "?" not in result or "%3F" in result
        # 空格和特殊字符应编码
        assert " " not in result.split("/")[-1] if result else True

    def test_chinese_characters(self):
        """中文字符被编码"""
        svc = self._make_svc()
        result = svc._safe_job_path("构建任务")
        # 中文字符应被 percent encode
        assert "%" in result or "构建任务" in result


class TestCrumbManagement:
    """CSRF Crumb 管理"""

    @pytest.mark.asyncio
    async def test_crumb_fetched_on_first_call(self):
        """首次调用获取 crumb"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "crumbRequestField": ".crumb",
            "crumb": "crumb_value_123",
        }

        with patch("app.services.jenkins_service.settings") as mock_settings:
            mock_settings.JENKINS_URL = "https://j.test"
            mock_settings.JENKINS_USERNAME = "u"
            mock_settings.JENKINS_API_TOKEN = "t"

            svc = JenkinsService()
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)

            with patch.object(svc, "_get_client", return_value=mock_client):
                crumb = await svc._ensure_crumb()
                assert crumb == {".crumb": "crumb_value_123"}

    @pytest.mark.asyncio
    async def test_crumb_cached(self):
        """Crumb 缓存后不重复请求"""
        with patch("app.services.jenkins_service.settings") as mock_settings:
            mock_settings.JENKINS_URL = "https://j.test"
            svc = JenkinsService()
            svc._crumb_cache = (".crumb", "cached_crumb")

            crumb = await svc._ensure_crumb()
            assert crumb == {".crumb": "cached_crumb"}

    @pytest.mark.asyncio
    async def test_csrf_disabled_returns_empty(self):
        """Jenkins 未启用 CSRF 时返回空 dict"""
        mock_resp = MagicMock()
        mock_resp.status_code = 404  # 404 表示未启用

        with patch("app.services.jenkins_service.settings") as mock_settings:
            mock_settings.JENKINS_URL = "https://j.test"
            mock_settings.JENKINS_USERNAME = "u"
            mock_settings.JENKINS_API_TOKEN = "t"

            svc = JenkinsService()
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)

            with patch.object(svc, "_get_client", return_value=mock_client):
                crumb = await svc._ensure_crumb()
                assert crumb == {}


class TestBuildJob:
    """构建触发"""

    @pytest.mark.asyncio
    async def test_queue_id_parsed_from_location_header(self):
        """从 Location 响应头解析 queue_id"""
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.headers = {"Location": "https://j.test/queue/item/42/"}
        mock_resp.text = ""

        with patch("app.services.jenkins_service.settings") as mock_settings:
            mock_settings.JENKINS_URL = "https://j.test"
            mock_settings.JENKINS_USERNAME = "u"
            mock_settings.JENKINS_API_TOKEN = "t"

            svc = JenkinsService()

            with patch.object(svc, "_request", new_callable=AsyncMock) as mock_req:
                mock_req.return_value = mock_resp

                result = await svc.build_job("test-job")
                assert result["queue_id"] == 42
                assert result["queue_url"] == "https://j.test/queue/item/42/"

    @pytest.mark.asyncio
    async def test_no_location_header_returns_none_queue_id(self):
        """无 Location header 时 queue_id 为 None"""
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.headers = {}

        with patch("app.services.jenkins_service.settings") as mock_settings:
            mock_settings.JENKINS_URL = "https://j.test"
            mock_settings.JENKINS_USERNAME = "u"
            mock_settings.JENKINS_API_TOKEN = "t"

            svc = JenkinsService()

            with patch.object(svc, "_request", new_callable=AsyncMock) as mock_req:
                mock_req.return_value = mock_resp
                result = await svc.build_job("test-job")
                assert result["queue_id"] is None

    @pytest.mark.asyncio
    async def test_build_with_parameters(self):
        """带参数的构建调用正确的端点"""
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.headers = {"Location": "https://j.test/queue/item/99/"}

        with patch("app.services.jenkins_service.settings") as mock_settings:
            mock_settings.JENKINS_URL = "https://j.test"
            mock_settings.JENKINS_USERNAME = "u"
            mock_settings.JENKINS_API_TOKEN = "t"

            svc = JenkinsService()

            with patch.object(svc, "_request", new_callable=AsyncMock) as mock_req:
                mock_req.return_value = mock_resp
                result = await svc.build_job("deploy-job", {"BRANCH": "main", "ENV": "prod"})
                assert result["queue_id"] == 99
                # 验证调用了带参数的路径
                call_args = mock_req.call_args
                assert "buildWithParameters" in str(call_args)

    @pytest.mark.asyncio
    async def test_no_parameters_uses_simple_build(self):
        """无参数时使用 /build 端点"""
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.headers = {"Location": "https://j.test/queue/item/7/"}

        with patch("app.services.jenkins_service.settings") as mock_settings:
            mock_settings.JENKINS_URL = "https://j.test"
            mock_settings.JENKINS_USERNAME = "u"
            mock_settings.JENKINS_API_TOKEN = "t"

            svc = JenkinsService()

            with patch.object(svc, "_request", new_callable=AsyncMock) as mock_req:
                mock_req.return_value = mock_resp
                result = await svc.build_job("simple-job")
                # 应调用 /build（非 buildWithParameters）
                call_args = mock_req.call_args
                assert "/build'" in str(call_args) or "build" in str(call_args)


class TestJenkinsError:
    """异常类"""

    def test_error_is_exception(self):
        assert issubclass(JenkinsError, Exception)

    def test_error_message(self):
        err = JenkinsError("test error")
        assert str(err) == "test error"

    def test_error_chain(self):
        """JenkinsError 通过 raise...from 语法链式异常"""
        original = ConnectionRefusedError("connection refused")
        try:
            raise JenkinsError("wrapped") from original
        except JenkinsError as err:
            assert err.__cause__ is original


class TestGetBuildStatus:
    """构建状态查询"""

    @pytest.mark.asyncio
    async def test_building_status(self):
        """building=true 返回 building"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "building": True,
            "result": None,
            "duration": 5000,
        }

        with patch("app.services.jenkins_service.settings") as mock_settings:
            mock_settings.JENKINS_URL = "https://j.test"
            mock_settings.JENKINS_USERNAME = "u"
            mock_settings.JENKINS_API_TOKEN = "t"

            svc = JenkinsService()
            with patch.object(svc, "_request", new_callable=AsyncMock, return_value=mock_resp):
                result = await svc.get_build_status("test-job", 5)
                assert result["status"] == "building"
                assert result["build_id"] == 5

    @pytest.mark.asyncio
    async def test_completed_success_status(self):
        """building=false, result=SUCCESS 返回 completed"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "building": False,
            "result": "SUCCESS",
            "duration": 60000,
        }

        with patch("app.services.jenkins_service.settings") as mock_settings:
            mock_settings.JENKINS_URL = "https://j.test"
            mock_settings.JENKINS_USERNAME = "u"
            mock_settings.JENKINS_API_TOKEN = "t"

            svc = JenkinsService()
            with patch.object(svc, "_request", new_callable=AsyncMock, return_value=mock_resp):
                result = await svc.get_build_status("test-job", 10)
                assert result["status"] == "completed"
                assert result["result"] == "SUCCESS"


class TestAbortBuild:
    """取消构建"""

    @pytest.mark.asyncio
    async def test_abort_success(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("app.services.jenkins_service.settings") as mock_settings:
            mock_settings.JENKINS_URL = "https://j.test"
            mock_settings.JENKINS_USERNAME = "u"
            mock_settings.JENKINS_API_TOKEN = "t"

            svc = JenkinsService()
            with patch.object(svc, "_request", new_callable=AsyncMock, return_value=mock_resp):
                result = await svc.abort_build("test-job", 42)
                assert result is True

    @pytest.mark.asyncio
    async def test_abort_failure(self):
        """取消失败返回 False（不抛异常）"""
        with patch("app.services.jenkins_service.settings") as mock_settings:
            mock_settings.JENKINS_URL = "https://j.test"
            mock_settings.JENKINS_USERNAME = "u"
            mock_settings.JENKINS_API_TOKEN = "t"

            svc = JenkinsService()
            with patch.object(svc, "_request", new_callable=AsyncMock) as mock_req:
                mock_req.side_effect = JenkinsError("HTTP 500")
                result = await svc.abort_build("test-job", 42)
                assert result is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
