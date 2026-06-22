# Phase 2 交付概览：通信骨架

## 交付物清单

### 1. WebSocket 客户端 `agent/ws_client/`（C++）

| 组件 | 文件 | 职责 |
|------|------|------|
| WsClient | `ws_client.h/cpp` | WebSocket 客户端，ECDH 握手 + 加密 I/O + 指数退避自动重连 |

### 2. C++ Forwarder（已归档到 `legacy/cpp_forwarder/`）

Phase 2 期间用 C++ 实现了 WsServer + AgentSession + AgentManager，用于验证传输层可行性。架构变更为混合架构后归档，相关逻辑由 Python Forwarder 的 WebSocket 服务端替代。

### 3. 集成测试 `tests/integration/`（4 项全绿）

| 测试 | 耗时 | 覆盖内容 |
|------|------|----------|
| HandshakeAndMessageExchange | 254ms | ECDH 握手 + 双向加密消息交换 |
| ProjectRouting | 252ms | 1:N 项目路由 |
| AutoReconnectAfterServerRestart | 1256ms | 服务端重启后自动重连 |
| MultipleAgents | 202ms | 多 Agent 并发连接 |

## 关键实现细节

### 握手流程

```
1. Agent TCP connect → Forwarder
2. Agent WS upgrade (/agent-ws)
3. Agent 生成临时 ECDH P-256 密钥对
4. Agent 签名 "agent_id|ecdh_pub_pem|ecdsa_pub_pem" → HandshakeInit JSON
5. Forwarder 验签 → 生成临时 ECDH 密钥对
6. Forwarder 签名 "ecdh_pub_pem|ecdsa_pub_pem" → HandshakeAck JSON
7. 双方各自: session_key = SHA256(ECDH(my_priv, peer_pub))
8. 后续消息: CryptoEnvelope JSON (AES-256-GCM 加密 + ECDSA 签名)
```

### 自动重连

- 初始退避: 1 秒
- 指数退避: 1 → 2 → 4 → 8 → 16 → 30（上限 30 秒）
- 成功连接后重置退避
- 可中断的 backoff sleep（condition_variable + running_ 标志）

### 线程模型

```
WsClient:
  io_thread_ → run() → connect_and_handshake() → io_loop() (阻塞 read)
  主线程     → send() (mutex 保护 write)
```

## 关键 Bug 修复

| 问题 | 根因 | 修复 |
|------|------|------|
| 测试卡死 | Linux 阻塞 `accept()` 不能被跨线程 `close()` 可靠唤醒 | 改用非阻塞 accept + 50ms 轮询 |
| socket read 不退出 | `close()` 不能可靠唤醒阻塞的 `ws_.read()` | 改为 `shutdown(both) + close()` |
| 析构竞态 | WsServer 析构后 io_thread 仍访问 AgentManager | AgentManager 添加 `stop_all()` + `stopped_` 原子标志 |

## 编译与测试

```bash
# 在 WSL Ubuntu 24.04 中
cd /mnt/d/workspace/jd-relay
cmake -B build -G Ninja -DCMAKE_BUILD_TYPE=Debug
cmake --build build
cd build && ctest --output-on-failure   # 40/40 passed (39 unit + 1 integration suite)
```

## 下一阶段

**Phase 2.5**：Python Forwarder 骨架 + 跨语言加密互通测试
**Phase 3**：钉钉 HTTPS 回调 + Jenkins API + 状态机 + MySQL 持久化
