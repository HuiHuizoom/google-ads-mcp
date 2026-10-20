from __future__ import annotations

import argparse
import json
import logging
import os
from importlib.metadata import version
from pathlib import Path

from pydantic import ValidationError

from .auth import disconnect, login
from .config import API_VERSION, AccountPolicy, load_settings, save_settings, state_path
from .errors import safe_error
from .models import Account, micros
from .server import main as serve_main
from .server import make_engine


def parser():
    root = argparse.ArgumentParser(
        prog="team-ads", description="团队本地 Google Ads MCP；secret/token 不通过聊天传递"
    )
    root.add_argument("--state-dir", help="本机私有状态目录，默认当前目录 .state")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("serve", help="启动 stdio MCP")
    commands.add_parser("doctor", help="无网络检查本机版本和配置")
    configure = commands.add_parser(
        "configure", help="本机账户白名单与预算边界；不修改 Google Cloud"
    )
    configure.add_argument("--cloud-project", required=True)
    configure.add_argument("--customer-id", required=True)
    configure.add_argument("--login-customer-id")
    configure.add_argument(
        "--allow-write", action="store_true", help="允许该账户通过预览校验后提交写入"
    )
    configure.add_argument("--max-daily-budget", default="100", help="账户本币字符串")
    configure.add_argument("--auth-mode", choices=["oauth", "adc"], default="oauth")
    auth = commands.add_parser("auth", help="授权由本机浏览器完成，凭据放 OS Keychain")
    auth_cmd = auth.add_subparsers(dest="auth_command", required=True)
    login_cmd = auth_cmd.add_parser("login", help="Desktop OAuth loopback + PKCE")
    login_cmd.add_argument("--client-file", type=Path, required=True)
    logout_cmd = auth_cmd.add_parser("logout", help="移除本地凭据，可选择撤销 Google 授权")
    logout_cmd.add_argument(
        "--revoke", action="store_true", help="同时向 Google 提交撤销（影响此 OAuth 授权）"
    )
    for command, help_text in [
        ("show-operation", "显示完整 before/after 计划"),
        ("submit", "提交已预览校验的计划并回读"),
        ("reconcile", "核对结果不明的操作，绝不重发"),
    ]:
        child = commands.add_parser(command, help=help_text)
        child.add_argument("operation_id")
    recovery = commands.add_parser(
        "abandon-unknown-creation",
        help="仅人工接受无回执暂停创建的未知结果，保留审计并解除阻塞；不是取消 Google 请求",
    )
    recovery.add_argument("operation_id")
    recovery.add_argument("--expected-hash", required=True)
    recovery.add_argument("--reason", required=True)
    recovery.add_argument("--accept-unknown-outcome", action="store_true", required=True)
    return root


def main():
    logging.disable(logging.CRITICAL)
    args = parser().parse_args()
    if args.state_dir:
        os.environ["TEAM_ADS_STATE_DIR"] = str(Path(args.state_dir).absolute())
    if args.command == "serve":
        return serve_main()
    path = state_path()
    try:
        settings = load_settings(path)
        if args.command == "doctor":
            result = {
                "python_dependencies": {
                    name: version(name) for name in ["google-ads", "mcp", "google-auth-oauthlib"]
                },
                "api_version": API_VERSION,
                "state_dir": str(path),
                "config": settings.model_dump(),
                "live_google_checked": False,
                "next_step": "configure → auth login → MCP discover_accounts/preflight",
            }
        elif args.command == "configure":
            account = Account(
                customer_id=args.customer_id, login_customer_id=args.login_customer_id
            )
            micros(args.max_daily_budget)
            settings.cloud_project = args.cloud_project
            settings.auth_mode = args.auth_mode
            settings.accounts = [p for p in settings.accounts if p.account != account] + [
                AccountPolicy(
                    account=account, writes=args.allow_write, max_daily_budget=args.max_daily_budget
                )
            ]
            save_settings(path, settings)
            result = {
                "saved": True,
                "google_cloud_modified": False,
                "config": settings.model_dump(),
            }
        elif args.command == "auth":
            result = (
                login(path, settings, args.client_file)
                if args.auth_command == "login"
                else disconnect(path, settings, args.revoke)
            )
        else:
            engine = make_engine()
            with engine.provider.request_scope():
                if args.command == "show-operation":
                    result = engine.ledger.get(args.operation_id)
                elif args.command == "submit":
                    result = engine.submit(args.operation_id)
                elif args.command == "abandon-unknown-creation":
                    result = engine.abandon_unknown_creation(
                        args.operation_id, args.expected_hash, args.reason
                    )
                else:
                    result = engine.reconcile(args.operation_id)
        from .media import public_payload

        print(json.dumps(public_payload(result), ensure_ascii=False, indent=2))
    except (ValidationError, ValueError):
        print(
            json.dumps(
                {
                    "error": {
                        "code": "INVALID_INPUT",
                        "message": "本机输入或配置无效，请检查参数格式",
                    }
                },
                ensure_ascii=False,
            )
        )
        raise SystemExit(2) from None
    except Exception as exc:
        print(json.dumps({"error": safe_error(exc).public()}, ensure_ascii=False))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
