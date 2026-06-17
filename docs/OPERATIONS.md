# 运维手册 — Jenkins & 钉钉交互转发器 v2.0.0

## 目录

1. [系统架构](#1-系统架构)
2. [部署指南](#2-部署指南)
3. [配置管理](#3-配置管理)
4. [日常运维](#4-日常运维)
5. [监控告警](#5-监控告警)
6. [备份恢复](#6-备份恢复)
7. [升级流程](#7-升级流程)

---

## 1. 系统架构

```
                    ┌─────────────────────────────────────┐
                    │         Nginx (SSL Termination)       │
                    │         :443 / :80 → :8000            │
                    └─────────────────┬───────────────────┘
                                      │
                    ┌─────────────────▼───────────────────┐
                    │     FastAPI 转发器 (Docker)          │
                    │                                     │
                    │  ┌─────────┐  ┌──────────┐         │
                    │  │ DingTalk │  │ Jenkins  │         │
                    │  │ Service  │  │ Service  │         │
                    │  └────┬────┘  └────┬─────┘         │
                    │       │            │                 │
                    │  ┌────▼────────────▼─────┐          │
                    │  │    RelayService       │          │
                    │  │  (业务编排 + 加密)     │          │
                    │  └──────────┬───────────┘          │
                    │             │                       │
                    │  ┌──────────▼───────────┐          │
                    │  │  SQLite (aiosqlite)  │          │
                    │  └──────────────────────┘          │
                    └─────────────────────────────────────┘
                                      │
                    ┌─────────────────┼───────────────────┐
                    │                 │                    │
               ┌────▼────┐     ┌─────▼─────┐              │
               │ 钉钉平台  │     │  Jenkins  │              │
               │ (外网)    │     │  (内网)   │              │
               └─────────┘     └───────────┘              │
```

**核心数据流**:
- **流程1**: 钉钉审批 → 转发器验证 → 触发 Jenkins Job
- **流程2**: Jenkins 暂停 → 转发器发起钉钉审批 → 审批结果回调 Jenkins

---

## 2. 部署指南

### 2.1 环境要求

| 组件   | 最低版本      | 说明                    |
|--------|---------------|-------------------------|
| Docker | 24.0+         | 容器运行时              |
| Nginx  | 1.25+         | 反向代理（可选但推荐）  |
| Python | 3.11+         | 仅 CLI 工具需要         |
| 内存   | 512MB+        | 含 Docker overhead      |
| 磁盘   | 10GB+         | 日志和数据持久化        |

### 2.2 快速部署

```bash
# 1. 克隆仓库
git clone <repo-url> /opt/jd-relay
cd /opt/jd-relay

# 2. 配置环境变量
cp deploy/.env.production.example .env.production
# 编辑 .env.production，填写所有必需值

# 3. 生成密钥（如未手动生成）
python3 -c "
import secrets
for k in ['RELAY_API_KEY','AES_ENCRYPTION_KEY','HMAC_SECRET','CONFIG_MASTER_KEY','SESSION_SECRET']:
    print(f'{k}={secrets.token_hex(32)}')
"

# 4. 一键部署
./deploy/deploy.sh --ssl --domain jd-relay.your-company.com
```

### 2.3 手动部署

```bash
# 构建镜像
docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml build

# 启动
docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml up -d

# 查看日志
docker compose logs -f relay

# 健康检查
curl http://localhost:8000/health
```

### 2.4 配置 Nginx SSL

```bash
# 安装 certbot
apt-get install -y certbot python3-certbot-nginx

# 获取证书
certbot --nginx -d jd-relay.your-company.com

# 配置自动续期
echo "0 3 * * * certbot renew --quiet && nginx -s reload" > /etc/cron.d/certbot-renew
```

---

## 3. 配置管理

### 3.1 环境变量参考

| 变量                  | 必填 | 说明                                      |
|-----------------------|------|-------------------------------------------|
| RELAY_API_KEY         | 是   | API 认证密钥（Jenkins CLI 调用用）         |
| AES_ENCRYPTION_KEY    | 是   | AES-256-GCM 加密密钥（64 hex chars）       |
| HMAC_SECRET           | 是   | HMAC-SHA256 签名密钥（64 hex chars）       |
| CONFIG_MASTER_KEY     | 是   | 配置值加密主密钥                          |
| SESSION_SECRET        | 是   | Admin 面板 session 签名密钥               |
| DINGTALK_APP_KEY      | 否*  | 钉钉应用 AppKey                           |
| DINGTALK_APP_SECRET   | 否*  | 钉钉应用 AppSecret                        |
| DINGTALK_AGENT_ID     | 否*  | 钉钉应用 AgentId                          |
| JENKINS_URL           | 否*  | Jenkins 服务地址                          |
| JENKINS_USERNAME      | 否*  | Jenkins API 用户名                        |
| JENKINS_API_TOKEN     | 否*  | Jenkins API Token                         |
| ADMIN_USERNAME        | 否   | 管理员用户名（默认 admin）                |
| ADMIN_PASSWORD_HASH   | 否   | 管理员密码 bcrypt hash                    |
| DEBUG                 | 否   | 调试模式（生产环境设为 false）            |
| LOG_LEVEL             | 否   | 日志级别: DEBUG/INFO/WARNING/ERROR        |

> *标 `否*` 的变量：功能需要时必填。如不使用钉钉功能可不填钉钉变量。

### 3.2 密钥轮换

```bash
# 1. 生成新密钥
NEW_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")

# 2. 更新 .env.production
sed -i "s/^AES_ENCRYPTION_KEY=.*/AES_ENCRYPTION_KEY=$NEW_KEY/" .env.production

# 3. 滚动重启
docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml up -d --no-deps --force-recreate relay

# 4. 通知下游更新密钥
```

---

## 4. 日常运维

### 4.1 常用命令

```bash
# 查看服务状态
docker compose ps

# 查看实时日志
docker compose logs -f relay --tail 100

# 重启服务
docker compose restart relay

# 进入容器调试
docker exec -it jd-relay python3

# 查看数据库记录数
docker exec jd-relay python3 -c "
from app.database import async_session
from app.models import Approval, Build, Log
from sqlalchemy import select
async def count():
    async with async_session() as s:
        a = (await s.execute(select(Approval))).scalars().all()
        b = (await s.execute(select(Build))).scalars().all()
        l = (await s.execute(select(Log))).scalars().all()
        print(f'Approvals: {len(a)}, Builds: {len(b)}, Logs: {len(l)}')
import asyncio; asyncio.run(count())
"
```

### 4.2 日志管理

日志位置:
- 应用日志: `docker compose logs relay`
- Nginx 访问日志: `/var/log/nginx/jd-relay-access.log`
- Nginx 错误日志: `/var/log/nginx/jd-relay-error.log`

日志轮转已内置:
- Docker: `max-size=10m, max-file=5`
- 系统级: 配置 logrotate

### 4.3 性能调优

```yaml
# docker-compose.prod.yml 中调整资源
deploy:
  resources:
    limits:
      cpus: '2'        # 根据负载调整
      memory: 1G
```

- 高并发场景：增加 worker 数量（uvicorn --workers 4）
- 大量日志：定期清理 `DELETE FROM logs WHERE timestamp < date('now', '-30 days')`

---

## 5. 监控告警

### 5.1 健康检查端点

```bash
# 简单探活
curl -f https://jd-relay.example.com/health

# 详细状态
curl -H "X-API-Key: $RELAY_API_KEY" https://jd-relay.example.com/api/v1/dashboard
```

### 5.2 Prometheus 指标（可选）

可在 Nginx 层启用 stub_status:
```nginx
location /nginx_status {
    stub_status on;
    allow 127.0.0.1;
    deny all;
}
```

### 5.3 告警规则建议

| 指标             | 阈值          | 说明                 |
|------------------|---------------|----------------------|
| 健康检查失败     | 连续 3 次     | 服务不可用           |
| 响应时间 > 5s    | 持续 2 分钟   | 性能下降             |
| 错误率 > 5%      | 持续 5 分钟   | 下游服务异常         |
| 磁盘使用 > 80%   | 任意时间      | 需清理日志/数据      |
| pending 审批 > 50| 持续 1 小时   | 审批堆积             |
| 容器重启 > 3 次  | 1 小时内      | 服务不稳定           |

---

## 6. 备份恢复

### 6.1 数据备份

```bash
# 备份 SQLite 数据库
docker cp jd-relay:/app/data/relay.db ./backups/relay-$(date +%Y%m%d-%H%M).db

# 备份配置
cp .env.production ./backups/env-$(date +%Y%m%d-%H%M).backup

# 一键备份脚本
cat > /opt/jd-relay/backup.sh << 'EOF'
#!/bin/bash
BACKUP_DIR="/opt/jd-relay/backups/$(date +%Y%m%d)"
mkdir -p "$BACKUP_DIR"
docker cp jd-relay:/app/data/relay.db "$BACKUP_DIR/relay.db"
cp .env.production "$BACKUP_DIR/.env.production"
tar -czf "$BACKUP_DIR.tar.gz" -C /opt/jd-relay/backups "$(date +%Y%m%d)"
rm -rf "$BACKUP_DIR"
# 保留最近 30 天
find /opt/jd-relay/backups/ -name "*.tar.gz" -mtime +30 -delete
EOF
chmod +x /opt/jd-relay/backup.sh

# 添加定时任务
echo "0 2 * * * /opt/jd-relay/backup.sh" > /etc/cron.d/jd-relay-backup
```

### 6.2 数据恢复

```bash
# 1. 停止服务
docker compose down

# 2. 恢复数据库
cp backups/relay-20250101-0200.db data/relay.db

# 3. 恢复配置
cp backups/env-20250101-0200.backup .env.production

# 4. 启动服务
docker compose up -d

# 5. 验证
curl http://localhost:8000/health
```

---

## 7. 升级流程

### 7.1 标准升级

```bash
# 1. 拉取最新代码
cd /opt/jd-relay
git pull origin main

# 2. 查看变更
git log --oneline HEAD@{1}..HEAD

# 3. 备份（重要！）
./backup.sh

# 4. 拉取新镜像并重新构建
docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml build --pull

# 5. 滚动更新
docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml up -d --wait

# 6. 验证
curl http://localhost:8000/health
docker compose logs relay --tail 20
```

### 7.2 回滚

```bash
# 回退到上一个 Git 版本
git checkout HEAD@{1}

# 重建并部署
docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml build
docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml up -d --wait
```

### 7.3 数据库迁移

如果新版本包含数据库 schema 变更:
```bash
docker exec jd-relay alembic upgrade head
```

---

## 故障排查

常见问题快速参考：

| 现象                   | 检查项                                    |
|------------------------|-------------------------------------------|
| 401 认证失败           | RELAY_API_KEY 是否一致                    |
| 502 Jenkins 不可达     | JENKINS_URL 是否正确，网络是否通          |
| 钉钉回调无响应         | 检查签名、时间戳、AppSecret 配置          |
| 容器反复重启           | `docker logs jd-relay` 查看启动错误       |
| 数据库锁定             | SQLite 并发写入限制，考虑迁移到 PostgreSQL|
