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
| Phase 2.5 | Python Forwarder 骨架 + 跨语言互通 | ⬜ 待开始 |
| Phase 3 | 钉钉/Jenkins 对接 + 状态机 + MySQL | ⬜ 待开始 |
| Phase 4 | 端到端联调 + 部署 + Web 面板 | ⬜ 待开始 |

## 目录结构

```
jd-relay/
├── agent/                      # C++ Agent（Phase 1+2 已完成）
│   ├── crypto/                 #   加密模块 (OpenSSL)
│   ├── protocol/               #   握手协议
│   ├── ws_client/              #   WebSocket 客户端
│   └── tools/                  #   CLI 工具 (keygen/encryptor/decryptor)
├── forwarder/                  # Python Forwarder（待实现）
├── tests/                      # C++ 测试 (unit + integration)
├── docs/                       # 文档
│   ├── ARCHITECTURE.md         #   架构设计
│   ├── CRYPTO_SPEC.md          #   加密协议规范
│   ├── PHASE1_SUMMARY.md       #   Phase 1 交付概览
│   ├── PHASE2_SUMMARY.md       #   Phase 2 交付概览
│   ├── PROMPT_FOR_AI.md        #   后续实现提示词（交给 AI）
│   └── UIUX.md                 #   UI/UX 设计参考
├── config/                     # 配置样例
├── legacy/                     # 归档代码
│   ├── python/                 #   旧 Python/FastAPI 代码
│   ├── cpp_forwarder/          #   旧 C++ Forwarder
│   └── docs/                   #   旧文档
├── CMakeLists.txt              # 顶层 CMake（只构建 Agent）
├── Makefile                    # 构建辅助
└── TODO.md                     # 待办事项
```

## 构建 C++ Agent

```bash
# 在 WSL Ubuntu 24.04 中
cd /mnt/d/workspace/jd-relay
cmake -B build -G Ninja -DCMAKE_BUILD_TYPE=Debug
cmake --build build

# 运行测试
cd build && ctest --output-on-failure   # 43/43 passed

# 生成密钥
./build/bin/keygen /tmp/keys

# 加密/解密工具
echo '{"hello":"world"}' | ./build/bin/encryptor --ecdsa-key /tmp/keys/ecdsa_private.pem \
    --peer-pub /tmp/keys/ecdsa_public.pem --aes-key <hex> --type HEARTBEAT
```

## 关键文档

- [加密协议规范](docs/CRYPTO_SPEC.md) — C++ ↔ Python 互通的精确规范
- [架构设计](docs/ARCHITECTURE.md) — 系统拓扑、数据流、部署架构
- [后续实现提示词](docs/PROMPT_FOR_AI.md) — 交给 AI 的完整实现指引
- [待办事项](TODO.md) — Phase 2.5 / 3 / 4 详细任务

## 加密链路

```
ECDH P-256 密钥协商 → SHA256(shared_secret) → AES-256-GCM 会话密钥
                                          ↓
消息加密: AES-256-GCM (IV=12B, Tag=16B, 分开存储)
签名: ECDSA P-256 + SHA-256 (DER 编码)
防重放: ±5 分钟时间戳窗口 + nonce 缓存查重
信封: CryptoEnvelope JSON (msg_id/timestamp/nonce/type/iv/ciphertext/tag/signature)
```
