# 加密协议规范 — C++ Agent ↔ Python Forwarder 互通

> 版本：1.0 ｜ 日期：2026-06-19
> 状态：**实现 Python Forwarder 时必须严格遵循本规范**
> C++ Agent 端已完成实现（Phase 1+2，43/43 测试全绿），Python 端必须与之完全兼容

---

## TL;DR

| 要素 | 规范 |
|------|------|
| 密钥协商 | ECDH P-256，公钥以 PEM 格式交换 |
| 会话密钥推导 | `SHA256(raw_ecdh_shared_secret)` → 32 字节 AES key（**不是 HKDF**） |
| 对称加密 | AES-256-GCM，IV=12B，Tag=16B，**ciphertext 和 tag 分开存储** |
| 身份签名 | ECDSA P-256 + SHA-256，签名 DER 编码 |
| 签名载荷 | `msg_id\|timestamp\|nonce\|type\|iv\|ciphertext\|tag`（管道符拼接） |
| 防重放 | 时间戳 ±5 分钟窗口 + nonce 缓存查重 |
| 传输格式 | JSON（CryptoEnvelope） |
| WebSocket 路径 | `/agent-ws` |

---

## 1. 密钥体系

### 1.1 持久密钥（ECDSA）

每方持有一对 **ECDSA P-256** 持久密钥对，用于握手签名和消息签名验证。

- 曲线：`secp256r1`（即 P-256 / prime256v1）
- 格式：PEM（SubjectPublicKeyInfo / PKCS#8 PrivateKeyInfo）
- 私钥文件：`ecdsa_private.pem`
- 公钥文件：`ecdsa_public.pem`

**Python 生成方式**（`cryptography` 库）：
```python
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization

private_key = ec.generate_private_key(ec.SECP256R1())
private_pem = private_key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption()
)
public_pem = private_key.public_key().public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo
)
```

**关键**：C++ 端用 `PEM_read_bio_PrivateKey` / `PEM_read_bio_PUBKEY` 加载，对应 Python 的 PKCS#8 / SubjectPublicKeyInfo 格式。

### 1.2 临时密钥（ECDH）

每次 WebSocket 连接建立时，双方各自生成一对 **临时 ECDH P-256** 密钥对，用于推导会话密钥。

- 曲线：`secp256r1`（P-256）
- 生命周期：单次 WebSocket 连接
- 公钥交换格式：PEM（SubjectPublicKeyInfo）

**Python 生成方式**：
```python
ecdh_private = ec.generate_private_key(ec.SECP256R1())
ecdh_pub_pem = ecdh_private.public_key().public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo
).decode('utf-8')
```

### 1.3 会话密钥推导

```
shared_secret = ECDH(my_ecdh_private, peer_ecdh_public)   # 原始共享密钥，32 字节
session_key   = SHA256(shared_secret)                      # 32 字节 AES-256 key
```

**关键**：C++ 端直接 `SHA256(secret.data(), secret.size(), aes_key.data())`，**不是** HKDF，**不是** X9.63 KDF，就是简单的 `SHA256(raw_shared_secret)`。

**Python 实现**：
```python
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hashes import SHA256

shared_secret = ecdh_private.exchange(ec.ECDH(), peer_ecdh_public_key)
# C++ 端做的是 SHA256(shared_secret)，不是 X9.63 KDF
import hashlib
session_key = hashlib.sha256(shared_secret).digest()  # 32 bytes
```

---

## 2. AES-256-GCM 加密

### 2.1 参数

| 参数 | 值 |
|------|------|
| 密钥长度 | 32 字节（256 位） |
| IV 长度 | 12 字节（96 位），每条消息随机生成 |
| Tag 长度 | 16 字节（128 位） |
| 模式 | GCM（无额外 padding） |

### 2.2 C++ 端实现（OpenSSL EVP）

C++ 端使用 OpenSSL `EVP_aes_256_gcm()`：
- `EVP_EncryptInit_ex` → 传入 key + IV
- `EVP_EncryptUpdate` → 加密明文
- `EVP_EncryptFinal_ex` → 完成
- `EVP_CTRL_GCM_GET_TAG` → 获取 16 字节 tag
- **ciphertext 和 tag 分别存储**，不拼接

### 2.3 Python 端实现（必须匹配）

**关键差异**：Python `cryptography` 库的 AESGCM 默认将 tag 追加在 ciphertext 末尾。C++ 端是分开的。Python 端必须手动拆分：

```python
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import os

def encrypt(session_key: bytes, plaintext: bytes) -> tuple[bytes, bytes, bytes]:
    """返回 (iv, ciphertext, tag) — 与 C++ 端格式一致"""
    iv = os.urandom(12)
    aesgcm = AESGCM(session_key)
    # cryptography 库返回 ciphertext + tag 拼接
    ct_and_tag = aesgcm.encrypt(iv, plaintext, associated_data=None)
    ciphertext = ct_and_tag[:-16]   # 前 N-16 字节是密文
    tag = ct_and_tag[-16:]           # 后 16 字节是 tag
    return iv, ciphertext, tag

def decrypt(session_key: bytes, iv: bytes, ciphertext: bytes, tag: bytes) -> bytes:
    """从分开的 ciphertext + tag 解密 — 与 C++ 端格式一致"""
    aesgcm = AESGCM(session_key)
    ct_and_tag = ciphertext + tag    # 拼接回 cryptography 库期望的格式
    return aesgcm.decrypt(iv, ct_and_tag, associated_data=None)
```

---

## 3. ECDSA 签名

### 3.1 参数

| 参数 | 值 |
|------|------|
| 曲线 | P-256 (secp256r1) |
| 哈希 | SHA-256 |
| 签名编码 | **DER**（ASN.1 SEQUENCE of two INTEGERs） |
| 签名长度 | ~70-72 字节（DER 编码，不定长） |

### 3.2 签名 / 验签

C++ 端使用 `EVP_DigestSign` / `EVP_DigestVerify`，默认输出 DER 编码签名。

**Python 实现**：
```python
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import utils

# 签名
signature = private_key.sign(data, ec.ECDSA(hashes.SHA256()))
# cryptography 库默认输出 DER 编码，与 C++ 兼容

# 验签
try:
    public_key.verify(signature, data, ec.ECDSA(hashes.SHA256()))
    valid = True
except Exception:
    valid = False
```

**注意**：签名是对原始 data 的 SHA-256 哈希后签名，不是对 data 直接签名。C++ 的 `EVP_DigestSign` 内部做了哈希，Python 的 `sign(data, ec.ECDSA(hashes.SHA256()))` 也做了哈希，两者一致。

---

## 4. CryptoEnvelope（加密信封）

### 4.1 JSON 结构

每条加密消息序列化为以下 JSON：

```json
{
  "msg_id": "550e8400-e29b-41d4-a716-446655440000",
  "timestamp": 1718793600000,
  "nonce": "vFQ8cZ4b7x2nKp1m",
  "type": "BUILD_TRIGGER",
  "iv": "AAAAAAAAAAAAAAAA",
  "ciphertext": "BBBBBBBBBBBBBBBB",
  "tag": "CCCCCCCCCCCCCCCC",
  "signature": "DDDDDDDDDDDDDDDD"
}
```

### 4.2 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `msg_id` | string | UUID v4，消息唯一标识 |
| `timestamp` | integer | Unix 时间戳（**毫秒**），不是秒 |
| `nonce` | string | Base64(16 字节随机数)，防重放 |
| `type` | string | 消息类型枚举（见下表） |
| `iv` | string | Base64(12 字节 AES-GCM IV) |
| `ciphertext` | string | Base64(AES-256-GCM 密文) |
| `tag` | string | Base64(16 字节 GCM tag) |
| `signature` | string | Base64(DER 编码 ECDSA 签名) |

### 4.3 消息类型枚举

| type 字符串 | 枚举值 | 方向 | 用途 |
|-------------|--------|------|------|
| `BUILD_TRIGGER` | 0 | Forwarder → Agent | 触发 Jenkins 构建 |
| `BUILD_RESULT` | 1 | Agent → Forwarder | 构建结果回传 |
| `SENSITIVE_REVIEW_REQ` | 2 | Agent → Forwarder | 敏感文件审核请求 |
| `SECOND_REVIEW_RESULT` | 3 | Forwarder → Agent | 二次审核结果 |
| `HEARTBEAT` | 4 | 双向 | 心跳保活 |
| `ACK` | 5 | 双向 | 消息确认 |

### 4.4 签名载荷（signing payload）

签名覆盖以下字段的管道符拼接：

```
msg_id|timestamp|nonce|type|iv|ciphertext|tag
```

**关键细节**：
- `timestamp` 是数字的**字符串表示**（如 `"1718793600000"`），不是浮点数
- 分隔符是 `|`（管道符，ASCII 0x7C）
- 没有前导/后缀
- Base64 值使用**标准 Base64**（带 `=` padding）

**C++ 实现**（`envelope.cpp`）：
```cpp
std::string build_signing_payload(const CryptoEnvelope& env) {
    return env.msg_id + "|"
         + std::to_string(env.timestamp) + "|"
         + env.nonce + "|"
         + env.type + "|"
         + env.iv + "|"
         + env.ciphertext + "|"
         + env.tag;
}
```

**Python 实现**：
```python
def build_signing_payload(env: dict) -> str:
    return f"{env['msg_id']}|{env['timestamp']}|{env['nonce']}|{env['type']}|{env['iv']}|{env['ciphertext']}|{env['tag']}"
```

### 4.5 加密 + 签名流程（发送方）

```
1. 生成 msg_id = uuid4()
2. 生成 timestamp = 当前 Unix 毫秒时间戳
3. 生成 nonce = base64(os.urandom(16))
4. AES-256-GCM 加密明文 → (iv, ciphertext, tag)
5. base64 编码 iv, ciphertext, tag
6. 构造签名载荷 = "msg_id|timestamp|nonce|type|iv|ciphertext|tag"
7. ECDSA 签名签名载荷 → signature (DER)
8. base64 编码 signature
9. 组装 JSON
```

### 4.6 解密 + 验签流程（接收方）

**校验顺序（严格按此顺序）**：

```
1. 时间戳窗口检查：|timestamp - now_ms| <= 300_000 (5 分钟)
   → 失败则拒绝，不记录 nonce
2. 防重放检查：nonce 是否在缓存中
   → 失败则拒绝
3. ECDSA 签名验证：验证 "msg_id|timestamp|nonce|type|iv|ciphertext|tag" 的签名
   → 失败则拒绝
4. AES-256-GCM 解密 + tag 校验
   → 失败则拒绝
5. 记录 nonce 到缓存（防止重放）
```

**关键**：nonce 只在所有校验通过后才记录。如果签名验证失败，nonce 不被记录（允许合法消息使用相同 nonce 重试）。

---

## 5. WebSocket 握手协议

### 5.1 流程

```
Agent                          Forwarder
  │                                │
  │──── TCP connect ──────────────→│
  │──── WS upgrade (/agent-ws) ───→│
  │                                │
  │──── HandshakeInit (JSON) ─────→│  (明文，含 Agent ECDH+ECDSA 公钥)
  │                                │
  │←─── HandshakeAck (JSON) ───────│  (明文，含 Forwarder ECDH+ECDSA 公钥)
  │                                │
  │    双方各自推导 session_key      │
  │    = SHA256(ECDH(my_priv, peer_pub))
  │                                │
  │════ 加密消息交换 (CryptoEnvelope) ════│
```

### 5.2 HandshakeInit（Agent → Forwarder）

```json
{
  "type": "HANDSHAKE_INIT",
  "agent_id": "agent-001",
  "projects": ["proj-a", "proj-b"],
  "ecdh_pub_pem": "-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----\n",
  "ecdsa_pub_pem": "-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----\n",
  "signature": "base64(DER ECDSA signature)"
}
```

**签名载荷**：
```
agent_id|ecdh_pub_pem|ecdsa_pub_pem
```

签名用 Agent 的 ECDSA 持久私钥，对上述字符串的 SHA-256 哈希签名（DER 输出）。

**关键**：`ecdh_pub_pem` 和 `ecdsa_pub_pem` 是完整的 PEM 字符串（含 `-----BEGIN/END-----` 头尾和换行符）。

### 5.3 HandshakeAck（Forwarder → Agent）

```json
{
  "type": "HANDSHAKE_ACK",
  "status": "OK",
  "ecdh_pub_pem": "-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----\n",
  "ecdsa_pub_pem": "-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----\n",
  "signature": "base64(DER ECDSA signature)"
}
```

如果握手失败：
```json
{
  "type": "HANDSHAKE_ACK",
  "status": "ERROR",
  "error": "Signature verification failed",
  "ecdh_pub_pem": "",
  "ecdsa_pub_pem": "",
  "signature": ""
}
```

**签名载荷**：
```
ecdh_pub_pem|ecdsa_pub_pem
```

签名用 Forwarder 的 ECDSA 持久私钥。

### 5.4 握手后密钥组装

握手完成后，双方各自组装 `CryptoCodec`：

```
session_key = SHA256(ECDH(my_ecdh_private, peer_ecdh_public))

signer   = my ECDSA private key     (用于签名发出的消息)
verifier = peer ECDSA public key    (用于验证收到的消息)
cipher   = AES-256-GCM(session_key)
guard    = ReplayGuard(window=300s)
```

---

## 6. Base64 编码

所有二进制数据（iv、ciphertext、tag、nonce、signature）使用**标准 Base64** 编码：
- 字符集：`A-Z a-z 0-9 + /`
- padding：`=`（1 或 2 个）
- 换行：无（单行）

**Python**：
```python
import base64
encoded = base64.b64encode(data).decode('utf-8')
decoded = base64.b64decode(encoded)
```

**C++**（OpenSSL BIO）：
```cpp
// 使用 EVP_EncodeBlock / EVP_DecodeBlock 或 BIO_new(BIO_f_base64())
```

---

## 7. 防重放机制

### 7.1 时间戳窗口

- 单位：Unix **毫秒**时间戳
- 窗口：±300 秒（±300,000 毫秒）
- 检查：`abs(timestamp - now_ms) <= 300_000`

### 7.2 Nonce 缓存

- nonce 格式：Base64(16 字节随机数)
- 存储：内存中的 `dict<nonce, expiry_timestamp>`
- 过期时间：`now_ms + 300_000`（5 分钟后过期）
- 清理：当缓存超过 10,000 条时自动清理过期项
- 检查：收到消息时检查 nonce 是否已存在于缓存

---

## 8. 消息体格式（明文 JSON）

### 8.1 BUILD_TRIGGER（Forwarder → Agent）

```json
{
  "work_order_id": "WO-2024-001",
  "issue": "ISS-001",
  "project": "proj-a",
  "branch": "main",
  "build_cmd": "make all"
}
```

### 8.2 BUILD_RESULT（Agent → Forwarder）

```json
{
  "work_order_id": "WO-2024-001",
  "build_number": 42,
  "status": "SUCCESS",
  "log_url": "http://jenkins.local/job/42/log"
}
```

`status` 枚举：`SUCCESS` / `FAILED` / `ABORTED`

### 8.3 SENSITIVE_REVIEW_REQ（Agent → Forwarder）

```json
{
  "work_order_id": "WO-2024-001",
  "file_path": "config/special.md",
  "diff": "@@ -1,3 +1,5 @@\n+new sensitive line"
}
```

### 8.4 SECOND_REVIEW_RESULT（Forwarder → Agent）

```json
{
  "work_order_id": "WO-2024-001",
  "approved": true,
  "reviewer": "张三"
}
```

---

## 9. Python 兼容性检查清单

实现 Python Forwarder 时，以下每项必须验证与 C++ Agent 的互通：

- [ ] ECDH P-256 公钥以 PEM (SubjectPublicKeyInfo) 格式交换
- [ ] 会话密钥 = `SHA256(raw_shared_secret)`，不是 HKDF / X9.63
- [ ] AES-256-GCM：IV=12B, Tag=16B, ciphertext 和 tag **分开存储**
- [ ] `cryptography` 库的 AESGCM 返回 ciphertext+tag 拼接，需手动拆分
- [ ] ECDSA 签名为 DER 编码（`cryptography` 库默认）
- [ ] 签名载荷 = `msg_id|timestamp|nonce|type|iv|ciphertext|tag`
- [ ] timestamp 为 Unix **毫秒**整数
- [ ] 所有二进制字段使用标准 Base64（带 padding）
- [ ] HandshakeInit 签名载荷 = `agent_id|ecdh_pub_pem|ecdsa_pub_pem`
- [ ] HandshakeAck 签名载荷 = `ecdh_pub_pem|ecdsa_pub_pem`
- [ ] PEM 字符串包含完整头尾（`-----BEGIN PUBLIC KEY-----` ... `-----END PUBLIC KEY-----\n`）
- [ ] 防重放：先检查时间戳窗口 → 再检查 nonce → 再验签 → 再解密
- [ ] nonce 只在校验全通过后记录
- [ ] WebSocket 路径为 `/agent-ws`
- [ ] 握手消息是明文 JSON，握手后所有消息是 CryptoEnvelope JSON
