# Phase 1 交付概览：加密模块

## 交付物清单

### 1. 加密库 `crypto/`（可独立编译、独立测试）

| 组件 | 文件 | 职责 |
|------|------|------|
| ICipher 接口 | `include/jd_relay/crypto/icipher.h` | 抽象加密接口，当前 AES-256-GCM，未来可换 SM4-GCM |
| ISigner 接口 | `include/jd_relay/crypto/isigner.h` | 抽象签名接口，当前 ECDSA P-256，未来可换 SM2 |
| AesGcmCipher | `aes_gcm_cipher.h/cpp` | AES-256-GCM 加解密实现（OpenSSL EVP） |
| EcdsaSigner | `ecdsa_signer.h/cpp` | ECDSA P-256 签名/验签实现 |
| EcdhKeyExchange | `ecdh_key_exchange.h/cpp` | ECDH P-256 密钥协商，WebSocket 握手时推导会话密钥 |
| CryptoEnvelope | `envelope.h/cpp` | 加密信封数据结构 + JSON 序列化 |
| ReplayGuard | `replay_guard.h/cpp` | 防重放：±5分钟时间戳窗口 + nonce 去重 |
| KeyManager | `key_manager.h/cpp` | 密钥管理：加载 PEM 文件 + AES hex key + 密钥生成 |
| CryptoCodec | `crypto_codec.h/cpp` | 顶层编解码器：组装 cipher+signer+guard → encrypt/decrypt |
| base64 | `base64.h/cpp` | Base64 编解码 + Hex 编解码 + CSPRNG 随机数 + UUID 生成 |

### 2. 独立 CLI 工具 `tools/`

| 工具 | 用法 | 说明 |
|------|------|------|
| `keygen` | `keygen <output_dir>` | 生成 ECDSA + ECDH 密钥对 + AES 会话密钥 |
| `encryptor` | `encryptor --ecdsa-key <priv.pem> --peer-pub <pub.pem> --aes-key <hex> [--type <TYPE>]` | stdin 读明文 → stdout 输出加密信封 JSON |
| `decryptor` | `decryptor --ecdsa-key <priv.pem> --peer-pub <pub.pem> --aes-key <hex>` | stdin 读信封 → stdout 输出明文，失败返回 exit 1 |

### 3. 单元测试 `crypto/tests/`（39 项全绿）

| 测试套件 | 测试数 | 覆盖内容 |
|---------|--------|---------|
| AesGcmTest | 9 | 加解密往返、空明文、大 payload、错误密钥拒绝、篡改 ciphertext/tag/IV 拒绝、hex key 构造、非法密钥长度抛异常 |
| EcdsaTest | 7 | 签名验证、仅公钥验签、错误签名拒绝、错误数据拒绝、不同密钥不同签名、空数据签名、DER 导出 |
| EcdhTest | 4 | 双方推导相同密钥、不同对产生不同密钥、PEM 密钥交换、公钥导出非空 |
| EnvelopeRoundTripTest | 5 | 基本往返、JSON 序列化反序列化、所有消息类型、空明文、大 payload |
| ReplayGuardTest | 8 | 新鲜消息接受、过期拒绝、未来拒绝、重复 nonce 拒绝、不同 nonce 接受、窗口判断、过期清理、边界时间戳 |
| TamperDetectionTest | 6 | 篡改 ciphertext 拒绝、篡改 tag 拒绝、篡改 signature 拒绝、过期时间戳拒绝、重放 nonce 拒绝、错误签名密钥拒绝 |

### 4. 加密信封格式

```json
{
  "msg_id": "uuid-v4",
  "timestamp": 1699999999999,
  "nonce": "base64-16bytes",
  "type": "BUILD_TRIGGER|BUILD_RESULT|SENSITIVE_REVIEW_REQ|SECOND_REVIEW_RESULT|HEARTBEAT|ACK",
  "iv": "base64-12bytes",
  "ciphertext": "base64(AES-256-GCM(plaintext))",
  "tag": "base64-16bytes",
  "signature": "base64(ECDSA(sha256(msg_id|timestamp|nonce|type|iv|ciphertext|tag)))"
}
```

### 5. 接收方校验顺序

1. **时间戳窗口**：±5 分钟内，否则拒绝
2. **防重放**：nonce 未在窗口缓存中出现过，否则拒绝
3. **ECDSA 签名验证**：签名覆盖 msg_id|timestamp|nonce|type|iv|ciphertext|tag
4. **AES-GCM 解密 + tag 校验**：GCM tag 自带完整性认证，无需额外 HMAC

任一失败：拒绝处理并写入 `crypto_audit`（Phase 3 实现）。

## 编译与测试

```bash
# 在 WSL Ubuntu 24.04 中
cd /mnt/d/workspace/jd-relay
cmake -B build -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build build
./build/bin/crypto_tests          # 39/39 passed
./build/bin/keygen /tmp/keys      # 生成密钥
echo '{"hello":"world"}' | ./build/bin/encryptor --ecdsa-key /tmp/keys/ecdsa_private.pem --peer-pub /tmp/keys/ecdsa_public.pem --aes-key <hex> --type HEARTBEAT  # 加密
cat envelope.json | ./build/bin/decryptor --ecdsa-key ... --peer-pub ... --aes-key ...  # 解密
```

## 下一阶段

**Phase 2**：Forwarder WsServer + Agent WsClient + ECDH 握手 + 加密信封集成 + 断线重连
