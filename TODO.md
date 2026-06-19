# TODO — JD-Relay 待办事项

> 最后更新：2026-06-19
> 架构：Python Forwarder + C++ Agent（混合架构）
> 当前状态：Phase 1 ✅ Phase 2 ✅ | C++ 测试 43/43 全绿

---

## Phase 2.5：Python Forwarder 骨架 + 跨语言互通

### 2.5.1 Python 加密模块（与 C++ 互通）
- [ ] 实现 ECDH P-256 密钥协商（`SHA256(raw_shared_secret)` 派生会话密钥）
- [ ] 实现 AES-256-GCM 加解密（IV=12B, Tag=16B, **ciphertext 和 tag 分开存储**）
- [ ] 实现 ECDSA P-256 签名/验签（DER 编码，SHA-256 哈希）
- [ ] 实现 CryptoEnvelope JSON 序列化（字段名/格式与 C++ 完全一致）
- [ ] 实现 ReplayGuard（±5 分钟时间戳窗口 + nonce 缓存查重）
- [ ] 实现 CryptoCodec 顶层编解码器（encrypt → 签名 / 验签 → decrypt）

### 2.5.2 WebSocket 服务端
- [ ] 实现 `/agent-ws` WebSocket 服务端（`websockets` 或 `aiohttp`）
- [ ] 实现 HandshakeInit 接收 + 验签
- [ ] 实现 HandshakeAck 生成 + 签名
- [ ] 实现 Agent 连接管理器（by_agent_id + by_project 双索引）
- [ ] 实现加密消息收发（CryptoEnvelope 解密/加密）

### 2.5.3 跨语言互通测试
- [ ] Python ECDH ↔ C++ ECDH 共享密钥一致性验证
- [ ] Python AES-GCM 加密 → C++ decryptor 解密
- [ ] C++ encryptor 加密 → Python AES-GCM 解密
- [ ] Python ECDSA 签名 → C++ 验签
- [ ] C++ ECDSA 签名 → Python 验签
- [ ] CryptoEnvelope JSON 格式一致性
- [ ] 握手协议 JSON 格式一致性
- [ ] Python Forwarder ↔ C++ Agent 端到端握手 + 消息交换

---

## Phase 3：钉钉/Jenkins 对接

### 3.1 钉钉回调 HTTPS 接口 (Forwarder/Python)
- [ ] 钉钉审批回调接收与解析（ISSUE/Project/Branch/Build 字段提取）
- [ ] 钉钉签名验证（timestamp + sign 校验）
- [ ] 钉钉卡片回调（审批状态更新推送）
- [ ] 钉钉审批 API 调用（发起审批、查询审批状态）
- [ ] 钉钉消息通知（构建结果推送到群/个人）
- [ ] HTTPS 服务器配置（uvicorn + Nginx 反向代理）

### 3.2 Jenkins API 客户端 (Agent/C++)
- [ ] Jenkins REST API 客户端封装（触发构建、查询状态、获取日志）
- [ ] Jenkins API Token 认证
- [ ] 构建参数传递（ISSUE/Project/Branch/Build 参数）
- [ ] 构建状态轮询
- [ ] 构建日志获取与截断
- [ ] special.md 变更检测逻辑

### 3.3 状态机 (Forwarder/Python)
- [ ] 定义工单状态枚举（DRAFT → PENDING_APPROVAL → APPROVED → BUILDING → SUCCESS/FAILED → CLOSED）
- [ ] 二次审核状态（PENDING_SECOND_REVIEW → SECOND_APPROVED/SECOND_REJECTED）
- [ ] 状态转换合法性校验
- [ ] 状态持久化到 MySQL

### 3.4 MySQL 持久化 (Forwarder/Python)
- [ ] SQLAlchemy 模型定义
  - [ ] work_orders 表（工单）
  - [ ] agents 表（Agent 注册信息）
  - [ ] approvals 表（审批记录）
  - [ ] build_results 表（构建结果）
  - [ ] crypto_audit 表（加密校验失败记录）
- [ ] 数据库迁移脚本（Alembic）
- [ ] CRUD 操作封装

### 3.5 敏感文件二次审核
- [ ] Agent 检测 special.md 变更（git diff）
- [ ] Agent 发送 SENSITIVE_REVIEW_REQ 给 Forwarder
- [ ] Forwarder 发起钉钉二次审批
- [ ] 二次审批结果通过 SECOND_REVIEW_RESULT 发回 Agent
- [ ] 审批超时自动拒绝

---

## Phase 4：端到端联调

### 4.1 流程 A：钉钉 → Jenkins
- [ ] 钉钉填单 → Forwarder 接收 → 三人审批 → 转发 BUILD_TRIGGER → Agent → Jenkins 触发
- [ ] 审批超时自动拒绝
- [ ] 重复提交防重

### 4.2 流程 B：Jenkins → 钉钉
- [ ] Jenkins 构建完成 → Agent 回传 BUILD_RESULT → Forwarder → 钉钉通知
- [ ] 构建日志链接推送
- [ ] special.md 变更 → 二次审核全链路

### 4.3 配置文件
- [ ] Forwarder 配置（forwarder.conf：端口、密钥路径、MySQL 连接、钉钉 AppKey/Secret）
- [ ] Agent 配置（agent.conf：Forwarder 地址、密钥路径、Jenkins URL/Token、projects 列表）
- [ ] 配置文件热加载（可选）

### 4.4 部署与运维
- [ ] Forwarder systemd service 文件
- [ ] Agent systemd service 文件
- [ ] Forwarder Dockerfile（Python）
- [ ] Agent Dockerfile（C++）
- [ ] docker-compose.yml（开发环境）
- [ ] Nginx 反向代理配置（HTTPS 终止 + WebSocket 代理）
- [ ] 日志轮转配置

### 4.5 Web 管理面板
- [ ] 工单列表页（状态、筛选、搜索）
- [ ] 工单详情页（审批记录、构建结果、日志链接）
- [ ] Agent 状态页（在线状态、连接时间、负责项目）
- [ ] 审批统计面板

### 4.6 文档
- [ ] 更新 README.md（安装、配置、部署完整指南）
- [ ] SECURITY.md（加密方案、密钥管理、安全审计清单）
- [ ] OPERATIONS.md（部署、监控、故障排查）

---

## 技术债务

- [ ] Agent 离线缓冲 SQLite 模块（断线重连后重发未确认消息）
- [ ] Forwarder 心跳机制（定期 HEARTBEAT 检测 Agent 存活）
- [ ] 消息 ACK 确认机制（BUILD_TRIGGER 发送后等待 Agent ACK）
- [ ] WebSocket 连接池上限与拒绝策略
- [ ] Agent 日志系统（当前使用 std::cerr，需替换为 spdlog 或类似）
- [ ] Agent 配置文件解析（当前硬编码，需读取 agent.conf）
- [ ] CI/CD 流水线更新（当前 .github/workflows 仍为旧 Python 版本）

---

## 未来增强

- [ ] 国密算法支持（SM2/SM3/SM4，通过 ICipher/ISigner 接口扩展）
- [ ] 多 Forwarder 高可用（主备切换）
- [ ] 消息队列缓冲（RabbitMQ/Redis，应对高峰）
- [ ] Prometheus 指标导出
- [ ] 构建 Artifact 管理与分发
- [ ] 钉钉机器人命令行交互（快速查询工单状态）
