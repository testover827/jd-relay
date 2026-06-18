# CLI 工具使用指南 (jdcli)

## 简介

`jdcli` 是 Jenkins Pipeline 内调用的命令行工具，用于与转发器交互。支持 4 个子命令：

| 命令 | 说明 | 典型场景 |
|------|------|---------|
| `request-approval` | 发起钉钉审批请求 | Pipeline 门禁检测到需要审批 |
| `wait-approval` | 轮询等待审批结果 | Pipeline 阻塞等待审批通过 |
| `check-approval` | 查询审批状态 | Pipeline 检查当前审批进度 |
| `notify-result` | 通知构建结果 | Pipeline 构建完成后通知钉钉 |

---

## 安装

```bash
cd cli
pip install -e .
```

安装后 `jdcli` 命令可用。

---

## 环境变量配置

所有连接参数通过环境变量配置：

| 变量 | 必填 | 说明 | 默认值 |
|------|------|------|--------|
| `JD_API_KEY` | 是 | 转发器 API 认证密钥 | 无 |
| `JD_AES_KEY` | 是 | AES-256 加密密钥（64位十六进制） | 无 |
| `JD_HMAC_SECRET` | 是 | HMAC 签名密钥（64位十六进制） | 无 |
| `JD_RELAY_URL` | 否 | 转发器地址 | `http://localhost:8000` |

> 注意：`JD_AES_KEY` 和 `JD_HMAC_SECRET` 必须与服务端 `.env` 中 `AES_ENCRYPTION_KEY` 和 `HMAC_SECRET` 完全一致，否则加解密/验签会失败。

---

## 命令详解

### 1. request-approval — 发起审批

向转发器发起钉钉审批请求。CLI 会自动加密回调参数（AES-256-GCM）和签名（HMAC-SHA256）。

```bash
jdcli request-approval \
  --job "deploy/prod-service" \
  --build 42 \
  --title "生产环境部署审批" \
  --content "## 变更内容\n- 升级 API 版本到 v2.1" \
  --approvers "manager01,lead02" \
  --originator "user001" \
  --process-code "PROC-XXXX" \
  --callback-params '{"BRANCH":"main","ENV":"production"}'
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--job` | 是 | Jenkins Job 名称/路径 |
| `--build` | 是 | Jenkins Build ID（整数） |
| `--title` | 是 | 审批标题（≤200字符） |
| `--content` | 是 | 审批详情（支持 Markdown，≤10000字符） |
| `--approvers` | 是 | 审批人钉钉 userId，逗号分隔 |
| `--originator` | 否 | 发起人钉钉 userId |
| `--process-code` | 否 | 钉钉审批模板 processCode |
| `--callback-params` | 否 | 审批通过后回调 Jenkins 的 JSON 参数 |

**输出**：

```json
{
  "approval_id": "uuid-v4-here",
  "process_instance_id": "pi_dingtalk_xxx",
  "status": "pending"
}
uuid-v4-here
```

> 最后一行单独输出 `approval_id`，便于 Pipeline 赋值给后续步骤。

**加密流程**：

```
callback_params JSON → AES-256-GCM 加密 → ciphertext + nonce
→ HMAC-SHA256 签名 → POST /api/v1/dingtalk/send-approval
```

---

### 2. wait-approval — 等待审批结果

阻塞式轮询，等待审批通过/拒绝。用于 Jenkins Pipeline 的 `input` step。

```bash
jdcli wait-approval \
  --id "uuid-v4-here" \
  --timeout 3600 \
  --poll 5
```

| 参数 | 必填 | 说明 | 默认值 |
|------|------|------|--------|
| `--id` | 是 | 审批 ID（request-approval 返回的） | — |
| `--timeout` | 否 | 超时秒数 | 3600 |
| `--poll` | 否 | 轮询间隔秒数 | 5 |

**输出**：

- 审批通过：`APPROVED`（退出码 0）
- 审批拒绝/取消/超时：退出码 1

---

### 3. check-approval — 查询审批状态

一次性查询，不阻塞。

```bash
jdcli check-approval --id "uuid-v4-here"
```

**输出**：

```json
{
  "approval_id": "uuid-v4-here",
  "status": "pending",
  "title": "生产环境部署审批",
  "approver_user_ids": ["manager01", "lead02"],
  "created_at": "2026-06-18T10:00:00",
  "updated_at": "2026-06-18T10:30:00"
}
```

---

### 4. notify-result — 通知构建结果

构建完成后通知转发器，转发器会通过钉钉工作通知推送结果给审批人。

```bash
jdcli notify-result \
  --job "deploy/prod-service" \
  --build 42 \
  --result SUCCESS \
  --output "All tests passed. Deploy completed." \
  --approval-id "uuid-v4-here" \
  --duration 120000
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--job` | 是 | Jenkins Job 名称 |
| `--build` | 是 | Jenkins Build ID |
| `--result` | 是 | 构建结果：`SUCCESS` / `FAILURE` / `ABORTED` |
| `--output` | 否 | 构建输出摘要（≤2000字符） |
| `--approval-id` | 否 | 关联的审批 ID |
| `--duration` | 否 | 构建耗时（毫秒） |

**输出**：

```json
{"ok": true, "message": "构建记录已更新"}
```

---

## Jenkins Pipeline 使用示例

### 完整门禁流程（流程2）

```groovy
pipeline {
    agent any

    environment {
        JD_API_KEY      = credentials('jd-relay-api-key')
        JD_AES_KEY      = credentials('jd-relay-aes-key')
        JD_HMAC_SECRET  = credentials('jd-relay-hmac-secret')
        JD_RELAY_URL    = 'https://relay.example.com'
    }

    stages {
        stage('Build') {
            steps {
                sh 'make build'
            }
        }

        stage('Approval Gate') {
            steps {
                script {
                    // 发起审批
                    def result = sh(
                        script: "jdcli request-approval --job ${env.JOB_NAME} --build ${env.BUILD_NUMBER} --title '部署审批' --content '请审批部署' --approvers 'manager01'",
                        returnStdout: true
                    ).trim()

                    def approvalId = result.split('\n').last()

                    // 阻塞等待审批
                    sh "jdcli wait-approval --id ${approvalId} --timeout 3600"
                }
            }
        }

        stage('Deploy') {
            steps {
                sh 'make deploy'
            }
        }
    }

    post {
        always {
            sh "jdcli notify-result --job ${env.JOB_NAME} --build ${env.BUILD_NUMBER} --result ${currentBuild.result ?: 'SUCCESS'}"
        }
    }
}
```

> Jenkins credentials 中存储 `JD_API_KEY`、`JD_AES_KEY`、`JD_HMAC_SECRET`，
> 避免在 Pipeline 脚本中硬编码敏感信息。

---

## 安全说明

| 机制 | 说明 |
|------|------|
| AES-256-GCM 加密 | `callback_params` 加密传输，服务端解密后加密存储 |
| HMAC-SHA256 签名 | 防篡改，服务端验签后才处理 |
| API Key 认证 | `X-API-Key` Header，时序安全比较 |
| 密钥一致性 | CLI 和服务端必须使用相同的 AES Key 和 HMAC Secret |
