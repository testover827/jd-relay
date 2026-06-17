"""CLI 命令：查询审批/构建状态 & 等待审批"""

import os
import json
import click

from ..client import RelayClient
from ..crypto import CLICrypto


def _get_client() -> RelayClient:
    relay_url = os.environ.get("JD_RELAY_URL", "http://localhost:8000")
    api_key = os.environ.get("JD_API_KEY", "")
    aes_key = os.environ.get("JD_AES_KEY", "")
    hmac_secret = os.environ.get("JD_HMAC_SECRET", "")
    crypto = CLICrypto(aes_key, hmac_secret)
    return RelayClient(relay_url, api_key, crypto)


@click.command()
@click.option("--id", "approval_id", required=True, help="审批 ID")
@click.option("--timeout", default=3600, type=int, help="超时秒数，默认 3600")
@click.option("--poll", default=5, type=int, help="轮询间隔秒数，默认 5")
def wait_approval(approval_id, timeout, poll):
    """轮询等待审批结果（阻塞命令，用于 Jenkins Pipeline input step）"""
    client = _get_client()

    click.echo(f"等待审批 {approval_id} (超时: {timeout}s, 间隔: {poll}s)...")

    result = client.wait_for_approval(
        approval_id=approval_id,
        timeout=timeout,
        poll_interval=poll,
    )

    click.echo(json.dumps({"approval_id": approval_id, "result": result}))

    if result == "approved":
        click.echo("APPROVED")
        return
    else:
        click.echo(f"审批未通过: {result}")
        raise SystemExit(1)


@click.command()
@click.option("--id", "approval_id", required=True, help="审批 ID")
def check_approval(approval_id):
    """查询审批状态"""
    client = _get_client()
    try:
        status = client.check_approval_status(approval_id)
        click.echo(json.dumps(status, ensure_ascii=False))
    except Exception as e:
        click.echo(f"错误: {e}", err=True)
        raise SystemExit(1)
