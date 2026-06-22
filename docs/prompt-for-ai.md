# JD-Relay 后续实现提示词（交给 AI 执行）

> 本文档是自包含的完整实现指引。Phase 2.5 + Phase 3 已完成，本文档保留供参考。

---

## 项目背景

你正在开发 **JD-Relay** —— 一个跨网审批构建转发系统，桥接外网钉钉与内网 Jenkins。

### 架构

系统采用 **混合架构**：

| 组件 | 语言 | 部署位置 | 职责 |
|------|------|---------|------|
| Forwarder | Python (FastAPI) | DMZ | 接收钉钉回调、管理工单状态机、MySQL 持久化、WebSocket 服务端（与 Agent 通信） |
| Agent | C++ (C++17/20) | 内网 | 主动出站连接 Forwarder、触发 Jenkins 构建、采集结果、检测敏感文件 |

### 四条链路

1. 钉钉 → Forwarder：HTTPS + 钉钉原生签名（审批回调）
2. Forwarder ↔ Agent：WebSocket + ECDH+AES-GCM+ECDSA 加密（**核心加密链路**）
3. Agent → Jenkins：REST API + Token（触发构建/采集结果）
4. Forwarder → 钉钉：HTTPS + 钉钉 API（创建审批/推送通知）

### 当前进度

- **Phase 1 ✅**：C++ 加密模块完成（39/39 单元测试全绿）
- **Phase 2 ✅**：C++ WebSocket 客户端完成（4/4 集成测试全绿）
- **Phase 2.5**：Python Forwarder 骨架 + 跨语言加密互通 ← **你要做的**
- **Phase 3**：钉钉/Jenkins 对接 ← **你要做的**

### 项目根目录

```
D:\workspace\jd-relay\  (Windows)  或  /mnt/d/workspace/jd-relay/  (WSL)
```

### 当前目录结构

```
jd-relay/
├── agent/                      # C++ Agent（已完成）
│   ├── crypto/                 #   加密模块 (OpenSSL: ECDH/AES-GCM/ECDSA)
│   │   ├── include/jd_relay/crypto/
│   │   │   ├── icipher.h       #   ICipher 接口
│   │   │   ├── isigner.h       #   ISigner 接口
│   │   │   ├── aes_gcm_cipher.h
│   │   │   ├── ecdh_key_exchange.h
│   │   │   ├── ecdsa_signer.h
│   │   │   ├── envelope.h      #   CryptoEnvelope 结构 + MessageType 枚举
│   │   │   ├── crypto_codec.h  #   顶层编解码器
│   │   │   ├── replay_guard.h  #   防重放
│   │   │   ├── base64.h
│   │   │   └── key_manager.h
│   │   └── src/                #   实现文件
│   ├── protocol/               #   握手协议
│   │   └── include/jd_relay/protocol/handshake.h
│   ├── ws_client/              #   WebSocket 客户端 (Boost.Beast)
│   │   ├── include/jd_relay/agent/ws_client.h
│   │   └── src/ws_client.cpp
│   ├── tools/                  #   CLI 工具 (keygen/encryptor/decryptor)
│   └── CMakeLists.txt
├── tests/
│   ├── unit/                   #   Phase 1 单元测试 (39)
│   └── integration/            #   Phase 2 集成测试 (4)
├── docs/
│   ├── architecture.md       #   架构设计文档
│   ├── crypto-spec.md         #   加密协议规范（**必读**）
│   ├── phase1-summary.md     #   Phase 1 交付概览
│   ├── phase2-summary.md     #   Phase 2 交付概览
│   └── ui-ux.md              #   UI/UX 设计参考
├── legacy/
│   ├── python/                 #   旧 Python/FastAPI 代码（可参考复用）
│   │   └── server/app/         #     含 dingtalk_service.py, models.py 等
│   ├── cpp_forwarder/          #   旧 C++ Forwarder（已归档）
│   └── docs/                   #   旧文档
├── config/
│   ├── forwarder.conf.example
│   ├── agent.conf.example
│   └── special.md
├── deploy/
├── jenkins/
├── CMakeLists.txt
└── TODO.md
```

---

## 任务 1：实现 Python Forwarder 骨架 + 加密模块 + WebSocket 服务端

### 1.1 创建 `forwarder/` 目录结构

```
forwarder/
├── app/
│   ├── __init__.py
│   ├── main.py                 # FastAPI 入口
│   ├── config.py               # 配置加载 (pydantic-settings)
│   ├── crypto/
│   │   ├── __init__.py
│   │   ├── ecdh.py             # ECDH P-256 密钥协商
│   │   ├── aes_gcm.py          # AES-256-GCM 加解密
│   │   ├── ecdsa.py            # ECDSA P-256 签名/验签
│   │   ├── envelope.py         # CryptoEnvelope 结构 + JSON 序列化
│   │   ├── replay_guard.py     # 防重放
│   │   └── codec.py            # 顶层编解码器 (encrypt+sign / verify+decrypt)
│   ├── ws/
│   │   ├── __init__.py
│   │   ├── server.py           # WebSocket 服务端 (/agent-ws)
│   │   ├── agent_session.py    # 单个 Agent 会话管理
│   │   └── agent_manager.py    # Agent 注册表 (by_agent_id + by_project)
│   ├── api/
│   │   ├── __init__.py
│   │   ├── dingtalk.py         # 钉钉回调路由
│   │   ├── admin.py            # 管理 API
│   │   └── health.py           # 健康检查
│   ├── services/
│   │   ├── __init__.py
│   │   ├── approval.py         # 审批服务
│   │   ├── build.py            # 构建服务
│   │   └── agent_router.py     # Agent 路由服务
│   ├── models/
│   │   ├── __init__.py
│   │   └── database.py         # SQLAlchemy 模型 (Phase 3)
│   ├── middleware/
│   │   ├── __init__.py
│   │   ├── auth.py             # API Key 认证
│   │   └── logging.py          # 请求日志
│   └── templates/              # Jinja2 模板 (Phase 3)
├── tests/
│   ├── __init__.py
│   ├── test_crypto.py          # 加密模块单元测试
│   ├── test_handshake.py       # 握手协议测试
│   └── test_ws_server.py       # WebSocket 服务端测试
├── pyproject.toml
└── .env.example
```

### 1.2 加密模块（`forwarder/app/crypto/`）— 必须与 C++ Agent 完全兼容

**这是最关键的部分。** 加密模块必须与 C++ Agent 端完全互通。

详细规范见 `docs/crypto-spec.md`，以下是要点：

#### ECDH 密钥协商
```python
# 曲线: P-256 (secp256r1)
# 公钥交换格式: PEM (SubjectPublicKeyInfo)
# 会话密钥推导: SHA256(raw_shared_secret) — 不是 HKDF!
from cryptography.hazmat.primitives.asymmetric import ec
import hashlib

shared_secret = my_ecdh_private.exchange(ec.ECDH(), peer_ecdh_public)
session_key = hashlib.sha256(shared_secret).digest()  # 32 bytes
```

#### AES-256-GCM
```python
# IV=12B, Tag=16B, ciphertext 和 tag 分开存储!
# cryptography 库返回 ciphertext+tag 拼接，需手动拆分
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import os

iv = os.urandom(12)
ct_and_tag = AESGCM(session_key).encrypt(iv, plaintext, associated_data=None)
ciphertext = ct_and_tag[:-16]
tag = ct_and_tag[-16:]
```

#### ECDSA 签名
```python
# P-256 + SHA-256, DER 编码 (cryptography 库默认)
from cryptography.hazmat.primitives import hashes

signature = private_key.sign(data, ec.ECDSA(hashes.SHA256()))  # DER 编码
```

#### CryptoEnvelope JSON
```json
{
  "msg_id": "uuid-v4",
  "timestamp": 1718793600000,
  "nonce": "base64(16 bytes)",
  "type": "BUILD_TRIGGER",
  "iv": "base64(12 bytes)",
  "ciphertext": "base64(...)",
  "tag": "base64(16 bytes)",
  "signature": "base64(DER ECDSA)"
}
```

#### 签名载荷
```python
# 管道符拼接，timestamp 是数字的字符串表示
payload = f"{msg_id}|{timestamp}|{nonce}|{type}|{iv}|{ciphertext}|{tag}"
```

#### 解密验签顺序
```
1. 时间戳窗口检查 (±5 分钟)
2. nonce 防重放检查
3. ECDSA 签名验证
4. AES-256-GCM 解密 + tag 校验
5. 记录 nonce (仅全部通过后)
```

### 1.3 WebSocket 服务端（`forwarder/app/ws/`）

#### 握手流程

```
1. Agent 连接 WebSocket /agent-ws
2. Agent → Forwarder: HandshakeInit JSON (明文)
   {
     "type": "HANDSHAKE_INIT",
     "agent_id": "agent-001",
     "projects": ["proj-a", "proj-b"],
     "ecdh_pub_pem": "-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----\n",
     "ecdsa_pub_pem": "-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----\n",
     "signature": "base64(DER ECDSA(agent_id|ecdh_pub_pem|ecdsa_pub_pem))"
   }
3. Forwarder 验签 → 生成临时 ECDH 密钥对
4. Forwarder → Agent: HandshakeAck JSON (明文)
   {
     "type": "HANDSHAKE_ACK",
     "status": "OK",
     "ecdh_pub_pem": "...",
     "ecdsa_pub_pem": "...",
     "signature": "base64(DER ECDSA(ecdh_pub_pem|ecdsa_pub_pem))"
   }
5. 双方各自: session_key = SHA256(ECDH(my_priv, peer_pub))
6. 后续消息: CryptoEnvelope JSON (加密)
```

#### Agent 管理器

- `by_agent_id`: `dict[str, AgentSession]`
- `by_project`: `dict[str, str]` (project → agent_id)
- 线程安全（asyncio.Lock 或 threading.Lock）
- 支持按 agent_id 和按 project 查找
- Agent 断线时自动移除

### 1.4 跨语言互通测试

编写测试验证 Python 加密模块与 C++ Agent 的互通性：

1. **使用 C++ keygen 工具生成密钥对**：
   ```bash
   # 在 WSL 中
   cd /mnt/d/workspace/jd-relay/build
   ./bin/keygen /tmp/test_keys
   ```

2. **Python 读取 C++ 生成的 PEM 密钥**：验证能正确加载

3. **ECDH 互通**：Python 用 C++ 的 ECDH 公钥推导共享密钥，与 C++ 用 Python 的 ECDH 公钥推导的结果一致

4. **AES-GCM 互通**：
   - Python 加密 → C++ decryptor 解密
   - C++ encryptor 加密 → Python 解密

5. **ECDSA 互通**：
   - Python 签名 → C++ 验签
   - C++ 签名 → Python 验签

6. **CryptoEnvelope 格式一致**：Python 和 C++ 生成的 JSON 结构完全匹配

7. **端到端握手**：Python WebSocket 服务端 ↔ C++ WsClient 握手 + 消息交换

### 1.5 技术选型

```
Python 3.12+
FastAPI >= 0.110
uvicorn[standard]
SQLAlchemy >= 2.0
cryptography >= 42.0
websockets >= 12.0  (或 aiohttp)
pydantic-settings
httpx  (钉钉 API 调用)
Jinja2  (Web 面板, Phase 3)
pytest + pytest-asyncio  (测试)
```

---

## 任务 2：Phase 3 — 钉钉/Jenkins 对接

### 2.1 钉钉回调接口 (Forwarder/Python)

实现以下 FastAPI 路由：

| 路由 | 方法 | 用途 |
|------|------|------|
| `/dt/callback` | POST | 钉钉审批回调接收 |
| `/dt/card-callback` | POST | 钉钉卡片回调 |
| `/admin/work-orders` | GET | 工单列表 |
| `/admin/work-orders/{id}` | GET | 工单详情 |
| `/admin/agents` | GET | Agent 状态 |
| `/health` | GET | 健康检查 |

钉钉回调需验证签名（timestamp + sign），解析审批表单字段：
- ISSUE: 问题单号
- Project: 项目名（用于 Agent 路由）
- Branch: 分支名
- Build: 构建命令

### 2.2 Jenkins API 客户端 (Agent/C++)

在 `agent/` 下新增 `jenkins/` 模块：

```
agent/
├── jenkins/
│   ├── include/jd_relay/jenkins/
│   │   ├── jenkins_client.h     # Jenkins REST API 客户端
│   │   └── build_status.h       # 构建状态枚举
│   └── src/
│       └── jenkins_client.cpp
```

功能：
- `trigger_build(job_name, params)`: 触发 Jenkins 构建
- `get_build_status(job_name, build_number)`: 查询构建状态
- `get_build_log(job_name, build_number)`: 获取构建日志
- `check_special_md(repo_path, branch)`: 检测 special.md 变更

Jenkins 认证使用 API Token（用户名 + Token）。

### 2.3 状态机 (Forwarder/Python)

```
DRAFT → PENDING_APPROVAL → APPROVED → BUILDING
                                        ├─ 无敏感文件 → SUCCESS/FAILED → CLOSED
                                        └─ 敏感文件 → PENDING_SECOND_REVIEW
                                                       ├─ SECOND_APPROVED → BUILDING → SUCCESS/FAILED → CLOSED
                                                       └─ SECOND_REJECTED → ABORTED → CLOSED
```

### 2.4 MySQL 持久化 (Forwarder/Python)

SQLAlchemy 模型：

| 表名 | 说明 | 关键字段 |
|------|------|---------|
| work_orders | 工单 | id, issue, project, branch, build_cmd, status, agent_id, created_at |
| agents | Agent 注册 | agent_id, projects, last_seen, online |
| approvals | 审批记录 | id, work_order_id, approver, approved, round(1st/2nd), timestamp |
| build_results | 构建结果 | id, work_order_id, build_number, status, log_url, timestamp |
| crypto_audit | 加密校验失败 | id, msg_id, error, timestamp |

### 2.5 敏感文件二次审核

1. Agent 在构建过程中检测 `special.md` 的 git diff
2. 如果有变更，Agent 发送 `SENSITIVE_REVIEW_REQ` 给 Forwarder
3. Forwarder 发起钉钉二次审批
4. 审批结果通过 `SECOND_REVIEW_RESULT` 发回 Agent
5. Agent 根据结果恢复或终止构建

---

## 约束与规范

### 代码规范

- **Python**: 类型注解必填，docstring 必填，ruff 格式化
- **C++**: C++17 标准，snake_case 命名，头文件 `#pragma once`
- **JSON**: 字段名使用 snake_case
- **错误处理**: Python 用自定义异常 + FastAPI 异常处理器；C++ 用异常或返回码

### 加密兼容性（绝对红线）

**Python 加密模块必须与 C++ Agent 完全互通。** 以下每项必须验证：

1. ECDH 共享密钥 = `SHA256(raw_shared_secret)`，不是 HKDF
2. AES-256-GCM：IV=12B, Tag=16B，ciphertext 和 tag **分开存储**
3. `cryptography` 库返回 ciphertext+tag 拼接，需手动拆分/拼接
4. ECDSA 签名 DER 编码（`cryptography` 库默认）
5. 签名载荷 = `msg_id|timestamp|nonce|type|iv|ciphertext|tag`（管道符拼接）
6. timestamp 为 Unix **毫秒**整数
7. Base64 使用标准编码（带 `=` padding）
8. PEM 字符串包含完整头尾

### 测试要求

- 加密互通测试必须使用 C++ keygen 生成的密钥
- WebSocket 端到端测试必须连接真实的 C++ Agent
- 所有测试必须在 WSL Ubuntu 24.04 环境运行

### 旧代码复用

`legacy/python/server/app/` 下有旧 Python/FastAPI 代码，可参考复用：
- `services/dingtalk_service.py`: 钉钉 API 封装（~95% 可复用）
- `services/jenkins_service.py`: Jenkins API（移到 Agent 端，~10% 复用）
- `models.py`: 数据模型（需调整字段）
- `templates/`: Web 面板模板（~90% 可复用）
- `middleware/`: 认证和日志中间件（~95% 可复用）

---

## 参考文档

实现前请仔细阅读以下文档：

1. `docs/crypto-spec.md` — 加密协议精确规范（**最重要**）
2. `docs/architecture.md` — 架构设计文档
3. `docs/phase1-summary.md` — C++ 加密模块交付概览
4. `docs/phase2-summary.md` — C++ 传输层交付概览
5. `TODO.md` — 完整待办事项

---

## 开始指令

```
你是一个资深全栈工程师，精通 Python 和 C++。

请阅读项目文档（docs/crypto-spec.md, docs/architecture.md），然后：

1. 在 forwarder/ 目录下创建 Python Forwarder 项目骨架
2. 实现 forwarder/app/crypto/ 加密模块，确保与 C++ Agent 完全兼容
3. 实现 forwarder/app/ws/ WebSocket 服务端（握手 + 加密 I/O + Agent 管理）
4. 编写跨语言互通测试（使用 C++ keygen 生成的密钥）
5. 在 WSL 中运行测试验证：Python Forwarder ↔ C++ Agent 端到端握手 + 消息交换

完成后进入 Phase 3：钉钉回调接口 + 状态机 + MySQL 持久化 + Agent 端 Jenkins API。

关键约束：加密模块必须严格遵循 docs/crypto-spec.md 规范，与 C++ Agent 完全互通。
```
