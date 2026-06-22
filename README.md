# JD-Relay — 跨网审批构建转发系统

> Python Forwarder (DMZ) + C++ Agent (内网) 混合架构
> ECDH P-256 + AES-256-GCM + ECDSA P-256 加密链路

## 项目简介

JD-Relay 桥接外网钉钉与内网 Jenkins，实现"钉钉审批 → Jenkins 构建"的双向闭环：

- **流程 A**：钉钉填单 → 三人审批 → Forwarder 加密转发 → Agent 触发 Jenkins 构建
- **流程 B**：Jenkins 构建结果 / 敏感文件变更 → Agent 加密回传 → Forwarder 推送钉钉

## 架构

```
钉钉 (外网) ──HTTPS──→ Forwarder (DMZ, Python) ──WebSocket(加密)──→ Agent (内网, C++) ──REST API──→ Jenkins
```

| 组件 | 语言 | 技术栈 | 职责 |
|------|------|--------|------|
| Forwarder | Python 3.12+ | FastAPI, SQLAlchemy, MySQL, cryptography | 钉钉回调、状态机、MySQL 持久化、WebSocket 服务端 |
| Agent | C++17/20 | Boost.Beast, OpenSSL, nlohmann/json | WebSocket 客户端、Jenkins API、敏感文件检测 |

## 当前进度

| Phase | 内容 | 状态 |
|-------|------|------|
| Phase 1 | C++ 加密模块 (ECDH/AES-GCM/ECDSA) | ✅ 39/39 测试 |
| Phase 2 | C++ WebSocket 客户端 (握手/重连/路由) | ✅ 4/4 测试 |
| Phase 2.5 | Python Forwarder 骨架 + 跨语言互通 | ✅ 65/65 测试 |
| Phase 3 | 钉钉/Jenkins 对接 + 状态机 + 数据模型 | ✅ 11/11 测试 |
| Phase 4 | 端到端联调 + 部署 | 🔄 进行中 |

## 目录结构

```
jd-relay/
├── forwarder/                 # Python Forwarder (Phase 3 ✅)
│   ├── app/
│   │   ├── main.py             #   FastAPI 入口
│   │   ├── config.py           #   配置加载 (pydantic-settings)
│   │   ├── state.py            #   工单状态机
│   │   ├── models.py           #   SQLAlchemy 数据模型
│   │   ├── database.py         #   异步数据库引擎 + FastAPI dependency
│   │   ├── api/                #   REST API 路由
│   │   │   ├── dingtalk.py    #   钉钉回调 / 审批触发
│   │   │   └── admin.py       #   管理面板 API（真实数据库查询）
│   │   ├── services/           #   业务逻辑
│   │   │   ├── dingtalk.py   #   钉钉 SDK 封装（alibabacloud-dingtalk）
│   │   │   ├── relay.py       #   relay 服务（审批→构建→结果回传）
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
│   ├── crypto/                 #   加密模块 (OpenSSL: ECDH/AES-GCM/ECDSA)
│   ├── protocol/               #   握手协议
│   ├── ws_client/              #   WebSocket 客户端
│   ├── jenkins/                #   Jenkins REST API 客户端
│   ├── tools/                  #   CLI 工具 (keygen/encryptor/decryptor/agent_main)
│   └── CMakeLists.txt
├── tests/
│   ├── python/                 #   Python 测试 (65 tests)
│   ├── unit/                   #   C++ 单元测试
│   └── integration/            #   C++ 集成测试 + 跨语言
├── alembic/                    #   数据库迁移
├── config/                     #   配置样例
├── docs/                       #   文档
│   ├── architecture.md        #   架构设计文档
│   ├── crypto-spec.md         #   加密协议规范
│   ├── phase1-summary.md     #   Phase 1 交付概览
│   ├── phase2-summary.md     #   Phase 2 交付概览
│   ├── phase3-summary.md     #   Phase 3 交付概览（新建）
│   ├── security.md            #   安全设计
│   └── ui-ux.md              #   UI/UX 设计
├── deploy/                     #   部署文件 (Docker / systemd / Nginx)
├── legacy/                     #   归档代码 (旧 Python + 旧 C++ Forwarder)
├── CMakeLists.txt              #   顶层 CMake
├── pyproject.toml              #   Python 项目配置
├── docker-compose.yml          #   开发环境
└── TODO.md                     #   待办事项
```

## 快速开始

### Python Forwarder

```bash
# 安装依赖
cd D:/workspace/jd-relay/forwarder
python -m venv .venv && .venv/Scripts/activate
pip install -e ".[dev]"

# 生成密钥（ECDSA）
python -m forwarder.app.config --gen-keys

# 运行（开发模式）
python -m forwarder.app.main

# 运行测试
pytest forwarder/tests/ -v   # 65/65 passed
```

### C++ Agent

```bash
# 在 WSL Ubuntu 24.04 中构建
cd /mnt/d/workspace/jd-relay/agent
mkdir -p build && cd build
cmake .. -DCMAKE_BUILD_TYPE=Release
cmake --build . -j$(nproc)

# 运行测试
ctest --output-on-failure   # 43/43 passed

# 生成密钥
./bin/keygen /tmp/keys
```

## 关键文档

- [加密协议规范](docs/crypto-spec.md) — C++ ↔ Python 互通的精确规范
- [架构设计](docs/architecture.md) — 系统拓扑、数据流、部署架构
- [Phase 3 交付概览](docs/phase3-summary.md) — 钉钉/Jenkins 对接完成记录
- [待办事项](TODO.md) — Phase 4 详细任务

## 加密链路

```
ECDH P-256 密钥协商 → SHA256(shared_secret) → AES-256-GCM 会话密钥
                                          ↓
消息加密: AES-256-GCM (IV=12B, Tag=16B, 分开存储)
签名: ECDSA P-256 + SHA-256 (DER 编码)
防重放: ±5 分钟时间戳窗口 + nonce 缓存查重
信封: CryptoEnvelope JSON (msg_id/timestamp/nonce/type/iv/ciphertext/tag/signature)
```
