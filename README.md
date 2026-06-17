# Jenkins & 钉钉 交互转发器

部署在中间服务器的消息中继系统，桥接 Jenkins（CI/CD 内网）与钉钉（办公协作平台）的双向审批触发流程。

## 架构

```
Jenkins (内网A)  ←──TLS+AES──→  转发器 (FastAPI)  ←──TLS+AES──→  钉钉 (办公网B)
                                      │
                                Web 面板 (SSR+SSE)
                                http://host:8000
```

## 两个核心流程

### 流程1：钉钉审批 → 触发 Jenkins
钉钉审批单填写构建参数 → 审批通过 → 转发器解密参数 → 调用 Jenkins API 触发 Job

### 流程2：Jenkins 构建门禁 → 钉钉审批 → 继续构建
Jenkins Pipeline 检测特殊文件变更 → CLI 发起加密审批请求 → 钉钉 Leader 审批 → 回调 Pipeline 继续构建

## 快速开始

### 1. 生成密钥

```bash
make keys
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入钉钉和 Jenkins 的配置
```

### 3. 生成管理员密码

```bash
python3 -c "
import bcrypt
pwd = input('Admin password: ')
print(bcrypt.hashpw(pwd.encode(), bcrypt.gensalt()).decode())
"
# 将输出的 hash 填入 .env 的 ADMIN_PASSWORD_HASH
```

### 4. 启动服务

```bash
# Docker 部署
docker compose up -d

# 或本地开发
make dev
```

### 5. 验证

```bash
curl http://localhost:8000/health
# {"status": "ok", "version": "0.1.0"}
```

打开浏览器访问 `http://localhost:8000` 进入 Web 面板。

## Jenkins Pipeline 集成

### 安装 CLI 工具

```bash
cd cli && pip install -e .
```

### 配置环境变量

```groovy
// Jenkins Pipeline 中
environment {
    JD_RELAY_URL    = 'http://relay-server:8000'
    JD_API_KEY      = credentials('jd-api-key')
    JD_AES_KEY      = credentials('jd-aes-key')
    JD_HMAC_SECRET  = credentials('jd-hmac-secret')
}
```

### Pipeline 示例

```groovy
// 检测特殊文件后发起审批
stage('钉钉审批') {
    steps {
        script {
            sh """
                jdcli request-approval \
                    --job "${env.JOB_NAME}" \
                    --build ${env.BUILD_ID} \
                    --title "生产环境部署审批" \
                    --content "变更文件: config/production.yaml" \
                    --approvers "manager001"
            """
            // 轮询等待审批
            def result = sh(
                script: "jdcli wait-approval --id \${APPROVAL_ID} --timeout 3600",
                returnStatus: true
            )
            if (result != 0) { error("审批未通过") }
        }
    }
}
```

完整示例见 `jenkins/Jenkinsfile.example`。

## CLI 命令

```bash
# 发起审批
jdcli request-approval --job <name> --build <id> --title "..." --content "..." --approvers "user1,user2"

# 轮询等待审批
jdcli wait-approval --id <approval_id> --timeout 3600 --poll 5

# 查询审批状态
jdcli check-approval --id <approval_id>

# 通知构建结果
jdcli notify-result --job <name> --build <id> --result SUCCESS --output "构建成功"
```

## API 端点

| 路径 | 方法 | 说明 |
|------|------|------|
| `/health` | GET | 健康检查 |
| `/api/v1/dingtalk/callback` | POST | 钉钉审批回调 |
| `/api/v1/dingtalk/send-approval` | POST | 发起审批 |
| `/api/v1/jenkins/trigger` | POST | 触发构建 |
| `/api/v1/jenkins/callback` | POST | 构建结果回调 |
| `/api/v1/jenkins/build/{id}/status` | GET | 查询构建状态 |
| `/api/v1/admin/dashboard` | GET | 仪表盘数据 |
| `/api/v1/admin/approvals` | GET | 审批列表 |
| `/api/v1/admin/builds` | GET | 构建列表 |
| `/api/v1/admin/logs` | GET | 请求日志 |
| `/api/v1/admin/logs/stream` | GET | SSE 实时日志流 |
| `/api/v1/admin/config` | GET/PUT | 配置管理 |

## 项目结构

```
jenkins-dingtalk-relay/
├── docker-compose.yml
├── .env.example
├── Makefile
├── server/              # FastAPI 服务端
│   └── app/
│       ├── api/         # REST API 路由
│       ├── services/    # 业务逻辑层
│       ├── middleware/  # 认证/日志中间件
│       └── models.py    # SQLAlchemy 模型
├── cli/                 # Python CLI 工具
├── jenkins/             # Pipeline 脚本
├── tests/               # 测试套件
│   ├── unit/            # 81 单元测试
│   ├── integration/     # 48 集成测试
│   └── e2e/             # 11 E2E 测试
├── deploy/              # 生产部署配置
│   ├── docker/          # 生产 Dockerfile
│   ├── nginx/           # Nginx 配置
│   └── docker-compose.prod.yml
└── docs/                # 文档
    ├── API.md           # API 接口文档
    ├── OPERATIONS.md    # 运维手册
    ├── SECURITY.md      # 安全白皮书
    └── TROUBLESHOOTING.md # 故障排查指南
```

## 文档

| 文档 | 说明 |
|------|------|
| [API 接口文档](docs/API.md) | 完整 API 参考，含请求/响应示例 |
| [运维手册](docs/OPERATIONS.md) | 部署、配置、备份、升级流程 |
| [安全白皮书](docs/SECURITY.md) | 威胁模型、加密方案、安全检查清单 |
| [故障排查指南](docs/TROUBLESHOOTING.md) | 常见问题诊断与解决 |

## 测试

```bash
# 运行全部测试 (137 passed, 3 skipped)
pytest tests/ -v

# 分类运行
pytest tests/unit/ -v         # 单元测试 (81)
pytest tests/integration/ -v  # 集成测试 (48)
pytest tests/e2e/ -v          # E2E 测试 (8 passed, 3 skipped)
```

## 安全

- **传输层**: TLS 1.2+ 加密通信
- **应用层**: AES-256-GCM 加密 + HMAC-SHA256 签名
- **配置存储**: PBKDF2 派生密钥二次加密敏感配置
- **认证**: API Key (Jenkins/CLI) + 钉钉回调签名验证 + Session (Web 面板)
