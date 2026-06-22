# 架构设计文档 — JD-Relay 混合架构

> 版本：3.0（混合架构）  日期：2026-06-19
> 变更：v2.x 为纯 C++ 双进程 → v3.0 改为 Python Forwarder + C++ Agent

---

## 1. 系统概述

JD-Relay 是一个跨网审批构建转发系统，桥接外网钉钉与内网 Jenkins，实现"钉钉审批 → Jenkins 构建"的双向闭环。

### 1.1 核心约束

| 约束 | 说明 |
|------|------|
| 网络隔离 | Jenkins 在内网，钉钉在外网，无法直连 |
| 加密通信 | Forwarder↔Agent 之间使用 ECDH+AES-256-GCM+ECDSA 加密 |
| 内网零入站 | Agent 主动出站连接 Forwarder，内网不开任何入站端口 |
| 钉钉 OA 审批 | 使用钉钉官方 OA 审批流引擎，三人会签 |
| 敏感文件二次审核 | 构建中检测 special.md 变更，触发二次审批 |

### 1.2 混合架构决策

| 组件 | 语言 | 理由 |
|------|------|------|
| Forwarder | **Python** (FastAPI) | I/O 密集型；钉钉/Jenkins SDK 生态丰富；SQLAlchemy ORM 远优于 C++ 手写 SQL；Web 管理面板直接复用 Jinja2 |
| Agent | **C++** (C++17/20) | 执行密集型；轻量高效触发构建；Phase 1 加密 + Phase 2 传输已完成，无需重写 |

---

## 2. 系统拓扑

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              外网 (Internet)                              │
│                                                                         │
│   ┌─────────────────────┐         ┌─────────────────────┐               │
│   │   钉钉开放平台        │         │   钉钉客户端         │               │
│   │   (Open Platform)    │         │   (审批人/发起人)     │               │
│   └──────────┬──────────┘         └─────────────────────┘               │
└──────────────┼──────────────────────────────────────────────────────────┘
               │ HTTPS
               │ ① 审批回调事件推送
               │ ② 调用钉钉 API (创建/查询审批)
               ▼
╔═══════════════════════════════════════════════════════════════════════════╗
║                          DMZ 区 (Forwarder)                               ║
║                                                                           ║
║  ┌─────────────────────────────────────────────────────────────────────┐ ║
║  │              Python 3.12+ / FastAPI / uvicorn                        │ ║
║  │                                                                       │ ║
║  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐               │ ║
║  │  │ 钉钉回调路由  │  │ 管理 API 路由 │  │ Web 管理面板  │               │ ║
║  │  │ /dt/callback │  │ /admin/*     │  │ Jinja2 模板   │               │ ║
║  │  └──────┬───────┘  └──────────────┘  └──────────────┘               │ ║
║  │         │                                                             │ ║
║  │  ┌──────▼─────────────────────────────────────────────────────────┐  │ ║
║  │  │                   业务服务层 (Services)                          │  │ ║
║  │  │  ApprovalService │ BuildService │ AgentRouter │ StateMachine    │  │ ║
║  │  └──────┬──────────────────────────────────────┬──────────────────┘  │ ║
║  │         │                                      │                     │ ║
║  │  ┌──────▼──────────┐              ┌───────────▼──────────────────┐  │ ║
║  │  │  钉钉 API 客户端  │              │  WebSocket 服务端 (agent-ws)  │  │ ║
║  │  │  (dingtalk SDK)  │              │  ECDH 握手 + 加密 I/O         │  │ ║
║  │  └─────────────────┘              └───────────┬──────────────────┘  │ ║
║  │                                                │                     │ ║
║  │  ┌─────────────────────────────────────────────▼──────────────────┐ │ ║
║  │  │           加密模块 (cryptography 库, 与 C++ 互通)                │ │ ║
║  │  │  ECDH P-256 │ AES-256-GCM │ ECDSA P-256 │ ReplayGuard          │ │ ║
║  │  └────────────────────────────────────────────────────────────────┘ │ ║
║  └─────────────────────────────────────────────────────────────────────┘ ║
║                                                                           ║
║  ┌─────────────────────────────────────────────────────────────────────┐ ║
║  │                    MySQL (SQLAlchemy ORM)                            │ ║
║  │  work_orders │ agents │ approvals │ build_results │ crypto_audit    │ ║
║  └─────────────────────────────────────────────────────────────────────┘ ║
╚════════════════════════════════════════╤══════════════════════════════════╝
                                         │
                  WebSocket (出站连接)     │
                  ECDH+AES-GCM+ECDSA 加密  │
                                         │
╔════════════════════════════════════════╧══════════════════════════════════╗
║                           内网 (Agent)                                    ║
║                                                                           ║
║  ┌─────────────────────────────────────────────────────────────────────┐ ║
║  │              C++17/20 (CMake + Ninja, Boost.Beast)                   │ ║
║  │                                                                       │ ║
║  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐               │ ║
║  │  │ WsClient     │  │ 加密模块      │  │ Jenkins API  │               │ ║
║  │  │ (出站连接)    │  │ (OpenSSL)    │  │ 客户端        │               │ ║
║  │  │ 自动重连      │  │ ECDH+AES-GCM │  │ REST API     │               │ ║
║  │  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘               │ ║
║  │         │                 │                  │                        │ ║
║  │  ┌──────▼─────────────────▼──────────────────▼───────────────────┐   │ ║
║  │  │              消息处理引擎                                       │   │ ║
║  │  │  BUILD_TRIGGER → Jenkins │ BUILD_RESULT → Forwarder           │   │ ║
║  │  │  SENSITIVE_REVIEW_REQ → Forwarder                             │   │ ║
║  │  └────────────────────────────────────────────────────────────────┘   │ ║
║  │                                                                       │ ║
║  │  ┌─────────────────────────────────────────────────────────────────┐  │ ║
║  │  │              SQLite (离线缓冲, 规划中)                            │  │ ║
║  │  └─────────────────────────────────────────────────────────────────┘  │ ║
║  └─────────────────────────────────────────────────────────────────────┘ ║
║                                                                           ║
║  ┌─────────────────────┐                                                 ║
║  │   Jenkins (内网)     │                                                 ║
║  │   REST API + Token   │                                                 ║
║  └─────────────────────┘                                                 ║
╚═══════════════════════════════════════════════════════════════════════════╝
```

---

## 3. 四条链路

| # | 链路 | 协议 | 用途 |
|---|------|------|------|
| 1 | 钉钉 → Forwarder | HTTPS + 钉钉原生签名 | 审批回调、卡片回调 |
| 2 | Forwarder ↔ Agent | WebSocket + ECDH+AES-GCM+ECDSA | **加密链路**，触发构建、回传结果 |
| 3 | Agent → Jenkins | REST API + API Token | 触发构建、恢复 input、采集结果 |
| 4 | Forwarder → 钉钉 | HTTPS + 钉钉 API | 创建审批、推送卡片、工作通知 |

---

## 4. 加密模型

详见 [crypto-spec.md](./crypto-spec.md)。摘要：

| 要素 | 规范 |
|------|------|
| 密钥协商 | ECDH P-256，PEM 格式交换公钥 |
| 会话密钥 | `SHA256(raw_shared_secret)` → 32B AES key |
| 对称加密 | AES-256-GCM（IV=12B, Tag=16B, 分开存储） |
| 身份签名 | ECDSA P-256 + SHA-256, DER 编码 |
| 签名载荷 | `msg_id\|timestamp\|nonce\|type\|iv\|ciphertext\|tag` |
| 防重放 | ±5 分钟时间戳窗口 + nonce 缓存查重 |
| 接口抽象 | ICipher/ISigner（C++），可替换 SM4/SM2 |

---

## 5. 状态机（Forwarder 侧管理）

```
DRAFT → PENDING_APPROVAL → APPROVED → BUILDING
                                        ├─ 无敏感文件 → SUCCESS/FAILED → CLOSED
                                        └─ 敏感文件 → PENDING_SECOND_REVIEW
                                                       ├─ SECOND_APPROVED → BUILDING → SUCCESS/FAILED → CLOSED
                                                       └─ SECOND_REJECTED → ABORTED → CLOSED
```

Agent 无业务状态，只执行命令和上报结果。

---

## 6. 1:N Agent 路由

Forwarder 支持多个 Agent 连接，通过 `project` 字段路由：

```
Agent-A (projects: ["proj-a", "proj-c"]) ──→ Forwarder
Agent-B (projects: ["proj-b"])            ──→ Forwarder
                                           │
                      钉钉工单 project="proj-b"
                                           │
                          路由到 Agent-B
```

- 握手时 Agent 声明其负责的 projects 列表
- Forwarder 维护 `project → agent_id` 映射表
- 工单的 project 字段决定转发目标

---

## 7. 核心业务流程

### 7.1 流程 A：钉钉 → Jenkins（审批触发构建）

```
钉钉填单 (ISSUE/Project/Branch/Build)
    │
    ▼
Forwarder 接收回调 → 创建工单 (状态: PENDING_APPROVAL)
    │
    ▼
钉钉 OA 审批流（三人会签）
    │
    ├─ 全部通过 → 状态: APPROVED
    │       │
    │       ▼
    │   Forwarder 通过 WebSocket 加密发送 BUILD_TRIGGER 给 Agent
    │       │
    │       ▼
    │   Agent 调用 Jenkins REST API 触发构建
    │       │
    │       ▼
    │   状态: BUILDING
    │
    └─ 任一拒绝 → 状态: REJECTED → CLOSED
```

### 7.2 流程 B：Jenkins → 钉钉（构建结果 + 敏感文件审核）

```
Agent 轮询 Jenkins 构建状态
    │
    ├─ 构建成功/失败
    │       │
    │       ▼
    │   Agent 加密发送 BUILD_RESULT 给 Forwarder
    │       │
    │       ▼
    │   Forwarder 更新工单状态 → 推送钉钉通知 → CLOSED
    │
    └─ 检测到 special.md 变更
            │
            ▼
        Agent 加密发送 SENSITIVE_REVIEW_REQ 给 Forwarder
            │
            ▼
        Forwarder 发起钉钉二次审批 → 状态: PENDING_SECOND_REVIEW
            │
            ├─ 通过 → SECOND_REVIEW_RESULT(approved=true) → Agent 恢复构建
            │
            └─ 拒绝 → SECOND_REVIEW_RESULT(approved=false) → Agent 终止构建
```

---

## 8. 目录结构

```
jd-relay/
├── agent/                      # C++ Agent（已完成 Phase 1+2）
│   ├── crypto/                 #   加密模块 (ECDH/AES-GCM/ECDSA)
│   ├── protocol/               #   握手协议 (HandshakeInit/Ack)
│   ├── ws_client/              #   WebSocket 客户端 (自动重连)
│   ├── tools/                  #   CLI 工具 (keygen/encryptor/decryptor)
│   └── CMakeLists.txt
├── forwarder/                  # Python Forwarder（Phase 3 ✅ 已完成）
│   ├── app/
│   │   ├── main.py             #   FastAPI 入口
│   │   ├── config.py           #   配置加载 (pydantic-settings)
│   │   ├── state.py            #   工单状态机
│   │   ├── models.py           #   SQLAlchemy 数据模型
│   │   ├── database.py        #   异步数据库引擎 + FastAPI dependency
│   │   ├── api/                #   REST API 路由
│   │   │   ├── dingtalk.py    #   钉钉回调 / 审批触发
│   │   │   └── admin.py       #   管理面板 API（真实数据库查询）
│   │   ├── services/           #   业务逻辑
│   │   │   ├── dingtalk.py   #   钉钉 SDK 封装（alibabacloud-dingtalk）
│   │   │   ├── relay.py       #    relay 服务（审批→构建→结果回传）
│   │   │   └── jenkins.py    #   Jenkins API 客户端（C++ Agent 端）
│   │   ├── crypto/             #   加密模块（与 C++ 互通）
│   │   ├── ws/                 #   WebSocket 服务端
│   │   │   ├── server.py      #   FastAPI WebSocket 路由 + Agent 管理
│   │   │   ├── agent_session.py #   单 Agent 会话
│   │   │   └── agent_manager.py #   Agent 注册表（by_agent_id + by_project）
│   │   ├── templates/          #   Jinja2 模板（Web 管理面板）
│   │   │   ├── base.html      #   基础模板（亮/暗主题切换）
│   │   │   ├── dashboard.html #   仪表盘（统计卡片 + 活动流）
│   │   │   ├── orders.html    #   工单列表 + 过滤/搜索
│   │   │   └── agents.html   #   Agent 状态监控
│   │   └── static/            #   静态资源（CSS/JS）
│   ├── config/                 #   配置样例（forwarder.conf.example）
│   ├── tests/                 #   Python 测试（65 项全绿）
│   ├── pyproject.toml        #   Python 项目配置（v3.0.0）
│   ├── alembic.ini            #   Alembic 迁移配置（已更新路径）
│   └── docker-compose.yml    #   开发环境（MySQL + Forwarder）
├── agent/                      # C++ Agent（Phase 1+2 ✅，Phase 3 main ✅）
│   ├── crypto/                 #   加密模块 (ECDH/AES-GCM/ECDSA)
│   ├── protocol/               #   握手协议 (HandshakeInit/Ack)
│   ├── ws_client/              #   WebSocket 客户端 (自动重连)
│   ├── jenkins/                #   Jenkins REST API 客户端
│   ├── tools/                  #   CLI 工具 (keygen/encryptor/decryptor/agent_main)
│   └── CMakeLists.txt
├── tests/                      # C++ 测试
│   ├── unit/                   #   Phase 1 单元测试 (39)
│   └── integration/            #   Phase 2 集成测试 (4) + 跨语言 (1)
├── config/                     # 配置样例
│   ├── forwarder.conf.example
│   ├── agent.conf.example
│   └── special.md
├── docs/                       # 文档
│   ├── architecture.md        #   本文档
│   ├── crypto-spec.md         #   加密协议规范
│   ├── phase1-summary.md     #   Phase 1 交付概览
│   ├── phase2-summary.md     #   Phase 2 交付概览
│   ├── phase3-summary.md     #   Phase 3 交付概览（新建）
│   ├── security.md            #   安全设计
│   └── ui-ux.md              #   UI/UX 设计
├── deploy/                     # 部署配置
├── jenkins/                    # Jenkins 脚本
├── legacy/                     # 归档代码
│   ├── python/                 #   旧 Python/FastAPI 代码
│   ├── cpp_forwarder/          #   旧 C++ Forwarder
│   └── docs/                   #   旧文档
├── CMakeLists.txt              # 顶层 CMake（只构建 Agent）
├── Makefile                    # 构建辅助
├── README.md
└── TODO.md
```

---

## 9. 技术栈

### 9.1 Forwarder (Python)

| 组件 | 技术选型 | 说明 |
|------|---------|------|
| Web 框架 | FastAPI + uvicorn | 异步 HTTPS 服务 |
| ORM | SQLAlchemy 2.0 + MySQL | 工单/审批/构建记录持久化 |
| WebSocket | `websockets`（原生，FastAPI 集成） | Agent 连接管理（/agent-ws）|
| 加密 | `cryptography` 库 | ECDH/AES-GCM/ECDSA，与 C++ 互通 |
| 钉钉 | alibabacloud-dingtalk（钉钉官方 Python SDK） | 审批流/消息通知/工作通知 |
| 模板 | Jinja2 | Web 管理面板 |
| 配置 | pydantic-settings | 环境变量 + 配置文件 |

### 9.2 Agent (C++)

| 组件 | 技术选型 | 说明 |
|------|---------|------|
| 构建 | CMake + Ninja | WSL Ubuntu 24.04 |
| WebSocket | Boost.Beast + Boost.Asio | 客户端 + 自动重连 |
| 加密 | OpenSSL | ECDH/AES-GCM/ECDSA |
| JSON | nlohmann/json | 序列化 |
| 测试 | GoogleTest | 43/43 全绿 |
| 离线缓冲 | SQLite (规划中) | 断线重连后重发 |

---

## 10. 密钥管理

### 10.1 密钥文件

| 文件 | 用途 | 生命周期 |
|------|------|---------|
| `ecdsa_private.pem` | ECDSA 签名私钥 | 持久，部署时生成 |
| `ecdsa_public.pem` | ECDSA 验签公钥 | 持久，部署时生成 |
| ECDH 密钥对 | 会话密钥协商 | 临时，每次连接生成 |

### 10.2 密钥分发

- Forwarder 和 Agent 各自持有自己的 ECDSA 密钥对
- 双方的 ECDSA 公钥在握手阶段通过 HandshakeInit/Ack 明文交换
- ECDSA 公钥应预先在安全渠道交换并比对指纹（防止 MITM）

### 10.3 密钥生成

使用 C++ 端的 `keygen` 工具生成：
```bash
./build/bin/keygen /path/to/keys
# 生成: ecdsa_private.pem, ecdsa_public.pem, ecdh_private.pem, ecdh_public.pem, aes_key.txt
```

或使用 OpenSSL 命令行：
```bash
openssl ecparam -name prime256v1 -genkey -noout -out ecdsa_private.pem
openssl ec -in ecdsa_private.pem -pubout -out ecdsa_public.pem
```

---

## 11. 部署架构

### 11.1 Forwarder 部署（DMZ）

```
Nginx (HTTPS 终止, 443)
    │
    ├──→ uvicorn (FastAPI, 8000)
    │         │
    │         ├──→ MySQL (3306)
    │         └──→ WebSocket /agent-ws (8000)
    │
    └──→ 静态文件 (Web 面板)
```

### 11.2 Agent 部署（内网）

```
Agent 进程 (C++ binary)
    │
    ├──→ 出站 WebSocket → Forwarder:443 (通过 Nginx 代理)
    └──→ Jenkins REST API → jenkins.local:8080
```

### 11.3 防火墙规则

| 区域 | 方向 | 端口 | 用途 |
|------|------|------|------|
| DMZ → 外网 | 出站 | 443 | 钉钉 API 调用 |
| 外网 → DMZ | 入站 | 443 | 钉钉回调 |
| 内网 → DMZ | 出站 | 443 | Agent WebSocket 连接 |
| 内网 | 本地 | 8080 | Agent → Jenkins |

**内网无入站端口。**
