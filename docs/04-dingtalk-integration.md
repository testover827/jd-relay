# 钉钉集成配置指南

## 交互模式总览

钉钉与转发器之间有**两个方向的交互**：

| 方向 | 触发方 | 转发器端点 | 安全机制 |
|------|--------|-----------|---------|
| 钉钉→转发器 | 钉钉审批回调 | `POST /api/v1/dingtalk/callback` | 钉钉 HMAC-SHA256 签名 |
| 转发器→钉钉 | Jenkins CLI | `POST /api/v1/dingtalk/send-approval` | API Key + AES-GCM + HMAC |

---

## 方向一：钉钉审批回调 → 转发器

### 工作原理

钉钉在审批人同意/拒绝审批单后，自动向转发器 POST 审批结果。转发器验证签名、更新审批状态，如审批通过则触发 Jenkins Job。

### 配置步骤

#### 1. 钉钉开放平台配置回调 URL

登录 [钉钉开放平台](https://open-dev.dingtalk.com/)，进入你创建的审批应用：

- **事件订阅** → 添加回调 URL：`https://<你的域名>/api/v1/dingtalk/callback`
- 填入 **token** 和 **aesKey**（钉钉用于签名和加密回调请求）
- 验证 URL 时钉钉会发 GET 请求，转发器需要正确响应

> **注意**：此端点**不需要 API Key**，使用钉钉自有签名验证。转发器中间件已配置白名单跳过认证。

#### 2. `.env` 配置钉钉凭证

```ini
DINGTALK_APP_KEY=dingxxxxxxxx          # 应用 AppKey
DINGTALK_APP_SECRET=xxxxxxxxxxxx       # 应用 AppSecret（签名验证依赖此值）
DINGTALK_AGENT_ID=xxxxxxxxxxxx         # 应用 AgentId（发送工作通知必需）
```

#### 3. 钉钉回调请求格式

钉钉发送 POST 请求时的 Header 和请求体：

**请求头**：

| Header | 说明 |
|--------|------|
| `timestamp` | Unix 时间戳（毫秒） |
| `nonce` | 随机字符串 |
| `signature` | `HMAC-SHA256(AppSecret, timestamp + "\n" + nonce)` 的 Base64 |

**请求体**：

```json
{
  "processInstanceId": "pi_xxxxxx",
  "result": "agree",
  "staffId": "manager01"
}
```

`result` 可选值：
- `agree` — 审批通过 → 转发器触发 Jenkins
- `refuse` — 审批拒绝 → 转发器更新状态为 rejected

#### 4. 转发器处理流程

```
1. 验证钉钉签名（dingtalk_service.verify_callback_signature）
2. 检查 timestamp 时效性（5分钟内有效，防重放攻击）
3. 查找数据库中对应的 Approval 记录
4. 更新审批状态（approved/rejected）
5. 如审批通过：解密存储的 callback payload → 调用 Jenkins API 触发 Job
6. 通过钉钉工作通知推送结果给发起人
```

#### 5. 转发器响应

```json
{"errcode": 0, "errmsg": "ok"}
```

签名验证失败时：

```json
{"errcode": 1, "errmsg": "签名验证失败"}
```

---

## 方向二：转发器 → 钉钉（发起审批）

### 工作原理

Jenkins Pipeline 通过 CLI (`jdcli request-approval`) 发起加密请求到转发器，转发器解密验签后调用钉钉 API 创建审批实例。

### 配置步骤

#### 1. 创建钉钉审批模板

进入钉钉管理后台 → **审批管理** → **创建审批模板**：

- 设置审批表单字段（如：审批内容、Jenkins Job、Build ID）
- 配置审批流程节点（审批人、条件分支）
- 记录 **processCode**（审批模板 ID，CLI `--process-code` 参数需要）

#### 2. 获取审批人 userId

通过以下方式获取审批人的钉钉 `userId`：

- 钉钉管理后台 → 通讯录 → 查看成员详情
- 钉钉 API：`GET /v1.0/contact/users/{userId}`
- 钉钉 API：`GET /v1.0/contact/users`（批量查询）

> **注意**：这是钉钉内部 userId，不是手机号或姓名。

#### 3. CLI 调用

```bash
export JD_RELAY_URL=https://your-relay-domain
export JD_API_KEY=your-api-key
export JD_AES_KEY=your-aes-key-hex      # 64位十六进制，与服务端一致
export JD_HMAC_SECRET=your-hmac-hex      # 64位十六进制，与服务端一致

jdcli request-approval \
  --job "deploy/prod-service" \
  --build 42 \
  --title "生产环境部署审批" \
  --content "变更说明内容" \
  --approvers "manager01,lead02" \
  --process-code "PROC-XXXX" \
  --originator "user001" \
  --callback-params '{"BRANCH":"main","ENV":"production"}'
```

CLI 自动完成：
- 加密 `callback_params`（AES-256-GCM）
- HMAC-SHA256 签名（防篡改）
- POST 到转发器 `/api/v1/dingtalk/send-approval`

#### 4. 转发器处理流程

```
1. 验证 HMAC 签名
2. 解密 callback payload（AES-256-GCM）
3. 加密存储到数据库（PBKDF2 派生密钥二次加密）
4. 调用钉钉 API create_process_instance 创建审批实例
5. 返回 approval_id + process_instance_id
```

#### 5. 等待审批结果

```bash
# 阻塞轮询（最多3600秒）
jdcli wait-approval --id <approval_id> --timeout 3600

# 或查询状态
jdcli check-approval --id <approval_id>
```

#### 6. 构建完成后通知钉钉

```bash
jdcli notify-result \
  --job "deploy/prod-service" \
  --build 42 \
  --result SUCCESS \
  --output "All tests passed"
```

转发器通过钉钉**工作通知 API**（`send_work_notification`）推送构建结果给审批人。

---

## 钉钉开放平台配置清单

| 配置项 | 位置 | 说明 |
|--------|------|------|
| AppKey / AppSecret | 应用管理 → 应用信息 | API 调用凭证 |
| AgentId | 应用管理 → 应用信息 | 工作通知发送必需 |
| 审批回调 URL | 事件订阅 → 审批 | `https://<域名>/api/v1/dingtalk/callback` |
| 审批模板 processCode | 管理后台 → 审批 | CLI `--process-code` 参数 |
| IP 白名单 | 应用管理 → 安全设置 | 添加转发器服务器公网 IP |
| 权限 | 应用管理 → 权限管理 | 审批实例读写、工作通知发送、通讯录读取 |

---

## 网络要求

转发器部署在中间服务器，需满足三个网络条件：

| 条件 | 方向 | 说明 |
|------|------|------|
| 对外可达 | 钉钉→转发器 | 钉钉回调需公网或办公网能访问转发器 |
| 可达钉钉 API | 转发器→钉钉 | `api.dingtalk.com` HTTPS 出口 |
| 可达 Jenkins | 转发器→Jenkins | 内网 Jenkins URL 可访问 |

典型部署架构：

```
┌──────────┐     HTTPS      ┌──────────────┐    HTTPS     ┌──────────┐
│  钉钉云   │ ←────────────→ │  转发器服务器  │ ←──────────→ │ Jenkins  │
│ (办公网)  │    回调+API    │ (中间网络)     │   触发+查询   │ (内网)    │
└──────────┘                └──────────────┘              └──────────┘
```

---

## 测试建议

### 无钉钉凭证时测试

`.env` 中钉钉配置留空即可启动服务，审批/构建的 HTTP 端点仍可正常接收请求，
但调用钉钉 API 的步骤会失败（预期行为）。

### 使用钉钉沙箱环境

钉钉开放平台提供沙箱环境（`sandbox.dingtalk.com`），可以模拟审批流程进行测试，
不影响真实审批数据。

### 手动模拟回调

```bash
# 模拟钉钉审批回调（需计算签名）
curl -X POST http://localhost:8000/api/v1/dingtalk/callback \
  -H "Content-Type: application/json" \
  -H "timestamp: $(date +%s000)" \
  -H "nonce: test123" \
  -H "signature: <计算出的签名>" \
  -d '{"processInstanceId":"pi_test","result":"agree","staffId":"manager01"}'
```

---

## 常见问题

| 问题 | 原因 | 解决方案 |
|------|------|---------|
| 回调签名验证失败 | AppSecret 不匹配 | 确认 `.env` 中 `DINGTALK_APP_SECRET` 与钉钉平台一致 |
| 回调 timestamp 过期 | 时钟偏移或延迟 | 确保服务器时钟准确，转发器允许 5 分钟窗口 |
| 审批实例创建失败 | processCode 无效或权限不足 | 检查审批模板 ID 和应用权限 |
| 工作通知发送失败 | AgentId 缺失或权限不足 | 配置 `DINGTALK_AGENT_ID`，确认消息发送权限 |
| Jenkins 触发失败 | Jenkins URL/凭证错误 | 检查 `.env` 中 Jenkins 配置 |
