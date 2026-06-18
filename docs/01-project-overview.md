# jd-relay 项目框架与功能总览

## 项目定位

**Jenkins & 钉钉交互转发器** — 部署在中间服务器，桥接内网 Jenkins（CI/CD）与办公网钉钉（协作审批）之间的双向审批流程。

---

## 技术框架

| 层次 | 技术选型 |
|------|---------|
| Web 框架 | **FastAPI 0.104** + Uvicorn（异步 ASGI） |
| ORM | **SQLAlchemy >= 2.0.36**（async 模式） + aiosqlite |
| 数据库 | **SQLite**（轻量文件型，适合中间服务） |
| 数据验证 | **Pydantic v2** |
| 加密 | **pycryptodome**（AES-256-GCM + HMAC-SHA256） |
| 模板引擎 | **Jinja2**（服务端渲染 Web 面板） |
| 认证 | bcrypt 密码哈希 + itsdangerous session |
| HTTP 客户端 | httpx（异步调用钉钉/Jenkins API） |
| 部署 | Docker Compose + Nginx 反代 + Makefile |
| 测试 | pytest（137 个用例，覆盖单元/集成/E2E） |

---

## 核心业务流程

### 流程 1：钉钉审批通过 → 触发 Jenkins 构建

```
钉钉审批单提交 → 审批通过 → 钉钉回调转发器 → 解密参数 → 调用 Jenkins API 触发 Job
```

### 流程 2：Jenkins Pipeline 门禁 → 钉钉审批 → 继续构建

```
Jenkins Pipeline 检测门禁 → CLI 发起加密审批请求 → 转发器创建钉钉审批
→ Leader 审批 → 回调转发器 → 解密回调参数 → Pipeline 继续执行
```

---

## 安全机制（双层保护）

| 安全层 | 机制 |
|--------|------|
| 传输层 | TLS 1.2+ |
| 应用层（Jenkins→转发器） | AES-256-GCM 加密 + HMAC-SHA256 防篡改签名 |
| 应用层（钉钉→转发器） | 钉钉自有 HMAC-SHA256 签名验证 |
| 配置存储 | PBKDF2 派生密钥二次加密敏感配置（100,000 次迭代） |
| 认证 | API Key（Jenkins/CLI）+ 钉钉回调签名 + Session（Web 面板） |

---

## 数据模型（4 张核心表）

| 表名 | 说明 | 状态机 |
|------|------|--------|
| `approvals` | 审批单据 | `pending → approved/rejected/cancelled/expired` |
| `builds` | Jenkins 构建记录 | `pending → queued → building → success/failure/aborted` |
| `logs` | 请求/操作审计日志 | 含来源、耗时、请求 ID |
| `config` | 运行时热更新配置 | 敏感值加密存储 |

---

## 项目结构关键路径

```
server/app/
├── main.py              # FastAPI 入口，中间件注册
├── models.py            # 4 张 ORM 表定义
├── config.py            # Pydantic Settings（从 .env 加载）
├── database.py          # async SQLAlchemy session 管理
├── api/                 # 路由层
│   ├── dingtalk.py      #   钉钉回调 + 发起审批端点
│   ├── jenkins.py       #   触发构建 + 构建回调端点
│   ├── admin.py         #   仪表盘 + 审批/构建/日志/配置管理
│   └── health.py        #   健康检查
├── services/            # 业务层
│   ├── relay_service.py #   核心编排（两个流程的主逻辑）
│   ├── crypto_service.py#   AES-GCM + HMAC 加解密
│   ├── dingtalk_service.py # 钉钉 API 封装（审批实例+工作通知）
│   └── jenkins_service.py  # Jenkins API 封装（触发+查询）
└── middleware/           # 中间件层
    ├── auth.py           #   API Key 认证
    └── logging.py        #   纯 ASGI 日志中间件

cli/jdcli/               # Pipeline CLI 工具
├── main.py              #   入口（4 个子命令）
├── client.py            #   HTTP 客户端
├── crypto.py            #   CLI 端加解密（与服务端一致）
└── commands/
    ├── trigger.py       #   request-approval 子命令
    ├── status.py        #   wait-approval / check-approval 子命令
    └── notify.py        #   notify-result 子命令

tests/                   # 单元/集成/E2E 测试（137 个用例）
deploy/                  # Docker Compose + Nginx + SSL 部署配置
```

---

## API 端点汇总

| 端点 | 认证 | 说明 |
|------|------|------|
| `GET /health` | 无需 | 健康检查 |
| `POST /api/v1/dingtalk/callback` | 钉钉签名 | 钉钉审批回调 |
| `POST /api/v1/dingtalk/send-approval` | API Key | Jenkins CLI 发起审批 |
| `POST /api/v1/jenkins/trigger` | API Key | 触发 Jenkins 构建 |
| `POST /api/v1/jenkins/callback` | API Key | Jenkins 构建结果回调 |
| `GET /api/v1/jenkins/build/{id}/status` | API Key | 构建状态查询 |
| `GET /api/v1/admin/*` | Session + API Key | 管理面板 API |
| `GET /api/v1/sse/logs` | Session + API Key | SSE 实时日志流 |
