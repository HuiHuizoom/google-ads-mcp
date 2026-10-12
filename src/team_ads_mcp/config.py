from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import Field

from .errors import AdsError
from .models import Account, Money, StrictModel, digest, micros

API_VERSION = "v25"
ADS_SCOPE = "https://www.googleapis.com/auth/adwords"


class AccountPolicy(StrictModel):
    account: Account
    writes: bool = False
    max_daily_budget: Money = "100"


class Settings(StrictModel):
    mode: Literal["google"] = "google"
    auth_mode: Literal["oauth", "adc"] = "oauth"
    cloud_project: str = Field(default="", pattern=r"^$|^[a-z][a-z0-9-]{4,61}[a-z0-9]$")
    auth_epoch: str = "unconnected"
    oauth_authorization_recorded: bool = False
    accounts: list[AccountPolicy] = Field(default_factory=list)
    plan_ttl_seconds: int = Field(default=900, ge=60, le=3600)
    timeout_seconds: int = Field(default=30, ge=5, le=120)

    def policy(self, account: Account, write: bool = False) -> AccountPolicy:
        for policy in self.accounts:
            if policy.account == account:
                if write and not policy.writes:
                    raise AdsError("WRITE_DISABLED", "该账户未在本机配置中启用写入")
                return policy
        raise AdsError(
            "ACCOUNT_NOT_ALLOWED", "账户及 MCC 路径不在本机允许列表，请使用 team-ads configure 添加"
        )

    def limit(self, account: Account, amount_micros: int):
        if amount_micros > micros(self.policy(account, write=True).max_daily_budget):
            raise AdsError("BUDGET_LIMIT", "金额超过本机配置的账户日预算上限")

    def binding(self) -> str:
        return digest(self.model_dump())


def private_dir(path: Path) -> Path:
    if path.is_symlink():
        raise AdsError("UNSAFE_STATE_PATH", "状态目录不能是符号链接")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


def state_path() -> Path:
    return private_dir(Path(os.environ.get("TEAM_ADS_STATE_DIR", ".state")).absolute())


def load_settings(path: Path) -> Settings:
    config = path / "config.json"
    if not config.exists():
        return Settings()
    return Settings.model_validate_json(config.read_text())


def save_settings(path: Path, settings: Settings):
    private_dir(path)
    tmp = path / "config.tmp"
    with open(tmp, "w", opener=lambda p, f: os.open(p, f, 0o600)) as fp:
        fp.write(json.dumps(settings.model_dump(), indent=2))
    os.replace(tmp, path / "config.json")
