# JD-Relay 安全设计

## 加密方案

### 会话密钥协商（ECDH P-256）

1. Agent 和 Forwarder 各自生成临时 ECDH P-256 密钥对
2. 交换公钥（DER SubjectPublicKeyInfo），通过 ECDSA 签名确保身份
3. 各自计算 ECDH 共享密钥
4. `AES-256 Key = SHA-256(raw_shared_secret)`

### 消息加密（AES-256-GCM）

- 算法：AES-256-GCM（Galois/Counter Mode）
- IV：12 字节随机数
- Tag：16 字节认证标签（与密文**分开存储**，不拼接）
- 每消息独立加密，使用独立 nonce

### 消息签名（ECDSA P-256）

- 算法：ECDSA P-256，SHA-256 哈希，DER 编码签名
- 签名覆盖：`msg_id|timestamp|nonce|type|iv|ciphertext|tag`
- 签名不包含自己（签名在外层）

### 防重放（ReplayGuard）

- 时间戳窗口：±5 分钟
- Nonce 缓存：每条消息 16 字节随机 nonce
- 缓存上限：10,000 条，超限自动清理过期条目

## 密钥管理

### 密钥类型

| 密钥 | 生命周期 | 存储位置 | 用途 |
|------|---------|---------|------|
| ECDSA 私钥 | 长期（年） | 文件系统（PEM） | 握手签名 + 消息签名 |
| ECDSA 公钥 | 长期（年） | 配置文件 | 验签 |
| ECDH 密钥对 | 临时（每连接） | 内存 | 会话密钥协商 |
| AES-256 会话密钥 | 临时（每连接） | 内存 | 消息加解密 |
| DingTalk AppSecret | 长期 | 环境变量 / 配置文件 | 钉钉 API 认证 |

### 安全存储

- **禁止**将私钥提交到版本控制
- 生产环境使用环境变量或密钥管理服务（KMS）
- 会话密钥仅存内存，进程退出自动销毁（`OPENSSL_cleanse`）
- 定期轮换 ECDSA 密钥对（建议 6 个月）

## 安全审计清单

- [ ] ECDSA 私钥文件权限检查（应为 600）
- [ ] 配置文件权限检查（应为 640）
- [ ] MySQL 连接使用 TLS
- [ ] Forwarder ↔ Agent WebSocket 使用 WSS（TLS）
- [ ] DingTalk 回调签名验证启用
- [ ] 时间戳防重放启用
- [ ] 日志不包含明文密钥
- [ ] 数据库密码使用环境变量

## 已知安全边界

1. **CryptoEnvelope 元数据明文**：msg_id、timestamp、nonce、type 在 JSON 中明文可见。攻击者可获知消息类型和时间。
2. **base64 格式泄露长度**：base64 编码不隐藏明文长度。
3. **时序侧信道**：未对加解密操作做常量时间处理（OpenSSL 软件实现）。
