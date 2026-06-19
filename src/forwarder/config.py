"""Forwarder configuration.

Reads from forwarder.conf (TOML), environment variables, and defaults.
"""

import os
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ForwarderConfig:
    """JD-Relay Forwarder configuration."""

    # ── Server ────────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8000

    # ── Crypto ────────────────────────────────────────────────────
    ecdsa_private_key_file: str = "config/keys/forwarder_ecdsa_priv.pem"
    ecdsa_public_key_file: str = "config/keys/forwarder_ecdsa_pub.pem"

    # ── MySQL ─────────────────────────────────────────────────────
    mysql_url: str = "mysql+aiomysql://root:root@localhost:3306/jd_relay"

    # ── DingTalk ──────────────────────────────────────────────────
    dingtalk_app_key: str = ""
    dingtalk_app_secret: str = ""
    dingtalk_agent_id: int = 0

    # ── Jenkins ───────────────────────────────────────────────────
    jenkins_url: str = ""
    jenkins_user: str = ""
    jenkins_token: str = ""


def load_config(config_path: str | None = None) -> ForwarderConfig:
    """Load configuration from file and environment variables.

    Priority: env var > config file > default
    """
    config = ForwarderConfig()

    # Try to load TOML config file
    if config_path and os.path.exists(config_path):
        try:
            import tomllib
            with open(config_path, "rb") as f:
                data = tomllib.load(f)
        except ImportError:
            import tomli as tomllib  # Python < 3.11
            with open(config_path, "rb") as f:
                data = tomllib.load(f)

        _apply_toml(config, data)

    # Override from environment variables
    _apply_env(config)

    return config


def _apply_toml(config: ForwarderConfig, data: dict) -> None:
    """Apply TOML config data to ForwarderConfig."""
    # Server
    server = data.get("server", {})
    if "host" in server:
        config.host = server["host"]
    if "port" in server:
        config.port = server["port"]

    # Crypto
    crypto = data.get("crypto", {})
    if "ecdsa_private_key_file" in crypto:
        config.ecdsa_private_key_file = crypto["ecdsa_private_key_file"]
    if "ecdsa_public_key_file" in crypto:
        config.ecdsa_public_key_file = crypto["ecdsa_public_key_file"]

    # MySQL
    mysql = data.get("mysql", {})
    if "url" in mysql:
        config.mysql_url = mysql["url"]

    # DingTalk
    dt = data.get("dingtalk", {})
    if "app_key" in dt:
        config.dingtalk_app_key = dt["app_key"]
    if "app_secret" in dt:
        config.dingtalk_app_secret = dt["app_secret"]
    if "agent_id" in dt:
        config.dingtalk_agent_id = dt["agent_id"]

    # Jenkins
    jk = data.get("jenkins", {})
    if "url" in jk:
        config.jenkins_url = jk["url"]
    if "user" in jk:
        config.jenkins_user = jk["user"]
    if "token" in jk:
        config.jenkins_token = jk["token"]


def _apply_env(config: ForwarderConfig) -> None:
    """Override config from environment variables."""
    env_map = {
        "RELAY_HOST": ("host", str),
        "RELAY_PORT": ("port", int),
        "RELAY_ECDSA_PRIVATE_KEY": ("ecdsa_private_key_file", str),
        "RELAY_ECDSA_PUBLIC_KEY": ("ecdsa_public_key_file", str),
        "RELAY_MYSQL_URL": ("mysql_url", str),
        "RELAY_DINGTALK_APP_KEY": ("dingtalk_app_key", str),
        "RELAY_DINGTALK_APP_SECRET": ("dingtalk_app_secret", str),
        "RELAY_DINGTALK_AGENT_ID": ("dingtalk_agent_id", int),
        "RELAY_JENKINS_URL": ("jenkins_url", str),
        "RELAY_JENKINS_USER": ("jenkins_user", str),
        "RELAY_JENKINS_TOKEN": ("jenkins_token", str),
    }
    for env_var, (attr, typ) in env_map.items():
        val = os.environ.get(env_var)
        if val is not None:
            setattr(config, attr, typ(val))
