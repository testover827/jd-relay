#!/bin/bash
# ──────────────────────────────────────────
# 一键部署脚本 — Jenkins & 钉钉转发器
# ──────────────────────────────────────────
# 用法:
#   chmod +x deploy/deploy.sh
#   ./deploy/deploy.sh [--ssl] [--dry-run]
#
# 选项:
#   --ssl      配置 Let's Encrypt SSL 证书
#   --dry-run  仅显示将执行的操作，不实际执行
#   --help     显示帮助
# ──────────────────────────────────────────

set -euo pipefail

# ── 颜色输出 ──
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }
log_step()  { echo -e "\n${BLUE}==>${NC} $*"; }

# ── 默认参数 ──
DRY_RUN=false
SETUP_SSL=false
PROJECT_DIR="/opt/jd-relay"
DOMAIN_NAME="${DOMAIN_NAME:-jd-relay.example.com}"
ADMIN_EMAIL="${ADMIN_EMAIL:-admin@example.com}"

# ── 解析参数 ──
while [[ $# -gt 0 ]]; do
    case $1 in
        --ssl)      SETUP_SSL=true; shift ;;
        --dry-run)  DRY_RUN=true; shift ;;
        --domain)   DOMAIN_NAME="$2"; shift 2 ;;
        --email)    ADMIN_EMAIL="$2"; shift 2 ;;
        --help)
            echo "用法: $0 [--ssl] [--dry-run] [--domain DOMAIN] [--email EMAIL]"
            exit 0
            ;;
        *) log_error "未知参数: $1"; exit 1 ;;
    esac
done

# ── 前置检查 ──
log_step "前置检查"

if [ "$(id -u)" -ne 0 ] && [ "$DRY_RUN" = false ]; then
    log_error "请使用 root 权限运行（需要安装系统包和配置 Nginx）"
    exit 1
fi

# 检查 Docker
if ! command -v docker &>/dev/null; then
    log_error "Docker 未安装，请先安装: https://docs.docker.com/engine/install/"
    exit 1
fi

if ! docker compose version &>/dev/null 2>&1; then
    log_error "Docker Compose 未安装或版本过旧"
    exit 1
fi

# 检查 .env.production
if [ ! -f .env.production ]; then
    log_error ".env.production 不存在"
    log_info "请从模板创建: cp deploy/.env.production.example .env.production"
    log_info "然后填写所有必需的环境变量"
    exit 1
fi

# 检查必需的密钥是否已填写
source .env.production
MISSING=()
for VAR in RELAY_API_KEY AES_ENCRYPTION_KEY HMAC_SECRET CONFIG_MASTER_KEY SESSION_SECRET; do
    if [ -z "${!VAR:-}" ] || [ "${!VAR}" = "CHANGE_ME" ]; then
        MISSING+=("$VAR")
    fi
done
if [ ${#MISSING[@]} -gt 0 ]; then
    log_error "以下环境变量未填写: ${MISSING[*]}"
    exit 1
fi
log_info "环境变量检查通过"

# ── 创建目录 ──
log_step "创建数据目录"
if [ "$DRY_RUN" = false ]; then
    mkdir -p "$PROJECT_DIR"/{data,logs}
    mkdir -p /var/www/certbot
fi
log_info "目录已创建"

# ── SSL 证书配置 ──
if [ "$SETUP_SSL" = true ]; then
    log_step "配置 Let's Encrypt SSL 证书"

    if [ "$DRY_RUN" = false ]; then
        # 安装 certbot
        if ! command -v certbot &>/dev/null; then
            log_info "安装 certbot..."
            apt-get update && apt-get install -y certbot
        fi

        # 获取证书
        certbot certonly --standalone \
            -d "$DOMAIN_NAME" \
            --email "$ADMIN_EMAIL" \
            --agree-tos --non-interactive

        # 复制到 deploy 目录
        mkdir -p deploy/nginx/ssl
        cp -L /etc/letsencrypt/live/"$DOMAIN_NAME"/fullchain.pem deploy/nginx/ssl/
        cp -L /etc/letsencrypt/live/"$DOMAIN_NAME"/privkey.pem deploy/nginx/ssl/

        log_info "SSL 证书配置完成"
    fi
else
    log_warn "跳过 SSL 配置（使用 --ssl 启用）"
fi

# ── 替换 Nginx 配置中的变量 ──
log_step "生成 Nginx 配置"
if [ "$DRY_RUN" = false ]; then
    sed "s/\${DOMAIN_NAME}/$DOMAIN_NAME/g" deploy/nginx/nginx.conf > deploy/nginx/nginx.generated.conf
    log_info "Nginx 配置已生成: deploy/nginx/nginx.generated.conf"
fi

# ── 构建并启动 ──
log_step "构建 Docker 镜像"
if [ "$DRY_RUN" = false ]; then
    docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml build --pull
    log_info "镜像构建完成"
fi

log_step "启动服务"
if [ "$DRY_RUN" = false ]; then
    docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml up -d --wait
    log_info "服务已启动"
fi

# ── 健康检查 ──
log_step "健康检查"
if [ "$DRY_RUN" = false ]; then
    sleep 5
    if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
        log_info "✅ 转发器服务运行正常"
    else
        log_error "❌ 健康检查失败，请检查日志: docker compose logs relay"
        exit 1
    fi
fi

# ── 完成 ──
echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║     Jenkins & 钉钉 转发器 — 部署完成              ║"
echo "╠══════════════════════════════════════════════════╣"
echo "║  Web 面板:  https://$DOMAIN_NAME                  ║"
echo "║  API 文档:  https://$DOMAIN_NAME/docs             ║"
echo "║  健康检查:  https://$DOMAIN_NAME/health           ║"
echo "║  查看日志:  docker compose logs -f relay          ║"
echo "╚══════════════════════════════════════════════════╝"
