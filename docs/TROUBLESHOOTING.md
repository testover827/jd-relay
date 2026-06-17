# 故障排查指南 — Jenkins & 钉钉交互转发器

## 快速诊断

```bash
# 1. 检查服务是否存活
curl -f http://localhost:8000/health

# 2. 查看最近日志
docker compose logs relay --tail 50

# 3. 检查容器状态
docker compose ps
```

---

## 常见问题

### 1. 钉钉回调无响应 / 签名验证失败

**症状**: 钉钉后台显示回调失败，转发器日志出现 "签名验证失败"

**排查步骤**:
1. 确认 `DINGTALK_APP_SECRET` 与钉钉开放平台一致
2. 确认服务器时间与 NTP 同步: `timedatectl status`
3. 查看详细日志: `docker compose logs relay | grep "签名验证"`
4. 测试签名算法:
```python
import hmac, hashlib, time
app_secret = "your_dingtalk_app_secret"
timestamp = str(int(time.time()))
nonce = "test_nonce"
signature = hmac.new(
    app_secret.encode(),
    f"{timestamp}\n{nonce}".encode(),
    hashlib.sha256
).digest()
print(f"timestamp={timestamp}")
print(f"signature={signature.hex()}")
```

**解决方案**:
- 核对 AppSecret，必要时在钉钉开放平台重新生成
- 同步服务器时间: `ntpdate -u ntp.aliyun.com`

---

### 2. Jenkins 构建触发失败 (502)

**症状**: 触发构建返回 502，日志显示 JenkinsError

**排查步骤**:
1. 确认 `JENKINS_URL` 从转发器服务器可访问:
   ```bash
   docker exec jd-relay curl -u "$JENKINS_USERNAME:$JENKINS_API_TOKEN" "$JENKINS_URL/api/json"
   ```
2. 检查 Jenkins API Token 是否过期
3. 检查 CSRF 保护是否启用（转发器自动处理 CSRF Crumb）

**解决方案**:
- 更新 `JENKINS_API_TOKEN`
- 确认网络连通性（防火墙/安全组）
- 如果 Jenkins 使用自签名证书，设置 `JENKINS_VERIFY_SSL=false`

---

### 3. Admin 面板无法登录

**症状**: 登录后立即跳回登录页或显示 401

**排查步骤**:
1. 检查 `SESSION_SECRET` 是否配置
2. 确认浏览器 Cookie 未被阻止
3. 查看登录日志:
   ```bash
   docker compose logs relay | grep "login"
   ```

**解决方案**:
- 确保 `SESSION_SECRET` 值不变（重启后需一致）
- 清除浏览器缓存和 Cookie
- 开发模式快速登录: 访问 `/login?dev=true`（仅 `DEBUG=true` 时可用）

---

### 4. 数据库锁定 (SQLite Busy)

**症状**: 日志出现 `database is locked` 错误

**原因**: SQLite 不支持高并发写入，多个请求同时写入会导致锁冲突

**解决方案**:
- 短期: 重启服务 `docker compose restart relay`
- 长期: 迁移到 PostgreSQL（修改 `DATABASE_URL`）

**迁移到 PostgreSQL**:
```bash
# 1. 修改 .env.production
DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/jd_relay

# 2. 安装依赖
docker exec jd-relay pip install asyncpg

# 3. 运行迁移
docker exec jd-relay alembic upgrade head

# 4. 重启
docker compose restart relay
```

---

### 5. 容器反复重启

**症状**: `docker compose ps` 显示 Restarting

**排查步骤**:
```bash
# 查看完整启动日志
docker logs jd-relay --tail 100

# 常见错误:
# - "RELAY_API_KEY is required" → 环境变量缺失
# - "ModuleNotFoundError" → 代码不完整
# - "Address already in use" → 端口冲突
```

**解决方案**:
- 检查 `.env.production` 所有必填变量
- 确认端口未被占用: `netstat -tlnp | grep 8000`
- 重新构建镜像: `docker compose build --no-cache`

---

### 6. 磁盘空间不足

**症状**: 服务响应变慢，日志写入失败

**排查**:
```bash
# 检查磁盘
df -h

# 检查日志大小
du -sh logs/
docker system df
```

**清理**:
```bash
# 清理 Docker 未使用的镜像和容器
docker system prune -a --filter "until=24h"

# 清理旧日志（保留 7 天）
find logs/ -name "*.log" -mtime +7 -delete

# 清理数据库中的旧日志记录
docker exec jd-relay python3 -c "
import asyncio
from app.database import async_session
from sqlalchemy import text
async def clean():
    async with async_session() as s:
        await s.execute(text(\"DELETE FROM logs WHERE timestamp < datetime('now', '-30 days')\"))
        await s.commit()
        print('Old logs cleaned')
asyncio.run(clean())
"
```

---

### 7. Nginx 502 Bad Gateway

**症状**: 访问域名返回 Nginx 502

**排查**:
1. 确认转发器容器运行: `docker compose ps relay`
2. 确认端口连通: `curl http://127.0.0.1:8000/health`
3. 检查 Nginx 错误日志: `tail -f /var/log/nginx/jd-relay-error.log`

**解决方案**:
- 转发器未启动: `docker compose up -d relay`
- 端口不匹配: 检查 `deploy/nginx/nginx.conf` 中 `upstream` 端口
- 超时: 增加 `proxy_read_timeout`（SSE 长连接场景）

---

### 8. SSL 证书过期

**症状**: 浏览器显示证书错误

**排查**:
```bash
# 检查证书到期时间
openssl x509 -in /etc/letsencrypt/live/your-domain.com/fullchain.pem -noout -dates

# 检查自动续期
certbot renew --dry-run
```

**解决方案**:
```bash
# 手动续期
certbot renew --force-renewal

# 重载 Nginx
nginx -s reload
```

---

## 诊断命令速查

```bash
# 服务状态
docker compose ps
docker compose logs relay --tail 100 -f

# 数据库状态
docker exec jd-relay python3 -c "
import asyncio; from app.database import async_session
from app.models import Approval, Build, Log
from sqlalchemy import select, func
async def stats():
    async with async_session() as s:
        a = (await s.execute(select(func.count(Approval.id)))).scalar()
        b = (await s.execute(select(func.count(Build.id)))).scalar()
        l = (await s.execute(select(func.count(Log.id)))).scalar()
        print(f'Approvals: {a} | Builds: {b} | Logs: {l}')
asyncio.run(stats())
"

# 网络诊断
docker exec jd-relay curl -sv http://localhost:8000/health
docker exec jd-relay curl -sv $JENKINS_URL/api/json 2>&1 | head -20

# 资源使用
docker stats jd-relay --no-stream
```

---

## 联系支持

如以上步骤无法解决问题，收集以下信息后联系技术支持:

1. 转发器版本: `curl http://localhost:8000/health`
2. 最近 100 行日志: `docker compose logs relay --tail 100`
3. 环境信息: `docker version && docker compose version`
4. 错误复现步骤
