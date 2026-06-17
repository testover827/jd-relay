"""CLI 命令：触发审批请求"""

import os
import sys
import click

from ..client import RelayClient
from ..crypto import CLICrypto


def _get_client() -> RelayClient:
    relay_url = os.environ.get("JD_RELAY_URL", "http://localhost:8000")
    api_key = os.environ.get("JD_API_KEY", "")
    aes_key = os.environ.get("JD_AES_KEY", "")
    hmac_secret = os.environ.get("JD_HMAC_SECRET", "")

    if not api_key:
        click.echo("错误: JD_API_KEY 环境变量未设置", err=True)
        sys.exit(1)

    crypto = CLICrypto(aes_key, hmac_secret)
    return RelayClient(relay_url, api_key, crypto)


@click.command()
@click.option("--job", required=True, help="Jenkins Job 名称")
@click.option("--build", required=True, type=int, help="Jenkins Build ID")
@click.option("--title", required=True, help="审批标题")
@click.option("--content", required=True, help="审批内容")
@click.option("--approvers", required=True, help="审批人 ID，逗号分隔")
@click.option("--originator", default="", help="发起人钉钉 userId")
@click.option("--process-code", default="", help="钉钉审批模板 processCode")
@click.option("--callback-params", default="{}", help="审批通过后回调 Jenkins 的 JSON 参数")
def request_approval(job, build, title, content, approvers, originator, process_code, callback_params):
    """向转发器发起钉钉审批请求（Jenkins Pipeline 中调用）"""
    import json

    approver_list = [a.strip() for a in approvers.split(",") if a.strip()]
    try:
        cb_params = json.loads(callback_params)
    except json.JSONDecodeError:
        click.echo("错误: --callback-params 必须是有效的 JSON", err=True)
        sys.exit(1)

    client = _get_client()
    try:
        result = client.request_approval(
            job_name=job,
            build_id=build,
            title=title,
            content=content,
            approver_ids=approver_list,
            callback_params=cb_params,
            originator_user_id=originator,
            process_code=process_code,
        )
        click.echo(json.dumps(result, ensure_ascii=False))
        # 输出 approval_id 供 Pipeline 后续步骤使用
        click.echo(result.get("approval_id", ""))
    except Exception as e:
        click.echo(f"错误: {e}", err=True)
        sys.exit(1)
