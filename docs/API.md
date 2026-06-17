# API 接口文档 — Jenkins & 钉钉交互转发器 v2.0.0

## 概述

转发器提供 RESTful API，桥接 Jenkins 和钉钉之间的双向审批-构建流程。所有 API 端点（除特别标注外）需要 `X-API-Key` 认证。

**基础 URL**: `https://<domain>`

**认证方式**: HTTP Header `X-API-Key: <your-api-key>`

---

## 目录

1. [健康检查](#1-健康检查)
2. [钉钉回调](#2-钉钉回调)
3. [发起审批](#3-发起审批)
4. [Jenkins 触发构建](#4-jenkins-触发构建)
5. [Jenkins 构建回调](#5-jenkins-构建回调)
6. [构建状态查询](#6-构建状态查询)
7. [Admin API](#7-admin-api)

---

## 1. 健康检查

### `GET /health`

无需认证。用于负载均衡器和监控系统探活。

**响应 200**:
```json
{
  "status": "ok",
  "version": "2.0.0",
  "uptime_seconds": 3600
}
```

---

## 2. 钉钉回调

### `POST /api/v1/dingtalk/callback`

**无需 API Key**（使用钉钉自有签名验证）。钉钉在审批完成后向此端点 POST 审批结果。

**安全机制**:
1. `timestamp` + `nonce` + 钉钉 AppSecret → HMAC-SHA256 签名验证
2. 时间戳重放防护（5 分钟窗口）
3. JSON 注入防护

**请求头**:
| Header      | 必填 | 说明                           |
|-------------|------|--------------------------------|
| timestamp   | 是   | Unix 时间戳（秒）              |
| nonce       | 是   | 随机字符串                     |
| signature   | 是   | HMAC-SHA256(timestamp\nnonce)  |

**请求体**:
```json
{
  "processInstanceId": "pi_abc123",
  "result": "agree",
  "staffId": "manager01"
}
```

`result` 可选值: `agree` → 审批通过, `refuse` → 审批拒绝

**响应 200**:
```json
{"errcode": 0, "errmsg": "ok"}
```

**错误响应**:
```json
{"errcode": 1, "errmsg": "签名验证失败"}
```

---

## 3. 发起审批

### `POST /api/v1/dingtalk/send-approval`

**需要 API Key**。由 Jenkins Pipeline 中的 CLI 工具调用，发起钉钉审批流程。

**请求头**:
| Header     | 必填 | 说明      |
|------------|------|-----------|
| X-API-Key  | 是   | API 密钥  |

**请求体**:
```json
{
  "jenkins_job_name": "deploy/prod-service",
  "build_id": 42,
  "title": "生产环境部署审批",
  "content": "## 变更内容\n- 升级 API 版本到 v2.1",
  "approver_user_ids": ["manager01", "lead02"],
  "encrypted_payload": "base64_encoded_aes_gcm_ciphertext",
  "signature": "hmac_sha256_signature"
}
```

| 字段                | 类型     | 必填 | 说明                              |
|---------------------|----------|------|-----------------------------------|
| jenkins_job_name    | string   | 是   | Jenkins Job 名称/路径             |
| build_id            | int      | 是   | Jenkins 构建号                    |
| title               | string   | 是   | 审批标题（≤200 字符）             |
| content             | string   | 否   | 审批详情（Markdown，≤10000 字符） |
| approver_user_ids   | string[] | 是   | 审批人钉钉 user_id 列表           |
| encrypted_payload   | string   | 是   | AES-GCM 加密的构建回调参数        |
| signature           | string   | 是   | HMAC-SHA256 签名                  |

**响应 200**:
```json
{
  "approval_id": "uuid-v4-here",
  "process_instance_id": "pi_dingtalk_xxx",
  "status": "pending"
}
```

**错误**:
- `401` — 缺少 API Key
- `400` — 参数校验失败
- `500` — 钉钉接口调用失败

---

## 4. Jenkins 触发构建

### `POST /api/v1/jenkins/trigger`

**需要 API Key**。触发 Jenkins Job 构建，支持加密参数合并。

**请求体**:
```json
{
  "job_name": "deploy/prod-service",
  "parameters": {
    "BRANCH": "main",
    "ENV": "production"
  },
  "encrypted_payload": "optional_base64_ciphertext"
}
```

| 字段              | 类型   | 必填 | 说明                                      |
|-------------------|--------|------|-------------------------------------------|
| job_name          | string | 是   | Jenkins Job 名称（支持路径如 folder/job） |
| parameters        | object | 否   | 构建参数键值对                            |
| encrypted_payload | string | 否   | 加密的附加参数（解密后合并到 parameters） |

**响应 200**:
```json
{
  "queue_id": 123,
  "status": "queued",
  "jenkins_url": "https://jenkins.example.com/job/deploy/prod-service/"
}
```

**错误**:
- `401` — 缺少 API Key
- `400` — 加密 payload 解密失败
- `502` — Jenkins 服务不可达或返回异常

---

## 5. Jenkins 构建回调

### `POST /api/v1/jenkins/callback`

**需要 API Key**。Jenkins Pipeline 构建完成后，通过 CLI 或直接 HTTP 通知转发器。

**请求体**:
```json
{
  "job_name": "deploy/prod-service",
  "build_id": 42,
  "result": "SUCCESS",
  "duration_ms": 120000,
  "output_summary": "All tests passed. Deploy completed.",
  "related_approval_id": "uuid-of-approval"
}
```

| 字段                | 类型   | 必填 | 说明                                                |
|---------------------|--------|------|-----------------------------------------------------|
| job_name            | string | 是   | Jenkins Job 名称                                    |
| build_id            | int    | 是   | Jenkins 构建号                                      |
| result              | string | 是   | 构建结果: `SUCCESS`, `FAILURE`, `ABORTED`           |
| duration_ms         | int    | 否   | 构建耗时（毫秒）                                    |
| output_summary      | string | 否   | 输出摘要（≤2000 字符，自动 Markdown 转义）          |
| related_approval_id | string | 否   | 关联的审批单 ID（建立审批→构建关联）                |

**响应 200**:
```json
{"ok": true, "message": "构建记录已更新"}
```

**错误**:
- `401` — 缺少 API Key
- `422` — 参数校验失败（Pydantic）
- `400` — 业务逻辑错误（如缺少 job_name）
- `500` — 内部处理错误

**安全注意**: `output_summary` 中的 Markdown 特殊字符会被自动转义，防止注入攻击。

---

## 6. 构建状态查询

### `GET /api/v1/jenkins/build/{build_id}/status?job_name=<job_name>`

**需要 API Key**。查询 Jenkins 构建状态（合并 Jenkins 远程状态和本地数据库记录）。

**路径参数**:
| 参数     | 类型 | 必填 | 说明             |
|----------|------|------|------------------|
| build_id | int  | 是   | Jenkins 构建号   |

**查询参数**:
| 参数     | 类型   | 必填 | 说明               |
|----------|--------|------|--------------------|
| job_name | string | 是   | Jenkins Job 名称   |

**响应 200**:
```json
{
  "build_id": 42,
  "job_name": "deploy/prod-service",
  "status": "success",
  "progress_pct": 100
}
```

**错误**:
- `401` — 缺少 API Key
- `502` — Jenkins 查询失败

---

## 7. Admin API

Admin API 使用 **Session Cookie + API Key** 双重认证。

### `GET /api/v1/dashboard`

获取仪表盘统计数据。

**响应 200**:
```json
{
  "stats": {
    "total_approvals": 150,
    "pending_approvals": 3,
    "total_builds": 200,
    "running_builds": 2,
    "success_rate_pct": 95.5
  },
  "recent_approvals": [...],
  "recent_builds": [...],
  "uptime_seconds": 86400
}
```

### `GET /api/v1/approvals?page=1&page_size=20&status=pending`

分页查询审批列表。

| 查询参数  | 类型   | 默认值 | 说明                                          |
|-----------|--------|--------|-----------------------------------------------|
| page      | int    | 1      | 页码                                          |
| page_size | int    | 20     | 每页数量（最大 100）                           |
| status    | string | —      | 筛选: pending/approved/rejected/cancelled     |

### `GET /api/v1/approvals/{id}`

获取单个审批详情。

### `GET /api/v1/builds?page=1&page_size=20`

分页查询构建列表。

### `GET /api/v1/logs?page=1&page_size=50&source=jenkins`

分页查询操作日志。

| 查询参数  | 说明                                                    |
|-----------|---------------------------------------------------------|
| source    | 筛选: dingtalk/jenkins/relay/system                     |
| level     | 筛选: DEBUG/INFO/WARNING/ERROR                          |

### `GET /api/v1/sse/logs?source=jenkins`

Server-Sent Events (SSE) 实时日志流。支持 `source` 和 `level` 查询参数筛选。

### `POST /api/v1/config`

更新配置项（白名单控制）。

**请求体**:
```json
{
  "updates": {
    "JENKINS_URL": "https://new-jenkins.example.com",
    "DINGTALK_APP_KEY": "dingxxx"
  }
}
```

### `GET /api/v1/config`

获取当前配置（敏感值显示为 `****`）。

---

## 通用信息

### 错误响应格式

所有错误统一返回 JSON:
```json
{
  "detail": "错误描述",
  "error_type": "JenkinsError",
  "request_id": "abc12345"
}
```

### 速率限制

- API 端点: 每 IP 每秒 20 请求（burst 30）
- 登录接口: 每 IP 每分钟 5 请求（burst 3）
- 健康检查: 不限流

### 数据加密

所有敏感数据传输使用 AES-256-GCM 加密 + HMAC-SHA256 签名，详见安全白皮书。
