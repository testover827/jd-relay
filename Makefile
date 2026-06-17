.PHONY: help up down build logs shell keys test

help: ## 显示帮助信息
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

up: ## 启动服务
	docker compose up -d

down: ## 停止服务
	docker compose down

build: ## 构建镜像
	docker compose build

logs: ## 查看日志
	docker compose logs -f relay

shell: ## 进入容器
	docker compose exec relay bash

keys: ## 生成安全密钥
	@echo "AES_ENCRYPTION_KEY=$$(python3 -c 'import os; print(os.urandom(32).hex())')"
	@echo "HMAC_SECRET=$$(python3 -c 'import os; print(os.urandom(32).hex())')"
	@echo "CONFIG_MASTER_KEY=$$(python3 -c 'import os; print(os.urandom(32).hex())')"
	@echo "RELAY_API_KEY=$$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
	@echo "SESSION_SECRET=$$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"

test: ## 运行测试
	cd server && python -m pytest tests/ -v

dev: ## 本地开发模式启动
	cd server && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
