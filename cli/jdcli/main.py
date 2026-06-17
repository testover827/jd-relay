"""CLI 工具入口 — Click 命令组"""

import click

from .commands.trigger import request_approval
from .commands.status import wait_approval, check_approval
from .commands.notify import notify_result


@click.group()
def main():
    """Jenkins & 钉钉 转发器 CLI 工具 (jdcli)

    用于 Jenkins Pipeline 中调用转发器的加密 API。
    通过环境变量配置连接参数：
      JD_RELAY_URL    - 转发器地址 (默认 http://localhost:8000)
      JD_API_KEY      - API 认证密钥
      JD_AES_KEY      - AES-256 加密密钥 (hex)
      JD_HMAC_SECRET  - HMAC 签名密钥 (hex)
    """
    pass


main.add_command(request_approval, "request-approval")
main.add_command(wait_approval, "wait-approval")
main.add_command(check_approval, "check-approval")
main.add_command(notify_result, "notify-result")


if __name__ == "__main__":
    main()
