"""Forwarder configuration — v3.0

配置来源优先级：环境变量 > TOML 配置文件 > 默认值
"""

import os
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ForwarderConfig:
    """JD-Relay Forwarder 完整配置"""

    # ── Server ────────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8000

    # ── Crypto ────────────────────────────────────────────────────
    ecdsa_private_key_file: str = "config/keys/forwarder_ecdsa_priv.pem"
    ecdsa_public_key_file: str = "config/keys/forwarder_ecdsa_pub.pem"

    # ── MySQL ─────────────────────────────────────────────────────
    mysql_url: str = "mysql+aiomysql://root:root@localhost:3306/jd_relay"

    # ── DingTalk（官方 SDK 配置）──────────────────────────────────
    dingtalk_app_key: str = ""
    dingtalk_app_secret: str = ""
    dingtalk_agent_id: int = 0

    # 三人会签审批人 userId 列表（逗号分隔的字符串或列表）
    dingtalk_approvers: list = field(default_factory=list)
    # 审批流程模板 processCode（在钉钉审批管理页面 URL 中获取）
    dingtalk_process_code: str = ""
    # 发起人 userId（工单提交人，可动态传入或固定配置）
    dingtalk_originator: str = ""

    # ── Jenkins ───────────────────────────────────────────────────
    jenkins_url: str = ""
    jenkins_user: str = ""
    jenkins_token: str = ""

    # ── Admin Web Panel ───────────────────────────────────────────
    # 管理面板 Basic Auth（生产环境请改为强密码）
    admin_username: str = "admin"
    admin_password: str = "changeme"


def load_config(config_path: str | None = None) -> ForwarderConfig:
    """加载配置（优先级：环境变量 > 配置文件 > 默认值）"""
    config = ForwarderConfig()

    if config_path and os.path.exists(config_path):
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib  # type: ignore[no-redef]
        with open(config_path, "rb") as f:
            data = tomllib.load(f)
        _apply_toml(config, data)

    _apply_env(config)
    return config


def _apply_toml(config: ForwarderConfig, data: dict) -> None:
    """将 TOML 配置写入 ForwarderConfig"""
    server = data.get("server", {})
    if "host" in server:
        config.host = server["host"]
    if "port" in server:
        config.port = server["port"]

    crypto = data.get("crypto", {})
    if "ecdsa_private_key_file" in crypto:
        config.ecdsa_private_key_file = crypto["ecdsa_private_key_file"]
    if "ecdsa_public_key_file" in crypto:
        config.ecdsa_public_key_file = crypto["ecdsa_public_key_file"]

    mysql = data.get("mysql", {})
    if "url" in mysql:
        config.mysql_url = mysql["url"]

    dt = data.get("dingtalk", {})
    if "app_key" in dt:
        config.dingtalk_app_key = dt["app_key"]
    if "app_secret" in dt:
        config.dingtalk_app_secret = dt["app_secret"]
    if "agent_id" in dt:
        config.dingtalk_agent_id = int(dt["agent_id"])
    if "approvers" in dt:
        config.dingtalk_approvers = dt["approvers"]  # TOML array
    if "process_code" in dt:
        config.dingtalk_process_code = dt["process_code"]
    if "originator" in dt:
        config.dingtalk_originator = dt["originator"]

    jk = data.get("jenkins", {})
    if "url" in jk:
        config.jenkins_url = jk["url"]
    if "user" in jk:
        config.jenkins_user = jk["user"]
    if "token" in jk:
        config.jenkins_token = jk["token"]

    admin = data.get("admin", {})
    if "username" in admin:
        config.admin_username = admin["username"]
    if "password" in admin:
        config.admin_password = admin["password"]


def _apply_env(config: ForwarderConfig) -> None:
    """从环境变量覆盖配置"""
    env_map: dict[str, tuple] = {
        "RELAY_HOST": ("host", str),
        "RELAY_PORT": ("port", int),
        "RELAY_ECDSA_PRIVATE_KEY": ("ecdsa_private_key_file", str),
        "RELAY_ECDSA_PUBLIC_KEY": ("ecdsa_public_key_file", str),
        "RELAY_MYSQL_URL": ("mysql_url", str),
        "RELAY_DINGTALK_APP_KEY": ("dingtalk_app_key", str),
        "RELAY_DINGTALK_APP_SECRET": ("dingtalk_app_secret", str),
        "RELAY_DINGTALK_AGENT_ID": ("dingtalk_agent_id", int),
        "RELAY_DINGTALK_PROCESS_CODE": ("dingtalk_process_code", str),
        "RELAY_DINGTALK_ORIGINATOR": ("dingtalk_originator", str),
        "RELAY_JENKINS_URL": ("jenkins_url", str),
        "RELAY_JENKINS_USER": ("jenkins_user", str),
        "RELAY_JENKINS_TOKEN": ("jenkins_token", str),
        "RELAY_ADMIN_USERNAME": ("admin_username", str),
        "RELAY_ADMIN_PASSWORD": ("admin_password", str),
    }
    for env_var, (attr, typ) in env_map.items():
        val = os.environ.get(env_var)
        if val is not None:
            setattr(config, attr, typ(val))

    # 审批人列表：逗号分隔
    approvers_env = os.environ.get("RELAY_DINGTALK_APPROVERS", "")
    if approvers_env:
        config.dingtalk_approvers = [u.strip() for u in approvers_env.split(",") if u.strip()]
