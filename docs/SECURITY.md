# 安全白皮书 — Jenkins & 钉钉交互转发器 v2.0.0

## 1. 概述

本文档阐述转发器系统的安全架构设计、威胁模型和防护措施。转发器作为 Jenkins 和钉钉之间的数据中继，承载敏感的 CI/CD 审批和构建信息，安全是其核心设计目标。

## 2. 威胁模型

### 2.1 攻击面分析

| 攻击向量               | 风险等级 | 影响范围         |
|------------------------|----------|------------------|
| API Key 泄露           | 高       | 所有 API 端点    |
| 中间人攻击 (MITM)      | 高       | 传输中的数据     |
| 重放攻击               | 中       | 钉钉回调/构建触发|
| 注入攻击               | 中       | Markdown/JSON    |
| 时序攻击               | 中       | API Key 比较     |
| Session 劫持           | 中       | Admin 面板       |
| 数据泄露               | 高       | 数据库文件       |
| DoS/DDoS               | 低       | 服务可用性       |

### 2.2 信任边界

```
┌─ 不可信区域 ──────────────────────────────────────────┐
│  钉钉平台 (外网)                                        │
│  - 回调请求需签名验证                                   │
│  - 时间戳必须在 5 分钟内                                │
└────────────────────┬───────────────────────────────────┘
                     │ TLS + HMAC-SHA256
┌────────────────────▼───────────────────────────────────┐
│  转发器 (中间服务器)                                    │
│  - 加密存储敏感配置                                     │
│  - API Key 认证                                        │
│  - 输入验证 + 转义                                     │
└────────────────────┬───────────────────────────────────┘
                     │ TLS + Basic Auth
┌────────────────────▼───────────────────────────────────┐
│  Jenkins (内网)                                        │
│  - 仅接受经过审批的构建请求                             │
└────────────────────────────────────────────────────────┘
```

## 3. 安全架构

### 3.1 应用层加密

```
┌─────────────────────────────────────────────────────────┐
│  原始数据: {"job_name": "deploy/prod", "params": {...}} │
└───────────────────────┬─────────────────────────────────┘
                        │
                  ┌─────▼──────┐
                  │  AES-256-  │  使用 AES_ENCRYPTION_KEY
                  │  GCM 加密  │  生成随机 12-byte nonce
                  └─────┬──────┘
                        │
                  ┌─────▼──────┐
                  │  HMAC-     │  使用 HMAC_SECRET
                  │  SHA256    │  签名(密文 + nonce)
                  └─────┬──────┘
                        │
┌───────────────────────▼─────────────────────────────────┐
│  传输格式: {"ciphertext":"base64...",                   │
│             "nonce":"base64...",                         │
│             "signature":"hex..."}                        │
└─────────────────────────────────────────────────────────┘
```

**加密算法**: AES-256-GCM（认证加密）
- 密钥长度: 256 bits
- Nonce: 96 bits 随机生成，永不重用
- 认证标签: 128 bits，自动验证

**签名算法**: HMAC-SHA256
- 密钥长度: 256 bits
- 签名内容: `HMAC(ciphertext || nonce)`
- 使用 `hmac.compare_digest` 进行时序安全比较

### 3.2 配置值加密

敏感配置（Jenkins Token、钉钉 Secret 等）在数据库中加密存储:

```
PBKDF2(CONFIG_MASTER_KEY, salt=config_key, iterations=100000)
  → AES-256-GCM(config_value)
  → 存储密文 + nonce
```

### 3.3 认证体系

| 端点类型           | 认证方式                        | 防攻击措施                     |
|--------------------|--------------------------------|--------------------------------|
| 钉钉回调           | HMAC-SHA256 签名 + 时间戳      | 重放窗口 5min, fail-closed     |
| Jenkins/CLI API    | X-API-Key Header               | hmac.compare_digest 时序安全   |
| Admin 面板         | Session Cookie (itsdangerous)  | 签名 cookie, 7天过期           |
| 健康检查           | 无                             | 仅返回 status/version          |

### 3.4 网络层安全

- **TLS 1.2+**: Nginx 终止 SSL，仅启用安全密码套件
- **HSTS**: `max-age=63072000; includeSubDomains; preload`
- **安全头**: X-Content-Type-Options, X-Frame-Options, X-XSS-Protection
- **内网隔离**: 转发器仅通过内网访问 Jenkins

## 4. 防护措施详解

### 4.1 重放攻击防护

```
钉钉回调:
  timestamp = request.headers["timestamp"]
  if abs(now - timestamp) > 300:
      reject  # 5 分钟窗口

构建触发:
  每个请求携带唯一 nonce
  服务端记录已处理的 nonce（内存 LRU cache）
```

### 4.2 注入防护

**Markdown 注入**（钉钉通知场景）:
```python
def _escape_markdown(text: str) -> str:
    """转义所有 Markdown 特殊字符"""
    escape_chars = r"\`*_{}[]()#+-.!|~"
    for char in escape_chars:
        text = text.replace(char, f"\\{char}")
    return text
```

**JSON 注入**（钉钉 API 调用）:
```python
# 使用 json.dumps() 而非字符串拼接
payload = json.dumps({"content": user_input})
```

**SQL 注入**: 使用 SQLAlchemy ORM 参数化查询，无原始 SQL 拼接

### 4.3 时序攻击防护

```python
# API Key 比较使用 hmac.compare_digest
if not hmac.compare_digest(provided_key, settings.RELAY_API_KEY):
    return 401
```

`compare_digest` 在恒定时间内完成比较，无论输入如何都不会泄露密钥长度或部分匹配信息。

### 4.4 Fail-Closed 安全策略

```python
# 钉钉签名验证 — 缺少 secret 时拒绝所有请求
def verify_callback_signature(timestamp, nonce, signature, body):
    if not self.app_secret:
        logger.error("钉钉 AppSecret 未配置，拒绝所有回调")
        return False  # fail-closed
    # ... 正常验证逻辑
```

### 4.5 敏感数据脱敏

日志中间件自动脱敏:
- 匹配敏感字段名: `password`, `token`, `secret`, `key`, `credential` 等
- 匹配敏感值模式: `Bearer xxx`, 长随机字符串
- 脱敏输出: `****`

### 4.6 Session 安全

- Cookie 使用 `itsdangerous.TimestampSigner` 签名，防篡改
- 7 天自动过期
- `HttpOnly` 和 `Secure` flag（生产环境）
- 登录接口速率限制: 5 req/min

## 5. 部署安全检查清单

- [ ] `.env.production` 中所有密钥已生成且互不相同
- [ ] `DEBUG=false`
- [ ] SSL 证书已配置且自动续期
- [ ] Nginx 仅暴露 443 端口（80 仅用于重定向和 ACME）
- [ ] 防火墙规则限制 SSH 和转发器端口访问
- [ ] 数据库文件权限: `chmod 600 data/relay.db`
- [ ] Docker 容器以非 root 用户运行
- [ ] `no-new-privileges:true` 已启用
- [ ] 日志中无敏感信息泄露
- [ ] 定期备份已配置
- [ ] 监控告警已就绪

## 6. 密钥管理建议

| 密钥               | 轮换周期 | 轮换影响                     |
|--------------------|----------|------------------------------|
| RELAY_API_KEY      | 每季度   | 需更新所有 CLI 调用方        |
| AES_ENCRYPTION_KEY | 每半年   | 历史加密数据无法解密         |
| HMAC_SECRET        | 每半年   | 签名验证失败需同步           |
| SESSION_SECRET     | 每月     | 所有用户需重新登录           |
| CONFIG_MASTER_KEY  | 每半年   | 所有加密配置需重新加密       |

**密钥生成**: `python3 -c "import secrets; print(secrets.token_hex(32))"`

**密钥存储**: 推荐使用 HashiCorp Vault 或云 KMS，最低要求是 `.env.production` 文件权限 600。

## 7. 事件响应

### 疑似 API Key 泄露

1. 立即轮换 `RELAY_API_KEY`
2. 检查访问日志确认泄露期间的异常请求
3. 通知所有 API 使用方更新密钥
4. 评估是否需要轮换数据加密密钥

### 疑似数据泄露

1. 检查数据库访问日志
2. 评估泄露范围（加密数据 vs 明文数据）
3. 轮换所有可能泄露的密钥
4. 通知相关方

### 安全漏洞报告

请通过内部安全渠道报告，勿在公开 Issue 中披露。
