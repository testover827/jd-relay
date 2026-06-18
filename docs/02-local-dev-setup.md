# 本地开发与运行指南

## 前置条件

- Python 3.13+（3.14 亦可，需 SQLAlchemy >= 2.0.36）
- Git
- Windows / Linux / macOS

---

## 步骤 1：克隆项目

```bash
git clone git@github.com:testover827/jd-relay.git
cd jd-relay
```

---

## 步骤 2：创建虚拟环境并安装依赖

```bash
# 在项目根目录创建 venv
python -m venv venv

# 激活
# Windows:
venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

# 安装依赖
pip install -r server/requirements.txt
```

> **已知问题**：`requirements.txt` 中已包含 `tenacity>=8.2.3` 和 `sqlalchemy>=2.0.36`，
> 无需手动补充。若使用旧版本 requirements.txt，请确认这两个包已安装。

---

## 步骤 3：生成安全密钥

项目自带密钥生成命令：

```bash
make keys
```

如果 Windows 上没有 `make`，手动生成：

```bash
# AES/HMAC/Config 密钥（64位十六进制 = 256bit）
python -c "import os; print('AES_ENCRYPTION_KEY=' + os.urandom(32).hex()); print('HMAC_SECRET=' + os.urandom(32).hex()); print('CONFIG_MASTER_KEY=' + os.urandom(32).hex())"

# API Key 和 Session Secret
python -c "import secrets; print('RELAY_API_KEY=' + secrets.token_urlsafe(32)); print('SESSION_SECRET=' + secrets.token_urlsafe(32))"
```

---

## 步骤 4：生成管理员密码哈希

```bash
python -c "import bcrypt; print(bcrypt.hashpw(input('password: ').encode(), bcrypt.gensalt()).decode())"
# 输入密码，复制输出的 hash
```

---

## 步骤 5：配置 `.env`

```bash
cp .env.example server/.env
```

编辑 `server/.env`，填入以下内容：

```ini
# ── 基础配置 ──
RELAY_PORT=8000
DEBUG=true
LOG_LEVEL=INFO

# ── 安全密钥（步骤3生成的）──
RELAY_API_KEY=<生成的>
AES_ENCRYPTION_KEY=<64位hex>
HMAC_SECRET=<64位hex>
CONFIG_MASTER_KEY=<64位hex>
SESSION_SECRET=<生成的>

# ── 钉钉（测试时可留空，服务能启动但审批功能不可用）──
DINGTALK_APP_KEY=
DINGTALK_APP_SECRET=
DINGTALK_AGENT_ID=

# ── Jenkins（测试时可留空）──
JENKINS_URL=http://jenkins:8080
JENKINS_USERNAME=
JENKINS_API_TOKEN=

# ── 管理员 ──
ADMIN_USERNAME=admin
ADMIN_PASSWORD_HASH=<步骤4生成的hash>
```

> **关键说明**：
> - `.env` 放在 `server/` 目录下（Pydantic 从工作目录读取）
> - `.env.example` 里的全零密钥只是占位符，必须替换为真实生成的密钥
> - 钉钉/Jenkins 配置可留空启动，但审批/构建功能需要真实凭证

---

## 步骤 6：创建数据库目录

SQLite 数据库路径是 `server/data/relay.db`，**必须手动创建 `data/` 目录**：

```bash
mkdir -p server/data
```

> 启动时会通过 `init_db()` 自动建表，无需手动执行迁移。

---

## 步骤 7：启动服务

```bash
cd server
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

或用 Makefile：

```bash
make dev
```

---

## 步骤 8：验证

```bash
# 健康检查
curl http://localhost:8000/health
# 应返回: {"status":"ok","version":"0.1.0"}
```

浏览器打开：

| 页面 | URL | 说明 |
|------|-----|------|
| Web 面板 | http://localhost:8000 | 自动重定向到 `/admin/` |
| Swagger UI | http://localhost:8000/docs | 交互式 API 文档 |
| ReDoc | http://localhost:8000/redoc | 阅读 API 文档 |

---

## CLI 工具安装（供 Jenkins Pipeline 调用）

```bash
cd cli
pip install -e .
```

安装后可使用 `jdcli` 命令，详见 [03-cli-guide.md](03-cli-guide.md)。

---

## 运行测试

```bash
pip install -r requirements-test.txt

# 全部测试
pytest tests/ -v

# 分类测试
pytest tests/unit/ -v          # 单元测试
pytest tests/integration/ -v   # 集成测试
pytest tests/e2e/ -v           # E2E 测试
```

或用 Makefile：

```bash
make test
```

---

## Docker 部署（生产环境）

### 启动

```bash
# 配置环境变量
cp .env.example .env
# 编辑 .env

# 启动
docker compose up -d

# 或用 Makefile
make up      # 启动
make build   # 重新构建
make logs    # 查看日志
make down    # 停止
make shell   # 进入容器
```

### 生产部署（带 Nginx + SSL）

```bash
cp deploy/.env.production.example .env.production
# 编辑 .env.production

# 一键部署
chmod +x deploy/deploy.sh
./deploy/deploy.sh --ssl --domain your-domain.com
```

---

## 已知问题与注意事项

| 问题 | 说明 | 修复 |
|------|------|------|
| Python 3.14 + SQLAlchemy 2.0.23 | 元类不兼容新增的 `__firstlineno__`/`__static_attributes__` | 升级 SQLAlchemy 到 `>=2.0.36` |
| `tenacity` 缺失 | `dingtalk_service.py` 引用但未在 requirements.txt | 已补充 `tenacity>=8.2.3` |
| `server/data/` 目录不存在 | SQLite 无法创建数据库文件 | 手动 `mkdir -p server/data` |
| 登录 POST 无响应 | `BaseHTTPMiddleware` 消耗 body 流 | 已改为纯 ASGI 中间件 |
| `.env` 位置 | Pydantic 从工作目录读取 | 本地开发放 `server/`，Docker 放项目根目录 |
