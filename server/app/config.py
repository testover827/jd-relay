"""配置管理 — 环境变量注入 + 默认值"""

from pathlib import Path
from pydantic_settings import BaseSettings

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    # ── 数据库 ──
    DATABASE_URL: str = f"sqlite+aiosqlite:///{BASE_DIR}/data/relay.db"

    # ── 服务 ──
    RELAY_HOST: str = "0.0.0.0"
    RELAY_PORT: int = 8000
    DEBUG: bool = False

    # ── 钉钉开放平台 ──
    DINGTALK_APP_KEY: str = ""
    DINGTALK_APP_SECRET: str = ""
    DINGTALK_AGENT_ID: str = ""

    # ── Jenkins ──
    JENKINS_URL: str = ""
    JENKINS_USERNAME: str = ""
    JENKINS_API_TOKEN: str = ""

    # ── 安全 ──
    RELAY_API_KEY: str = ""
    AES_ENCRYPTION_KEY: str = ""   # hex-encoded 32 bytes
    HMAC_SECRET: str = ""          # hex-encoded 32 bytes
    CONFIG_MASTER_KEY: str = ""    # hex-encoded 32 bytes

    # ── Web 面板 ──
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD_HASH: str = ""  # bcrypt hash
    SESSION_SECRET: str = ""       # itsdangerous signing key

    # ── 日志 ──
    LOG_LEVEL: str = "INFO"

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()
