# TODO — JD-Relay 待办事项

> 最后更新：2026-06-22
> 架构：Python Forwarder + C++ Agent（混合架构）
> 当前状态：Phase 1 ✅ Phase 2 ✅ Phase 2.5 ✅ Phase 3 ✅ Phase 4 🔄 | C++ 43/43 ✅ | Python 65/65 ✅ | Web 面板 ✅

---

## Phase 2.5：Python Forwarder 骨架 + 跨语言互通 ✅

### 2.5.1 Python 加密模块（与 C++ 互通）✅
- [x] ECDH P-256 密钥协商
- [x] AES-256-GCM 加解密（IV=12B, Tag=16B, ciphertext/tag 分开）
- [x] ECDSA P-256 签名/验签（DER 编码，SHA-256）
- [x] CryptoEnvelope JSON 序列化（base64 \n 格式匹配 C++ OpenSSL BIO）
- [x] ReplayGuard（±5 分钟 + nonce 缓存）
- [x] CryptoCodec 顶层编解码器

### 2.5.2 WebSocket 服务端 ✅
- [x] `/agent-ws` WebSocket 服务端（FastAPI + uvicorn）
- [x] HandshakeInit 接收 + 验签
- [x] HandshakeAck 生成 + 签名
- [x] Agent 连接管理器（by_agent_id + by_project 双索引）
- [x] 加密消息收发

### 2.5.3 跨语言互通测试 ✅
- [x] Python ECDH ↔ C++ ECDH
- [x] Python AES-GCM ↔ C++ decryptor/encryptor
- [x] Python ECDSA ↔ C++ 签名/验签
- [x] CryptoEnvelope JSON 格式一致性
- [x] 篡改检测（两方向）
- [x] 全部 6 种 MessageType
- [x] Python Forwarder ↔ C++ Agent 端到端握手（Boost.Beast↔websockets 已验证）

---

## Phase 3：钉钉/Jenkins 对接 ✅

### 3.1 钉钉回调 HTTPS 接口 ✅
- [x] DingTalkService（access_token / 审批创建 / 查询 / 工作通知 / 签名验证）
- [x] 钉钉回调 API 路由（/api/dingtalk/callback / card-callback / create-approval）

### 3.2 Jenkins API 客户端 ✅
- [x] C++ JenkinsClient（触发构建 / 状态轮询 / 日志获取 / special.md 检测）
- [x] CMakeLists.txt 集成（需 libcurl，WSL 网络恢复后编译验证）

### 3.3 状态机 ✅
- [x] 10 状态 / 16 合法转换（DRAFT → ... → CLOSED + 二次审核）
- [x] 转换合法性校验（StateError）
- [x] 终态检测

### 3.4 MySQL 持久化 ✅
- [x] SQLAlchemy 模型（4 tables: work_orders / agents / approvals / build_results）
- [x] Alembic 迁移（001_initial_schema）
- [x] 异步数据库引擎

### 3.5 敏感文件二次审核 ✅
- [x] RelayService.on_sensitive_change_detected()
- [x] RelayService.on_second_review_result()
- [x] 二次审核钉钉审批流程

---

## Phase 4：端到端联调 🔄

### 4.1 流程 A：钉钉 → Jenkins 🔄
- [x] RelayService.submit_for_approval() + trigger_build()
- [ ] 端到端全链路实际运行验证

### 4.2 流程 B：Jenkins → 钉钉 🔄
- [x] RelayService.on_build_result() + send_notification()
- [ ] 端到端实际运行验证

### 4.3 配置文件 ✅
- [x] forwarder.conf.example（已有）
- [x] agent.conf.example（已有）
- [x] pyproject.toml + alembic.ini + docker-compose.yml

### 4.4 部署与运维 ✅
- [x] Forwarder systemd service
- [x] Agent systemd service
- [x] Python Dockerfile
- [x] docker-compose.yml（开发环境）
- [x] Nginx 反向代理配置（已有 deploy/nginx/）

### 4.5 Web 管理面板 ✅
- [x] Admin API 路由（/api/admin/orders / agents / stats）

### 4.6 文档 ✅
- [x] README.md（更新）
- [x] security.md（加密方案 + 密钥管理 + 审计清单）
- [ ] OPERATIONS.md（部署运维手册）

---

## 技术债务

- [ ] Agent 离线缓冲 SQLite 模块
- [x] Agent 主程序（agent_main.cpp，含配置解析 + BUILD_RESULT 回传 + special.md 检测）
- [ ] Agent 日志系统（spdlog 替换 std::cerr）
- [ ] 消息 ACK 确认机制
- [ ] Forwarder 心跳机制
- [ ] WebSocket 连接池上限与拒绝策略
- [ ] CI/CD 流水线更新（当前仍为旧 Python 版本）

## 未来增强

- [ ] 国密算法支持（SM2/SM3/SM4）
- [ ] 多 Forwarder 高可用
- [ ] 消息队列缓冲（RabbitMQ/Redis）
- [ ] Prometheus 指标导出
- [ ] 构建 Artifact 管理
- [ ] 钉钉机器人命令行交互
