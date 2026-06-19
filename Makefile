# JD-Relay — 跨网审批构建转发系统
# Makefile for C++ build/test convenience (runs in WSL)

.PHONY: help configure build test test-unit test-integration clean keys

BUILD_DIR ?= build

help: ## 显示帮助
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

configure: ## CMake 配置 (Ninja)
	cmake -B $(BUILD_DIR) -G Ninja -DCMAKE_BUILD_TYPE=Debug

configure-release: ## CMake 配置 (Release)
	cmake -B $(BUILD_DIR) -G Ninja -DCMAKE_BUILD_TYPE=Release

build: ## 编译全部目标
	cmake --build $(BUILD_DIR) --parallel

test: ## 运行全部测试
	cd $(BUILD_DIR) && ctest --output-on-failure

test-unit: ## 仅运行单元测试
	cd $(BUILD_DIR) && ./bin/crypto_tests

test-integration: ## 仅运行集成测试
	cd $(BUILD_DIR) && ./bin/phase2_tests

clean: ## 清理构建目录
	rm -rf $(BUILD_DIR)

keys: ## 生成 ECDSA + ECDH 密钥对
	$(BUILD_DIR)/bin/keygen config/keys
