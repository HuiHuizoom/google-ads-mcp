from __future__ import annotations

import hashlib
import json
import os
import plistlib
import subprocess
import sys
from pathlib import Path

from .errors import AdsError


def review_text(plan):
    body = plan["body"]
    request, prepared = body["request"], body["prepared"]
    account = prepared["before"]["account"]
    change = request["change"]
    kind_names = {
        "create_search": "创建暂停的 Search 广告",
        "set_budget": "修改平均日预算",
        "set_status": "修改启用/暂停状态",
        "add_keywords": "添加广告组关键词",
        "add_negatives": "添加 Campaign 否定词",
        "remove_keywords": "移除关键词",
    }
    lines = [
        "请审核以下具体变更",
        "",
        f"账户：{account.get('descriptive_name', '')} ({request['account']['customer_id']})",
        f"MCC 路径：{request['account'].get('login_customer_id') or '直接访问'}",
        f"币种 / 时区：{account['currency_code']} / {account['time_zone']}",
        f"动作：{kind_names[change['kind']]}",
        f"计划到期：{body['expires_at']}",
        f"API 预检：{body['validation']['source']} / {body['validation']['at']}",
    ]
    if "daily_amount" in change:
        lines.append(f"目标平均日预算：{change['daily_amount']} {change['currency']}")
        lines.append("这是预算参数，不是单日扣费硬上限；Google 可能在个别日期花费更多。")
    if change["kind"] == "set_status":
        current = prepared["before"]["resource"]
        resource = next(
            k for k, v in current.items() if v.get("resource_name") == change["resource"]
        )
        lines.append(
            f"资源：{change['resource']}\n状态：{current[resource].get('status')} → {change['status']}"
        )
    if change["kind"] == "set_budget":
        lines.append("受影响的全部 Campaign：")
        lines.extend(
            f"  {r['campaign']['name']} / {r['campaign']['resource_name']}"
            for r in prepared["before"]["campaigns"]
        )
    if change["kind"] == "create_search":
        lines.extend(
            [
                f"名称：{change['name']}",
                "Campaign、广告组、广告均为 PAUSED；关键词随父级保持不投放。",
                f"地域 ID：{', '.join(change['geo_target_ids'])}；语言 ID：{', '.join(change['language_ids'])}",
                "出价：手动 CPC；仅 Google Search；地域采用实际所在地 PRESENCE。",
            ]
        )
    lines.extend(
        [
            "",
            *prepared["warnings"],
            "",
            "具体输入（请检查文案、网址、关键词、金额和资源）：",
            json.dumps(change, ensure_ascii=False, indent=2),
            "",
            "当前远端状态和完整变更计划：",
            json.dumps(plan, ensure_ascii=False, indent=2),
        ]
    )
    return "\n".join(lines)


def approve_with_user_presence(engine, plan_id):
    plan = engine.approval_candidate(plan_id)
    if sys.platform != "darwin":
        raise AdsError(
            "APPROVAL_PLATFORM_UNSUPPORTED",
            "此版本的可信本机审批需要 macOS；其他系统可读取和预检，写入审批暂不开放",
        )
    source = Path(__file__).with_name("approval.swift")
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
    bundle = engine.path / ("Team Ads Approval-" + source_hash + ".app")
    contents = bundle / "Contents"
    executable = contents / "MacOS" / "TeamAdsApproval"
    if not executable.exists():
        executable.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        with (contents / "Info.plist").open("wb") as fp:
            plistlib.dump(
                {
                    "CFBundleIdentifier": "local.team-ads.approval",
                    "CFBundleName": "Team Ads Approval",
                    "CFBundleExecutable": "TeamAdsApproval",
                    "CFBundlePackageType": "APPL",
                    "LSUIElement": True,
                    "NSHighResolutionCapable": True,
                },
                fp,
            )
        result = subprocess.run(
            [
                "/usr/bin/swiftc",
                str(source),
                "-o",
                str(executable),
                "-framework",
                "AppKit",
                "-framework",
                "LocalAuthentication",
            ],
            capture_output=True,
            timeout=120,
            check=False,
        )
        if result.returncode:
            raise AdsError(
                "APPROVAL_BUILD_FAILED", "本机审批组件编译失败，需要 Xcode Command Line Tools"
            )
        executable.chmod(0o700)
    review = engine.path / ("review-" + plan_id + ".txt")
    try:
        fd = os.open(review, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as fp:
            fp.write(review_text(plan))
        customer = plan["body"]["request"]["account"]["customer_id"]
        result = subprocess.run(
            [str(executable), str(review), plan["plan_hash"], customer],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        if result.returncode or result.stdout.strip() != "APPROVED " + plan["plan_hash"]:
            raise AdsError("APPROVAL_CANCELLED", "审批已取消或本机身份验证未完成")
        # Native UI may have stayed open until the plan expired or config changed.
        from .config import load_settings

        engine.settings = load_settings(engine.path)
        engine.approval_candidate(plan_id)
        engine.ledger.approve(plan_id, plan["plan_hash"], "macos_device_owner_authentication")
        return {
            "operation_id": plan_id,
            "approved_plan_hash": plan["plan_hash"],
            "submitted": False,
        }
    finally:
        review.unlink(missing_ok=True)
