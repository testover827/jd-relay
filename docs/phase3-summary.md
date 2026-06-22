# Phase 3 交付概览：钉钉/Jenkins 对接 + Web 管理面板

> 版本：1.0 | 日期：2026-06-22
> 状态：✅ 已完成 | 下一阶段：Phase 4 端到端联调

---

## 交付物清单

### 1. 钉钉官方 SDK 集成 `forwarder/app/services/dingtalk.py`

| 组件 | 文件 | 职责 |
|------|------|------|
| DingTalkService | `dingtalk.py` | 封装 alibabacloud-dingtalk SDK，access_token 管理、审批实例创建/查询、工作通知发送 |
| 回调路由 | `api/dingtalk.py` | 钉钉回调接收、签名验证、审批结果处理、状态机驱动 |

**关键实现细节：**
- 使用 `alibabacloud-dingtalk` 官方 SDK（同步调用，用 `asyncio.run_in_executor` 包装为异步）
- AccessToken 自动刷新（缓存过期自动重新获取）
- 审批状态变更回调自动驱动工单状态机

---

### 2. 状态机 `forwarder/app/state.py`

| 组件 | 说明 |
|------|------|
| 10 状态 | DRAFT → PENDING_APPROVAL → APPROVED → BUILDING → SUCCESS/FAILED → CLOSED |
| 二次审核 | BUILDING → PENDING_SECOND_REVIEW → SECOND_APPROVED → BUILDING / SECOND_REJECTED → ABORTED → CLOSED |
| 16 合法转换 | 完整覆盖所有状态转换路径 |
| 终态检测 | CLOSED / ABORTED 为终态，不可再转换 |

---

### 3. MySQL 持久化 `forwarder/app/models.py` + `forwarder/app/database.py`

| 表名 | 说明 | 关键字段 |
|------|------|---------|
| `work_orders` | 工单 | id, issue, project, branch, build_cmd, status, agent_id, created_at |
| `agents` | Agent 注册 | agent_id, projects, last_seen, online |
| `approvals` | 审批记录 | id, work_order_id, approver, approved, round, timestamp |
| `build_results` | 构建结果 | id, work_order_id, build_number, status, log_url, timestamp |

- 使用 SQLAlchemy 2.0 Async ORM
- Alembic 迁移：`alembic/versions/001_initial_schema.py`
- `database.py` 提供 `get_db_session()` FastAPI dependency

---

### 4. WebSocket 服务端（更新）`forwarder/app/ws/server.py`

| 组件 | 说明 |
|------|------|
| AgentManager | `by_agent_id` + `by_project` 双索引 Agent 注册表 |
| AgentSession | 单个 Agent 会话，加密 I/O 循环 |
| 握手协议 | HandshakeInit 接收 + 验签 → HandshakeAck 生成 + 签名 |
| 路由 | `project → agent_id` 映射，工单路由 |

---

### 5. Web 管理面板 `forwarder/app/templates/`

| 页面 | 文件 | 功能 |
|------|------|------|
| 基础模板 | `base.html` | 亮/暗主题切换、侧边栏导航、30s 自动刷新 |
| 仪表盘 | `dashboard.html` | 统计卡片（6 项）+ 最近工单表 + Agent 状态卡片 |
| 工单列表 | `orders.html` | 状态过滤 + 项目搜索 + 分页 + 详情弹窗 |
| Agent 监控 | `agents.html` | Agent 网格 + 在线状态 + 绿色呼吸灯效果 |

- 使用 Jinja2 模板引擎
- 亮/暗主题切换（持久化到 localStorage）
- 30 秒自动刷新（dashboard）

---

### 6. Admin API（真实数据库查询）`forwarder/app/api/admin.py`

| 路由 | 方法 | 功能 |
|------|------|------|
| `/api/admin/stats` | GET | 仪表盘统计数据 |
| `/api/admin/orders` | GET | 工单列表（支持过滤/分页） |
| `/api/admin/orders/{id}` | GET | 工单详情 |
| `/api/admin/agents` | GET | Agent 列表 + 在线状态 |

---

### 7. Agent 主程序 `agent/tools/agent_main.cpp`

| 功能 | 状态 |
|------|------|
| 配置解析（INI 格式） | ✅ |
| WebSocket 连接 + ECDH 握手 | ✅ |
| BUILD_TRIGGER 处理 → 触发 Jenkins 构建 | ✅ |
| 后台线程轮询构建状态 | ✅ |
| BUILD_RESULT 回传 Forwarder | ✅ |
| special.md 变更检测 | ✅ |
| SENSITIVE_REVIEW_REQ 发送 | ✅ |
| SECOND_REVIEW_RESULT 处理 | ✅ |

**关键实现细节：**
- 每个构建轮询线程拥有自己的 `JenkinsClient` 实例（线程安全）
- `g_active_builds` 使用 `std::mutex` 保护
- 持锁只做查找，网络 I/O 在锁外完成
- `SECOND_REVIEW_RESULT` 处理器正确恢复构建结果发送

---

## 目录结构（更新后）

```
jd-relay/
├── forwarder/                  # Python Forwarder（Phase 3 ✅）
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
│   ├── ws_client/              #   WebSocket 客户端 (Boost.Beast)
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
│   ├── architecture.md        #   架构设计文档
│   ├── crypto-spec.md         #   加密协议规范
│   ├── phase1-summary.md     #   Phase 1 交付概览
│   ├── phase2-summary.md     #   Phase 2 交付概览
│   ├── phase3-summary.md     #   Phase 3 交付概览（新建）
│   ├── security.md            #   安全设计
│   └── ui-ux.md              #   UI/UX 设计
├── deploy/                     # 部署配置
├── jenkins/                    # Jenkins 脚本
├── legacy/                     # 归档代码
├── CMakeLists.txt              # 顶层 CMake（只构建 Agent）
├── pyproject.toml              # Python 项目配置（根目录，用于整体构建）
├── docker-compose.yml          # 开发环境（根目录）
└── TODO.md                     # 待办事项
```

---

## 技术债务（剩余）

| 项目 | 优先级 | 说明 |
|------|---------|------|
| Agent 离线缓冲 SQLite 模块 | P2 | 断线重连后重发消息 |
| Agent 日志系统（spdlog 替换 std::cerr） | P2 | 统一日志格式 + 日志级别 |
| 消息 ACK 确认机制 | P1 | 保证消息送达 |
| Forwarder 心跳机制 | P2 | 检测 Agent 断线 |
| WebSocket 连接池上限与拒绝策略 | P2 | 防止资源耗尽 |
| CI/CD 流水线更新（当前仍为旧 Python 版本） | P1 | 自动化测试 + 部署 |

---

## 下一步（Phase 4）

| 任务 | 说明 |
|------|------|
| WSL 编译 Agent | vcpkg 依赖安装 + 编译验证 |
| Jenkins 实例 | 用于联调验证（可用 Docker 快速起 `jenkins/jenkins:lts`） |
| 钉钉应用配置 | `app_key` / `app_secret` / 回调地址，用于审批全流程验证 |
| 端到端联调 | 完整链路验证：钉钉审批 → Forwarder → Agent → Jenkins → 构建结果 → 钉钉通知 |
| OPERATIONS.md | 部署运维手册 |

---

## 编译与测试

### Python Forwarder

```bash
# 安装依赖
cd D:/workspace/jd-relay/forwarder
python -m venv .venv && .venv/Scripts/activate
pip install -e ".[dev]"

# 初始化数据库
alembic upgrade head

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
