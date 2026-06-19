# jd-relay 项目长期记忆

## 项目概述
Jenkins-DingTalk 中继系统，**混合架构**：Python Forwarder（DMZ）+ C++ Agent（内网），ECDH+AES-256-GCM+ECDSA 加密链路。

## 架构决策（2026-06-19 变更）
- **Forwarder 改用 Python/FastAPI**（从 C++ 切换），复用 legacy/python/ 约 60% 代码
- **Agent 保持 C++17/20 不变**（Phase 1 加密 + Phase 2 传输层已完成）
- 核心挑战：Python 加密模块必须与 C++ Agent 端完全兼容（ECDH/AES-GCM/ECDSA/CryptoEnvelope/握手协议）

## 技术栈
### Agent (C++)
- C++17/20 + CMake + Ninja（WSL Ubuntu 24.04 构建）
- Boost.Beast + Boost.Asio（WebSocket 客户端）
- OpenSSL（ECDH P-256, AES-256-GCM, ECDSA P-256, SHA-256）
- nlohmann/json, GoogleTest
- SQLite（离线缓冲，规划中）

### Forwarder (Python)
- Python 3.12+ / FastAPI / uvicorn
- SQLAlchemy + MySQL（持久化）
- `cryptography` 库（ECDH/AES-GCM/ECDSA，与 C++ 互通）
- `websockets` 或 `aiohttp`（WebSocket 服务端，与 Agent 通信）
- Jinja2（Web 管理面板，复用 legacy 模板）

## 分阶段交付
- Phase 1：C++ 加密模块 — 完成（39/39 测试，3 个 CLI 工具）
- Phase 2：C++ 传输层 — 完成（4/4 测试，ECDH 握手+加密I/O+路由+重连+多Agent）
- Phase 2.5：Python Forwarder 骨架 + 跨语言加密互通 — 待开始（提示词已导出到 docs/PROMPT_FOR_AI.md）
- Phase 3：钉钉/Jenkins 对接 — 待开始
- Phase 4：端到端联调 — 待开始

## 文档体系（2026-06-19 整理）
- docs/ARCHITECTURE.md — 混合架构设计文档（拓扑、四条链路、状态机、部署）
- docs/CRYPTO_SPEC.md — 加密协议精确规范（C++↔Python 互通圣经，含 Python 代码示例）
- docs/PHASE1_SUMMARY.md / PHASE2_SUMMARY.md — 交付概览
- docs/PROMPT_FOR_AI.md — 后续实现提示词（自包含，可直接交给另一个 AI）
- docs/UIUX.md — UI/UX 设计参考
- 12 个旧文档已归档到 legacy/docs/

## 目录结构（2026-06-19 最终版）
- agent/ — C++ Agent（crypto/protocol/ws_client/tools）
- forwarder/ — Python Forwarder（待创建）
- tests/ — C++ 测试（unit/integration）
- legacy/ — 归档代码（python/cpp_forwarder/docs）
- docs/ — 活跃文档

## 关键设计决策
- ICipher/ISigner 可替换接口（未来可换 SM4/SM2）
- CryptoEnvelope 加密信封（msg_id/timestamp/nonce/type/iv/ciphertext/tag/signature）
- WebSocket 握手协议（HandshakeInit/HandshakeAck，明文 JSON 交换 ECDH+ECDSA 公钥）
- 1:N Agent 路由（project 字段映射）
- Agent 主动出站连接，内网不开入站端口

## 重要经验教训
- Linux 阻塞 `accept()` 不能被跨线程 `close()` 可靠唤醒 → 改用非阻塞 accept + 轮询
- Boost.Beast `websocket::stream` 的 socket 关闭需 `shutdown(both) + close()` 才能可靠唤醒阻塞 `read()`
- AgentManager `stop_all()` 必须先拷贝 session 列表、解锁，再 stop/join，避免与 io_loop 的 `remove_session` 死锁
