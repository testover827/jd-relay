# JD-Relay Python Forwarder Makefile
# Development commands for the Python Forwarder

.PHONY: help install test test-unit test-integration test-all run keys clean

help:
	@echo "JD-Relay Python Forwarder"
	@echo ""
	@echo "  make install      Install dependencies"
	@echo "  make test         Run all Python tests"
	@echo "  make test-unit    Run crypto unit tests"
	@echo "  make test-ws      Run WebSocket tests"
	@echo "  make test-cross   Run cross-language tests"
	@echo "  make run          Start Forwarder (dev)"
	@echo "  make keys         Generate ECDSA key pair"
	@echo "  make clean        Clean build artifacts"

install:
	python -m pip install -e ".[dev]"

test:
	python -m pytest tests/python/ -v

test-unit:
	python -m pytest tests/python/ -v -k "not ws_server and not cross_language and not e2e"

test-ws:
	python -m pytest tests/python/test_ws_server.py -v

test-cross:
	python -m pytest tests/python/test_cross_language.py -v

run:
	python -m forwarder.main

keys:
	@mkdir -p config/keys
	python -c "\
from forwarder.crypto import EcdsaSigner;\
EcdsaSigner.generate_keypair('config/keys/forwarder_ecdsa_priv.pem', 'config/keys/forwarder_ecdsa_pub.pem');\
print('Keys generated: config/keys/forwarder_ecdsa_*.pem')"

clean:
	rm -rf .pytest_cache
	rm -rf __pycache__ src/forwarder/**/__pycache__
	find . -name "*.pyc" -delete
