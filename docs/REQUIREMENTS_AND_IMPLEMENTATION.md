# Jenkins & 钉钉交互中继系统 — 需求与实现归纳

## 一、业务需求概述

### 核心场景

企业环境中 **Jenkins**（内网构建服务器）与 **钉钉**（外部审批平台）之间存在**网络隔离**，无法直接通信。需要构建一个 **C/S 架构的中继服务 (Relay)** 部署在中间层网络节点上，桥接两端的交互。

### 两大功能流

| # | 功能方向 | 触发条件 | 流程 |
|---|---------|---------|------|
| **Flow 1** | **钉钉 → Jenkins** | 钉钉审批通过（带参数） | 审批通过 → 中继接收回调 → 解密参数 → 触发对应 Jenkins Job |
| **Flow 2** | **Jenkins → 钉钉 → Jenkins** | Jenkins 检测到敏感文件变更 | 发起审批请求 → 中继转发至钉钉 → 负责人点击通过 → 回调中继 → Jenkins 继续构建 |

### 非功能性需求

| 维度 | 要求 |
|------|------|
| **架构** | C/S 架构，中继部署于中间服务器，Jenkins/钉钉各自通过指定端口与中继通信 |
| **安全性** | 传输数据加密（AES-256-GCM + HMAC-SHA256 签名），API Key 认证，会话管理 |
| **可观测性** | Web 前端展示数据流全貌、实时日志（SSE 推送） |
| **部署** | Docker Compose 一键部署 |
| **集成** | 提供 CLI 工具供 Jenkins Pipeline 直接调用 |

---

## 二、系统架构设计

### 网络拓扑

```
┌─────────────────┐         ┌──────────────────┐         ┌─────────────────┐
│                 │  :8080  │                  │  HTTPS  │                 │
│   Jenkins       │◄──────►│    Relay Server   │◄──────►│     钉钉         │
│   (内网)        │  API    │   (中间网络)      │  API    │   (公网)        │
│                 │         │                  │         │                 │
└───────┬─────────┘         └────────┬──────────┘         └─────────────────┘
        │                            │
        │ CLI (Pipeline)              │ Web Dashboard (:8000)
        └────────────────────────────┘
```

### 技术选型决策

| 层面 | 选型 | 理由 |
|------|------|------|
| 后端框架 | Python FastAPI | 异步高性能、自动 API 文档、Pydantic 校验 |
| 数据库 | SQLite + SQLAlchemy (async) | 轻量级零运维、异步不阻塞事件循环 |
| 前端 | Jinja2 SSR + Tailwind CSS CDN | 无需 Node 构建链、快速交付监控面板 |
| 加密 | AES-256-GCM + HMAC-SHA256 | 机密性+完整性双重保障 |
| 配置加密 | PBKDF2 密钥派生 | 安全存储钉钉/Jenkins 凭证 |
| 实时推送 | SSE (Server-Sent Events) | 单向推送、比 WebSocket 轻量 |
| CLI 工具 | Click + requests | Jenkins Pipeline 友好（shell 执行） |
| 容器化 | Docker Compose | 一键编排 |

### 项目结构 (Monorepo)

```
jenkins-dingtalk-relay/
├── server/                      # FastAPI 中继服务
│   ├── app/
│   │   ├── main.py              # 应用入口，路由注册
│   │   ├── config.py             # Pydantic Settings 配置
│   │   ├── database.py           # SQLAlchemy async 引擎
│   │   ├── models.py             # 4 张 ORM 表定义
│   │   ├── schemas.py            # 14 个 Pydantic 请求/响应模型
│   │   ├── api/                  # API 路由层
│   │   │   ├── health.py         # GET /health
│   │   │   ├── dingtalk.py       # 钉钉回调 & 发起审批
│   │   │   ├── jenkins.py        # Jenkins 触发 & 回调 & 状态查询
│   │   │   └── admin.py          # 管理面板 API + SSR 页面
│   │   ├── services/             # 业务逻辑层
│   │   │   ├── relay_service.py  # 核心编排（双流程）
│   │   │   ├── dingtalk_service.py # 钉钉 API 封装
│   │   │   ├── jenkins_service.py  # Jenkins API 封装
│   │   │   └── crypto_service.py   # 加解密 & 配置加密
│   │   ├── middleware/            # 中间件
│   │   │   ├── auth.py           # API Key 认证
│   │   │   └── logging.py        # 请求日志记录
│   │   ├── templates/            # Jinja2 模板（7 个页面）
│   │   └── static/               # 静态资源
│   ├── requirements.txt
│   ├── Dockerfile
│   └── entrypoint.sh
├── cli/                         # Jenkins Pipeline CLI 工具
│   ├── jdcli/
│   │   ├── main.py              # Click 入口
│   │   ├── client.py            # RelayClient HTTP 客户端
│   │   ├── crypto.py            # CLI 端加解密（与服务端匹配）
│   │   └── commands/
│   │       ├── trigger.py       # request-approval, wait-approval
│   │       ├── status.py        # check-approval
│   │       └── notify.py        # notify-result
│   ├── setup.py
│   └── requirements.txt
├── jenkins/                     # Jenkins 共享库 & Pipeline 示例
│   ├── vars/dingtalkNotify.groovy
│   └── Jenkinsfile.example
├── docker-compose.yml
├── .env.example
├── Makefile
└── README.md
```

---

## 三、数据模型设计

### ER 关系

```
┌──────────────┐       ┌──────────────┐
│   Approval   │──1:N──│    Build     │
├──────────────┤       ├──────────────┤
│ id (PK)      │       │ id (PK)      │
│ type         │       │ approval_id (FK)│
│ status       │       │ jenkins_job  │
│ encrypted_*  │       │ jenkins_build#│
│ dingtalk_*   │       │ status       │
│ created_at   │       │ created_at   │
└──────────────┘       └──────┬───────┘
                              │
┌──────────────┐       ┌──────▼───────┐
│     Log      │       │    Config    │
├──────────────┤       ├──────────────┤
│ id (PK)      │       │ key (PK)     │
│ source       │       │ value (enc)  │
│ level        │       │ updated_at   │
│ action       │       └──────────────┘
│ payload      │
│ timestamp    │
└──────────────┘
```

### Approval 表 — 双类型设计

```python
class Approval(Base):
    type: Literal['dingtalk_to_jenkins', 'jenkins_to_dingtalk']
    status: Literal['pending', 'approved', 'rejected', 'expired', 'failed']

    # Flow 1 字段：钉钉发起的审批
    dingtalk_process_instance_id: Optional[str]
    dingtalk_callback_payload: Optional[str]    # 加密存储

    # Flow 2 字段：Jenkins 发起的审批
    jenkins_job: Optional[str]
    jenkins_build_number: Optional[int]
    jenkins_encrypted_params: Optional[str]     # 加密存储
```

---

## 四、核心数据流详解

### Flow 1: 钉钉审批 → 触发 Jenkins 构建

```
钉钉用户提交审批表单(带参数)
        │
        ▼
  钉钉服务器 POST /api/v1/dingtalk/callback
  (携带 processInstanceId, result, formValues)
        │
        ▼
  ┌─ auth middleware: 跳过 (白名单路径)
  ├─ verify_callback_signature() ← 验证钉钉签名 ✓
  ├─ decrypt(dingtalk_callback_payload)
  ├─ 更新 Approval.status = 'approved'
  ├─ 提取 job_name, parameters
  │
  ▼
  jenkins_service.build_with_params(job_name, params)
  → POST {JENKINS_URL}/job/{name}/buildWithParameters
        │
        ▼
  创建 Build 记录 (status='running')
  写入 Log (action='trigger_build')
  返回 200 OK 给钉钉
```

### Flow 2: Jenkins 构建门禁审批

```
Jenkins Pipeline 检测到敏感文件变更
        │
        ▼
  CLI: jdcli request-approval \
       --job "deploy-prod" \
       --params '{"files": ["config.yaml"]}' \
       --approver "leader_dingtalk_id"
        │
        ▼
  CLI 端: AES-256-GCM 加密 params → HMAC 签名
  POST /api/v1/jenkins/trigger
  Header: X-API-Key: {key}
  Body: { ciphertext, nonce, signature }
        │
        ▼
  ┌─ auth middleware: 验证 X-API-Key ✓
  ├─ verify_hmac_signature() ✓
  ├─ decrypt(encrypted_params)
  ├─ 创建 Approval (type='jenkins_to_dingtalk', status='pending')
  ├─ dingtalk_service.create_process_instance()
        │
  ▼
  返回 { approval_id } 给 CLI
        │
        ▼
  CLI: jdcli wait-approval --id {approval_id} --timeout 300
  → 轮询 GET /api/v1/approval/{id}/status
        │
        ╳═════════════════════════════════╗
       ║  (异步并行的钉钉审批流程)          ║
       ║  钉钉负责人收到审批通知            ║
       ║  点击「同意」或「拒绝」            ║
        ║  钉钉 POST /api/v1/dingtalk/callback║
        ║  → relay_service.handle_dingtalk_callback()║
        ║  → 更新 Approval.status          ║
        ╚═════════════════════════════════╝
        │
        ▼
  轮询返回 approved/rejected
  CLI exit code: 0 (通过) / 1 (拒绝)
  Jenkins Pipeline 根据 exit code 继续/终止
```

---

## 五、安全体系设计

### 多层防御模型

```
┌─────────────────────────────────────────────────┐
│                传输层 (TLS 1.3)                   │
│         Docker 内部网络 / 反向代理 SSL            │
├─────────────────────────────────────────────────┤
│              应用层认证                           │
│  ┌─────────────┐ ┌──────────────┐ ┌───────────┐ │
│  │ X-API-Key   │ │ DingTalk Sig │ │ Session   │ │
│  │ (Jenkins/   │ │ (Callback    │ │ Cookie    │ │
│  │  CLI)       │ │  验签)       │ │ (Web面板) │ │
│  └─────────────┘ └──────────────┘ └───────────┘ │
├─────────────────────────────────────────────────┤
│              应用层加密                           │
│  ┌────────────────────────────────────────────┐  │
│  │  AES-256-GCM (机密性 + 完整性 tag)         │  │
│  │  HMAC-SHA256 (消息认证签名)                  │  │
│  │  存储格式: base64(ciphertext+tag)           │  │
│  │  PBKDF2 (配置值加密派生密钥)                 │  │
│  └────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────┘
```

### 加密数据格式

**CryptoService.encrypt() 输出结构：**
```json
{
  "ciphertext": "base64(ciphertext_16byte_tag)",
  "nonce": "base64(random_12bytes)",
  "signature": "hmac_sha256_base64"
}
```

**SecureConfig 存储格式 (数据库Config表)：**
```
raw_bytes = salt(16) + nonce(16) + tag(16) + ciphertext
```

---

## 六、API 路由总览 (27 个端点)

### 公开端点

| 方法 | 路径 | 用途 |
|------|------|------|
| GET | `/health` | 健康检查 |
| POST | `/api/v1/dingtalk/callback` | 钉钉审批结果回调 |
| GET | `/`, `/login` | Web 页面 |

### Jenkins/CLI 端点 (需 X-API-Key)

| 方法 | 路径 | 用途 |
|------|------|------|
| POST | `/api/v1/jenkins/trigger` | Jenkins 发起审批请求 |
| POST | `/api/v1/jenkins/callback` | Jenkins 构建完成回调 |
| GET | `/api/v1/jenkins/build/{id}/status` | 查询构建状态 |

### 钉钉端点

| 方法 | 路径 | 用途 |
|------|------|------|
| POST | `/api/v1/dingtalk/send-approval` | 主动发送审批（管理用） |

### 管理后台 (需 Session 登录)

| 方法 | 路径 | 用途 |
|------|------|------|
| GET | `/api/v1/dashboard/stats` | 面板统计数据 |
| GET/POST | `/api/v1/approvals` | 审批列表/创建 |
| GET | `/api/v1/approvals/{id}` | 审批详情 |
| GET/POST | `/api/v1/builds` | 构建列表 |
| GET | `/api/v1/builds/{id}` | 构建详情 |
| GET | `/api/v1/logs` | 日志列表 |
| GET | `/api/v1/logs/stream` | SSE 实时日志流 |
| GET/PUT | `/api/v1/config` | 配置管理 |

### SSR 页面

| 路径 | 页面 |
|------|------|
| `/` | 仪表盘首页 |
| `/login` | 登录页 |
| `/approvals` | 审批列表 |
| `/builds` | 构建列表 |
| `/logs` | 日志查看 (SSE) |
| `/config` | 配置管理 |

---

## 七、CLI 命令参考 (Jenkins Pipeline 集成)

### 安装与配置

```bash
# 安装
pip install -e cli/

# 环境变量 (Jenkins Credential 中配置)
export JD_RELAY_URL="http://relay:8080"
export JD_API_KEY="your-api-key"
export JD_AES_KEY="32-byte-hex-key"
export JD_HMAC_SECRET="your-hmac-secret"
```

### 命令清单

```bash
# 发起审批请求 (Flow 2 入口)
jdcli request-approval \
  --job "deploy-production" \
  --params '{"version": "1.2.3", "files": ["config.yaml"]}' \
  --approver "manager01" \
  --title "生产环境部署审批"

# 阻塞等待审批结果 (Pipeline 中阻塞执行)
jdcli wait-approval \
  --id "approval-uuid" \
  --timeout 300 \
  --interval 5

# 非阻塞检查状态
jdcli check-approval --id "approval-uuid"

# 通知审批结果回钉钉
jdcli notify-result \
  --approval-id "approval-uuid" \
  --build-url "https://jenkins/job/deploy/42/" \
  --status "success" \
  --message "部署成功完成"
```

### Jenkins Pipeline 集成示例

```groovy
// Jenkinsfile 中使用
pipeline {
    agent any
    stages {
        stage('检测变更') {
            steps {
                script {
                    def changedFiles = ...
                    if (hasSensitiveFile(changedFiles)) {
                        // 发起钉钉审批
                        def result = sh(
                            script: "jdcli request-approval --job '${JOB_NAME}' --params '{\"files\": [\"config.yaml\"]}'",
                            returnStatus: true
                        )
                        // 阻塞等待
                        sh "jdcli wait-approval --id ${result.approval_id} --timeout 300"
                    }
                }
            }
        }
    }
}
```

---

## 八、Web 监控面板功能

### 页面功能矩阵

| 页面 | 功能 | 技术实现 |
|------|------|---------|
| **仪表盘** | 审批/构建/日志统计卡片、最近活动时间线 | Chart.js + Fetch API |
| **审批列表** | 分页表格、类型筛选、状态标签色、详情弹窗 | Fetch + 模态框 |
| **构建列表** | Job 名称/编号/状态/关联审批、重试按钮 | Fetch + 状态轮询 |
| **日志中心** | 历史日志列表 + SSE 实时流自动滚动 | EventSource API |
| **配置管理** | 钉钉 AppKey/Secret、Jenkins URL/Token 编辑 | 表单 PUT |

### SSE 实时日志实现

```javascript
// 前端 EventSource 连接
const es = new EventSource('/api/v1/logs/stream');
es.onmessage = (event) => {
    const log = JSON.parse(event.data);
    appendLogLine(log.timestamp, log.source, log.level, log.action);
};

// 服务端 asyncio.Queue 广播
_log_queue = asyncio.Queue()
async def stream_logs():
    while True:
        log_data = await _log_queue.get()
        yield f"data: {json.dumps(log_data)}\n\n"
```

---

## 九、部署架构

### Docker Compose 编排

```yaml
services:
  relay:
    build: ./server
    ports:
      - "8080:8080"    # API (Jenkins/钉钉/CLI)
      - "8000:8000"    # Web Dashboard
    env_file: .env
    volumes:
      - ./data:/app/data   # SQLite 持久化
    restart: unless-stopped
```

### 关键环境变量 (.env)

| 变量 | 说明 | 示例 |
|------|------|------|
| `DATABASE_URL` | SQLite 连接串 | `sqlite:///data/relay.db` |
| `DINGTALK_APP_KEY` | 钉钉应用 Key | `dingxxxxxxxx` |
| `DINGTALK_APP_SECRET` | 钉钉应用 Secret | `********` |
| `DINGTALK_PROCESS_CODE` | 审批模板 ID | `proc_xxxxx` |
| `JENKINS_URL` | Jenkins 地址 | `http://jenkins:8080` |
| `JENKINS_USER` | Jenkins 用户 | `admin` |
| `JENKINS_API_TOKEN` | Jenkins API Token | `********` |
| `AES_ENCRYPTION_KEY` | AES-256 十六进制密钥 | (64 hex chars) |
| `HMAC_SECRET` | HMAC 签名密钥 | (随机字符串) |
| `API_KEY` | Jenkins/CLI 调用密钥 | (随机字符串) |
| `ADMIN_USERNAME` | 面板管理员用户名 | `admin` |
| `ADMIN_PASSWORD_HASH` | 面板密码 bcrypt hash | `$2b$12$...` |

---

## 十、已解决的技术问题记录

| 问题 | 原因 | 解决方案 |
|------|------|---------|
| SecureConfig MAC 校验失败 | PBKDF2 缺少 `dklen=32` 参数，nonce 切片位置错误 (16B 非 12B) | 添加 dklen 参数，修正 raw[16:32] / raw[32:48] 切片 |
| aiosqlite 模块缺失 | 依赖未安装 | `pip3 install aiosqlite` |
| 静态文件 404 | 相对路径在 uvicorn 启动目录不同时失效 | 使用 `BASE_DIR / "app" / "static"` 绝对路径 |
| 循环导入 | admin.py → main.py → admin.py | 将 uptime 变量本地化为 `_START_TIME` |
| API 路径 307 重定向 | FastAPI 尾部斜杠自动重定向 + prefix 叠加 | 统一使用 `/api/v1/xxx` 无尾斜杠路径 |
| uvicorn 启动模块找不到 | 从项目根目录启动而非 server/ 目录 | `uvicorn app.main:app --app-dir server/` 或 cd server/ |

---

## 十一、文件清单统计

| 目录 | 文件数 | 用途 |
|------|-------|------|
| `server/app/api/` | 5 | API 路由 |
| `server/app/services/` | 4 | 业务服务 |
| `server/app/middleware/` | 2 | 中间件 |
| `server/app/templates/` | 7 | SSR 页面 |
| `server/app/` (根) | 6 | 入口/配置/模型 |
| `cli/jdcli/` | 6 + 3 commands | CLI 工具 |
| `jenkins/` | 2 | Pipeline 集成 |
| 项目根 | 5 | 编排/配置/文档 |
| **合计** | **~46 文件** | |
