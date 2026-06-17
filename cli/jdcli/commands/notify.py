"""CLI 命令：通知转发器构建结果"""

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
@click.option("--job", required=True, help="Jenkins Job 名称")
@click.option("--build", required=True, type=int, help="Jenkins Build ID")
@click.option("--result", required=True,
              type=click.Choice(["SUCCESS", "FAILURE", "ABORTED"]),
              help="构建结果")
@click.option("--output", default="", help="构建输出摘要")
@click.option("--approval-id", default=None, help="关联的审批 ID")
@click.option("--duration", default=None, type=int, help="构建耗时(ms)")
def notify_result(job, build, result, output, approval_id, duration):
    """通知转发器构建结果"""
    client = _get_client()
    try:
        resp = client.notify_build_result(
            job_name=job,
            build_id=build,
            result=result,
            output_summary=output,
            approval_id=approval_id,
            duration_ms=duration,
        )
        click.echo(json.dumps(resp, ensure_ascii=False))
    except Exception as e:
        click.echo(f"错误: {e}", err=True)
        raise SystemExit(1)
