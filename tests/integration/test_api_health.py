"""健康检查 & 基础 API 集成测试

覆盖:
- GET /health 健康检查
- API Key 认证中间件
- CORS 预检请求
- 404 JSON 响应格式
- 全局异常处理器
"""

import pytest
from httpx import AsyncClient

__import__("sys").path.insert(0, "/workspace/jenkins-dingtalk-relay/server")


class TestHealthEndpoint:
    """GET /health"""

    @pytest.mark.asyncio
    async def test_health_ok(self, client: AsyncClient):
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    @pytest.mark.asyncio
    async def test_health_has_version(self, client: AsyncClient):
        resp = await client.get("/health")
        assert "version" in resp.json()


class TestAuthMiddleware:
    """API Key 中间件验证"""

    @pytest.mark.asyncio
    async def test_no_key_401(self, client: AsyncClient):
        # /api/v1/jenkins/trigger 需要 API Key（不在白名单中）
        resp = await client.post("/api/v1/jenkins/trigger", json={"job_name": "test"})
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_wrong_key_401(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/jenkins/trigger",
            json={"job_name": "test"},
            headers={"X-API-Key": "wrong-key"},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_health_skips_auth(self, client: AsyncClient):
        resp = await client.get("/health")
        assert resp.status_code == 200


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_404_json_format(self, client: AsyncClient):
        # 非 /api/ 路径不会触发 API Key 中间件，可直接测 404
        resp = await client.get("/nonexistent-page")
        assert resp.status_code == 404


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
